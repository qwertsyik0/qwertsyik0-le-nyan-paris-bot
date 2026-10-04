from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, ContextTypes, MessageHandler, filters

from .config import Config
from .db import get_user_application, upsert_user
from .main_base import _is_admin, _row_value
from .security import validate_webapp_init_data

router = APIRouter()

CHARACTER_STATUSES = {
    "free": "свободен",
    "looking": "ищу игру",
    "in_scene": "в сцене",
    "afk": "афк",
    "dnd": "не беспокоить",
}

STATUS_ALIASES = {
    "свободен": "free",
    "свободна": "free",
    "свободно": "free",
    "ищу игру": "looking",
    "ищу": "looking",
    "поиск": "looking",
    "в сцене": "in_scene",
    "сцена": "in_scene",
    "играю": "in_scene",
    "афк": "afk",
    "afk": "afk",
    "не беспокоить": "dnd",
    "не беспокоить меня": "dnd",
    "dnd": "dnd",
}

LOCATIONS = [
    "дворец",
    "суд и канцелярия",
    "полиция",
    "тюрьма",
    "газета и слухи",
    "рынок",
    "кафе и салон",
    "больница",
    "театр",
    "армия и гарнизон",
    "подполье и катакомбы",
    "улицы Парижа",
    "другое",
]

LOCATION_ALIASES = {
    "двор": "дворец",
    "дворец": "дворец",
    "суд": "суд и канцелярия",
    "канцелярия": "суд и канцелярия",
    "суд и канцелярия": "суд и канцелярия",
    "полиция": "полиция",
    "тюрьма": "тюрьма",
    "газета": "газета и слухи",
    "пресса": "газета и слухи",
    "газета и слухи": "газета и слухи",
    "рынок": "рынок",
    "кафе": "кафе и салон",
    "салон": "кафе и салон",
    "кафе и салон": "кафе и салон",
    "больница": "больница",
    "медицина": "больница",
    "театр": "театр",
    "армия": "армия и гарнизон",
    "гарнизон": "армия и гарнизон",
    "армия и гарнизон": "армия и гарнизон",
    "подполье": "подполье и катакомбы",
    "катакомбы": "подполье и катакомбы",
    "подполье и катакомбы": "подполье и катакомбы",
    "улицы": "улицы Парижа",
    "улицы парижа": "улицы Парижа",
    "город": "улицы Парижа",
    "другое": "другое",
}


async def ensure_presence_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS character_status TEXT NOT NULL DEFAULT 'free';"
        )
        await conn.execute(
            "ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS current_location TEXT NOT NULL DEFAULT '';"
        )
        await conn.execute(
            "ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS presence_updated_at TIMESTAMPTZ;"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS paris_applications_character_status_idx ON paris_applications (character_status);"
        )


def _normalize_status(raw: Any) -> str | None:
    text = str(raw or "").strip().casefold()
    if not text:
        return None
    if text in CHARACTER_STATUSES:
        return text
    return STATUS_ALIASES.get(text)


def _normalize_location(raw: Any) -> str:
    text = " ".join(str(raw or "").strip().split())
    if not text:
        return ""
    normalized = LOCATION_ALIASES.get(text.casefold())
    if normalized:
        return normalized
    if len(text) > 80:
        raise ValueError("название локации слишком длинное")
    return text


