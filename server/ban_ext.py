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

BANNED_USERNAMES = {"leya_666"}
BLOCKED_TITLE = "доступ ограничен"
BLOCKED_MESSAGE = (
    "вы заблокированы в Le Nyan Paris. "
    "доступ к боту, Mini App, анкетам, письмам и участию в проекте закрыт."
)
BLOCKED_HTML = (
    "⛔ <b>доступ ограничен</b>\n\n"
    "вы заблокированы в <b>Le Nyan Paris</b>.\n\n"
    "доступ к боту, Mini App, анкетам, письмам и участию в проекте закрыт."
)


def _clean_username(username: Any) -> str:
    return str(username or "").strip().lstrip("@").lower()


def blocked_detail() -> dict[str, Any]:
    return {
        "blocked": True,
        "title": BLOCKED_TITLE,
        "message": BLOCKED_MESSAGE,
    }


async def is_banned_user(pool: asyncpg.Pool | None, telegram_id: int | None, username: Any = None) -> bool:
    username_clean = _clean_username(username)
    if username_clean in BANNED_USERNAMES:
        return True

    if pool is None or telegram_id is None:
        return False

    for banned_username in BANNED_USERNAMES:
        try:
            row = await find_user_by_identifier(pool, f"@{banned_username}")
        except Exception as exc:
            print(f"failed to check banned user {banned_username}: {exc}")
            continue
        if row is not None and int(row["telegram_id"]) == int(telegram_id):
            return True

    return False


async def banned_message_handler(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    pool: asyncpg.Pool | None = context.application.bot_data.get("pool")
    if not await is_banned_user(pool, user.id, user.username):
        return

    message = update.effective_message
    if message is not None:
        await message.reply_text(BLOCKED_HTML, parse_mode=ParseMode.HTML)
    raise ApplicationHandlerStop


async def banned_callback_handler(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    query = update.callback_query
    if user is None or query is None:
        return

    pool: asyncpg.Pool | None = context.application.bot_data.get("pool")
    if not await is_banned_user(pool, user.id, user.username):
        return

    await query.answer("доступ ограничен", show_alert=True)
    if query.message is not None:
        await query.message.reply_text(BLOCKED_HTML, parse_mode=ParseMode.HTML)
    raise ApplicationHandlerStop


async def banned_api_middleware(request: Request, call_next):
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

    if await is_banned_user(pool, telegram_id, user.get("username")):
        return JSONResponse(status_code=403, content={"detail": blocked_detail()})

    return await call_next(request)


def patch_banned_access(main_base_module) -> None:
    app = main_base_module.app
    if getattr(app.state, "banned_access_patch_installed", False):
        return

    app.middleware("http")(banned_api_middleware)

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config, pool):
        telegram_app = original_build_application(config, pool)
        telegram_app.add_handler(CallbackQueryHandler(banned_callback_handler), group=-100)
        telegram_app.add_handler(MessageHandler(filters.ALL, banned_message_handler), group=-100)
        return telegram_app

    main_base_module.build_application = wrapped_build_application
    app.state.banned_access_patch_installed = True
