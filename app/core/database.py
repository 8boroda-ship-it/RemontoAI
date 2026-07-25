from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from app.core.scheduling import Booking


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    client_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    city TEXT NOT NULL,
    address TEXT NOT NULL,
    description TEXT NOT NULL,
    service_code TEXT NOT NULL,
    service_name TEXT NOT NULL,
    price_min INTEGER,
    price_max INTEGER,
    duration_hours INTEGER NOT NULL,
    starts_at TEXT NOT NULL,
    photo_file_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_starts_at ON orders(starts_at);
CREATE INDEX IF NOT EXISTS ix_orders_city ON orders(city);
"""


@dataclass(frozen=True, slots=True)
class CreatedOrder:
    id: int
    starts_at: datetime


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()

    async def create_order(self, data: dict) -> CreatedOrder:
        now = datetime.now(tz=data["starts_at"].tzinfo).isoformat()
        query = """
            INSERT INTO orders (
                telegram_user_id, telegram_username, client_name, phone,
                city, address, description, service_code, service_name,
                price_min, price_max, duration_hours, starts_at,
                photo_file_ids, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
        """
        values = (
            data["telegram_user_id"],
            data.get("telegram_username"),
            data["client_name"],
            data["phone"],
            data["city"],
            data["address"],
            data["description"],
            data["service_code"],
            data["service_name"],
            data.get("price_min"),
            data.get("price_max"),
            data["duration_hours"],
            data["starts_at"].isoformat(),
            json.dumps(data.get("photo_file_ids", []), ensure_ascii=False),
            now,
        )
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(query, values)
            await connection.commit()
            return CreatedOrder(int(cursor.lastrowid), data["starts_at"])

    async def future_bookings(self, from_time: datetime) -> list[Booking]:
        query = """
            SELECT starts_at, duration_hours, city
            FROM orders
            WHERE starts_at >= ? AND status NOT IN ('cancelled', 'done')
            ORDER BY starts_at
        """
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(query, (from_time.isoformat(),))
            rows = await cursor.fetchall()
        return [
            Booking(
                starts_at=datetime.fromisoformat(row["starts_at"]),
                duration_hours=int(row["duration_hours"]),
                city=row["city"],
            )
            for row in rows
        ]

    async def upcoming_orders(self, limit: int = 20) -> list[dict]:
        query = """
            SELECT * FROM orders
            WHERE status NOT IN ('cancelled', 'done')
            ORDER BY starts_at
            LIMIT ?
        """
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(query, (limit,))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def export_csv(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute("SELECT * FROM orders ORDER BY starts_at")
            rows = await cursor.fetchall()

        headers = list(rows[0].keys()) if rows else [
            "id", "telegram_user_id", "telegram_username", "client_name",
            "phone", "city", "address", "description", "service_code",
            "service_name", "price_min", "price_max", "duration_hours",
            "starts_at", "photo_file_ids", "status", "created_at",
        ]
        with destination.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=headers, delimiter=";")
            writer.writeheader()
            writer.writerows(dict(row) for row in rows)
        return destination

