from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from telegram import MenuButtonWebApp, Update, WebAppInfo
from telegram.constants import ParseMode

from .bot import CITY_SHEET_URL, EVENT_CHAT_URL, build_application, mini_app_url, notify_admins_about_application, set_bot_commands
from .config import Config, get_config
from .db import (
    count_unread_letters,
    create_letter,
    create_pool,
    find_user_by_identifier,
    get_admin_letter,
    get_application,
    get_user_application,
    init_db,
    list_accepted_applications,
    list_admin_letters,
    list_admin_user_letters,
    list_pending_applications,
    list_user_letters,
    mark_user_letters_read,
    submit_application,
    update_letter_status,
    upsert_user,
)
from .security import validate_webapp_init_data

ROOT_DIR = Path(__file__).resolve().parents[1]
MINIAPP_DIR = ROOT_DIR / "miniapp"

LETTER_STATUS_LABELS = {
    "new": "новое",
    "read": "прочитано",
    "in_work": "в работе",
    "closed": "закрыто",
    "hidden": "скрыто",
}

LETTER_TYPE_LABELS = {
    "letter": "письмо",
    "summons": "повестка",
    "task": "задание",
    "rumor": "слух",
    "warning": "предупреждение",
}

LETTER_TYPE_ICONS = {
    "letter": "📜",
    "summons": "⚖️",
    "task": "🕯",
    "rumor": "📰",
    "warning": "⚠️",
}


def _is_admin(config: Config, telegram_id: int) -> bool:
    return telegram_id in config.admin_ids


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _username(row: Any) -> str | None:
    username = _row_value(row, "username")
    if not username:
        return None
    username_text = str(username)
    return f"@{username_text}" if not username_text.startswith("@") else username_text


def _character_name(row: Any) -> str:
    first_name = str(_row_value(row, "character_first_name", "") or "")
    last_name = str(_row_value(row, "character_last_name", "") or "")
    return f"{first_name} {last_name}".strip()


def _compact_application(row: Any) -> dict[str, Any]:
    return {
        "id": _row_value(row, "id"),
        "status": _row_value(row, "status"),
        "telegram_id": _row_value(row, "telegram_id"),
        "username": _username(row),
        "character_name": _character_name(row),
        "character_age": _row_value(row, "character_age"),
        "affiliation": _row_value(row, "affiliation"),
        "role_preference": _row_value(row, "role_preference"),
        "assigned_role": _row_value(row, "assigned_role") or _row_value(row, "owner_comment") or "",
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
    }


def _application_details(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": _row_value(row, "id"),
        "status": _row_value(row, "status"),
        "character_first_name": _row_value(row, "character_first_name"),
        "character_last_name": _row_value(row, "character_last_name"),
        "character_name": _character_name(row),
        "character_age": _row_value(row, "character_age"),
        "character_gender": _row_value(row, "character_gender"),
        "character_orientation": _row_value(row, "character_orientation"),
        "role_preference": _row_value(row, "role_preference"),
        "affiliation": _row_value(row, "affiliation"),
        "character_description": _row_value(row, "character_description"),
        "character_personality": _row_value(row, "character_personality"),
        "roleplay_experience": _row_value(row, "roleplay_experience"),
        "applicant_comment": _row_value(row, "applicant_comment"),
        "owner_comment": _row_value(row, "owner_comment"),
        "assigned_role": _row_value(row, "assigned_role") or _row_value(row, "owner_comment") or "",
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
        "reviewed_at": _iso(_row_value(row, "reviewed_at")),
    }


def _compact_letter(row: Any, *, include_player: bool = False) -> dict[str, Any]:
    letter_type = str(_row_value(row, "letter_type", "letter") or "letter")
    status = str(_row_value(row, "status", "new") or "new")
    data = {
        "id": _row_value(row, "id"),
        "type": letter_type,
        "type_label": LETTER_TYPE_LABELS.get(letter_type, letter_type),
        "type_icon": LETTER_TYPE_ICONS.get(letter_type, "📜"),
        "title": _row_value(row, "title"),
        "body": _row_value(row, "body"),
        "status": status,
        "status_label": LETTER_STATUS_LABELS.get(status, status),
        "is_read": bool(_row_value(row, "is_read", False)),
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
        "status_changed_at": _iso(_row_value(row, "status_changed_at")),
    }
    if include_player:
        data.update(
            {
                "telegram_id": _row_value(row, "telegram_id"),
                "username": _username(row),
                "character_name": _character_name(row),
                "assigned_role": _row_value(row, "assigned_role") or _row_value(row, "owner_comment") or "",
            }
        )
    return data


