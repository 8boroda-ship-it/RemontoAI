from __future__ import annotations

import csv
import io
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .domain import ACTIVE_STATUSES, STATUS_NEW, Page


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    """Small SQLite repository with idempotent legacy-schema migration."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._lock, self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            self._stage_v1_orders(conn)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER,
                    username TEXT,
                    full_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '',
                    difficulty INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'new',
                    city TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    preferred_time TEXT NOT NULL DEFAULT '',
                    scheduled_at TEXT NOT NULL DEFAULT '',
                    estimated_price INTEGER NOT NULL DEFAULT 0,
                    actual_price INTEGER NOT NULL DEFAULT 0,
                    material_cost INTEGER NOT NULL DEFAULT 0,
                    duration_minutes INTEGER NOT NULL DEFAULT 0,
                    admin_note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'telegram',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(client_id) REFERENCES clients(id)
                );

                CREATE TABLE IF NOT EXISTS order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    actor_id INTEGER,
                    event_type TEXT NOT NULL,
                    old_value TEXT NOT NULL DEFAULT '',
                    new_value TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(order_id) REFERENCES orders(id)
                );

                CREATE TABLE IF NOT EXISTS inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 0,
                    unit TEXT NOT NULL DEFAULT 'шт.',
                    min_quantity REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._import_v1_orders(conn)
            self._migrate_legacy(conn)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_clients_telegram
                    ON clients(telegram_id);
                CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
                CREATE INDEX IF NOT EXISTS idx_orders_schedule ON orders(scheduled_at);
                CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_id);
                CREATE INDEX IF NOT EXISTS idx_events_order ON order_events(order_id);
                """
            )

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            is not None
        )

    def _stage_v1_orders(self, conn: sqlite3.Connection) -> None:
        """Move the original production schema aside before creating v2 tables."""
        if not self._table_exists(conn, "orders"):
            return
        columns = self._columns(conn, "orders")
        if "telegram_user_id" not in columns or "client_id" in columns:
            return
        if self._table_exists(conn, "orders_legacy_v1"):
            raise RuntimeError("Найдена незавершённая миграция orders_legacy_v1")
        conn.execute("ALTER TABLE orders RENAME TO orders_legacy_v1")

    def _import_v1_orders(self, conn: sqlite3.Connection) -> None:
        """Import the first Railway schema without losing existing requests."""
        if not self._table_exists(conn, "orders_legacy_v1"):
            return
        rows = conn.execute(
            "SELECT * FROM orders_legacy_v1 ORDER BY id"
        ).fetchall()
        for row in rows:
            item = dict(row)
            telegram_id = int(item["telegram_user_id"])
            client = conn.execute(
                "SELECT id FROM clients WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
            if client:
                client_id = int(client["id"])
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO clients (
                        telegram_id, username, full_name, phone, city, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telegram_id,
                        item.get("telegram_username"),
                        item.get("client_name") or "",
                        item.get("phone") or "",
                        item.get("city") or "",
                        item.get("created_at") or utc_now(),
                    ),
                )
                client_id = int(cursor.lastrowid)

            notes: list[str] = []
            if item.get("service_name"):
                notes.append(f"Услуга: {item['service_name']}")
            if item.get("price_max"):
                notes.append(f"Верхняя оценка: {item['price_max']}")
            if item.get("duration_hours"):
                notes.append(f"Длительность: {item['duration_hours']} ч.")
            created_at = item.get("created_at") or utc_now()
            conn.execute(
                """
                INSERT INTO orders (
                    id, client_id, status, city, address, description,
                    scheduled_at, estimated_price, admin_note, source,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'telegram', ?, ?)
                """,
                (
                    item["id"],
                    client_id,
                    item.get("status") or STATUS_NEW,
                    item.get("city") or "",
                    item.get("address") or "",
                    item.get("description") or "",
                    item.get("starts_at") or "",
                    item.get("price_min") or 0,
                    "; ".join(notes),
                    created_at,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO order_events (
                    order_id, event_type, new_value, created_at
                ) VALUES (?, 'migrated:v1', ?, ?)
                """,
                (item["id"], item.get("status") or STATUS_NEW, utc_now()),
            )
        conn.execute("DROP TABLE orders_legacy_v1")

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}

    def _ensure_columns(
        self, conn: sqlite3.Connection, table: str, columns: dict[str, str]
    ) -> None:
        existing = self._columns(conn, table)
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _migrate_legacy(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            "clients",
            {
                "telegram_id": "INTEGER",
                "username": "TEXT",
                "full_name": "TEXT NOT NULL DEFAULT ''",
                "phone": "TEXT NOT NULL DEFAULT ''",
                "city": "TEXT NOT NULL DEFAULT ''",
                "difficulty": "INTEGER NOT NULL DEFAULT 0",
                "notes": "TEXT NOT NULL DEFAULT ''",
                "created_at": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self._ensure_columns(
            conn,
            "orders",
            {
                "client_id": "INTEGER",
                "status": "TEXT NOT NULL DEFAULT 'new'",
                "city": "TEXT NOT NULL DEFAULT ''",
                "address": "TEXT NOT NULL DEFAULT ''",
                "description": "TEXT NOT NULL DEFAULT ''",
                "preferred_time": "TEXT NOT NULL DEFAULT ''",
                "scheduled_at": "TEXT NOT NULL DEFAULT ''",
                "estimated_price": "INTEGER NOT NULL DEFAULT 0",
                "actual_price": "INTEGER NOT NULL DEFAULT 0",
                "material_cost": "INTEGER NOT NULL DEFAULT 0",
                "duration_minutes": "INTEGER NOT NULL DEFAULT 0",
                "admin_note": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT 'telegram'",
                "created_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
        )
        now = utc_now()
        conn.execute("UPDATE clients SET created_at=? WHERE created_at=''", (now,))
        conn.execute("UPDATE orders SET created_at=? WHERE created_at=''", (now,))
        conn.execute("UPDATE orders SET updated_at=created_at WHERE updated_at=''")
        conn.execute("UPDATE orders SET status=? WHERE status=''", (STATUS_NEW,))

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def upsert_client(
        self,
        telegram_id: int,
        username: str | None,
        full_name: str,
        phone: str = "",
        city: str = "",
    ) -> int:
        now = utc_now()
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM clients WHERE telegram_id=?", (telegram_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE clients
                    SET username=?, full_name=?,
                        phone=CASE WHEN ?='' THEN phone ELSE ? END,
                        city=CASE WHEN ?='' THEN city ELSE ? END
                    WHERE id=?
                    """,
                    (username, full_name, phone, phone, city, city, row["id"]),
                )
                return int(row["id"])
            cursor = conn.execute(
                """
                INSERT INTO clients
                    (telegram_id, username, full_name, phone, city, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (telegram_id, username, full_name, phone, city, now),
            )
            return int(cursor.lastrowid)

    def create_order(
        self,
        client_id: int,
        city: str,
        description: str,
        address: str = "",
        preferred_time: str = "",
        estimated_price: int = 0,
    ) -> int:
        now = utc_now()
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    client_id, status, city, address, description,
                    preferred_time, estimated_price, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    STATUS_NEW,
                    city,
                    address,
                    description,
                    preferred_time,
                    estimated_price,
                    now,
                    now,
                ),
            )
            order_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO order_events
                    (order_id, event_type, new_value, created_at)
                VALUES (?, 'created', ?, ?)
                """,
                (order_id, STATUS_NEW, now),
            )
            return order_id

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT o.*, c.full_name, c.phone, c.telegram_id, c.username,
                       c.difficulty, c.notes AS client_notes
                FROM orders o
                LEFT JOIN clients c ON c.id=o.client_id
                WHERE o.id=?
                """,
                (order_id,),
            ).fetchone()
            return self._row(row)

    def list_orders(
        self, status: str | None = None, page: int = 0, per_page: int = 8
    ) -> Page:
        page = max(page, 0)
        where = ""
        params: list[Any] = []
        if status and status != "all":
            where = "WHERE o.status=?"
            params.append(status)
        with self._connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM orders o {where}", params
                ).fetchone()[0]
            )
            pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, pages - 1)
            rows = conn.execute(
                f"""
                SELECT o.id, o.status, o.city, o.description, o.scheduled_at,
                       o.estimated_price, o.actual_price, o.created_at,
                       c.full_name, c.phone
                FROM orders o
                LEFT JOIN clients c ON c.id=o.client_id
                {where}
                ORDER BY
                    CASE o.status WHEN 'new' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                    o.created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, per_page, page * per_page],
            ).fetchall()
            return Page([dict(row) for row in rows], page, pages, total)

    def update_order(
        self, order_id: int, field: str, value: Any, actor_id: int | None = None
    ) -> bool:
        allowed = {
            "status",
            "city",
            "address",
            "description",
            "preferred_time",
            "scheduled_at",
            "estimated_price",
            "actual_price",
            "material_cost",
            "duration_minutes",
            "admin_note",
        }
        if field not in allowed:
            raise ValueError(f"Поле {field!r} нельзя менять")
        with self._lock, self._connection() as conn:
            row = conn.execute(
                f"SELECT {field} FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if not row:
                return False
            old_value = "" if row[field] is None else str(row[field])
            new_value = "" if value is None else str(value)
            conn.execute(
                f"UPDATE orders SET {field}=?, updated_at=? WHERE id=?",
                (value, utc_now(), order_id),
            )
            conn.execute(
                """
                INSERT INTO order_events
                    (order_id, actor_id, event_type, old_value, new_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    actor_id,
                    f"updated:{field}",
                    old_value,
                    new_value,
                    utc_now(),
                ),
            )
            return True

    def dashboard(self) -> dict[str, Any]:
        today = datetime.now(UTC).date().isoformat()
        month = today[:7]
        with self._connection() as conn:
            by_status = {
                str(row["status"]): int(row["amount"])
                for row in conn.execute(
                    "SELECT status, COUNT(*) amount FROM orders GROUP BY status"
                )
            }
            today_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?",
                    (today,),
                ).fetchone()[0]
            )
            month_revenue = int(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(actual_price),0)
                    FROM orders
                    WHERE status='done' AND substr(updated_at,1,7)=?
                    """,
                    (month,),
                ).fetchone()[0]
            )
            overdue = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM orders
                    WHERE status IN ({",".join("?" for _ in ACTIVE_STATUSES)})
                      AND scheduled_at<>'' AND scheduled_at<?
                    """,
                    [*ACTIVE_STATUSES, utc_now()],
                ).fetchone()[0]
            )
            low_stock = int(
                conn.execute(
                    "SELECT COUNT(*) FROM inventory WHERE quantity<=min_quantity"
                ).fetchone()[0]
            )
        return {
            "status": by_status,
            "today": today_count,
            "month_revenue": month_revenue,
            "overdue": overdue,
            "low_stock": low_stock,
        }

    def schedule(self, days: int = 14) -> list[dict[str, Any]]:
        start = datetime.now(UTC).date().isoformat()
        end = (datetime.now(UTC).date() + timedelta(days=days)).isoformat()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.scheduled_at, o.city, o.description, o.status,
                       c.full_name, c.phone
                FROM orders o
                LEFT JOIN clients c ON c.id=o.client_id
                WHERE o.scheduled_at<>'' AND substr(o.scheduled_at,1,10) BETWEEN ? AND ?
                  AND o.status NOT IN ('done','cancelled')
                ORDER BY o.scheduled_at
                """,
                (start, end),
            ).fetchall()
            return [dict(row) for row in rows]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        needle = f"%{query.strip()}%"
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.status, o.city, o.description, o.scheduled_at,
                       c.full_name, c.phone
                FROM orders o
                LEFT JOIN clients c ON c.id=o.client_id
                WHERE CAST(o.id AS TEXT)=?
                   OR o.description LIKE ? COLLATE NOCASE
                   OR o.address LIKE ? COLLATE NOCASE
                   OR c.full_name LIKE ? COLLATE NOCASE
                   OR c.phone LIKE ?
                ORDER BY o.created_at DESC LIMIT ?
                """,
                (query.strip().lstrip("#"), needle, needle, needle, needle, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def client_orders(self, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.status, o.description, o.city, o.created_at
                FROM orders o
                JOIN clients c ON c.id=o.client_id
                WHERE c.telegram_id=?
                ORDER BY o.created_at DESC
                LIMIT ?
                """,
                (telegram_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_clients(self, page: int = 0, per_page: int = 10) -> Page:
        page = max(0, page)
        with self._connection() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0])
            pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, pages - 1)
            rows = conn.execute(
                """
                SELECT c.*, COUNT(o.id) order_count,
                       COALESCE(SUM(CASE WHEN o.status='done' THEN o.actual_price ELSE 0 END),0)
                           total_revenue
                FROM clients c
                LEFT JOIN orders o ON o.client_id=c.id
                GROUP BY c.id
                ORDER BY c.created_at DESC LIMIT ? OFFSET ?
                """,
                (per_page, page * per_page),
            ).fetchall()
            return Page([dict(row) for row in rows], page, pages, total)

    def get_client(self, client_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT c.*, COUNT(o.id) order_count,
                       COALESCE(SUM(CASE WHEN o.status='done' THEN o.actual_price ELSE 0 END),0)
                           total_revenue
                FROM clients c
                LEFT JOIN orders o ON o.client_id=c.id
                WHERE c.id=?
                GROUP BY c.id
                """,
                (client_id,),
            ).fetchone()
            return self._row(row)

    def inventory(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory ORDER BY quantity<=min_quantity DESC, name"
            ).fetchall()
            return [dict(row) for row in rows]

    def add_inventory(
        self, name: str, quantity: float, unit: str, minimum: float
    ) -> int:
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO inventory(name, quantity, unit, min_quantity, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, quantity, unit, minimum, utc_now()),
            )
            return int(cursor.lastrowid)

    def change_inventory(self, item_id: int, delta: float) -> bool:
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE inventory
                SET quantity=MAX(0, quantity+?), updated_at=?
                WHERE id=?
                """,
                (delta, utc_now(), item_id),
            )
            return cursor.rowcount > 0

    def export_orders_csv(self) -> bytes:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT o.id, o.status, o.city, o.address, o.description,
                       o.preferred_time, o.scheduled_at, o.estimated_price,
                       o.actual_price, o.material_cost, o.duration_minutes,
                       o.admin_note, o.created_at, o.updated_at,
                       c.full_name, c.phone, c.username
                FROM orders o
                LEFT JOIN clients c ON c.id=o.client_id
                ORDER BY o.id
                """
            ).fetchall()
        output = io.StringIO()
        fields = [
            "id",
            "status",
            "city",
            "address",
            "description",
            "preferred_time",
            "scheduled_at",
            "estimated_price",
            "actual_price",
            "material_cost",
            "duration_minutes",
            "admin_note",
            "created_at",
            "updated_at",
            "full_name",
            "phone",
            "username",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
        return ("\ufeff" + output.getvalue()).encode("utf-8")

    def order_events(self, order_id: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM order_events WHERE order_id=? ORDER BY id", (order_id,)
            ).fetchall()
            return [dict(row) for row in rows]
