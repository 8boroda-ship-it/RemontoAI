from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from .config import Config
from .db import Database
from .domain import CITIES, STATUS_LABELS
from .formatters import esc, estimate_price, money
from .keyboards import client_menu

logger = logging.getLogger(__name__)


class RequestForm(StatesGroup):
    city = State()
    description = State()
    address = State()
    preferred_time = State()
    phone = State()


def _city_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=city, callback_data=f"r:city:{index}")]
            for index, city in enumerate(CITIES)
        ]
    )


def build_client_router(db: Database, config: Config) -> Router:
    router = Router(name="client")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        is_admin = message.from_user.id in config.admin_ids
        await message.answer(
            "<b>REMONTO AI</b> — домашний сервис\n\n"
            "Опишите задачу через короткую форму. Мастер увидит заявку, "
            "уточнит детали и подтвердит стоимость.",
            reply_markup=client_menu(is_admin),
        )

    @router.message(Command("cancel"))
    @router.message(F.text.casefold() == "отмена")
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Действие отменено.",
            reply_markup=client_menu(message.from_user.id in config.admin_ids),
        )

    @router.message(Command("admin"))
    async def denied_admin(message: Message) -> None:
        await message.answer(
            "Панель доступна только администратору.\n"
            f"Ваш Telegram ID: <code>{message.from_user.id}</code>"
        )

    @router.message(Command("id"))
    async def show_telegram_id(message: Message) -> None:
        await message.answer(
            f"Ваш Telegram ID: <code>{message.from_user.id}</code>"
        )

    @router.message(F.text == "📝 Оставить заявку")
    async def request_begin(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(RequestForm.city)
        await message.answer(
            "<b>1/5. Выберите город:</b>", reply_markup=_city_keyboard()
        )

    @router.callback_query(RequestForm.city, F.data.startswith("r:city:"))
    async def request_city(callback: CallbackQuery, state: FSMContext) -> None:
        index = int(callback.data.rsplit(":", 1)[1])
        if index < 0 or index >= len(CITIES):
            await callback.answer("Выберите город кнопкой", show_alert=True)
            return
        await state.update_data(city=CITIES[index])
        data = await state.get_data()
        if data.get("description"):
            await state.set_state(RequestForm.address)
            await callback.message.edit_text(
                "<b>3/5. Укажите адрес или район.</b>\n"
                "Если пока не хотите указывать точный адрес, отправьте «-»."
            )
            await callback.answer()
            return
        await state.set_state(RequestForm.description)
        await callback.message.edit_text(
            "<b>2/5. Что нужно сделать?</b>\n"
            "Опишите задачу одним сообщением. Можно подробно."
        )
        await callback.answer()

    @router.message(RequestForm.description)
    async def request_description(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if len(text) < 5:
            await message.answer("Опишите задачу чуть подробнее — минимум 5 символов.")
            return
        await state.update_data(description=text)
        await state.set_state(RequestForm.address)
        await message.answer(
            "<b>3/5. Укажите адрес или район.</b>\n"
            "Если пока не хотите указывать точный адрес, отправьте «-»."
        )

    @router.message(RequestForm.address)
    async def request_address(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        await state.update_data(address="" if text == "-" else text[:300])
        await state.set_state(RequestForm.preferred_time)
        await message.answer(
            "<b>4/5. Когда вам удобно?</b>\n"
            "Например: «завтра после 15:00». Если неважно — отправьте «-»."
        )

    @router.message(RequestForm.preferred_time)
    async def request_time(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        await state.update_data(preferred_time="" if text == "-" else text[:200])
        await state.set_state(RequestForm.phone)
        await message.answer(
            "<b>5/5. Оставьте номер телефона.</b>",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [
                        KeyboardButton(
                            text="📱 Отправить мой номер", request_contact=True
                        )
                    ],
                    [KeyboardButton(text="Пропустить")],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )

    @router.message(RequestForm.phone)
    async def request_phone(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.contact and message.contact.user_id == message.from_user.id:
            phone = message.contact.phone_number
        else:
            raw = (message.text or "").strip()
            phone = "" if raw == "Пропустить" else raw
            digits = "".join(char for char in phone if char.isdigit())
            if phone and len(digits) < 10:
                await message.answer("Проверьте номер или нажмите «Пропустить».")
                return

        data = await state.get_data()
        full_name = " ".join(
            part
            for part in (message.from_user.first_name, message.from_user.last_name)
            if part
        )
        client_id = db.upsert_client(
            message.from_user.id,
            message.from_user.username,
            full_name,
            phone,
            data["city"],
        )
        low, high = estimate_price(data["description"])
        order_id = db.create_order(
            client_id,
            data["city"],
            data["description"],
            data.get("address", ""),
            data.get("preferred_time", ""),
            low,
        )
        await state.clear()

        price_text = (
            f"Ориентир по работе: <b>{money(low)}–{money(high)}</b>."
            if low and high
            else "Предварительную стоимость мастер назовёт после уточнения деталей."
        )
        await message.answer(
            f"✅ <b>Заявка #{order_id} принята</b>\n\n"
            f"{price_text}\n"
            "Мастер получил уведомление и свяжется с вами.",
            reply_markup=client_menu(message.from_user.id in config.admin_ids),
        )

        admin_text = (
            f"🔔 <b>Новая заявка #{order_id}</b>\n"
            f"<b>Клиент:</b> {esc(full_name or 'Без имени')}\n"
            f"<b>Телефон:</b> {esc(phone or 'не указан')}\n"
            f"<b>Город:</b> {esc(data['city'])}\n"
            f"<b>Задача:</b> {esc(data['description'])}"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Открыть заявку", callback_data=f"a:o:{order_id}"
                    )
                ]
            ]
        )
        for admin_id in config.admin_ids:
            try:
                await bot.send_message(admin_id, admin_text, reply_markup=keyboard)
            except (TelegramBadRequest, TelegramForbiddenError):
                logger.warning("Cannot notify admin %s", admin_id)

    @router.message(F.text == "📋 Мои заявки")
    async def my_orders(message: Message) -> None:
        orders = db.client_orders(message.from_user.id)
        if not orders:
            await message.answer("У вас пока нет заявок.")
            return
        lines = ["<b>📋 Ваши заявки</b>"]
        for order in orders:
            lines.append(
                f"\n<b>#{order['id']}</b> · "
                f"{STATUS_LABELS.get(order['status'], esc(order['status']))}\n"
                f"{esc(order['description'][:120])}"
            )
        await message.answer("\n".join(lines))

    @router.message(F.text == "☎️ Связаться")
    async def contact(message: Message) -> None:
        await message.answer(
            "Связаться с мастером:\n"
            "📞 <b>+7 (959) 186-08-86</b>\n"
            "Telegram: <b>@remontopro</b>"
        )

    @router.message(F.text)
    async def request_from_first_message(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if text.startswith("/"):
            await message.answer(
                "Неизвестная команда. Нажмите кнопку меню или просто опишите поломку.",
                reply_markup=client_menu(message.from_user.id in config.admin_ids),
            )
            return
        if len(text) < 5:
            await message.answer(
                "Опишите поломку чуть подробнее — минимум 5 символов."
            )
            return
        await state.clear()
        await state.update_data(description=text[:2000])
        await state.set_state(RequestForm.city)
        await message.answer(
            "✅ Задачу сохранил.\n\n<b>1/5. Выберите город:</b>",
            reply_markup=_city_keyboard(),
        )

    return router
