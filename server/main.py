from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from telegram import MenuButtonWebApp, Update, WebAppInfo

from .bot import CITY_SHEET_URL, EVENT_CHAT_URL, build_application, notify_admins_about_application, set_bot_commands
from .config import Config, get_config
from .db import (
    create_pool,
    get_application,
    get_user_application,
    init_db,
    list_accepted_applications,
    list_pending_applications,
    submit_application,
    upsert_user,
)
from .security import validate_webapp_init_data

ROOT_DIR = Path(__file__).resolve().parents[1]
MINIAPP_DIR = ROOT_DIR / "miniapp"


def _is_admin(config: Config, telegram_id: int) -> bool:
    return telegram_id in config.admin_ids


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _compact_application(row: Any) -> dict[str, Any]:
    first_name = str(row["character_first_name"] or "")
    last_name = str(row["character_last_name"] or "")
    character_name = f"{first_name} {last_name}".strip()
    username = row.get("username") if hasattr(row, "get") else row["username"] if "username" in row else None
    return {
        "id": row["id"],
        "status": row["status"],
        "telegram_id": row["telegram_id"],
        "username": f"@{username}" if username and not str(username).startswith("@") else username,
        "character_name": character_name,
        "character_age": row["character_age"],
        "affiliation": row["affiliation"],
        "role_preference": row["role_preference"],
        "assigned_role": row["assigned_role"] or row["owner_comment"] or "",
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


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
    return {
        "ok": True,
        "is_admin": _is_admin(config, telegram_id),
        "links": {
            "chat": EVENT_CHAT_URL,
            "citySheet": CITY_SHEET_URL,
        },
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
        },
        "application": dict(application) if application else None,
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
    return {
        "ok": True,
        "pending_count": len(pending),
        "accepted_count": len(accepted),
        "pending": [_compact_application(row) for row in pending],
        "accepted": [_compact_application(row) for row in accepted],
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
