from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.admin import build_admin_router
from app.client import build_client_router
from app.config import Config
from app.db import Database
from app.filters import AdminFilter
from app.formatters import estimate_price, parse_admin_datetime
from app.keyboards import (
    admin_home_keyboard,
    clients_keyboard,
    inventory_keyboard,
    order_keyboard,
    orders_keyboard,
    search_results_keyboard,
    statuses_keyboard,
)


def callback_values(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


class UiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "ui.db"
        self.db = Database(self.path)
        self.db.initialize()
        client = self.db.upsert_client(
            12345, "client", "Очень Длинное Имя Тестового Клиента", "+79990000000"
        )
        self.order_id = self.db.create_order(
            client,
            "Брянка",
            "Очень длинное описание заявки для проверки безопасной длины кнопки",
        )
        self.db.add_inventory("Подрозетник глубокий усиленный", 20, "шт.", 5)
        self.config = Config(
            "123456:ABCDEF",
            frozenset({777}),
            self.path,
            "Europe/Moscow",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_admin_filter_allows_only_configured_ids(self) -> None:
        admin_filter = AdminFilter(frozenset({777}))
        self.assertTrue(
            await admin_filter(SimpleNamespace(from_user=SimpleNamespace(id=777)))
        )
        self.assertFalse(
            await admin_filter(SimpleNamespace(from_user=SimpleNamespace(id=778)))
        )
        self.assertFalse(await admin_filter(SimpleNamespace(from_user=None)))

    def test_all_generated_callbacks_fit_telegram_limit(self) -> None:
        order = self.db.get_order(self.order_id)
        order_page = self.db.list_orders()
        client_page = self.db.list_clients()
        inventory = self.db.inventory()
        markups = [
            admin_home_keyboard(self.db.dashboard()["status"]),
            orders_keyboard(order_page, "all"),
            order_keyboard(order),
            statuses_keyboard(self.order_id),
            search_results_keyboard(self.db.search("длинное")),
            clients_keyboard(client_page),
            inventory_keyboard(inventory),
        ]
        values = [value for markup in markups for value in callback_values(markup)]
        self.assertGreater(len(values), 25)
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in values))

    def test_routers_register_full_panels(self) -> None:
        admin = build_admin_router(self.db, self.config)
        client = build_client_router(self.db, self.config)
        self.assertGreaterEqual(len(admin.message.handlers), 5)
        self.assertGreaterEqual(len(admin.callback_query.handlers), 17)
        self.assertGreaterEqual(len(client.message.handlers), 10)
        self.assertGreaterEqual(len(client.callback_query.handlers), 1)

    def test_datetime_validation(self) -> None:
        self.assertTrue(
            parse_admin_datetime("27.07.2026 14:30", "Europe/Moscow").startswith(
                "2026-07-27T11:30"
            )
        )
        self.assertEqual(parse_admin_datetime("-", "Europe/Moscow"), "")
        with self.assertRaises(ValueError):
            parse_admin_datetime("когда-нибудь вечером", "Europe/Moscow")

    def test_price_estimate_is_conservative(self) -> None:
        self.assertEqual(estimate_price("Установить смеситель"), (1500, 3000))
        self.assertEqual(estimate_price("Непонятная работа"), (0, 0))


class ConfigTests(unittest.TestCase):
    def test_valid_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123456:ABCDEF",
                "ADMIN_IDS": "100, 200",
                "DATABASE_PATH": "/data/test.db",
                "TIMEZONE": "Europe/Moscow",
            },
            clear=True,
        ):
            config = Config.from_env()
        self.assertEqual(config.admin_ids, frozenset({100, 200}))
        self.assertEqual(config.db_path, Path("/data/test.db"))

    def test_missing_admin_is_rejected(self) -> None:
        with (
            patch.dict(
                os.environ, {"BOT_TOKEN": "123456:ABCDEF", "ADMIN_IDS": ""}, clear=True
            ),
            self.assertRaises(RuntimeError),
        ):
            Config.from_env()


if __name__ == "__main__":
    unittest.main()
