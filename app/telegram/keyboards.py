from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.core.scheduling import CITIES, Slot, display_slot


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Оставить заявку")],
            [KeyboardButton(text="💬 Узнать стоимость"), KeyboardButton(text="📞 Контакты")],
        ],
        resize_keyboard=True,
    )


def photos_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Фото загружены")],
            [KeyboardButton(text="➡️ Продолжить без фото")],
            [KeyboardButton(text="❌ Отменить")],
        ],
        resize_keyboard=True,
    )


def city_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=city)] for city in CITIES]
        + [[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)],
            [KeyboardButton(text="❌ Отменить")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def slots_keyboard(slots: list[Slot]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=display_slot(slot),
                callback_data=f"slot:{slot.callback_value}",
            )
        ]
        for slot in slots
    ]
    rows.append(
        [InlineKeyboardButton(text="Нужно другое время", callback_data="slot:manual")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="order:confirm"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="order:cancel"),
            ]
        ]
    )


REMOVE_KEYBOARD = ReplyKeyboardRemove()

