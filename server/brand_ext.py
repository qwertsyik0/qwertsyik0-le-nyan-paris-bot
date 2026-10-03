from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, ContextTypes

from .config import Config

PROJECT_TITLE = "L’Empire des Ombres"
PROJECT_SUBTITLE = "империя теней"


def empire_menu_markup(config: Config) -> InlineKeyboardMarkup:
    buttons = []
    if config.mini_app_url:
        buttons.append([InlineKeyboardButton("🜏 открыть кабинет", web_app=WebAppInfo(config.mini_app_url))])
        buttons.append([InlineKeyboardButton("🌑 открыть Mini App", url=config.mini_app_url)])
    return InlineKeyboardMarkup(buttons) if buttons else InlineKeyboardMarkup([])


def private_start_text() -> str:
    return (
        "🜏 <b>L’Empire des Ombres</b>\n"
        "<i>Париж, 1808 год · империя теней</i>\n\n"
        "это канцелярия темного Парижа.\n\n"
        "через бота можно подать анкету, открыть кабинет, получать письма, повестки, предупреждения, сюжетные уведомления и смотреть свою роль.\n\n"
        "<b>город больше не безопасен.</b>\n"
        "каждый указ, слух и письмо может втянуть персонажа в чужую игру."
    )


def group_start_text() -> str:
    return (
        "🜏 <b>L’Empire des Ombres</b>\n"
        "<i>Париж, 1808 год · империя теней</i>\n\n"
        "бот видит группу и готов к работе.\n\n"
        "доступные публичные команды:\n"
        "<code>Профиль</code> — карточка персонажа\n"
        "<code>/me</code> — карточка персонажа\n"
        "<code>/activity</code> — активность игроков для владельца\n"
        "<code>/ping</code> — проверка связи"
    )


async def branded_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    if chat.type in {"group", "supergroup"}:
        await message.reply_text(group_start_text(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await message.reply_text(
            private_start_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=empire_menu_markup(config),
            disable_web_page_preview=True,
        )
    raise ApplicationHandlerStop


async def branded_ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "🜏 <b>L’Empire des Ombres</b>\nбот на связи. тени слышат этот чат.",
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


def patch_brand_features(main_base_module: Any) -> None:
    if getattr(main_base_module.app.state, "empire_brand_patch_installed", False):
        return

    original_build_application = main_base_module.build_application

    def build_application_with_brand(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        app.add_handler(CommandHandler("start", branded_start_command), group=-200)
        app.add_handler(CommandHandler("ping", branded_ping_command), group=-200)
        return app

    main_base_module.build_application = build_application_with_brand
    main_base_module.app.state.empire_brand_patch_installed = True
