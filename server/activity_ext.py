from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

import asyncpg
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .db import find_user_by_identifier

_activity_tables_ready = False


BASE_COMMANDS = [
    BotCommand("start", "открыть канцелярию"),
    BotCommand("id", "показать Telegram ID"),
    BotCommand("admin", "админ-панель"),
    BotCommand("pending", "новые анкеты"),
    BotCommand("accepted", "принятые участники"),
    BotCommand("app", "открыть анкету по ID"),
    BotCommand("letter", "отправить письмо игроку"),
    BotCommand("letters", "список писем"),
    BotCommand("letterstatus", "изменить статус письма"),
    BotCommand("activity", "активность игроков"),
    BotCommand("active", "активность игроков"),
]


def _is_admin(config: Config, user_id: int | None) -> bool:
    return bool(user_id and user_id in config.admin_ids)


def _is_group_chat(chat_type: str | None) -> bool:
    return chat_type in {"group", "supergroup"}


def _format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return str(value or "—")


def _display_user(row: Any) -> str:
    username = str(row.get("username") or "").strip() if isinstance(row, dict) else str(row["username"] or "").strip()
    first_name = str(row.get("first_name") or "").strip() if isinstance(row, dict) else str(row["first_name"] or "").strip()
    telegram_id = row.get("telegram_id") if isinstance(row, dict) else row["telegram_id"]
    if username:
        return f"@{username.lstrip('@')}"
    if first_name:
        return first_name
    return f"id {telegram_id}"


def _safe_display_user(row: Any) -> str:
    return escape(_display_user(row))


