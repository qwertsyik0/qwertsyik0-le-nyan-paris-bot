from __future__ import annotations

from html import escape
from typing import Any, Callable

import asyncpg
from telegram import BotCommand, Update, User
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .bot import is_admin

LENYAN_PREFIXES = ("ле нян", "le nyan")
MIN_MENTIONS_PER_MESSAGE = 2
MAX_MENTIONS_PER_MESSAGE = 4
DEFAULT_MENTIONS_PER_MESSAGE = 3

_schema_ready = False


def _extract_lenyan_text(text: str | None) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    lowered = raw.casefold()
    for prefix in LENYAN_PREFIXES:
        if lowered == prefix:
            return ""
        if lowered.startswith(prefix + " ") or lowered.startswith(prefix + "\n") or lowered.startswith(prefix + ":"):
            return raw[len(prefix):].lstrip(" \n:—-").strip()
    return None


async def _ensure_group_members_schema(pool: asyncpg.Pool) -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_group_members (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_bot BOOLEAN NOT NULL DEFAULT FALSE,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                left_at TIMESTAMPTZ,
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_group_members_chat_seen_idx
            ON paris_group_members (chat_id, left_at, last_seen_at DESC);
            """
        )
    _schema_ready = True


def _user_full_name(user: User | Any) -> str:
    first_name = getattr(user, "first_name", None) or ""
    last_name = getattr(user, "last_name", None) or ""
    return f"{first_name} {last_name}".strip()


async def _remember_group_user(pool: asyncpg.Pool, chat_id: int, user: User | None) -> None:
    if user is None:
        return
    await _ensure_group_members_schema(pool)
    await pool.execute(
        """
        INSERT INTO paris_group_members (chat_id, user_id, username, first_name, last_name, is_bot, last_seen_at, left_at)
        VALUES ($1, $2, $3, $4, $5, $6, NOW(), NULL)
        ON CONFLICT (chat_id, user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            is_bot = EXCLUDED.is_bot,
            last_seen_at = NOW(),
            left_at = NULL;
        """,
        int(chat_id),
        int(user.id),
        user.username,
        user.first_name,
        user.last_name,
        bool(user.is_bot),
    )


async def _mark_group_user_left(pool: asyncpg.Pool, chat_id: int, user: User | None) -> None:
    if user is None:
        return
    await _ensure_group_members_schema(pool)
    await pool.execute(
        """
        INSERT INTO paris_group_members (chat_id, user_id, username, first_name, last_name, is_bot, left_at)
        VALUES ($1, $2, $3, $4, $5, $6, NOW())
        ON CONFLICT (chat_id, user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            is_bot = EXCLUDED.is_bot,
            left_at = NOW();
        """,
        int(chat_id),
        int(user.id),
        user.username,
        user.first_name,
        user.last_name,
        bool(user.is_bot),
    )


async def _list_known_group_members(pool: asyncpg.Pool, chat_id: int) -> list[asyncpg.Record]:
    await _ensure_group_members_schema(pool)
    rows = await pool.fetch(
        """
        SELECT *
        FROM paris_group_members
        WHERE chat_id = $1
          AND is_bot = FALSE
          AND left_at IS NULL
        ORDER BY last_seen_at DESC;
        """,
        int(chat_id),
    )
    return list(rows)


def _mention_for_member(row: Any) -> str | None:
    username = row["username"]
    if username:
        username_text = str(username).strip().lstrip("@")
        if username_text:
            return f"@{escape(username_text)}"

    user_id = row["user_id"]
    first_name = str(row["first_name"] or "").strip()
    last_name = str(row["last_name"] or "").strip()
    label = f"{first_name} {last_name}".strip() or f"участник {user_id}"
    return f'<a href="tg://user?id={int(user_id)}">{escape(label)}</a>'


def _chunk_mentions(mentions: list[str]) -> list[list[str]]:
    if len(mentions) <= MAX_MENTIONS_PER_MESSAGE:
        return [mentions]

    chunks = [mentions[i:i + DEFAULT_MENTIONS_PER_MESSAGE] for i in range(0, len(mentions), DEFAULT_MENTIONS_PER_MESSAGE)]
    if len(chunks) >= 2 and len(chunks[-1]) == 1 and len(chunks[-2]) > MIN_MENTIONS_PER_MESSAGE:
        chunks[-1].insert(0, chunks[-2].pop())
    return chunks


async def track_group_members_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or chat.type not in {"group", "supergroup"}:
        return

    if update.effective_user is not None:
        await _remember_group_user(pool, chat.id, update.effective_user)

    for member in (message.new_chat_members or []):
        await _remember_group_user(pool, chat.id, member)

    if message.left_chat_member is not None:
        await _mark_group_user_left(pool, chat.id, message.left_chat_member)


async def _send_lenyan_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, announcement: str) -> None:
    config: Config = context.application.bot_data["config"]
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    if not is_admin(config, user.id):
        return
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text("эта функция работает только в группе или супергруппе")
        return

    await _remember_group_user(pool, chat.id, user)

    clean_announcement = announcement.strip()
    if not clean_announcement:
        await message.reply_text("после «Ле нян» нужно написать текст объявления")
        return
    if len(clean_announcement) > 2800:
        await message.reply_text("текст слишком длинный. максимум 2800 символов")
        return

    rows = await _list_known_group_members(pool, chat.id)
    seen_ids: set[int] = set()
    mentions: list[str] = []
    for row in rows:
        user_id = int(row["user_id"])
        if user_id in seen_ids:
            continue
        seen_ids.add(user_id)
        mention = _mention_for_member(row)
        if mention:
            mentions.append(mention)

    if not mentions:
        await message.reply_text(
            "я пока не знаю участников этой группы.\n\n"
            "чтобы бот начал тегать всех, участники должны написать хотя бы одно сообщение после добавления бота, "
            "или нужно отключить Privacy Mode в BotFather."
        )
        return

    thread_id = getattr(message, "message_thread_id", None)
    send_kwargs: dict[str, Any] = {}
    if thread_id is not None:
        send_kwargs["message_thread_id"] = thread_id

    chunks = _chunk_mentions(mentions)
    safe_announcement = escape(clean_announcement)
    for chunk in chunks:
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"{safe_announcement}\n\n{' '.join(chunk)}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            **send_kwargs,
        )


async def lenyan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_lenyan_broadcast(update, context, " ".join(context.args).strip())


async def lenyan_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    announcement = _extract_lenyan_text(message.text)
    if announcement is None:
        return
    await _send_lenyan_broadcast(update, context, announcement)


def install_lenyan_broadcast(app: Application) -> None:
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, track_group_members_handler), group=-2)
    app.add_handler(CommandHandler("lenyan", lenyan_command), group=-1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lenyan_text_handler), group=-1)


def patch_lenyan_broadcast(main_base_module: Any) -> None:
    original_build_application: Callable[[Config, asyncpg.Pool], Application] = main_base_module.build_application

    def build_application_with_lenyan(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        install_lenyan_broadcast(app)
        return app

    async def set_bot_commands_with_lenyan(app: Application) -> None:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "открыть канцелярию"),
                BotCommand("id", "показать Telegram ID"),
                BotCommand("admin", "админ-панель"),
                BotCommand("pending", "новые анкеты"),
                BotCommand("accepted", "принятые участники"),
                BotCommand("app", "открыть анкету по ID"),
                BotCommand("letter", "отправить письмо игроку"),
                BotCommand("letters", "список писем"),
                BotCommand("letterstatus", "изменить статус письма"),
                BotCommand("lenyan", "объявление с отметками"),
            ]
        )

    main_base_module.build_application = build_application_with_lenyan
    main_base_module.set_bot_commands = set_bot_commands_with_lenyan
