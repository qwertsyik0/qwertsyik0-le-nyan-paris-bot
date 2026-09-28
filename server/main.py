from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from telegram import MenuButtonWebApp, Update, WebAppInfo

from .bot import CITY_SHEET_URL, EVENT_CHAT_URL, build_application, notify_admins_about_application, set_bot_commands
from .config import Config, get_config
from .db import (
    count_unread_letters,
    create_pool,
    get_application,
    get_user_application,
    init_db,
    list_accepted_applications,
    list_admin_letters,
    list_pending_applications,
    list_user_letters,
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


def _compact_letter(row: Any, *, include_player: bool = False) -> dict[str, Any]:
    data = {
        "id": _row_value(row, "id"),
        "type": _row_value(row, "letter_type"),
        "title": _row_value(row, "title"),
        "body": _row_value(row, "body"),
        "status": _row_value(row, "status", "new"),
        "status_label": LETTER_STATUS_LABELS.get(str(_row_value(row, "status", "new")), str(_row_value(row, "status", "new"))),
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


async def configure_menu_button(telegram_app, config: Config) -> None:
    if not config.mini_app_url:
        return
    try:
        await telegram_app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Кабинет",
                web_app=WebAppInfo(url=config.mini_app_url),
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


app = FastAPI(title="Le Nyan Paris Bot", lifespan=lifespan)
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
    if status not in {"all", *LETTER_STATUS_LABELS.keys()}:
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
    return {"ok": True, "letter": _compact_letter(row)}


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