def _letter_notification_text(title: str, letter_type: str, body: str) -> str:
    clean_type = letter_type if letter_type in LETTER_TYPE_LABELS else "letter"
    icon = LETTER_TYPE_ICONS.get(clean_type, "📜")
    type_label = LETTER_TYPE_LABELS.get(clean_type, "письмо")
    clean_title = title.strip() or type_label
    return (
        f"{icon} <b>{escape(clean_title)}</b>\n\n"
        f"<i>тип: {escape(type_label)} • от императорской канцелярии L’Empire des Ombres</i>\n\n"
        f"<blockquote>{escape(body).strip()}</blockquote>\n\n"
        "<b>сообщение сохранено в разделе «письма» вашего кабинета.</b>"
    )


async def configure_menu_button(telegram_app, config: Config) -> None:
    if not config.mini_app_url:
        return
    try:
        await telegram_app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Кабинет",
                web_app=WebAppInfo(url=mini_app_url(config)),
            )
        )
    except Exception as exc:
        # Telegram can reject the menu button if the Mini App domain is not
        # configured in BotFather yet. The bot must still start normally.
        print(f"failed to set Telegram menu button: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    pool = await create_pool(config.database_url)
    await init_db(pool)

    telegram_app = build_application(config, pool)
    await telegram_app.initialize()
    try:
        bot_me = await telegram_app.bot.get_me()
        print(
            "telegram bot diagnostics: "
            f"username=@{bot_me.username or ''} "
            f"can_read_all_group_messages={getattr(bot_me, 'can_read_all_group_messages', None)}"
        )
    except Exception as exc:
        print(f"telegram bot diagnostics failed: {exc}")
    await set_bot_commands(telegram_app)
    await configure_menu_button(telegram_app, config)
    await telegram_app.start()

    if config.public_base_url:
        webhook_url = f"{config.public_base_url}/telegram/webhook"
        await telegram_app.bot.set_webhook(
            webhook_url,
            secret_token=config.webhook_secret,
            allowed_updates=Update.ALL_TYPES,
        )

    app.state.config = config
    app.state.pool = pool
    app.state.telegram_app = telegram_app

    try:
        yield
    finally:
        # Do not delete webhook on shutdown. Render can stop/restart services,
        # and Telegram must keep the webhook URL so new updates can wake the service.
        await telegram_app.stop()
        await telegram_app.shutdown()
        await pool.close()


app = FastAPI(title="L’Empire des Ombres Bot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://qwertsyik0.github.io"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/miniapp", StaticFiles(directory=MINIAPP_DIR, html=True), name="miniapp")


@app.get("/")
async def root():
    return RedirectResponse(url="/miniapp/")


@app.get("/health")
async def health():
    return {"ok": True, "service": "le-nyan-paris-bot"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    config: Config = request.app.state.config
    if config.webhook_secret:
        header = request.headers.get("x-telegram-bot-api-secret-token")
        if header != config.webhook_secret:
            raise HTTPException(status_code=403, detail="invalid webhook secret")

    payload = await request.json()
    telegram_app = request.app.state.telegram_app
    update = Update.de_json(payload, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.post("/api/me")
async def api_me(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    await upsert_user(pool, user)
    application = await get_user_application(pool, telegram_id)
    unread_letters = await count_unread_letters(pool, telegram_id)
    return {
        "ok": True,
        "is_admin": _is_admin(config, telegram_id),
        "unread_letters": unread_letters,
        "links": {
            "citySheet": CITY_SHEET_URL,
        },
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
        },
        "application": dict(application) if application else None,
    }


@app.post("/api/letters")
async def api_letters(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    await upsert_user(pool, user)
    await mark_user_letters_read(pool, telegram_id)
    rows = await list_user_letters(pool, telegram_id, limit=50)
    unread_letters = await count_unread_letters(pool, telegram_id)
    return {
        "ok": True,
        "unread_letters": unread_letters,
        "letters": [_compact_letter(row) for row in rows],
    }


@app.post("/api/admin/overview")
async def api_admin_overview(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    if not _is_admin(config, telegram_id):
        raise HTTPException(status_code=403, detail="нет доступа")

    await upsert_user(pool, user)
    pending = await list_pending_applications(pool, limit=20)
    accepted = await list_accepted_applications(pool, limit=20)
    letters = await list_admin_letters(pool, status="all", limit=20)
    return {
        "ok": True,
        "pending_count": len(pending),
        "accepted_count": len(accepted),
        "letters_count": len(letters),
        "pending": [_compact_application(row) for row in pending],
        "accepted": [_compact_application(row) for row in accepted],
        "letters": [_compact_letter(row, include_player=True) for row in letters],
    }


@app.post("/api/admin/letters")
async def api_admin_letters(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    if not _is_admin(config, telegram_id):
        raise HTTPException(status_code=403, detail="нет доступа")

    await upsert_user(pool, user)
    status = str(body.get("status") or "all").strip()
    if status not in (set(LETTER_STATUS_LABELS) | {"all"}):
        raise HTTPException(status_code=400, detail="invalid letter status")
    rows = await list_admin_letters(pool, status=status, limit=80)
    return {
        "ok": True,
        "status": status,
        "letters": [_compact_letter(row, include_player=True) for row in rows],
    }


@app.post("/api/admin/letters/status")
async def api_admin_letter_status(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    if not _is_admin(config, telegram_id):
        raise HTTPException(status_code=403, detail="нет доступа")

    await upsert_user(pool, user)
    try:
        letter_id = int(body.get("letter_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid letter id") from exc
    status = str(body.get("status") or "").strip()
    if status not in LETTER_STATUS_LABELS:
        raise HTTPException(status_code=400, detail="invalid letter status")
    row = await update_letter_status(pool, letter_id, status)
    if row is None:
        raise HTTPException(status_code=404, detail="letter not found")
    full_row = await get_admin_letter(pool, letter_id)
    return {"ok": True, "letter": _compact_letter(full_row or row, include_player=True)}


@app.post("/api/admin/letters/send")
async def api_admin_letter_send(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    admin_id = int(user["id"])
    if not _is_admin(config, admin_id):
        raise HTTPException(status_code=403, detail="нет доступа")

    await upsert_user(pool, user)
    target_raw = str(body.get("target") or "").strip()
    if not target_raw:
        raise HTTPException(status_code=400, detail="recipient is required")
    target = await find_user_by_identifier(pool, target_raw)
    if target is None:
        raise HTTPException(status_code=404, detail="получатель не найден")

    letter_type = str(body.get("letter_type") or body.get("type") or "letter").strip().lower()
    if letter_type not in LETTER_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="invalid letter type")
    title = str(body.get("title") or "").strip() or LETTER_TYPE_LABELS[letter_type]
    body_text = str(body.get("body") or "").strip()
    if not body_text:
        raise HTTPException(status_code=400, detail="letter body is required")
    if len(body_text) > 3500:
        raise HTTPException(status_code=400, detail="letter body is too long")

    try:
        letter = await create_letter(
            pool,
            telegram_id=int(target["telegram_id"]),
            sender_admin_id=admin_id,
            body=body_text,
            title=title,
            letter_type=letter_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    notify_ok = True
    try:
        telegram_app = request.app.state.telegram_app
        await telegram_app.bot.send_message(
            chat_id=int(target["telegram_id"]),
            text=_letter_notification_text(title, letter_type, body_text),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        notify_ok = False
        print(f"failed to notify letter recipient {target['telegram_id']}: {exc}")

    full_row = await get_admin_letter(pool, int(letter["id"]))
    return {
        "ok": True,
        "notify_ok": notify_ok,
        "letter": _compact_letter(full_row or letter, include_player=True),
    }


@app.post("/api/admin/player")
async def api_admin_player(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    if not _is_admin(config, telegram_id):
        raise HTTPException(status_code=403, detail="нет доступа")

    await upsert_user(pool, user)
    identifier = str(body.get("identifier") or body.get("target") or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="player identifier is required")
    target = await find_user_by_identifier(pool, identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")

    target_id = int(target["telegram_id"])
    application = await get_user_application(pool, target_id)
    letters = await list_admin_user_letters(pool, target_id, limit=80)
    return {
        "ok": True,
        "player": {
            "telegram_id": target_id,
            "username": _username(target),
            "first_name": _row_value(target, "first_name"),
            "last_name": _row_value(target, "last_name"),
            "language_code": _row_value(target, "language_code"),
            "created_at": _iso(_row_value(target, "created_at")),
            "updated_at": _iso(_row_value(target, "updated_at")),
        },
        "application": _application_details(application),
        "letters": [_compact_letter(row, include_player=True) for row in letters],
    }


@app.post("/api/applications")
async def api_submit_application(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    await upsert_user(pool, user)

    payload = body.get("application")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="application payload is required")

    try:
        row = await submit_application(pool, int(user["id"]), payload)
    except ValueError as exc:
        detail = str(exc)
        if detail == "already_accepted":
            raise HTTPException(status_code=409, detail="application already accepted") from exc
        raise HTTPException(status_code=400, detail=detail) from exc

    full_row = await get_application(pool, int(row["id"]))
    telegram_app = request.app.state.telegram_app
    await notify_admins_about_application(telegram_app.bot, config, full_row or row)
    return {"ok": True, "application": dict(row)}
