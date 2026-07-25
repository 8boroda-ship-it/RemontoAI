from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.sheets import GoogleSheetsSync


class _Response:
    status = 200

    async def json(self, content_type=None):
        del content_type
        return {"ok": True}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Session:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return _Response()


class GoogleSheetsSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_sync_is_a_noop(self) -> None:
        sync = GoogleSheetsSync()
        self.assertFalse(await sync.sync_order({"id": 1}))

    async def test_order_is_sent_with_secret(self) -> None:
        session = _Session()
        with patch("app.sheets.aiohttp.ClientSession", return_value=session):
            sync = GoogleSheetsSync("https://example.test/webhook", "secret")
            self.assertTrue(await sync.sync_order({"id": 42, "status": "new"}))

        self.assertEqual(len(session.post_calls), 1)
        args, kwargs = session.post_calls[0]
        self.assertEqual(args[0], "https://example.test/webhook")
        self.assertEqual(kwargs["json"]["secret"], "secret")
        self.assertEqual(kwargs["json"]["order"]["id"], 42)
        self.assertTrue(kwargs["allow_redirects"])

    async def test_http_failure_never_escapes(self) -> None:
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.post.side_effect = RuntimeError("network unavailable")
        with (
            patch("app.sheets.aiohttp.ClientSession", return_value=session),
            patch("app.sheets.asyncio.sleep", new=AsyncMock()),
        ):
            sync = GoogleSheetsSync("https://example.test/webhook", "secret")
            self.assertFalse(await sync.sync_order({"id": 7}))


if __name__ == "__main__":
    unittest.main()
