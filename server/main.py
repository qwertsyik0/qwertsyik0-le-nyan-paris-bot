from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from telegram import Update

from .bot import build_application, notify_admins_about_application
from .config import Config, get_config
from .db import create_pool, get_user_application, init_db, submit_application, upsert_user
from .security import validate_webapp_init_data

ROOT_DIR = Path(__file__).resolve().parents[1]
MINIAPP_DIR = ROOT_DIR / "miniapp"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    pool = await create_pool(config.database_url)
    await init_db(pool)

    telegram_app = build_application(config, pool)
    await telegram_app.initialize()
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
    await upsert_user(pool, user)
    application = await get_user_application(pool, int(user["id"]))
    return {
        "ok": True,
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
        },
        "application": dict(application) if application else None,
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

    telegram_app = request.app.state.telegram_app
    await notify_admins_about_application(telegram_app.bot, config, row)
    return {"ok": True, "application": dict(row)}
