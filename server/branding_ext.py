from __future__ import annotations

from html import escape
from typing import Any, Callable

import asyncpg
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from .config import Config

PROJECT_TITLE = "L’Empire des Ombres"
PROJECT_SUBTITLE = "империя теней"
PROJECT_DESCRIPTION = (
    "мрачная ролевая о Париже 1808 года: имперская власть, суд, армия, "
    "подполье, угрозы, газета и тайны после наступления темноты."
)


def _main_menu_markup(config: Config) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📜 подать анкету", web_app=WebAppInfo(config.mini_app_url))],
            [InlineKeyboardButton("🕯 открыть кабинет", url=config.mini_app_url)],
        ]
    )


def _private_start_text() -> str:
    return (
        "🕯 <b>L’Empire des Ombres</b>\n"
        "<i>империя теней</i>\n\n"
        "Париж, 1808 год.\n"
        "город живет под властью императора, но после заката его улицы принадлежат слухам, "
        "запискам, жандармам и тем, кто не хочет оставлять следов.\n\n"
        "через эту канцелярию можно подать анкету, открыть кабинет, получить письма, "
        "повестки, предупреждения и сюжетные уведомления."
    )


def _group_start_text() -> str:
    return (
        "🕯 <b>L’Empire des Ombres</b>\n\n"
        "бот канцелярии активен.\n"
        "в группе работают: <code>/ping</code>, <code>/activity</code>, <code>/me</code> и команда <b>Профиль</b>.\n\n"
        "полная анкета и кабинет доступны только в личных сообщениях с ботом."
    )


async def branded_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    chat = update.effective_chat
    if message is None:
        return

    if chat is not None and chat.type in {"group", "supergroup"}:
        await message.reply_text(_group_start_text(), parse_mode=ParseMode.HTML)
        return

    await message.reply_text(
        _private_start_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_menu_markup(config),
        disable_web_page_preview=True,
    )


def branded_accepted_notification_text(role: str) -> str:
    return (
        "🕯 <b>канцелярия L’Empire des Ombres</b>\n\n"
        "ваша анкета рассмотрена и одобрена.\n\n"
        "вы приняты в число участников <b>L’Empire des Ombres</b> и внесены в городской реестр Парижа.\n\n"
        f"<b>назначенная роль:</b>\n{escape(role)}\n\n"
        "после входа в чат ознакомьтесь с правилами, текущими указами, городским листом и начните игру с локации, "
        "которая подходит вашему персонажу.\n\n"
        "париж не любит тех, кто выходит в темноту неподготовленным."
    )


def branded_letter_notification_text(body: str) -> str:
    safe_body = escape(body).strip()
    return (
        "📜 <b>вам доставлено письмо</b>\n\n"
        "<i>от имперской канцелярии L’Empire des Ombres</i>\n\n"
        f"<blockquote>{safe_body}</blockquote>\n\n"
        "<b>письмо сохранено в разделе «письма» вашего кабинета.</b>"
    )


def branded_profile_text(row: Any | None, unread: int = 0) -> str:
    from .player_features import PLAYER_STATUS_LABELS, name, role, tags, value

    if row is None:
        return "🕯 <b>мой профиль</b>\n\nанкета пока не найдена. откройте кабинет и подайте анкету."
    player_status = str(value(row, "player_status", "active") or "active")
    row_tags = tags(value(row, "story_tags"))
    return (
        "🕯 <b>профиль L’Empire des Ombres</b>\n\n"
        f"<b>персонаж:</b> {escape(name(row))}\n"
        f"<b>роль:</b> {escape(role(row))}\n"
        f"<b>раздел:</b> {escape(str(value(row, 'affiliation', '—') or '—'))}\n"
        f"<b>статус анкеты:</b> {escape(str(value(row, 'status', '—')))}\n"
        f"<b>сюжетный статус:</b> {escape(PLAYER_STATUS_LABELS.get(player_status, player_status))}\n"
        f"<b>письма:</b> {unread} непрочитанных\n\n"
        f"<b>возраст:</b> {escape(str(value(row, 'character_age', '—')))}\n"
        f"<b>пол:</b> {escape(str(value(row, 'character_gender', '—')))}\n"
        f"<b>ориентация:</b> {escape(str(value(row, 'character_orientation', '—')))}\n"
        f"<b>метки:</b> {escape(', '.join(row_tags) if row_tags else '—')}\n\n"
        f"<b>описание:</b>\n{escape(str(value(row, 'character_description', '—') or '—'))}\n\n"
        f"<b>характер:</b>\n{escape(str(value(row, 'character_personality', '—') or '—'))}"
    )


def branded_public_profile_text(row: Any | None, fallback_user: Any | None = None) -> str:
    from .player_features import PLAYER_STATUS_LABELS, name, role, tags, value
    from .public_profile_ext import _display_user

    if row is None:
        return (
            "🎭 <b>карточка персонажа</b>\n\n"
            "публичная карточка не найдена.\n\n"
            "возможные причины: игрок не принят, не запускал бота, исключен или еще не попал в базу."
        )

    player_status = str(value(row, "player_status", "active") or "active")
    row_tags = tags(value(row, "story_tags"))
    tag_line = ", ".join(row_tags) if row_tags else "—"

    return (
        "🎭 <b>карточка персонажа</b>\n"
        "<i>L’Empire des Ombres</i>\n\n"
        f"<b>игрок:</b> {escape(_display_user(row, fallback_user))}\n"
        f"<b>персонаж:</b> {escape(name(row))}\n"
        f"<b>роль:</b> {escape(role(row))}\n"
        f"<b>раздел:</b> {escape(str(value(row, 'affiliation', '—') or '—'))}\n"
        f"<b>статус:</b> {escape(PLAYER_STATUS_LABELS.get(player_status, player_status))}\n"
        f"<b>метки:</b> {escape(tag_line)}\n\n"
        "<i>показывается только публичная карточка. полная анкета скрыта.</i>"
    )


async def branded_set_bot_commands(app: Application, original: Callable[[Application], Any] | None = None) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "открыть канцелярию"),
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
    ])
    try:
        await app.bot.set_my_name(PROJECT_TITLE)
    except Exception as exc:
        print(f"failed to set bot name: {exc}")
    try:
        await app.bot.set_my_short_description(PROJECT_DESCRIPTION[:120])
    except Exception as exc:
        print(f"failed to set bot short description: {exc}")
    try:
        await app.bot.set_my_description(PROJECT_DESCRIPTION)
    except Exception as exc:
        print(f"failed to set bot description: {exc}")


def patch_shadow_empire_branding(main_base_module: Any) -> None:
    if getattr(main_base_module.app.state, "shadow_empire_branding_installed", False):
        return

    from . import bot as bot_module
    from . import player_features as player_features_module
    from . import public_profile_ext as public_profile_module

    bot_module.start_command = branded_start_command
    bot_module.accepted_notification_text = branded_accepted_notification_text
    bot_module.letter_notification_text = branded_letter_notification_text
    bot_module.main_menu_markup = _main_menu_markup
    player_features_module.profile_text = branded_profile_text
    public_profile_module.public_profile_text = branded_public_profile_text

    original_build_application = main_base_module.build_application

    def build_application_with_branding(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        return app

    main_base_module.build_application = build_application_with_branding
    main_base_module.set_bot_commands = branded_set_bot_commands
    main_base_module.app.state.shadow_empire_branding_installed = True
