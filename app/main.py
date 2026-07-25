import asyncio
import logging

from app.core.database import Database
from app.core.settings import Settings
from app.telegram.bot import run_bot


async def main() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_path)
    await database.initialize()
    await run_bot(settings, database)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(main())

