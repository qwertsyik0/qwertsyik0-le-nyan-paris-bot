from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, ContextTypes

from .bot import CITY_SHEET_URL, EVENT_CHAT_URL
from .config import Config
from .player_features import PLAYER_STATUS_LABELS, name, role, tags, value
from . import public_profile_ext

PROJECT_TITLE = "L’Empire des Ombres"
PROJECT_SUBTITLE = "Париж 1808 · империя теней"


def _links_markup(config: Config) -> InlineKeyboardMarkup:
    mini_app_url = config.mini_app_url or "https://qwertsyik0.github.io/le-nyan-paris/miniapp/"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🕯 открыть кабинет", url=mini_app_url)],
            [InlineKeyboardButton("📜 городской лист", url=CITY_SHEET_URL)],
        ]
    )


def _group_markup(config: Config) -> InlineKeyboardMarkup:
    mini_app_url = config.mini_app_url or "https://qwertsyik0.github.io/le-nyan-paris/miniapp/"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🕯 кабинет", url=mini_app_url), InlineKeyboardButton("📜 лист", url=CITY_SHEET_URL)],
        ]
    )


async def empire_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    if chat.type in {"group", "supergroup"}:
        await message.reply_text(
            "🕯 <b>L’Empire des Ombres</b>\n"
            "<i>Париж 1808 · империя теней</i>\n\n"
            "бот активен в группе.\n"
            "доступные публичные команды:\n"
            "<code>Профиль</code> — карточка персонажа\n"
            "<code>/me</code> — карточка персонажа\n"
            "<code>/activity</code> — активность игроков для владельца\n"
            "<code>/ping</code> — проверка бота",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_group_markup(config),
        )
        raise ApplicationHandlerStop

    await message.reply_text(
        "🕯 <b>L’Empire des Ombres</b>\n"
        "<i>Париж 1808 · империя теней</i>\n\n"
        "вы у дверей императорской канцелярии.\n"
        "через кабинет можно подать анкету, проверить письма, предупреждения и данные персонажа.\n\n"
        "город живет указами, слухами и страхом после заката.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=_links_markup(config),
    )
    raise ApplicationHandlerStop


def empire_public_profile_text(row: Any | None, fallback_user: Any | None = None) -> str:
    if row is None:
        return (
            "🕯 <b>карточка L’Empire des Ombres</b>\n\n"
            "публичная карточка не найдена.\n\n"
            "игрок может быть не принят, исключен, не запускал бота или еще не попал в базу."
        )

    player_status = str(value(row, "player_status", "active") or "active")
    row_tags = tags(value(row, "story_tags"))
    tag_line = ", ".join(row_tags) if row_tags else "—"

    return (
        "🕯 <b>карточка персонажа</b>\n"
        "<i>L’Empire des Ombres</i>\n\n"
        f"<b>игрок:</b> {escape(public_profile_ext._display_user(row, fallback_user))}\n"
        f"<b>персонаж:</b> {escape(name(row))}\n"
        f"<b>роль:</b> {escape(role(row))}\n"
        f"<b>раздел:</b> {escape(str(value(row, 'affiliation', '—') or '—'))}\n"
        f"<b>статус:</b> {escape(PLAYER_STATUS_LABELS.get(player_status, player_status))}\n"
        f"<b>метки:</b> {escape(tag_line)}\n\n"
        "<i>это публичная карточка. полная анкета скрыта.</i>"
    )


async def set_empire_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "канцелярия L’Empire des Ombres"),
            BotCommand("me", "карточка персонажа"),
            BotCommand("profile", "карточка персонажа"),
            BotCommand("myapp", "моя анкета"),
            BotCommand("invite", "ссылка на чат для принятых"),
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
        ]
    )


def patch_empire_style(main_base_module: Any) -> None:
    if getattr(main_base_module.app.state, "empire_style_patch_installed", False):
        return

    public_profile_ext.public_profile_text = empire_public_profile_text

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config: Config, pool: asyncpg.Pool) -> Application:
        telegram_app = original_build_application(config, pool)
        telegram_app.add_handler(CommandHandler("start", empire_start_command), group=-150)
        return telegram_app

    main_base_module.build_application = wrapped_build_application
    main_base_module.set_bot_commands = set_empire_commands
    main_base_module.app.state.empire_style_patch_installed = True
