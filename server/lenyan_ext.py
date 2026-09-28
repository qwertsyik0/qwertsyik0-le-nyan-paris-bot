from __future__ import annotations

from html import escape
from typing import Any, Callable

import asyncpg
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .db import list_accepted_applications
from .bot import is_admin, value, character_full_name

LENYAN_PREFIXES = ("ле нян", "le nyan")
MIN_MENTIONS_PER_MESSAGE = 2
MAX_MENTIONS_PER_MESSAGE = 4
DEFAULT_MENTIONS_PER_MESSAGE = 3


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


def _mention_for_row(row: Any) -> str | None:
    username = value(row, "username")
    if username:
        username_text = str(username).strip().lstrip("@")
        if username_text:
            return f"@{escape(username_text)}"

    telegram_id = value(row, "telegram_id")
    if telegram_id is None:
        return None
    label = character_full_name(row) or f"участник {telegram_id}"
    return f'<a href="tg://user?id={int(telegram_id)}">{escape(label)}</a>'


def _chunk_mentions(mentions: list[str]) -> list[list[str]]:
    if len(mentions) <= MAX_MENTIONS_PER_MESSAGE:
        return [mentions]

    chunks = [mentions[i:i + DEFAULT_MENTIONS_PER_MESSAGE] for i in range(0, len(mentions), DEFAULT_MENTIONS_PER_MESSAGE)]
    if len(chunks) >= 2 and len(chunks[-1]) == 1 and len(chunks[-2]) > MIN_MENTIONS_PER_MESSAGE:
        chunks[-1].insert(0, chunks[-2].pop())
    return chunks


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

    clean_announcement = announcement.strip()
    if not clean_announcement:
        await message.reply_text("после «Ле нян» нужно написать текст объявления")
        return
    if len(clean_announcement) > 2800:
        await message.reply_text("текст слишком длинный. максимум 2800 символов")
        return

    rows = await list_accepted_applications(pool, limit=500)
    seen_ids: set[int] = set()
    mentions: list[str] = []
    for row in rows:
        telegram_id = value(row, "telegram_id")
        if telegram_id is None:
            continue
        telegram_id_int = int(telegram_id)
        if telegram_id_int in seen_ids:
            continue
        seen_ids.add(telegram_id_int)
        mention = _mention_for_row(row)
        if mention:
            mentions.append(mention)

    if not mentions:
        await message.reply_text("нет принятых участников для отметки")
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
