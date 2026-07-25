from __future__ import annotations

from dataclasses import dataclass

STATUS_NEW = "new"
STATUS_CONFIRMED = "confirmed"
STATUS_SCHEDULED = "scheduled"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATUS_NEW: "🆕 Новая",
    STATUS_CONFIRMED: "✅ Подтверждена",
    STATUS_SCHEDULED: "📅 Запланирована",
    STATUS_IN_PROGRESS: "🛠 В работе",
    STATUS_DONE: "🏁 Выполнена",
    STATUS_CANCELLED: "⛔ Отменена",
}

ACTIVE_STATUSES = (
    STATUS_NEW,
    STATUS_CONFIRMED,
    STATUS_SCHEDULED,
    STATUS_IN_PROGRESS,
)

CITIES = ("Брянка", "Стаханов", "Алчевск", "Другой город")


@dataclass(frozen=True, slots=True)
class Page:
    items: list[dict]
    page: int
    pages: int
    total: int
