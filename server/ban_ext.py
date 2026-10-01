from __future__ import annotations

import json
from html import escape
from typing import Any

import asyncpg
from fastapi import Request
from fastapi.responses import JSONResponse
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from .db import find_user_by_identifier
from .security import validate_webapp_init_data

BANNED_USERNAMES: set[str] = set()
INACTIVE_EXCLUDED_USERNAMES = {
    "limeksvins",
    "leya_666",
    "luka_vo1d",
    "communityr34",
    "tvorog_t",
    "mimilset",
    "salamsister",
    "ilovekapebebra",
    "sofiysheva",
    "sofiyusheva",
}

RESTRICTION_MESSAGES = {
    "banned": {
        "title": "доступ ограничен",
        "heading": "вы заблокированы",
        "message": "вы заблокированы в Le Nyan Paris. доступ к боту, Mini App, анкетам, письмам и участию в проекте закрыт.",
        "html": (
            "⛔ <b>доступ ограничен</b>\n\n"
            "вы заблокированы в <b>Le Nyan Paris</b>.\n\n"
            "доступ к боту, Mini App, анкетам, письмам и участию в проекте закрыт."
        ),
    },
    "inactive_excluded": {
        "title": "вы исключены",
        "heading": "вы исключены",
        "message": "вы исключены из Le Nyan Paris за бездействие. доступ к боту, Mini App, анкетам, письмам и участию в проекте закрыт.",
        "html": (
            "⛔ <b>вы исключены</b>\n\n"
            "вы исключены из <b>Le Nyan Paris</b> за бездействие.\n\n"
            "доступ к боту, Mini App, анкетам, письмам и участию в проекте закрыт."
        ),
    },
}


def _clean_username(username: Any) -> str:
    return str(username or "").strip().lstrip("@").lower()


def restriction_detail(code: str) -> dict[str, Any]:
    data = RESTRICTION_MESSAGES.get(code, RESTRICTION_MESSAGES["banned"])
    return {
        "blocked": True,
        "code": code,
        "title": data["title"],
        "heading": data["heading"],
        "message": data["message"],
    }


async def _matches_known_username(pool: asyncpg.Pool | None, telegram_id: int | None, usernames: set[str]) -> bool:
    if pool is None or telegram_id is None:
        return False

    for username in usernames:
        try:
            row = await find_user_by_identifier(pool, f"@{username}")
        except Exception as exc:
            print(f"failed to check restricted user {username}: {exc}")
            continue
        if row is not None and int(row["telegram_id"]) == int(telegram_id):
            return True

    return False


async def get_restriction_code(pool: asyncpg.Pool | None, telegram_id: int | None, username: Any = None) -> str | None:
    username_clean = _clean_username(username)

    if username_clean in INACTIVE_EXCLUDED_USERNAMES:
        return "inactive_excluded"
    if await _matches_known_username(pool, telegram_id, INACTIVE_EXCLUDED_USERNAMES):
        return "inactive_excluded"

    if username_clean in BANNED_USERNAMES:
        return "banned"
    if await _matches_known_username(pool, telegram_id, BANNED_USERNAMES):
        return "banned"

    return None


async def restricted_message_handler(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    pool: asyncpg.Pool | None = context.application.bot_data.get("pool")
    code = await get_restriction_code(pool, user.id, user.username)
    if code is None:
        return

    message = update.effective_message
    if message is not None:
        await message.reply_text(RESTRICTION_MESSAGES[code]["html"], parse_mode=ParseMode.HTML)
    raise ApplicationHandlerStop


async def restricted_callback_handler(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    query = update.callback_query
    if user is None or query is None:
        return

    pool: asyncpg.Pool | None = context.application.bot_data.get("pool")
    code = await get_restriction_code(pool, user.id, user.username)
    if code is None:
        return

    await query.answer(RESTRICTION_MESSAGES[code]["title"], show_alert=True)
    if query.message is not None:
        await query.message.reply_text(RESTRICTION_MESSAGES[code]["html"], parse_mode=ParseMode.HTML)
    raise ApplicationHandlerStop


async def restricted_api_middleware(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    try:
        body_bytes = await request.body()
        payload = json.loads(body_bytes.decode("utf-8") or "{}")
        init_data = str(payload.get("initData") or "")
    except Exception:
        return await call_next(request)

    if not init_data:
        return await call_next(request)

    config = getattr(request.app.state, "config", None)
    pool = getattr(request.app.state, "pool", None)
    if config is None:
        return await call_next(request)

    try:
        user = validate_webapp_init_data(init_data, config.bot_token)
        telegram_id = int(user["id"])
    except Exception:
        return await call_next(request)

    code = await get_restriction_code(pool, telegram_id, user.get("username"))
    if code is not None:
        return JSONResponse(status_code=403, content={"detail": restriction_detail(code)})

    return await call_next(request)


def patch_banned_access(main_base_module) -> None:
    app = main_base_module.app
    if getattr(app.state, "banned_access_patch_installed", False):
        return

    app.middleware("http")(restricted_api_middleware)

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config, pool):
        telegram_app = original_build_application(config, pool)
        telegram_app.add_handler(CallbackQueryHandler(restricted_callback_handler), group=-100)
        telegram_app.add_handler(MessageHandler(filters.ALL, restricted_message_handler), group=-100)
        return telegram_app

    main_base_module.build_application = wrapped_build_application
    app.state.banned_access_patch_installed = True
