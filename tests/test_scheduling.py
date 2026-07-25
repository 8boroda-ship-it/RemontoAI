from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.scheduling import Booking, recommend_slots


TZ = ZoneInfo("Europe/Moscow")


def test_prefers_same_city_route_day() -> None:
    now = datetime(2026, 7, 27, 7, 0, tzinfo=TZ)  # понедельник
    bookings = [
        Booking(
            starts_at=datetime(2026, 7, 28, 9, 0, tzinfo=TZ),
            duration_hours=2,
            city="Стаханов",
        )
    ]
    slots = recommend_slots(
        "Стаханов",
        2,
        bookings,
        now=now,
        planning_days=7,
    )
    assert slots
    assert slots[0].starts_at.date().isoformat() == "2026-07-28"
    assert slots[0].starts_at.hour in {12, 15, 18}


def test_avoids_overlapping_booking() -> None:
    now = datetime(2026, 7, 27, 7, 0, tzinfo=TZ)
    bookings = [
        Booking(
            starts_at=datetime(2026, 7, 27, 9, 0, tzinfo=TZ),
            duration_hours=3,
            city="Брянка",
        )
    ]
    slots = recommend_slots("Брянка", 3, bookings, now=now, planning_days=1)
    assert all(
        not (
            slot.starts_at.date().isoformat() == "2026-07-27"
            and slot.starts_at.hour == 9
        )
        for slot in slots
    )
