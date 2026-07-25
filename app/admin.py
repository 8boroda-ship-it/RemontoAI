from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .config import Config
from .db import Database
from .domain import STATUS_LABELS, STATUS_NEW
from .filters import AdminFilter
from .formatters import (
    esc,
    local_datetime,
    money,
    order_card,
    parse_admin_datetime,
    short,
)
from .keyboards import (
    admin_home_keyboard,
    clients_keyboard,
    inventory_keyboard,
    order_keyboard,
    orders_keyboard,
    search_results_keyboard,
    simple_back,
    statuses_keyboard,
)
from .sheets import GoogleSheetsSync

logger = logging.getLogger(__name__)


class AdminInput(StatesGroup):
    edit_order = State()
    search = State()
    add_inventory = State()


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if not callback.message:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def build_admin_router(
    db: Database,
    config: Config,
    sheets: GoogleSheetsSync | None = None,
) -> Router:
    router = Router(name="admin")
    admin_filter = AdminFilter(config.admin_ids)
    router.message.filter(admin_filter)
    router.callback_query.filter(admin_filter)

    async def show_home(target: Message | CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        dashboard = db.dashboard()
        counts = dashboard["status"]
        active = sum(
            counts.get(key, 0)
            for key in ("new", "confirmed", "scheduled", "in_progress")
        )
        text = (
            "<b>🧑‍💼 REMONTO AI · Панель администратора</b>\n"
            f"Обновлено: {datetime.now(ZoneInfo(config.timezone)).strftime('%H:%M')}\n\n"
            f"🆕 Новых заявок: <b>{counts.get(STATUS_NEW, 0)}</b>\n"
            f"🧰 Активных: <b>{active}</b>\n"
            f"📥 Поступило сегодня: <b>{dashboard['today']}</b>\n"
            f"⚠️ Просрочено: <b>{dashboard['overdue']}</b>\n"
            f"💰 Выручка за месяц: <b>{money(dashboard['month_revenue'])}</b>\n"
            f"📦 Заканчивается на складе: <b>{dashboard['low_stock']}</b>"
        )
        keyboard = admin_home_keyboard(counts)
        if isinstance(target, CallbackQuery):
            await _safe_edit(target, text, keyboard)
            await target.answer()
        else:
            await target.answer(text, reply_markup=keyboard)

    @router.message(Command("admin"))
    @router.message(F.text == "🧑‍💼 Админ-панель")
    async def admin_home_message(message: Message, state: FSMContext) -> None:
        await show_home(message, state)

    @router.callback_query(F.data == "a:home")
    async def admin_home_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await show_home(callback, state)

    @router.callback_query(F.data.startswith("a:l:"))
    async def orders_list(callback: CallbackQuery) -> None:
        _, _, status, raw_page = callback.data.split(":", 3)
        page = db.list_orders(status, int(raw_page))
        title = "Все заявки" if status == "all" else STATUS_LABELS.get(status, status)
        await _safe_edit(
            callback,
            f"<b>📋 {esc(title)}</b>\nНайдено: <b>{page.total}</b>",
            orders_keyboard(page, status),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("a:o:"))
    async def order_detail(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        order_id = int(callback.data.rsplit(":", 1)[1])
        order = db.get_order(order_id)
        if not order:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        await _safe_edit(
            callback, order_card(order, config.timezone), order_keyboard(order)
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("a:sm:"))
    async def status_menu(callback: CallbackQuery) -> None:
        order_id = int(callback.data.rsplit(":", 1)[1])
        order = db.get_order(order_id)
        if not order:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        await _safe_edit(
            callback,
            f"<b>Статус заявки #{order_id}</b>\n"
            f"Сейчас: {STATUS_LABELS.get(order['status'], esc(order['status']))}",
            statuses_keyboard(order_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("a:ss:"))
    async def set_status(callback: CallbackQuery, bot: Bot) -> None:
        _, _, raw_order_id, status = callback.data.split(":", 3)
        if status not in STATUS_LABELS:
            await callback.answer("Неизвестный статус", show_alert=True)
            return
        order_id = int(raw_order_id)
        if not db.update_order(order_id, "status", status, callback.from_user.id):
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        order = db.get_order(order_id)
        if order and order.get("telegram_id"):
            try:
                await bot.send_message(
                    int(order["telegram_id"]),
                    f"Статус вашей заявки <b>#{order_id}</b> изменён:\n"
                    f"{STATUS_LABELS[status]}",
                )
            except (TelegramBadRequest, TelegramForbiddenError):
                logger.info("Cannot notify client for order %s", order_id)
        await _safe_edit(
            callback, order_card(order, config.timezone), order_keyboard(order)
        )
        await callback.answer("Статус обновлён")
        if sheets:
            await sheets.sync_order(order)

    edit_fields = {
        "dt": ("scheduled_at", "дату и время", "27.07.2026 14:30"),
        "est": ("estimated_price", "предварительную цену", "2500"),
        "act": ("actual_price", "итоговую цену", "3000"),
        "mat": ("material_cost", "стоимость материалов", "850"),
        "dur": ("duration_minutes", "время работы в минутах", "120"),
        "note": ("admin_note", "заметку", "Купить сифон 40 мм"),
    }

    @router.callback_query(F.data.startswith("a:e:"))
    async def begin_edit(callback: CallbackQuery, state: FSMContext) -> None:
        _, _, raw_order_id, code = callback.data.split(":", 3)
        if code not in edit_fields or not db.get_order(int(raw_order_id)):
            await callback.answer("Действие недоступно", show_alert=True)
            return
        field, label, example = edit_fields[code]
        await state.set_state(AdminInput.edit_order)
        await state.set_data(
            {"order_id": int(raw_order_id), "field": field, "code": code}
        )
        await _safe_edit(
            callback,
            f"<b>Заявка #{int(raw_order_id)}</b>\n"
            f"Введите {label}.\n\nПример: <code>{esc(example)}</code>\n"
            "Для очистки отправьте <code>-</code>.",
            simple_back(f"a:o:{int(raw_order_id)}"),
        )
        await callback.answer()

    @router.message(AdminInput.edit_order)
    async def finish_edit(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        order_id = int(data["order_id"])
        field = str(data["field"])
        raw = (message.text or "").strip()
        try:
            if field == "scheduled_at":
                value: str | int = parse_admin_datetime(raw, config.timezone)
            elif field in {
                "estimated_price",
                "actual_price",
                "material_cost",
                "duration_minutes",
            }:
                normalized = raw.replace(" ", "").replace("₽", "")
                value = 0 if normalized == "-" else int(normalized)
                if value < 0:
                    raise ValueError("Значение не может быть отрицательным")
            else:
                value = "" if raw == "-" else raw
                if len(value) > 2000:
                    raise ValueError("Заметка слишком длинная")
        except ValueError as exc:
            await message.answer(
                f"Не удалось сохранить: {esc(exc)}\nПопробуйте ещё раз.",
                reply_markup=simple_back(f"a:o:{order_id}"),
            )
            return
        db.update_order(order_id, field, value, message.from_user.id)
        await state.clear()
        order = db.get_order(order_id)
        await message.answer(
            f"✅ Сохранено\n\n{order_card(order, config.timezone)}",
            reply_markup=order_keyboard(order),
        )
        if sheets:
            await sheets.sync_order(order)

    @router.callback_query(F.data == "a:cal")
    async def schedule(callback: CallbackQuery) -> None:
        rows = db.schedule()
        if not rows:
            text = "<b>📅 Расписание на 14 дней</b>\n\nЗапланированных выездов нет."
        else:
            lines = ["<b>📅 Расписание на 14 дней</b>"]
            for row in rows:
                lines.append(
                    f"\n<b>{esc(local_datetime(row['scheduled_at'], config.timezone))}</b>"
                    f"\n#{row['id']} · {esc(row['city'])} · "
                    f"{esc(short(row['description'], 46))}"
                    f"\n{esc(row.get('full_name') or 'Без имени')} · "
                    f"{esc(row.get('phone') or 'без телефона')}"
                )
            text = "\n".join(lines)
        await _safe_edit(callback, text, simple_back())
        await callback.answer()

    @router.callback_query(F.data == "a:search")
    async def begin_search(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminInput.search)
        await _safe_edit(
            callback,
            "<b>🔎 Поиск заявок</b>\n\n"
            "Введите номер заявки, имя, телефон, адрес или часть описания.",
            simple_back(),
        )
        await callback.answer()

    @router.message(AdminInput.search)
    async def search(message: Message, state: FSMContext) -> None:
        query = (message.text or "").strip()
        if len(query) < 2 and not query.lstrip("#").isdigit():
            await message.answer("Введите минимум 2 символа.")
            return
        rows = db.search(query)
        await state.clear()
        if not rows:
            await message.answer(
                f"По запросу «{esc(query)}» ничего не найдено.",
                reply_markup=simple_back(),
            )
            return
        await message.answer(
            f"<b>Результаты поиска</b>\nНайдено: {len(rows)}",
            reply_markup=search_results_keyboard(rows),
        )

    @router.callback_query(F.data.startswith("a:clients:"))
    async def clients(callback: CallbackQuery) -> None:
        page = db.list_clients(int(callback.data.rsplit(":", 1)[1]))
        await _safe_edit(
            callback,
            f"<b>👥 Клиенты</b>\nВсего: <b>{page.total}</b>",
            clients_keyboard(page),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("a:c:"))
    async def client_detail(callback: CallbackQuery) -> None:
        client_id = int(callback.data.rsplit(":", 1)[1])
        client = db.get_client(client_id)
        if not client:
            await callback.answer("Клиент не найден", show_alert=True)
            return
        username = f"@{esc(client['username'])}" if client.get("username") else "нет"
        text = (
            f"<b>👤 {esc(client.get('full_name') or 'Без имени')}</b>\n\n"
            f"<b>Телефон:</b> {esc(client.get('phone') or 'не указан')}\n"
            f"<b>Telegram:</b> {username}\n"
            f"<b>Город:</b> {esc(client.get('city') or 'не указан')}\n"
            f"<b>Заявок:</b> {client.get('order_count', 0)}\n"
            f"<b>Оплачено:</b> {money(client.get('total_revenue'))}\n"
            f"<b>Уровень сложности:</b> {client.get('difficulty', 0)}/10\n\n"
            f"<b>Заметка:</b> {esc(client.get('notes') or '—')}"
        )
        await _safe_edit(callback, text, simple_back("a:clients:0"))
        await callback.answer()

    @router.callback_query(F.data == "a:stats")
    async def stats(callback: CallbackQuery) -> None:
        data = db.dashboard()
        counts = data["status"]
        lines = ["<b>💰 Статистика</b>"]
        for key, label in STATUS_LABELS.items():
            lines.append(f"{label}: <b>{counts.get(key, 0)}</b>")
        lines.extend(
            [
                "",
                f"Поступило сегодня: <b>{data['today']}</b>",
                f"Выручка за месяц: <b>{money(data['month_revenue'])}</b>",
                f"Просрочено: <b>{data['overdue']}</b>",
            ]
        )
        await _safe_edit(callback, "\n".join(lines), simple_back())
        await callback.answer()

    @router.callback_query(F.data == "a:inv")
    async def inventory(callback: CallbackQuery) -> None:
        items = db.inventory()
        if items:
            text = "<b>📦 Склад</b>\nКрасным не выделяем: ⚠️ означает остаток ниже минимума."
            lines = [text]
            for item in items:
                marker = "⚠️" if item["quantity"] <= item["min_quantity"] else "✅"
                lines.append(
                    f"{marker} {esc(item['name'])}: "
                    f"<b>{item['quantity']:g} {esc(item['unit'])}</b> "
                    f"(мин. {item['min_quantity']:g})"
                )
            text = "\n".join(lines)
        else:
            text = "<b>📦 Склад</b>\n\nПозиции ещё не добавлены."
        await _safe_edit(callback, text, inventory_keyboard(items))
        await callback.answer()

    @router.callback_query(F.data.startswith("a:iq:"))
    async def inventory_quantity(callback: CallbackQuery) -> None:
        _, _, raw_id, direction = callback.data.split(":", 3)
        db.change_inventory(int(raw_id), 1 if direction == "p" else -1)
        items = db.inventory()
        lines = ["<b>📦 Склад</b>"]
        for item in items:
            marker = "⚠️" if item["quantity"] <= item["min_quantity"] else "✅"
            lines.append(
                f"{marker} {esc(item['name'])}: "
                f"<b>{item['quantity']:g} {esc(item['unit'])}</b> "
                f"(мин. {item['min_quantity']:g})"
            )
        await _safe_edit(callback, "\n".join(lines), inventory_keyboard(items))
        await callback.answer("Остаток обновлён")

    @router.callback_query(F.data == "a:ia")
    async def begin_inventory_add(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AdminInput.add_inventory)
        await _safe_edit(
            callback,
            "<b>Добавление на склад</b>\n\n"
            "Отправьте строку:\n"
            "<code>Название | количество | единица | минимум</code>\n\n"
            "Пример:\n<code>Подрозетник | 20 | шт. | 5</code>",
            simple_back("a:inv"),
        )
        await callback.answer()

    @router.message(AdminInput.add_inventory)
    async def finish_inventory_add(message: Message, state: FSMContext) -> None:
        parts = [part.strip() for part in (message.text or "").split("|")]
        try:
            if len(parts) != 4 or not parts[0]:
                raise ValueError
            quantity = float(parts[1].replace(",", "."))
            minimum = float(parts[3].replace(",", "."))
            if quantity < 0 or minimum < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                "Неверный формат. Пример:\n<code>Подрозетник | 20 | шт. | 5</code>"
            )
            return
        db.add_inventory(parts[0], quantity, parts[2] or "шт.", minimum)
        await state.clear()
        await message.answer("✅ Позиция добавлена.", reply_markup=simple_back("a:inv"))

    @router.callback_query(F.data == "a:csv")
    async def export_csv(callback: CallbackQuery) -> None:
        raw = db.export_orders_csv()
        filename = (
            f"remonto_orders_"
            f"{datetime.now(ZoneInfo(config.timezone)):%Y-%m-%d_%H-%M}.csv"
        )
        await callback.message.answer_document(
            BufferedInputFile(raw, filename=filename),
            caption="📤 Выгрузка всех заявок REMONTO AI",
        )
        await callback.answer("Файл готов")

    @router.callback_query(F.data == "a:settings")
    async def settings(callback: CallbackQuery) -> None:
        text = (
            "<b>⚙️ Система</b>\n\n"
            "✅ Доступ только по ADMIN_IDS\n"
            "✅ Автомиграция базы включена\n"
            "✅ Журнал изменений включён\n"
            "✅ SQLite WAL включён\n"
            f"🌍 Часовой пояс: <code>{esc(config.timezone)}</code>\n"
            f"👤 Администраторов: <b>{len(config.admin_ids)}</b>\n\n"
            "Проверка связи: бот отвечает, панель доступна."
        )
        await _safe_edit(callback, text, simple_back())
        await callback.answer()

    @router.callback_query(F.data == "a:none")
    async def no_action(callback: CallbackQuery) -> None:
        await callback.answer()

    return router
