from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import build_admin_router
from app.client import build_client_router
from app.config import Config
from app.db import Database
from app.domain import STATUS_IN_PROGRESS


class DispatchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "dispatch.db"
        self.db = Database(self.path)
        self.db.initialize()
        client_id = self.db.upsert_client(11, "client", "Клиент")
        self.order_id = self.db.create_order(client_id, "Брянка", "Установить розетку")
        self.config = Config(
            "123456:ABCDEF",
            frozenset({777}),
            self.path,
            "Europe/Moscow",
        )
        self.sheets = SimpleNamespace(sync_order=AsyncMock(return_value=True))
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(
            build_admin_router(self.db, self.config, self.sheets)
        )
        self.dispatcher.include_router(
            build_client_router(self.db, self.config, self.sheets)
        )
        self.bot = Bot(self.config.bot_token)
        self.calls = []

    async def asyncTearDown(self) -> None:
        await self.bot.session.close()
        self.temp_dir.cleanup()

    async def fake_bot_call(self, method, request_timeout=None):
        del request_timeout
        self.calls.append(method)
        name = type(method).__name__
        if name in {"SendMessage", "SendDocument"}:
            return Message(
                message_id=999,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=getattr(method, "text", None),
            )
        if name in {"EditMessageText", "AnswerCallbackQuery"}:
            return True
        return True

    @staticmethod
    def user(user_id: int, name: str = "User") -> User:
        return User(id=user_id, is_bot=False, first_name=name)

    def message(self, user_id: int, text: str, message_id: int = 1) -> Message:
        entities = None
        if text.startswith("/"):
            entities = [{"type": "bot_command", "offset": 0, "length": len(text)}]
        return Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=self.user(user_id),
            text=text,
            entities=entities,
        )

    def callback(self, user_id: int, data: str) -> CallbackQuery:
        return CallbackQuery(
            id=f"callback-{user_id}-{data}",
            from_user=self.user(user_id),
            chat_instance="test",
            data=data,
            message=Message(
                message_id=50,
                date=datetime.now(UTC),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=1, is_bot=True, first_name="Bot"),
                text="old",
            ),
        )

    async def test_admin_command_opens_dashboard(self) -> None:
        update = Update(update_id=1, message=self.message(777, "/admin"))
        with patch.object(Bot, "__call__", self.fake_bot_call):
            await self.dispatcher.feed_update(self.bot, update)
        sent = [call for call in self.calls if type(call).__name__ == "SendMessage"]
        self.assertEqual(len(sent), 1)
        self.assertIn("Панель администратора", sent[0].text)

    async def test_non_admin_cannot_open_dashboard(self) -> None:
        update = Update(update_id=2, message=self.message(888, "/admin"))
        with patch.object(Bot, "__call__", self.fake_bot_call):
            await self.dispatcher.feed_update(self.bot, update)
        sent = [call for call in self.calls if type(call).__name__ == "SendMessage"]
        self.assertEqual(len(sent), 1)
        self.assertIn("только администратору", sent[0].text)
        self.assertIn("888", sent[0].text)
        self.assertNotIn("Панель администратора", sent[0].text)

    async def test_plain_first_message_starts_request_form(self) -> None:
        update = Update(
            update_id=5,
            message=self.message(888, "Течёт смеситель на кухне"),
        )
        with patch.object(Bot, "__call__", self.fake_bot_call):
            await self.dispatcher.feed_update(self.bot, update)
        sent = [call for call in self.calls if type(call).__name__ == "SendMessage"]
        self.assertEqual(len(sent), 1)
        self.assertIn("Задачу сохранил", sent[0].text)
        data = await self.dispatcher.fsm.get_context(
            bot=self.bot, chat_id=888, user_id=888
        ).get_data()
        self.assertEqual(data["description"], "Течёт смеситель на кухне")

    async def test_order_card_and_status_callback_end_to_end(self) -> None:
        with patch.object(Bot, "__call__", self.fake_bot_call):
            await self.dispatcher.feed_update(
                self.bot,
                Update(
                    update_id=3,
                    callback_query=self.callback(777, f"a:o:{self.order_id}"),
                ),
            )
            await self.dispatcher.feed_update(
                self.bot,
                Update(
                    update_id=4,
                    callback_query=self.callback(
                        777, f"a:ss:{self.order_id}:{STATUS_IN_PROGRESS}"
                    ),
                ),
            )
        edits = [
            call for call in self.calls if type(call).__name__ == "EditMessageText"
        ]
        self.assertTrue(any(f"Заявка #{self.order_id}" in call.text for call in edits))
        self.assertEqual(self.db.get_order(self.order_id)["status"], STATUS_IN_PROGRESS)
        self.sheets.sync_order.assert_awaited_once()
        synced_order = self.sheets.sync_order.await_args.args[0]
        self.assertEqual(synced_order["id"], self.order_id)
        self.assertEqual(synced_order["status"], STATUS_IN_PROGRESS)
        notifications = [
            call
            for call in self.calls
            if type(call).__name__ == "SendMessage" and int(call.chat_id) == 11
        ]
        self.assertEqual(len(notifications), 1)
        self.assertIn("В работе", notifications[0].text)


if __name__ == "__main__":
    unittest.main()
