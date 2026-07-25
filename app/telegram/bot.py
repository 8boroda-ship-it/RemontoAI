from __future__ import annotations

import html
import logging
import re
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    Message,
)

from app.core.database import Database
from app.core.pricing import PriceCatalog
from app.core.scheduling import (
    CITIES,
    display_slot,
    parse_clock,
    recommend_slots,
)
from app.core.settings import Settings
from app.telegram.keyboards import (
    REMOVE_KEYBOARD,
    city_keyboard,
    confirmation_keyboard,
    main_menu,
    phone_keyboard,
    photos_keyboard,
    slots_keyboard,
)
from app.telegram.states import OrderForm


logger = logging.getLogger(__name__)
router = Router()
runtime_settings: Settings | None = None
runtime_database: Database | None = None
runtime_catalog: PriceCatalog | None = None


def _database(message_or_callback: Message | CallbackQuery) -> Database:
    if runtime_database is None:
        raise RuntimeError("Database is not initialized")
    return runtime_database


def _settings(message_or_callback: Message | CallbackQuery) -> Settings:
    if runtime_settings is None:
        raise RuntimeError("Settings are not initialized")
    return runtime_settings


def _catalog(message_or_callback: Message | CallbackQuery) -> PriceCatalog:
    if runtime_catalog is None:
        raise RuntimeError("Price catalog is not initialized")
    return runtime_catalog


def _format_estimate(data: dict) -> str:
    if data.get("price_min") is None:
        price = "оценит мастер после уточнения деталей"
    else:
        price = (
            f"{data['price_min']:,}–{data['price_max']:,} ₽"
            .replace(",", " ")
        )
    return (
        f"<b>{html.escape(data['service_name'])}</b>\n"
        f"Предварительная работа: {price}.\n\n"
        "Это ориентир за работу. Материалы, сложный доступ и скрытые "
        "неисправности считаются отдельно после осмотра."
    )


def _format_order(data: dict, order_id: int | None = None) -> str:
    prefix = f"<b>Заявка №{order_id}</b>\n" if order_id else "<b>Проверьте заявку</b>\n"
    price = (
        "по согласованию"
        if data.get("price_min") is None
        else f"{data['price_min']:,}–{data['price_max']:,} ₽".replace(",", " ")
    )
    starts_at = data["starts_at"]
    return (
        prefix
        + f"Работа: {html.escape(data['service_name'])}\n"
        + f"Описание: {html.escape(data['description'])}\n"
        + f"Ориентир: {price}\n"
        + f"Город: {html.escape(data['city'])}\n"
        + f"Адрес: {html.escape(data['address'])}\n"
        + f"Время: {starts_at:%d.%m.%Y в %H:%M}\n"
        + f"Клиент: {html.escape(data['client_name'])}\n"
        + f"Телефон: {html.escape(data['phone'])}\n"
        + f"Фото: {len(data.get('photo_file_ids', []))}"
    )


async def _ask_city(message: Message, state: FSMContext) -> None:
    await state.set_state(OrderForm.city)
    await message.answer("В каком городе находится объект?", reply_markup=city_keyboard())


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>Remonto AI — помощник домашнего сервиса РЕМОНТО</b>\n\n"
        "Опишите задачу — я рассчитаю предварительную стоимость, "
        "предложу удобное время и передам заявку мастеру.\n\n"
        "Работаем: Брянка, Стаханов, Алчевск.",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отменить")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Заявка отменена.", reply_markup=main_menu())


@router.message(F.text.in_({"🛠 Оставить заявку", "💬 Узнать стоимость"}))
async def begin_order(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderForm.description)
    await message.answer(
        "Коротко опишите, что нужно сделать. Например:\n"
        "«Заменить смеситель на кухне» или «Собрать комод и повесить зеркало».",
        reply_markup=REMOVE_KEYBOARD,
    )


@router.message(F.text == "📞 Контакты")
async def contacts(message: Message) -> None:
    await message.answer(
        "РЕМОНТО — домашний сервис\n"
        "Телефон: +7 (959) 186-08-86\n"
        "Telegram: @remontopro",
        reply_markup=main_menu(),
    )


