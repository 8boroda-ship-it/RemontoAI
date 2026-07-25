from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class GoogleSheetsSync:
    """Best-effort, idempotent delivery of order snapshots to Apps Script."""

    def __init__(self, webhook_url: str = "", webhook_secret: str = ""):
        self.webhook_url = webhook_url.strip()
        self.webhook_secret = webhook_secret.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url and self.webhook_secret)

    async def sync_order(self, order: Mapping[str, Any] | None) -> bool:
        if not self.enabled or not order:
            return False

        order_id = order.get("id", "unknown")
        payload = {
            "secret": self.webhook_secret,
            "order": dict(order),
        }
        timeout = aiohttp.ClientTimeout(total=8, connect=4)

        for attempt in range(1, 4):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        self.webhook_url,
                        json=payload,
                        allow_redirects=True,
                    ) as response:
                        data = await response.json(content_type=None)
                        if response.status < 300 and data.get("ok") is True:
                            logger.info(
                                "Google Sheets synced order=%s attempt=%s",
                                order_id,
                                attempt,
                            )
                            return True
                        logger.warning(
                            "Google Sheets rejected order=%s status=%s attempt=%s",
                            order_id,
                            response.status,
                            attempt,
                        )
            except Exception:
                logger.warning(
                    "Google Sheets sync failed order=%s attempt=%s",
                    order_id,
                    attempt,
                    exc_info=True,
                )

            if attempt < 3:
                await asyncio.sleep(0.5 * attempt)

        return False
