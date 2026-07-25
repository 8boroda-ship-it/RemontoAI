from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


CITIES = ("Брянка", "Стаханов", "Алчевск")

# Базовая сетка выездов. Если на дне уже есть заявки, алгоритм отдаёт
# приоритет их городу и собирает соседние заявки в одну поездку.
CITY_WEEKDAYS: dict[str, set[int]] = {
    "Брянка": {0, 2, 4},
    "Стаханов": {1, 3},
    "Алчевск": {5},
}


@dataclass(frozen=True, slots=True)
class Booking:
    starts_at: datetime
    duration_hours: int
    city: str

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(hours=self.duration_hours)


@dataclass(frozen=True, slots=True)
class Slot:
    starts_at: datetime
    city: str
    score: int

    @property
    def callback_value(self) -> str:
        return self.starts_at.isoformat()


def _overlaps(start: datetime, duration_hours: int, booking: Booking) -> bool:
    end = start + timedelta(hours=duration_hours)
    return start < booking.ends_at and end > booking.starts_at


def recommend_slots(
    city: str,
    duration_hours: int,
    bookings: list[Booking],
    *,
    now: datetime,
    workday_start: time = time(9, 0),
    workday_end: time = time(19, 0),
    step_hours: int = 3,
    planning_days: int = 21,
    limit: int = 6,
) -> list[Slot]:
    if city not in CITIES:
        raise ValueError(f"Неизвестный город: {city}")
    if now.tzinfo is None:
        raise ValueError("now должен содержать часовой пояс")

    candidates: list[Slot] = []
    for offset in range(planning_days + 1):
        day = now.date() + timedelta(days=offset)
        if day.weekday() == 6:
            continue

        day_bookings = [item for item in bookings if item.starts_at.date() == day]
        same_city_count = sum(item.city == city for item in day_bookings)
        foreign_city_count = sum(item.city != city for item in day_bookings)

        cursor = datetime.combine(day, workday_start, tzinfo=now.tzinfo)
        day_end = datetime.combine(day, workday_end, tzinfo=now.tzinfo)
        while cursor + timedelta(hours=duration_hours) <= day_end:
            if cursor >= now + timedelta(hours=2) and not any(
                _overlaps(cursor, duration_hours, booking) for booking in bookings
            ):
                score = offset * 10
                if day.weekday() not in CITY_WEEKDAYS[city]:
                    score += 25
                score -= same_city_count * 18
                score += foreign_city_count * 30

                # Соседняя заявка того же города особенно выгодна:
                # меньше холостых переездов и разрывов в расписании.
                for booking in day_bookings:
                    gap = min(
                        abs((cursor - booking.ends_at).total_seconds()),
                        abs((booking.starts_at - (cursor + timedelta(hours=duration_hours))).total_seconds()),
                    )
                    if booking.city == city and gap <= 3600:
                        score -= 20
                candidates.append(Slot(cursor, city, score))
            cursor += timedelta(hours=step_hours)

    return sorted(candidates, key=lambda slot: (slot.score, slot.starts_at))[:limit]


def parse_clock(value: str) -> time:
    hours, minutes = (int(part) for part in value.split(":", maxsplit=1))
    return time(hours, minutes)


def display_slot(slot: Slot) -> str:
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    return (
        f"{weekdays[slot.starts_at.weekday()]}, "
        f"{slot.starts_at:%d.%m в %H:%M}"
    )