@router.message(OrderForm.description, F.text)
async def receive_description(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    if len(description) < 8:
        await message.answer("Добавьте немного деталей — хотя бы 8 символов.")
        return

    estimate = _catalog(message).estimate(description)
    await state.update_data(
        description=description,
        service_code=estimate.service_code,
        service_name=estimate.service_name,
        price_min=estimate.price_min,
        price_max=estimate.price_max,
        duration_hours=estimate.duration_hours,
        photo_file_ids=[],
    )
    await state.set_state(OrderForm.photos)
    await message.answer(_format_estimate(await state.get_data()))
    await message.answer(
        "Пришлите 1–5 фото — так расчёт будет точнее. "
        "После загрузки нажмите «Фото загружены».",
        reply_markup=photos_keyboard(),
    )


@router.message(OrderForm.description)
async def description_must_be_text(message: Message) -> None:
    await message.answer("Сначала пришлите описание задачи текстом.")


@router.message(OrderForm.photos, F.photo)
async def receive_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_ids = list(data.get("photo_file_ids", []))
    if len(photo_ids) >= 5:
        await message.answer("Пяти фото достаточно. Нажмите «Фото загружены».")
        return
    photo_ids.append(message.photo[-1].file_id)
    await state.update_data(photo_file_ids=photo_ids)
    await message.answer(f"Фото добавлено ({len(photo_ids)}/5).")


@router.message(
    OrderForm.photos,
    F.text.in_({"✅ Фото загружены", "➡️ Продолжить без фото"}),
)
async def finish_photos(message: Message, state: FSMContext) -> None:
    await _ask_city(message, state)


@router.message(OrderForm.photos)
async def waiting_for_photos(message: Message) -> None:
    await message.answer("Пришлите фото или нажмите кнопку продолжения.")


@router.message(OrderForm.city, F.text.in_(CITIES))
async def receive_city(message: Message, state: FSMContext) -> None:
    await state.update_data(city=message.text)
    await state.set_state(OrderForm.address)
    await message.answer(
        "Укажите улицу, дом и, если нужно, квартиру.",
        reply_markup=REMOVE_KEYBOARD,
    )


@router.message(OrderForm.city)
async def invalid_city(message: Message) -> None:
    await message.answer("Выберите один из трёх городов кнопкой ниже.")


@router.message(OrderForm.address, F.text)
async def receive_address(message: Message, state: FSMContext) -> None:
    if len(message.text.strip()) < 5:
        await message.answer("Укажите адрес подробнее.")
        return
    await state.update_data(address=message.text.strip())
    await state.set_state(OrderForm.name)
    default_name = message.from_user.full_name
    await message.answer(
        f"Как к вам обращаться? Можно написать имя или отправить «{default_name}»."
    )


@router.message(OrderForm.name, F.text)
async def receive_name(message: Message, state: FSMContext) -> None:
    await state.update_data(client_name=message.text.strip())
    await state.set_state(OrderForm.phone)
    await message.answer(
        "Оставьте номер телефона или нажмите кнопку отправки контакта.",
        reply_markup=phone_keyboard(),
    )


async def _save_phone_and_offer_slots(
    message: Message, state: FSMContext, phone: str
) -> None:
    await state.update_data(phone=phone)
    data = await state.get_data()
    settings = _settings(message)
    now = datetime.now(settings.timezone)
    bookings = await _database(message).future_bookings(now)
    slots = recommend_slots(
        data["city"],
        data["duration_hours"],
        bookings,
        now=now,
        workday_start=parse_clock(settings.workday_start),
        workday_end=parse_clock(settings.workday_end),
        step_hours=settings.slot_step_hours,
        planning_days=settings.planning_days,
    )
    await state.set_state(OrderForm.slot)
    if not slots:
        await message.answer(
            "Свободных автоматических окон пока нет. "
            "Напишите желаемые день и время — мастер согласует вручную.",
            reply_markup=REMOVE_KEYBOARD,
        )
        return
    await message.answer(
        f"Ближайшие удобные окна для города {data['city']}:",
        reply_markup=slots_keyboard(slots),
    )


@router.message(OrderForm.phone, F.contact)
async def receive_contact(message: Message, state: FSMContext) -> None:
    await _save_phone_and_offer_slots(message, state, message.contact.phone_number)


@router.message(OrderForm.phone, F.text)
async def receive_phone_text(message: Message, state: FSMContext) -> None:
    phone = re.sub(r"[^\d+]", "", message.text)
    if len(re.sub(r"\D", "", phone)) < 10:
        await message.answer("Проверьте номер: в нём должно быть не менее 10 цифр.")
        return
    await _save_phone_and_offer_slots(message, state, phone)


@router.callback_query(OrderForm.slot, F.data.startswith("slot:"))
async def choose_slot(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", maxsplit=1)[1]
    if value == "manual":
        await callback.message.answer(
            "Напишите желаемые дату и время, например «28 июля после 15:00». "
            "Мастер свяжется и подтвердит окно."
        )
        await callback.answer()
        return

    starts_at = datetime.fromisoformat(value)
    await state.update_data(starts_at=starts_at)
    data = await state.get_data()
    await state.set_state(OrderForm.confirmation)
    await callback.message.answer(
        _format_order(data),
        reply_markup=confirmation_keyboard(),
    )
    await callback.answer()


@router.message(OrderForm.slot, F.text)
async def manual_slot(message: Message, state: FSMContext) -> None:
    # Ручное пожелание ставим в ближайшее свободное техническое окно;
    # текст сохраняем в описании, чтобы мастер обязательно его увидел.
    data = await state.get_data()
    settings = _settings(message)
    now = datetime.now(settings.timezone)
    bookings = await _database(message).future_bookings(now)
    slots = recommend_slots(
        data["city"],
        data["duration_hours"],
        bookings,
        now=now,
        workday_start=parse_clock(settings.workday_start),
        workday_end=parse_clock(settings.workday_end),
        step_hours=settings.slot_step_hours,
        planning_days=settings.planning_days,
        limit=1,
    )
    if not slots:
        await message.answer("Свободное окно не найдено. Позвоните: +7 (959) 186-08-86")
        return
    await state.update_data(
        starts_at=slots[0].starts_at,
        description=f"{data['description']} | Пожелание по времени: {message.text.strip()}",
    )
    data = await state.get_data()
    await state.set_state(OrderForm.confirmation)
    await message.answer(
        "Пожелание сохранено. В заявке указано ближайшее резервное окно; "
        "мастер уточнит его при звонке.\n\n" + _format_order(data),
        reply_markup=confirmation_keyboard(),
    )


@router.callback_query(OrderForm.confirmation, F.data == "order:cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Заявка отменена.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(OrderForm.confirmation, F.data == "order:confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user = callback.from_user
    data.update(
        telegram_user_id=user.id,
        telegram_username=user.username,
    )
    database = _database(callback)
    created = await database.create_order(data)
    settings = _settings(callback)
    await database.export_csv(settings.orders_csv_path)

    await state.clear()
    await callback.message.answer(
        f"✅ Заявка №{created.id} принята.\n"
        f"Время: {created.starts_at:%d.%m в %H:%M}.\n\n"
        "Мастер свяжется с вами для окончательного подтверждения.",
        reply_markup=main_menu(),
    )

    admin_text = _format_order(data, created.id)
    for admin_id in settings.admin_ids:
        try:
            await callback.bot.send_message(admin_id, admin_text)
            for file_id in data.get("photo_file_ids", []):
                await callback.bot.send_photo(admin_id, file_id)
        except Exception:
            logger.exception("Не удалось уведомить администратора %s", admin_id)
    await callback.answer("Заявка сохранена")


def _admin_only(message: Message) -> bool:
    return message.from_user.id in _settings(message).admin_ids


@router.message(Command("orders"))
async def list_orders(message: Message) -> None:
    if not _admin_only(message):
        return
    rows = await _database(message).upcoming_orders()
    if not rows:
        await message.answer("Активных заявок нет.")
        return
    chunks = []
    for row in rows:
        starts_at = datetime.fromisoformat(row["starts_at"])
        chunks.append(
            f"№{row['id']} · {starts_at:%d.%m %H:%M} · "
            f"{row['city']} · {row['client_name']} · {row['phone']}\n"
            f"{row['service_name']}: {row['description']}"
        )
    await message.answer("\n\n".join(chunks))


@router.message(Command("export"))
async def export_orders(message: Message) -> None:
    if not _admin_only(message):
        return
    path = await _database(message).export_csv(_settings(message).orders_csv_path)
    await message.answer_document(FSInputFile(path), caption="Таблица заявок Remonto AI")


async def run_bot(settings: Settings, database: Database) -> None:
    global runtime_settings, runtime_database, runtime_catalog
    runtime_settings = settings
    runtime_database = database
    runtime_catalog = PriceCatalog()

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="cancel", description="Отменить заполнение"),
            BotCommand(command="orders", description="Активные заявки (мастер)"),
            BotCommand(command="export", description="Выгрузить таблицу (мастер)"),
        ]
    )

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    logger.info("Remonto AI started")
    await dispatcher.start_polling(bot)
