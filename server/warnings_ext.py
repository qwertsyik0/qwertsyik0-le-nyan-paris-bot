from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .bot import admin_denied_text, is_admin
from .config import Config
from .db import find_user_by_identifier, upsert_user
from .main_base import _is_admin, _iso, _row_value, _username
from .security import validate_webapp_init_data

router = APIRouter()

WARNING_TYPE_LABELS = {
    "oral": "устное замечание",
    "remark": "замечание",
    "warning": "предупреждение",
    "reprimand": "выговор",
}

WARNING_ALIASES = {
    "устное": "oral",
    "устное_замечание": "oral",
    "устное-замечание": "oral",
    "oral": "oral",
    "замечание": "remark",
    "remark": "remark",
    "note": "remark",
    "предупреждение": "warning",
    "warning": "warning",
    "warn": "warning",
    "выговор": "reprimand",
    "reprimand": "reprimand",
}


def normalize_warning_type(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    warning_type = WARNING_ALIASES.get(raw)
    if warning_type not in WARNING_TYPE_LABELS:
        raise ValueError("invalid_warning_type")
    return warning_type


def clean_text(value: Any, *, limit: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail="пустое поле")
    if len(text) > limit:
        raise HTTPException(status_code=400, detail="поле слишком длинное")
    return text


async def ensure_warning_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_warnings (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                admin_id BIGINT NOT NULL,
                warning_type TEXT NOT NULL CHECK (warning_type IN ('oral', 'remark', 'warning', 'reprimand')),
                reason TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                revoked_at TIMESTAMPTZ
            );
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS paris_warnings_user_created_idx ON paris_warnings (telegram_id, created_at DESC);")
        await conn.execute("CREATE INDEX IF NOT EXISTS paris_warnings_active_idx ON paris_warnings (telegram_id) WHERE revoked_at IS NULL;")


def warning_payload(row: Any) -> dict[str, Any]:
    warning_type = str(_row_value(row, "warning_type", "warning") or "warning")
    admin_username = _row_value(row, "admin_username")
    return {
        "id": _row_value(row, "id"),
        "telegram_id": _row_value(row, "telegram_id"),
        "admin_id": _row_value(row, "admin_id"),
        "admin": f"@{admin_username}" if admin_username else f"ID {_row_value(row, 'admin_id')}",
        "type": warning_type,
        "type_label": WARNING_TYPE_LABELS.get(warning_type, warning_type),
        "reason": _row_value(row, "reason", ""),
        "created_at": _iso(_row_value(row, "created_at")),
        "revoked_at": _iso(_row_value(row, "revoked_at")),
    }


async def create_warning(pool: asyncpg.Pool, *, telegram_id: int, admin_id: int, warning_type: str, reason: str) -> asyncpg.Record:
    await ensure_warning_schema(pool)
    return await pool.fetchrow(
        """
        INSERT INTO paris_warnings (telegram_id, admin_id, warning_type, reason)
        VALUES ($1, $2, $3, $4)
        RETURNING *;
        """,
        telegram_id,
        admin_id,
        warning_type,
        reason,
    )


async def list_user_warnings(pool: asyncpg.Pool, telegram_id: int, *, include_revoked: bool = False) -> list[asyncpg.Record]:
    await ensure_warning_schema(pool)
    return list(
        await pool.fetch(
            """
            SELECT w.*, u.username AS admin_username
            FROM paris_warnings w
            LEFT JOIN paris_users u ON u.telegram_id = w.admin_id
            WHERE w.telegram_id = $1
              AND ($2::BOOLEAN OR w.revoked_at IS NULL)
            ORDER BY w.created_at DESC
            LIMIT 100;
            """,
            telegram_id,
            include_revoked,
        )
    )


def warning_notification_text(warning_type: str, reason: str) -> str:
    label = WARNING_TYPE_LABELS.get(warning_type, warning_type)
    return (
        "⚠️ <b>предупреждение Имперской канцелярии</b>\n"
        "<i>L’Empire des Ombres</i>\n\n"
        f"<b>тип:</b> {escape(label)}\n"
        f"<b>причина:</b>\n{escape(reason)}\n\n"
        "<i>запись внесена в личное дело и доступна в вашем кабинете.</i>"
    )


async def admin_request(request: Request) -> tuple[Config, asyncpg.Pool, dict[str, Any], int]:
    config: Config = request.app.state.config
    pool: asyncpg.Pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    admin_id = int(user["id"])
    if not _is_admin(config, admin_id):
        raise HTTPException(status_code=403, detail="нет доступа")
    await upsert_user(pool, user)
    await ensure_warning_schema(pool)
    return config, pool, body, admin_id


@router.post("/api/warnings")
async def api_warnings(request: Request):
    config: Config = request.app.state.config
    pool: asyncpg.Pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    await upsert_user(pool, user)
    rows = await list_user_warnings(pool, telegram_id)
    return {
        "ok": True,
        "count": len(rows),
        "warnings": [warning_payload(row) for row in rows],
    }


@router.post("/api/admin/warnings/create")
async def api_admin_warning_create(request: Request):
    config, pool, body, admin_id = await admin_request(request)
    target_raw = clean_text(body.get("target") or body.get("identifier"), limit=128, required=True)
    reason = clean_text(body.get("reason"), limit=1200, required=True)
    try:
        warning_type = normalize_warning_type(body.get("warning_type") or body.get("type"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="неверный тип предупреждения") from exc

    target = await find_user_by_identifier(pool, target_raw)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")
    target_id = int(target["telegram_id"])
    row = await create_warning(pool, telegram_id=target_id, admin_id=admin_id, warning_type=warning_type, reason=reason)

    notify_ok = True
    try:
        await request.app.state.telegram_app.bot.send_message(
            chat_id=target_id,
            text=warning_notification_text(warning_type, reason),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        notify_ok = False
        print(f"failed to notify warning recipient {target_id}: {exc}")

    full_rows = await list_user_warnings(pool, target_id)
    warning = next((warning_payload(item) for item in full_rows if int(item["id"]) == int(row["id"])), warning_payload(row))
    return {"ok": True, "notify_ok": notify_ok, "warning": warning, "target": _username(target) or str(target_id)}


@router.post("/api/admin/warnings/list")
async def api_admin_warnings_list(request: Request):
    config, pool, body, admin_id = await admin_request(request)
    target_raw = clean_text(body.get("target") or body.get("identifier"), limit=128, required=True)
    target = await find_user_by_identifier(pool, target_raw)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")
    rows = await list_user_warnings(pool, int(target["telegram_id"]), include_revoked=True)
    return {"ok": True, "target": _username(target) or str(target["telegram_id"]), "warnings": [warning_payload(row) for row in rows]}


async def warn_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return
    user_id = user.id if user else None
    if not is_admin(config, user_id):
        await message.reply_text(admin_denied_text(user_id), parse_mode=ParseMode.HTML)
        return
    if user is not None:
        await upsert_user(pool, user.to_dict())
    if len(context.args) < 3:
        await message.reply_text(
            "формат:\n"
            "<code>/warn @username тип причина</code>\n\n"
            "типы: <code>устное</code>, <code>замечание</code>, <code>предупреждение</code>, <code>выговор</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target_raw = context.args[0]
    try:
        warning_type = normalize_warning_type(context.args[1])
    except ValueError:
        await message.reply_text("неверный тип. доступно: устное / замечание / предупреждение / выговор")
        return
    reason = " ".join(context.args[2:]).strip()
    if not reason:
        await message.reply_text("укажите причину")
        return
    if len(reason) > 1200:
        await message.reply_text("причина слишком длинная. максимум 1200 символов")
        return

    target = await find_user_by_identifier(pool, target_raw)
    if target is None:
        await message.reply_text("игрок не найден. он должен хотя бы раз открыть бота или Mini App.")
        return
    target_id = int(target["telegram_id"])
    row = await create_warning(pool, telegram_id=target_id, admin_id=int(user_id), warning_type=warning_type, reason=reason)

    notify_ok = True
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=warning_notification_text(warning_type, reason),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        notify_ok = False
        print(f"failed to notify warning recipient {target_id}: {exc}")

    suffix = "" if notify_ok else "\n\n⚠️ запись создана, но уведомление в Telegram не отправилось."
    await message.reply_text(
        f"предупреждение #{row['id']} выдано для {escape(str(_username(target) or target_id))}.\n"
        f"тип: <b>{escape(WARNING_TYPE_LABELS[warning_type])}</b>{suffix}",
        parse_mode=ParseMode.HTML,
    )


def patch_warning_features(main_base_module: Any) -> None:
    original_build_application = main_base_module.build_application
    original_set_bot_commands = main_base_module.set_bot_commands

    def build_application_with_warnings(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        app.add_handler(CommandHandler("warn", warn_command), group=-2)
        return app

    async def set_bot_commands_with_warnings(app: Application) -> None:
        try:
            await original_set_bot_commands(app)
        except TypeError:
            pass
        await app.bot.set_my_commands([
            BotCommand("start", "открыть канцелярию"),
            BotCommand("profile", "мой профиль"),
            BotCommand("myapp", "моя анкета"),
            BotCommand("invite", "ссылка на чат для принятых"),
            BotCommand("id", "показать Telegram ID"),
            BotCommand("admin", "админ-панель"),
            BotCommand("pending", "новые анкеты"),
            BotCommand("accepted", "принятые участники"),
            BotCommand("app", "открыть анкету по ID"),
            BotCommand("letter", "отправить письмо игроку"),
            BotCommand("lettergroup", "массовое письмо по разделу"),
            BotCommand("warn", "выдать предупреждение игроку"),
            BotCommand("letters", "список писем"),
            BotCommand("letterstatus", "изменить статус письма"),
            BotCommand("lenyan", "объявление с отметками"),
        ])

    main_base_module.build_application = build_application_with_warnings
    main_base_module.set_bot_commands = set_bot_commands_with_warnings
