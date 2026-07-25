from __future__ import annotations

import html
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .domain import STATUS_LABELS


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def money(value: object) -> str:
    try:
        amount = int(value or 0)
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:,}".replace(",", " ") + " ₽"


def short(value: object, limit: int = 42) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def local_datetime(value: object, timezone: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "не назначено"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.strftime("%d.%m.%Y %H:%M")
        return parsed.astimezone(ZoneInfo(timezone)).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return raw


def parse_admin_datetime(value: str, timezone: str) -> str:
    text = value.strip()
    if not text or text in {"-", "нет", "очистить"}:
        return ""
    formats = ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M", "%d.%m.%Y")
    parsed = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=ZoneInfo(timezone))
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError("Введите дату как 27.07.2026 14:30")
    return parsed.astimezone(UTC).isoformat()


def order_card(order: dict, timezone: str) -> str:
    status = STATUS_LABELS.get(order.get("status"), esc(order.get("status")))
    username = f"@{esc(order['username'])}" if order.get("username") else "нет"
    difficulty = "😌 обычный"
    if int(order.get("difficulty") or 0) >= 7:
        difficulty = "🔥 сложный"
    elif int(order.get("difficulty") or 0) >= 4:
        difficulty = "⚠️ требовательный"
    return (
        f"<b>Заявка #{int(order['id'])}</b>\n"
        f"{status}\n\n"
        f"<b>Клиент:</b> {esc(order.get('full_name') or 'Без имени')}\n"
        f"<b>Телефон:</b> {esc(order.get('phone') or 'не указан')}\n"
        f"<b>Telegram:</b> {username}\n"
        f"<b>Клиент:</b> {difficulty}\n\n"
        f"<b>Город:</b> {esc(order.get('city') or 'не указан')}\n"
        f"<b>Адрес:</b> {esc(order.get('address') or 'не указан')}\n"
        f"<b>Когда удобно:</b> {esc(order.get('preferred_time') or 'не указано')}\n"
        f"<b>Запланировано:</b> {esc(local_datetime(order.get('scheduled_at'), timezone))}\n\n"
        f"<b>Задача:</b>\n{esc(order.get('description') or 'без описания')}\n\n"
        f"<b>Предварительно:</b> {money(order.get('estimated_price'))}\n"
        f"<b>Итог:</b> {money(order.get('actual_price'))}\n"
        f"<b>Материалы:</b> {money(order.get('material_cost'))}\n"
        f"<b>Время:</b> {int(order.get('duration_minutes') or 0)} мин.\n\n"
        f"<b>Заметка:</b> {esc(order.get('admin_note') or '—')}"
    )


def estimate_price(description: str) -> tuple[int, int]:
    text = description.lower()
    rules = (
        (("смесител",), (1500, 3000)),
        (("розет", "выключател"), (1000, 2500)),
        (("люстр", "светильник"), (1500, 3500)),
        (("карниз", "полк", "телевизор"), (1500, 3500)),
        (("сифон",), (1000, 2200)),
        (("унитаз",), (2500, 5000)),
        (("стиральн", "стиралк"), (1800, 3500)),
        (("собрать", "сборка", "комод", "шкаф"), (1500, 5000)),
    )
    matches = [price for words, price in rules if any(word in text for word in words)]
    if not matches:
        return 0, 0
    return min(item[0] for item in matches), max(item[1] for item in matches)
