from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .domain import (
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_LABELS,
    STATUS_NEW,
    STATUS_SCHEDULED,
)
from .formatters import short


def client_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📝 Оставить заявку")],
        [KeyboardButton(text="📋 Мои заявки"), KeyboardButton(text="☎️ Связаться")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🧑‍💼 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_home_keyboard(counts: dict[str, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"🆕 Новые · {counts.get(STATUS_NEW, 0)}",
        callback_data=f"a:l:{STATUS_NEW}:0",
    )
    builder.button(
        text=f"🛠 В работе · {counts.get(STATUS_IN_PROGRESS, 0)}",
        callback_data=f"a:l:{STATUS_IN_PROGRESS}:0",
    )
    builder.button(text="📋 Все заявки", callback_data="a:l:all:0")
    builder.button(text="📅 Расписание", callback_data="a:cal")
    builder.button(text="🔎 Поиск", callback_data="a:search")
    builder.button(text="👥 Клиенты", callback_data="a:clients:0")
    builder.button(text="💰 Статистика", callback_data="a:stats")
    builder.button(text="📦 Склад", callback_data="a:inv")
    builder.button(text="📤 Выгрузить CSV", callback_data="a:csv")
    builder.button(text="⚙️ Система", callback_data="a:settings")
    builder.adjust(2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def orders_keyboard(page_data, status: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in page_data.items:
        label = (
            f"#{order['id']} · {short(order.get('full_name') or 'Без имени', 18)} · "
            f"{short(order.get('description'), 26)}"
        )
        builder.row(
            InlineKeyboardButton(text=label, callback_data=f"a:o:{order['id']}")
        )
    nav: list[InlineKeyboardButton] = []
    if page_data.page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"a:l:{status}:{page_data.page - 1}"
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page_data.page + 1}/{page_data.pages}", callback_data="a:none"
        )
    )
    if page_data.page + 1 < page_data.pages:
        nav.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"a:l:{status}:{page_data.page + 1}"
            )
        )
    builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="a:home"))
    return builder.as_markup()


def order_keyboard(order: dict) -> InlineKeyboardMarkup:
    order_id = int(order["id"])
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Статус", callback_data=f"a:sm:{order_id}")
    builder.button(text="📅 Дата/время", callback_data=f"a:e:{order_id}:dt")
    builder.button(text="💵 Предв. цена", callback_data=f"a:e:{order_id}:est")
    builder.button(text="✅ Итоговая цена", callback_data=f"a:e:{order_id}:act")
    builder.button(text="🧾 Материалы", callback_data=f"a:e:{order_id}:mat")
    builder.button(text="⏱ Время работы", callback_data=f"a:e:{order_id}:dur")
    builder.button(text="🗒 Заметка", callback_data=f"a:e:{order_id}:note")
    if order.get("telegram_id"):
        builder.button(
            text="✉️ Написать клиенту",
            url=f"tg://user?id={int(order['telegram_id'])}",
        )
    builder.button(text="↩️ К заявкам", callback_data="a:l:all:0")
    builder.button(text="🏠 Главная", callback_data="a:home")
    builder.adjust(2, 2, 2, 1, 1, 2)
    return builder.as_markup()


def statuses_keyboard(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for status in (
        STATUS_NEW,
        STATUS_CONFIRMED,
        STATUS_SCHEDULED,
        STATUS_IN_PROGRESS,
        STATUS_DONE,
        STATUS_CANCELLED,
    ):
        builder.button(
            text=STATUS_LABELS[status], callback_data=f"a:ss:{order_id}:{status}"
        )
    builder.button(text="↩️ Назад", callback_data=f"a:o:{order_id}")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def simple_back(callback_data: str = "a:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data=callback_data)]
        ]
    )


def search_results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in results:
        builder.row(
            InlineKeyboardButton(
                text=f"#{order['id']} · {short(order.get('full_name') or 'Без имени', 18)} · "
                f"{short(order.get('description'), 25)}",
                callback_data=f"a:o:{order['id']}",
            )
        )
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="a:home"))
    return builder.as_markup()


def clients_keyboard(page_data) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for client in page_data.items:
        builder.row(
            InlineKeyboardButton(
                text=f"{short(client.get('full_name') or 'Без имени', 24)} · "
                f"{client.get('order_count', 0)} заявок",
                callback_data=f"a:c:{client['id']}",
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page_data.page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"a:clients:{page_data.page - 1}"
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page_data.page + 1}/{page_data.pages}", callback_data="a:none"
        )
    )
    if page_data.page + 1 < page_data.pages:
        nav.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"a:clients:{page_data.page + 1}"
            )
        )
    builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="a:home"))
    return builder.as_markup()


def inventory_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        item_id = int(item["id"])
        builder.row(
            InlineKeyboardButton(text="➖", callback_data=f"a:iq:{item_id}:m"),
            InlineKeyboardButton(
                text=f"{short(item['name'], 24)} · {item['quantity']:g} {item['unit']}",
                callback_data="a:none",
            ),
            InlineKeyboardButton(text="➕", callback_data=f"a:iq:{item_id}:p"),
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить позицию", callback_data="a:ia"))
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="a:home"))
    return builder.as_markup()
