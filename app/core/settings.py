from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: tuple[int, ...]
    timezone: ZoneInfo
    database_path: Path
    orders_csv_path: Path
    workday_start: str
    workday_end: str
    slot_step_hours: int
    planning_days: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN не задан. Скопируйте .env.example в .env и вставьте токен."
            )

        admin_ids = tuple(
            int(value.strip())
            for value in os.getenv("ADMIN_IDS", "").split(",")
            if value.strip()
        )

        return cls(
            bot_token=token,
            admin_ids=admin_ids,
            timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow")),
            database_path=PROJECT_ROOT / os.getenv(
                "DATABASE_PATH", "data/remonto.db"
            ),
            orders_csv_path=PROJECT_ROOT / os.getenv(
                "ORDERS_CSV_PATH", "data/orders.csv"
            ),
            workday_start=os.getenv("WORKDAY_START", "09:00"),
            workday_end=os.getenv("WORKDAY_END", "19:00"),
            slot_step_hours=int(os.getenv("SLOT_STEP_HOURS", "3")),
            planning_days=int(os.getenv("PLANNING_DAYS", "21")),
        )

