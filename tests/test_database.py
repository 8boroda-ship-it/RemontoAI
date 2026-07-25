from __future__ import annotations

import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.domain import STATUS_DONE, STATUS_NEW


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(self.db_path)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_sample(self) -> tuple[int, int]:
        client_id = self.db.upsert_client(
            101, "master_client", "Иван Клиентов", "+79990000000", "Брянка"
        )
        order_id = self.db.create_order(
            client_id,
            "Брянка",
            "Установить смеситель",
            "ул. Центральная, 1",
            "завтра после 14:00",
            1800,
        )
        return client_id, order_id

    def test_create_and_read_order(self) -> None:
        client_id, order_id = self.create_sample()
        order = self.db.get_order(order_id)
        self.assertIsNotNone(order)
        self.assertEqual(order["client_id"], client_id)
        self.assertEqual(order["status"], STATUS_NEW)
        self.assertEqual(order["full_name"], "Иван Клиентов")
        self.assertEqual(order["estimated_price"], 1800)

    def test_client_upsert_does_not_duplicate(self) -> None:
        first = self.db.upsert_client(101, "one", "Иван")
        second = self.db.upsert_client(101, "two", "Иван Новый", "+70000000001")
        self.assertEqual(first, second)
        page = self.db.list_clients()
        self.assertEqual(page.total, 1)

    def test_status_and_money_updates_are_audited(self) -> None:
        _, order_id = self.create_sample()
        self.assertTrue(self.db.update_order(order_id, "status", STATUS_DONE, 777))
        self.assertTrue(self.db.update_order(order_id, "actual_price", 2500, 777))
        order = self.db.get_order(order_id)
        self.assertEqual(order["status"], STATUS_DONE)
        self.assertEqual(order["actual_price"], 2500)
        events = self.db.order_events(order_id)
        self.assertEqual(events[-2]["event_type"], "updated:status")
        self.assertEqual(events[-1]["event_type"], "updated:actual_price")
        self.assertEqual(events[-1]["actor_id"], 777)

    def test_rejects_arbitrary_sql_field(self) -> None:
        _, order_id = self.create_sample()
        with self.assertRaises(ValueError):
            self.db.update_order(order_id, "id=0; DROP TABLE orders; --", "x")
        self.assertIsNotNone(self.db.get_order(order_id))

    def test_list_pagination_and_search(self) -> None:
        client = self.db.upsert_client(101, None, "Иван", "+79990000000")
        for index in range(11):
            self.db.create_order(client, "Алчевск", f"Заявка номер {index}")
        first = self.db.list_orders(page=0, per_page=5)
        last = self.db.list_orders(page=99, per_page=5)
        self.assertEqual(first.total, 11)
        self.assertEqual(first.pages, 3)
        self.assertEqual(last.page, 2)
        self.assertEqual(len(self.db.search("номер 7")), 1)
        self.assertEqual(len(self.db.search("+7999")), 11)

    def test_csv_is_excel_friendly_and_complete(self) -> None:
        _, order_id = self.create_sample()
        self.db.update_order(order_id, "actual_price", 2300)
        raw = self.db.export_orders_csv()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["full_name"], "Иван Клиентов")
        self.assertEqual(rows[0]["actual_price"], "2300")

    def test_dashboard_counts(self) -> None:
        _, first = self.create_sample()
        client = self.db.upsert_client(202, None, "Пётр")
        second = self.db.create_order(client, "Стаханов", "Розетка")
        self.db.update_order(first, "status", STATUS_DONE)
        self.db.update_order(first, "actual_price", 3000)
        dashboard = self.db.dashboard()
        self.assertEqual(dashboard["status"][STATUS_NEW], 1)
        self.assertEqual(dashboard["status"][STATUS_DONE], 1)
        self.assertGreaterEqual(dashboard["month_revenue"], 3000)
        self.assertIsNotNone(self.db.get_order(second))


class LegacyMigrationTests(unittest.TestCase):
    def test_minimal_old_tables_are_upgraded_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE clients (
                    id INTEGER PRIMARY KEY,
                    full_name TEXT
                );
                INSERT INTO clients(id, full_name) VALUES (9, 'Старый клиент');
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    description TEXT
                );
                INSERT INTO orders(id, description) VALUES (15, 'Старая заявка');
                """
            )
            conn.commit()
            conn.close()

            db = Database(path)
            db.initialize()
            order = db.get_order(15)
            self.assertEqual(order["description"], "Старая заявка")
            self.assertEqual(order["status"], STATUS_NEW)
            self.assertEqual(db.list_clients().total, 1)

    def test_v1_railway_schema_is_rebuilt_and_remains_writable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "railway-v1.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE orders (
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
                INSERT INTO orders (
                    telegram_user_id, telegram_username, client_name, phone,
                    city, address, description, service_code, service_name,
                    price_min, price_max, duration_hours, starts_at, created_at
                ) VALUES (
                    321, 'old_client', 'Старый клиент', '+79990000000',
                    'Брянка', 'Центральная 1', 'Течёт кран', 'plumbing',
                    'Сантехника', 1500, 3000, 2, '2026-07-27T12:00:00',
                    '2026-07-25T20:00:00+00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            db = Database(path)
            db.initialize()
            migrated = db.get_order(1)
            self.assertEqual(migrated["telegram_id"], 321)
            self.assertEqual(migrated["description"], "Течёт кран")
            self.assertEqual(migrated["estimated_price"], 1500)
            new_client = db.upsert_client(654, None, "Новый клиент")
            new_order = db.create_order(new_client, "Алчевск", "Повесить полку")
            self.assertEqual(db.get_order(new_order)["description"], "Повесить полку")


if __name__ == "__main__":
    unittest.main()
