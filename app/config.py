from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load a simple .env file without adding another runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: Path
    timezone: str

    @classmethod
    def from_env(cls) -> Config:
        _load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        raw_ids = os.getenv("ADMIN_IDS", "").replace(";", ",")
        try:
            admin_ids = frozenset(
                int(item.strip()) for item in raw_ids.split(",") if item.strip()
            )
        except ValueError as exc:
            raise RuntimeError(
                "ADMIN_IDS должен содержать Telegram ID через запятую"
            ) from exc

        if not token:
            raise RuntimeError("Не задан BOT_TOKEN")
        if not admin_ids:
            raise RuntimeError("Не задан ADMIN_IDS")

        return cls(
            bot_token=token,
            admin_ids=admin_ids,
            db_path=Path(
                os.getenv("DB_PATH")
                or os.getenv("DATABASE_PATH")
                or "data/remonto.db"
            ),
            timezone=os.getenv("TIMEZONE", "Europe/Moscow"),
        )