def _presence_payload(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {
            "character_status": "free",
            "character_status_label": CHARACTER_STATUSES["free"],
            "current_location": "",
            "presence_updated_at": None,
        }
    status = str(_row_value(row, "character_status", "free") or "free")
    if status not in CHARACTER_STATUSES:
        status = "free"
    return {
        "character_status": status,
        "character_status_label": CHARACTER_STATUSES[status],
        "current_location": str(_row_value(row, "current_location", "") or ""),
        "presence_updated_at": (
            _row_value(row, "presence_updated_at").isoformat()
            if _row_value(row, "presence_updated_at") is not None
            else None
        ),
    }


async def _accepted_application(pool: asyncpg.Pool, telegram_id: int):
    await ensure_presence_schema(pool)
    row = await get_user_application(pool, telegram_id)
    if row is None or str(_row_value(row, "status") or "") != "accepted":
        raise HTTPException(status_code=403, detail="функция доступна после принятия анкеты")
    if str(_row_value(row, "player_status", "active") or "active") == "left":
        raise HTTPException(status_code=403, detail="участие завершено")
    return row


async def _web_auth(request: Request):
    config: Config = request.app.state.config
    pool: asyncpg.Pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    await upsert_user(pool, user)
    row = await _accepted_application(pool, telegram_id)
    return pool, body, telegram_id, row


@router.post("/api/game/presence")
async def game_presence(request: Request):
    pool, body, telegram_id, row = await _web_auth(request)
    return {
        "ok": True,
        **_presence_payload(row),
        "statuses": CHARACTER_STATUSES,
        "locations": LOCATIONS,
    }


@router.post("/api/game/presence/status")
async def game_presence_status(request: Request):
    pool, body, telegram_id, row = await _web_auth(request)
    status = _normalize_status(body.get("status"))
    if status is None:
        raise HTTPException(status_code=400, detail="неверный статус персонажа")
    updated = await pool.fetchrow(
        """
        UPDATE paris_applications
        SET character_status = $2, presence_updated_at = NOW(), updated_at = NOW()
        WHERE telegram_id = $1
        RETURNING *;
        """,
        telegram_id,
        status,
    )
    return {"ok": True, **_presence_payload(updated)}


@router.post("/api/game/presence/location")
async def game_presence_location(request: Request):
    pool, body, telegram_id, row = await _web_auth(request)
    try:
        location = _normalize_location(body.get("location"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not location:
        raise HTTPException(status_code=400, detail="укажи локацию")
    updated = await pool.fetchrow(
        """
        UPDATE paris_applications
        SET current_location = $2, presence_updated_at = NOW(), updated_at = NOW()
        WHERE telegram_id = $1
        RETURNING *;
        """,
        telegram_id,
        location,
    )
    return {"ok": True, **_presence_payload(updated)}


@router.post("/api/game/presence/location/clear")
async def game_presence_location_clear(request: Request):
    pool, body, telegram_id, row = await _web_auth(request)
    updated = await pool.fetchrow(
        """
        UPDATE paris_applications
        SET current_location = '', presence_updated_at = NOW(), updated_at = NOW()
        WHERE telegram_id = $1
        RETURNING *;
        """,
        telegram_id,
    )
    return {"ok": True, **_presence_payload(updated)}


def _status_help() -> str:
    return (
        "доступные статусы:\n"
        "• свободен\n"
        "• ищу игру\n"
        "• в сцене\n"
        "• афк\n"
        "• не беспокоить\n\n"
        "пример: <code>Статус ищу игру</code>"
    )


def _location_help() -> str:
    return (
        "чтобы изменить локацию, напиши, например:\n"
        "<code>Локация дворец</code>\n"
        "<code>Локация больница</code>\n"
        "<code>Локация улицы Парижа</code>\n\n"
        "посмотреть текущую: <code>Моя локация</code>\n"
        "очистить: <code>Покинуть локацию</code>"
    )


async def russian_presence_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = message.from_user if message is not None else None
    if message is None or user is None or not message.text:
        return

    text = " ".join(message.text.strip().split())
    lowered = text.casefold()

    recognized = (
        lowered in {"статус", "мой статус", "моя локация", "локация", "покинуть локацию", "команды"}
        or lowered.startswith("статус ")
        or lowered.startswith("локация ")
    )
    if not recognized:
        return

    # Если админ сейчас отвечает на служебный запрос принятия/отказа,
    # не перехватываем его текстовые ответы.
    try:
        from .bot import waiting_admin_actions
        if int(user.id) in waiting_admin_actions:
            return
    except Exception:
        pass

    if lowered == "команды":
        await message.reply_text(
            "📚 <b>команды участников</b>\n\n"
            "<code>Профиль</code> — мой профиль; ответом на сообщение — профиль этого игрока\n"
            "<code>Мой профиль</code> — всегда мой профиль\n"
            "<code>Статус</code> — посмотреть игровой статус\n"
            "<code>Статус свободен</code> / <code>Статус ищу игру</code> / <code>Статус в сцене</code> / <code>Статус афк</code> / <code>Статус не беспокоить</code>\n"
            "<code>Моя локация</code> — посмотреть локацию\n"
            "<code>Локация дворец</code> — изменить локацию\n"
            "<code>Покинуть локацию</code> — очистить локацию\n\n"
            "<b>сцены:</b>\n"
            "<code>Сцена</code> — меню сцен\n"
            "<code>Создать сцену</code> — создать новую сцену\n"
            "<code>Моя сцена</code> — текущая сцена\n"
            "<code>Открытые сцены</code> — найти сцену для вступления\n"
            "<code>Покинуть сцену</code> — выйти из сцены\n"
            "<code>Закрыть сцену</code> — завершить свою сцену\n"
            "<code>Пригласить в сцену</code> — ответом на сообщение игрока для закрытой сцены",
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    pool: asyncpg.Pool = context.application.bot_data["pool"]
    await upsert_user(pool, user.to_dict())

    try:
        row = await _accepted_application(pool, int(user.id))
    except HTTPException as exc:
        await message.reply_text(str(exc.detail))
        raise ApplicationHandlerStop

    if lowered in {"статус", "мой статус"}:
        presence = _presence_payload(row)
        await message.reply_text(
            "🎭 <b>статус персонажа</b>\n\n"
            f"<b>статус:</b> {escape(presence['character_status_label'])}\n"
            f"<b>локация:</b> {escape(presence['current_location'] or 'не указана')}\n\n"
            + _status_help(),
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    if lowered.startswith("статус "):
        raw = text[len("статус "):].strip()
        status = _normalize_status(raw)
        if status is None:
            await message.reply_text(_status_help(), parse_mode=ParseMode.HTML)
            raise ApplicationHandlerStop
        await pool.execute(
            """
            UPDATE paris_applications
            SET character_status = $2, presence_updated_at = NOW(), updated_at = NOW()
            WHERE telegram_id = $1;
            """,
            int(user.id),
            status,
        )
        await message.reply_text(
            f"🎭 статус персонажа: <b>{escape(CHARACTER_STATUSES[status])}</b>",
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    if lowered in {"моя локация", "локация"}:
        location = str(_row_value(row, "current_location", "") or "")
        await message.reply_text(
            "📍 <b>текущая локация</b>\n\n"
            f"{escape(location or 'не указана')}\n\n"
            + _location_help(),
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    if lowered == "покинуть локацию":
        await pool.execute(
            """
            UPDATE paris_applications
            SET current_location = '', presence_updated_at = NOW(), updated_at = NOW()
            WHERE telegram_id = $1;
            """,
            int(user.id),
        )
        await message.reply_text("📍 текущая локация очищена.")
        raise ApplicationHandlerStop

    if lowered.startswith("локация "):
        raw = text[len("локация "):].strip()
        try:
            location = _normalize_location(raw)
        except ValueError as exc:
            await message.reply_text(str(exc))
            raise ApplicationHandlerStop
        if not location:
            await message.reply_text(_location_help(), parse_mode=ParseMode.HTML)
            raise ApplicationHandlerStop
        await pool.execute(
            """
            UPDATE paris_applications
            SET current_location = $2, presence_updated_at = NOW(), updated_at = NOW()
            WHERE telegram_id = $1;
            """,
            int(user.id),
            location,
        )
        await message.reply_text(
            f"📍 текущая локация: <b>{escape(location)}</b>",
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop


def patch_presence_features(main_base_module: Any) -> None:
    if getattr(main_base_module.app.state, "presence_patch_installed", False):
        return

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, russian_presence_handler),
            group=-97,
        )
        return app

    main_base_module.build_application = wrapped_build_application
    main_base_module.app.state.presence_patch_installed = True