async def ensure_activity_tables(pool: asyncpg.Pool) -> None:
    global _activity_tables_ready
    if _activity_tables_ready:
        return
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS paris_group_activity (
            chat_id BIGINT NOT NULL,
            chat_title TEXT NOT NULL DEFAULT '',
            telegram_id BIGINT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            message_count BIGINT NOT NULL DEFAULT 0,
            first_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_id, telegram_id)
        );
        """
    )
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS paris_group_activity_daily (
            chat_id BIGINT NOT NULL,
            telegram_id BIGINT NOT NULL,
            activity_date DATE NOT NULL DEFAULT CURRENT_DATE,
            message_count BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, telegram_id, activity_date)
        );
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS paris_group_activity_count_idx
        ON paris_group_activity (chat_id, message_count DESC, last_message_at DESC);
        """
    )
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS paris_group_activity_daily_idx
        ON paris_group_activity_daily (chat_id, activity_date DESC, message_count DESC);
        """
    )
    _activity_tables_ready = True


async def track_activity_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    if user.is_bot or not _is_group_chat(chat.type):
        return

    pool: asyncpg.Pool = context.application.bot_data["pool"]
    await ensure_activity_tables(pool)

    username = str(user.username or "").strip()
    first_name = str(user.first_name or "").strip()
    last_name = str(user.last_name or "").strip()
    language_code = str(user.language_code or "").strip() or None
    chat_title = str(chat.title or "").strip()

    await pool.execute(
        """
        INSERT INTO paris_users (telegram_id, username, first_name, last_name, language_code)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (telegram_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            language_code = EXCLUDED.language_code,
            updated_at = NOW();
        """,
        int(user.id),
        username or None,
        first_name or None,
        last_name or None,
        language_code,
    )

    await pool.execute(
        """
        INSERT INTO paris_group_activity (
            chat_id,
            chat_title,
            telegram_id,
            username,
            first_name,
            last_name,
            message_count,
            first_message_at,
            last_message_at
        ) VALUES ($1, $2, $3, $4, $5, $6, 1, NOW(), NOW())
        ON CONFLICT (chat_id, telegram_id) DO UPDATE SET
            chat_title = EXCLUDED.chat_title,
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            message_count = paris_group_activity.message_count + 1,
            last_message_at = NOW();
        """,
        int(chat.id),
        chat_title,
        int(user.id),
        username,
        first_name,
        last_name,
    )

    await pool.execute(
        """
        INSERT INTO paris_group_activity_daily (chat_id, telegram_id, activity_date, message_count)
        VALUES ($1, $2, CURRENT_DATE, 1)
        ON CONFLICT (chat_id, telegram_id, activity_date) DO UPDATE SET
            message_count = paris_group_activity_daily.message_count + 1;
        """,
        int(chat.id),
        int(user.id),
    )


def _parse_activity_args(args: list[str]) -> tuple[str | None, int | None]:
    target: str | None = None
    days: int | None = None
    for raw in args:
        value = raw.strip()
        if not value:
            continue
        if value.isdigit():
            days = max(1, min(int(value), 365))
        elif value.lower() in {"all", "все", "всё"}:
            target = None
        elif target is None:
            target = value
    return target, days


async def _resolve_activity_target(pool: asyncpg.Pool, target: str) -> int | None:
    clean = target.strip()
    if not clean:
        return None
    if clean.startswith("@"):
        clean_username = clean[1:].strip().lower()
    else:
        clean_username = clean.lower()

    row = await find_user_by_identifier(pool, clean)
    if row is not None:
        return int(row["telegram_id"])

    if clean.isdigit():
        return int(clean)

    row = await pool.fetchrow(
        """
        SELECT telegram_id
        FROM paris_group_activity
        WHERE lower(username) = lower($1)
        ORDER BY last_message_at DESC
        LIMIT 1;
        """,
        clean_username,
    )
    if row is not None:
        return int(row["telegram_id"])
    return None


async def _fetch_activity_top(
    pool: asyncpg.Pool,
    *,
    chat_id: int | None,
    days: int | None,
    limit: int = 20,
) -> list[asyncpg.Record]:
    if days is None:
        rows = await pool.fetch(
            """
            SELECT
                telegram_id,
                max(username) AS username,
                max(first_name) AS first_name,
                max(last_name) AS last_name,
                sum(message_count) AS total_messages,
                max(last_message_at) AS last_message_at
            FROM paris_group_activity
            WHERE ($1::BIGINT IS NULL OR chat_id = $1)
            GROUP BY telegram_id
            ORDER BY sum(message_count) DESC, max(last_message_at) DESC
            LIMIT $2;
            """,
            chat_id,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT
                d.telegram_id,
                max(a.username) AS username,
                max(a.first_name) AS first_name,
                max(a.last_name) AS last_name,
                sum(d.message_count) AS total_messages,
                max(a.last_message_at) AS last_message_at
            FROM paris_group_activity_daily d
            LEFT JOIN paris_group_activity a
                ON a.chat_id = d.chat_id AND a.telegram_id = d.telegram_id
            WHERE ($1::BIGINT IS NULL OR d.chat_id = $1)
              AND d.activity_date >= (CURRENT_DATE - (($2::INT - 1) * INTERVAL '1 day'))::DATE
            GROUP BY d.telegram_id
            ORDER BY sum(d.message_count) DESC, max(a.last_message_at) DESC NULLS LAST
            LIMIT $3;
            """,
            chat_id,
            days,
            limit,
        )
    return list(rows)


async def _fetch_user_activity(
    pool: asyncpg.Pool,
    *,
    chat_id: int | None,
    telegram_id: int,
    days: int | None,
) -> asyncpg.Record | None:
    if days is None:
        return await pool.fetchrow(
            """
            SELECT
                telegram_id,
                max(username) AS username,
                max(first_name) AS first_name,
                max(last_name) AS last_name,
                sum(message_count) AS total_messages,
                min(first_message_at) AS first_message_at,
                max(last_message_at) AS last_message_at
            FROM paris_group_activity
            WHERE ($1::BIGINT IS NULL OR chat_id = $1)
              AND telegram_id = $2
            GROUP BY telegram_id;
            """,
            chat_id,
            telegram_id,
        )
    return await pool.fetchrow(
        """
        SELECT
            d.telegram_id,
            max(a.username) AS username,
            max(a.first_name) AS first_name,
            max(a.last_name) AS last_name,
            sum(d.message_count) AS total_messages,
            min(d.activity_date) AS first_message_at,
            max(a.last_message_at) AS last_message_at
        FROM paris_group_activity_daily d
        LEFT JOIN paris_group_activity a
            ON a.chat_id = d.chat_id AND a.telegram_id = d.telegram_id
        WHERE ($1::BIGINT IS NULL OR d.chat_id = $1)
          AND d.telegram_id = $2
          AND d.activity_date >= (CURRENT_DATE - (($3::INT - 1) * INTERVAL '1 day'))::DATE
        GROUP BY d.telegram_id;
        """,
        chat_id,
        telegram_id,
        days,
    )


