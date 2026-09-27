from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    database_url: str
    public_base_url: str
    mini_app_url: str
    admin_ids: set[int]
    webhook_secret: str | None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    result: set[int] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            result.add(int(value))
        except ValueError as exc:
            raise RuntimeError(f"ADMIN_IDS contains invalid value: {value}") from exc
    if not result:
        raise RuntimeError("ADMIN_IDS must contain at least one Telegram user id")
    return result


def get_config() -> Config:
    bot_token = _required("BOT_TOKEN")
    database_url = _required("DATABASE_URL")
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    mini_app_url = os.getenv("MINI_APP_URL", "").strip()
    if not mini_app_url and public_base_url:
        mini_app_url = f"{public_base_url}/miniapp/"
    if not mini_app_url:
        mini_app_url = "https://example.com/miniapp/"
    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip() or None
    return Config(
        bot_token=bot_token,
        database_url=database_url,
        public_base_url=public_base_url,
        mini_app_url=mini_app_url,
        admin_ids=_admin_ids(),
        webhook_secret=webhook_secret,
    )
