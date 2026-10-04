from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from telegram import BotCommand, Update, User
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Config
from .db import find_user_by_identifier, upsert_user
from .player_features import PLAYER_STATUS_LABELS, ensure_schema, name, role, tags, value
from .presence_features import CHARACTER_STATUSES, ensure_presence_schema

PROFILE_PREFIX = "профиль"


def _is_group_chat(chat_type: str | None) -> bool:
    return chat_type in {"group", "supergroup"}


def _user_identifier(user: User | None) -> str | None:
    if user is None:
        return None
    return str(int(user.id))


def _display_user(row: Any | None, fallback_user: User | None = None) -> str:
    username = None
    first_name = None
    last_name = None
    telegram_id = None

    if row is not None:
        try:
            username = row["username"]
            first_name = row["tg_first_name"]
            last_name = row["tg_last_name"]
            telegram_id = row["telegram_id"]
        except Exception:
            username = None

    if username:
        clean_username = str(username).strip().lstrip("@")
        if clean_username:
            return f"@{clean_username}"

    if fallback_user is not None:
        if fallback_user.username:
            return f"@{fallback_user.username}"
        full_name = f"{fallback_user.first_name or ''} {fallback_user.last_name or ''}".strip()
        if full_name:
            return full_name
        return f"id {fallback_user.id}"

    full_name = f"{first_name or ''} {last_name or ''}".strip()
    if full_name:
        return full_name
    return f"id {telegram_id}" if telegram_id else "неизвестный игрок"


def _clean_target(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    first = text.split()[0].strip()
    return first.rstrip(",.;:")


async def _fetch_public_application(pool: asyncpg.Pool, telegram_id: int) -> asyncpg.Record | None:
    await ensure_schema(pool)
    await ensure_presence_schema(pool)
    return await pool.fetchrow(
        """
        SELECT
            a.*,
            u.username,
            u.first_name AS tg_first_name,
            u.last_name AS tg_last_name
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE a.telegram_id = $1
          AND a.status = 'accepted'
          AND COALESCE(a.player_status, 'active') <> 'left';
        """,
        int(telegram_id),
    )


async def _resolve_target_id(pool: asyncpg.Pool, target_raw: str | None, reply_user: User | None, own_user: User | None) -> tuple[int | None, User | None]:
    target = _clean_target(target_raw)
    if target:
        row = await find_user_by_identifier(pool, target)
        if row is not None:
            return int(row["telegram_id"]), None
        if target.isdigit():
            return int(target), None
        return None, None

    if reply_user is not None:
        await upsert_user(pool, reply_user.to_dict())
        return int(reply_user.id), reply_user

    if own_user is not None:
        await upsert_user(pool, own_user.to_dict())
        return int(own_user.id), own_user

    return None, None


def public_profile_text(row: Any | None, fallback_user: User | None = None) -> str:
    if row is None:
        return (
            "🜏 <b>L’Empire des Ombres · карточка персонажа</b>\n\n"
            "публичная карточка не найдена.\n\n"
            "игрок может быть не принят, исключен, не запускал бота или еще не попал в базу."
        )

    player_status = str(value(row, "player_status", "active") or "active")
    character_status = str(value(row, "character_status", "free") or "free")
    current_location = str(value(row, "current_location", "") or "")
    row_tags = tags(value(row, "story_tags"))
    tag_line = ", ".join(row_tags) if row_tags else "—"

    return (
        "🜏 <b>карточка персонажа</b>\n"
        "<i>L’Empire des Ombres · Париж, 1808</i>\n\n"
        f"<b>игрок:</b> {escape(_display_user(row, fallback_user))}\n"
        f"<b>персонаж:</b> {escape(name(row))}\n"
        f"<b>роль:</b> {escape(role(row))}\n"
        f"<b>раздел:</b> {escape(str(value(row, 'affiliation', '—') or '—'))}\n"
        f"<b>статус:</b> {escape(PLAYER_STATUS_LABELS.get(player_status, player_status))}\n"
        f"<b>игровой статус:</b> {escape(CHARACTER_STATUSES.get(character_status, character_status))}\n"
        f"<b>локация:</b> {escape(current_location or 'не указана')}\n"
        f"<b>метки:</b> {escape(tag_line)}\n\n"
        "<i>это публичная карточка. полная анкета остается доступна только в личном кабинете.</i>"
    )


async def _send_public_profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_raw: str | None = None,
    *,
    force_self: bool = False,
) -> None:
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    if message is None:
        return

    own_user = message.from_user
    if force_self:
        if own_user is None:
            await message.reply_text("не удалось определить пользователя.")
            return
        await upsert_user(pool, own_user.to_dict())
        target_id, fallback_user = int(own_user.id), own_user
    else:
        reply_user = message.reply_to_message.from_user if message.reply_to_message is not None else None
        target_id, fallback_user = await _resolve_target_id(pool, target_raw, reply_user, own_user)
    if target_id is None:
        await message.reply_text("игрок не найден в базе. попробуй ответом на его сообщение или через @username.")
        return

    row = await _fetch_public_application(pool, target_id)
    await message.reply_text(public_profile_text(row, fallback_user), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_raw = " ".join(context.args or []).strip()
    await _send_public_profile(update, context, target_raw)
    raise ApplicationHandlerStop


async def russian_profile_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return

    text = " ".join(message.text.strip().split())
    lowered = text.casefold()

    if lowered == "мой профиль":
        await _send_public_profile(update, context, force_self=True)
        raise ApplicationHandlerStop

    if lowered != PROFILE_PREFIX and not lowered.startswith(PROFILE_PREFIX + " "):
        return

    target_raw = text[len(PROFILE_PREFIX):].strip()

    # "Профиль" без аргумента и без reply всегда означает профиль автора
    # сообщения. Это исключает случайную подстановку чужого профиля.
    if not target_raw and message.reply_to_message is None:
        await _send_public_profile(update, context, force_self=True)
    else:
        await _send_public_profile(update, context, target_raw)
    raise ApplicationHandlerStop


async def private_myapp_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if _is_group_chat(chat.type):
        await message.reply_text("полная анкета доступна только в личных сообщениях с ботом.")
        raise ApplicationHandlerStop


async def set_commands_with_public_profiles(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "открыть канцелярию"),
        BotCommand("me", "карточка персонажа"),
        BotCommand("profile", "карточка персонажа"),
        BotCommand("myapp", "моя анкета"),
        BotCommand("invite", "ссылка на чат для принятых"),
        BotCommand("scene", "сцены"),
        BotCommand("id", "показать Telegram ID"),
        BotCommand("ping", "проверка бота в группе"),
        BotCommand("activity", "активность игроков"),
        BotCommand("active", "активность игроков"),
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


def patch_public_profile_features(main_base_module: Any) -> None:
    if getattr(main_base_module.app.state, "public_profile_patch_installed", False):
        return

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config: Config, pool: asyncpg.Pool) -> Application:
        telegram_app = original_build_application(config, pool)
        telegram_app.add_handler(CommandHandler(["me", "profile"], me_command), group=-99)
        telegram_app.add_handler(CommandHandler("myapp", private_myapp_guard), group=-99)
        telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, russian_profile_text_handler), group=-98)
        return telegram_app

    main_base_module.build_application = wrapped_build_application
    main_base_module.set_bot_commands = set_commands_with_public_profiles
    main_base_module.app.state.public_profile_patch_installed = True
