from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .admin import build_admin_router
from .client import build_client_router
from .config import Config
from .db import Database
from .sheets import GoogleSheetsSync


async def run() -> None:
    config = Config.from_env()
    db = Database(config.db_path)
    db.initialize()
    sheets = GoogleSheetsSync(
        config.google_sheets_webhook_url,
        config.google_sheets_webhook_secret,
    )

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_admin_router(db, config, sheets))
    dispatcher.include_router(build_client_router(db, config, sheets))

    me = await bot.get_me()
    logging.getLogger(__name__).info(
        "Remonto AI started as @%s; admins=%s",
        me.username,
        sorted(config.admin_ids),
    )
    logging.getLogger(__name__).info(
        "Google Sheets sync: %s", "enabled" if sheets.enabled else "disabled"
    )
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Remonto AI stopped")


if __name__ == "__main__":
    main()
