from __future__ import annotations

from typing import Any

BLOCKED_USERNAMES = {"leya_666"}

BLOCKED_NOTICE_TITLE = "ДОСТУП ОГРАНИЧЕН"
BLOCKED_ACCESS_TEXT = (
    "доступ к Le Nyan Paris для вас ограничен.\n\n"
    "вы заблокированы и больше не можете пользоваться ботом, Mini App, анкетами, "
    "письмами и другими разделами проекта."
)


def normalize_username(username: Any) -> str:
    return str(username or "").strip().lower().removeprefix("@")


def is_blocked_username(username: Any) -> bool:
    return normalize_username(username) in BLOCKED_USERNAMES