def _activity_scope_text(chat_title: str | None, chat_id: int | None) -> str:
    if chat_id is None:
        return "все чаты"
    return chat_title or "текущий чат"


def _period_text(days: int | None) -> str:
    return "все время" if days is None else f"последние {days} дн."


async def activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return

    if not _is_admin(config, user.id):
        if chat.type == "private":
            await message.reply_text("нет доступа")
        return

    await ensure_activity_tables(pool)

    target_raw, days = _parse_activity_args(context.args or [])
    current_chat_id = int(chat.id) if _is_group_chat(chat.type) else None
    chat_title = str(chat.title or "").strip() if current_chat_id is not None else None

    if target_raw:
        target_id = await _resolve_activity_target(pool, target_raw)
        if target_id is None:
            await message.reply_text("игрок не найден в базе активности")
            return
        row = await _fetch_user_activity(pool, chat_id=current_chat_id, telegram_id=target_id, days=days)
        if row is None or int(row["total_messages"] or 0) <= 0:
            await message.reply_text(
                "по этому игроку пока нет сообщений в выбранном периоде.\n\n"
                "данные считаются только с момента включения активности."
            )
            return
        text = (
            "📊 <b>активность игрока</b>\n\n"
            f"<b>игрок:</b> {_safe_display_user(row)}\n"
            f"<b>чат:</b> {escape(_activity_scope_text(chat_title, current_chat_id))}\n"
            f"<b>период:</b> {escape(_period_text(days))}\n"
            f"<b>сообщений:</b> <code>{int(row['total_messages'] or 0)}</code>\n"
            f"<b>первое сообщение:</b> {escape(_format_dt(row['first_message_at']))}\n"
            f"<b>последнее сообщение:</b> {escape(_format_dt(row['last_message_at']))}"
        )
        await message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    rows = await _fetch_activity_top(pool, chat_id=current_chat_id, days=days, limit=20)
    if not rows:
        await message.reply_text(
            "пока нет данных активности.\n\n"
            "важно: бот начнет считать обычные сообщения только если privacy mode выключен в BotFather."
        )
        return

    lines = [
        "📊 <b>активность игроков</b>",
        "",
        f"<b>чат:</b> {escape(_activity_scope_text(chat_title, current_chat_id))}",
        f"<b>период:</b> {escape(_period_text(days))}",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {_safe_display_user(row)} — <code>{int(row['total_messages'] or 0)}</code>")
    lines.append("")
    lines.append("данные считаются с момента включения функции.")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def patch_activity_features(main_base_module) -> None:
    if getattr(main_base_module.app.state, "activity_patch_installed", False):
        return

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config, pool):
        telegram_app = original_build_application(config, pool)
        telegram_app.add_handler(CommandHandler(["activity", "active"], activity_command), group=1)
        telegram_app.add_handler(
            MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, track_activity_message),
            group=50,
        )
        return telegram_app

    main_base_module.build_application = wrapped_build_application

    async def wrapped_set_bot_commands(app):
        await app.bot.set_my_commands(BASE_COMMANDS)

    main_base_module.set_bot_commands = wrapped_set_bot_commands
    main_base_module.app.state.activity_patch_installed = True
