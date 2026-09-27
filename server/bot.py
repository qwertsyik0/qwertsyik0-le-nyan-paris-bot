from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .db import decide_application, get_application, list_pending_applications

WaitingAction = tuple[str, int]
waiting_admin_actions: dict[int, WaitingAction] = {}


def is_admin(config: Config, user_id: int | None) -> bool:
    return bool(user_id and user_id in config.admin_ids)


def value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def admin_denied_text(user_id: int | None) -> str:
    if user_id is None:
        return "нет доступа: не удалось определить Telegram ID."
    return (
        "нет доступа к админ-панели.\n\n"
        f"ваш Telegram ID: <code>{user_id}</code>\n\n"
        "если это владелец, добавьте этот ID в ADMIN_IDS на Render."
    )


def application_summary(row: Any) -> str:
    username = value(row, "username")
    username_line = f"@{username}" if username and not str(username).startswith("@") else (username or "без username")
    full_name = f"{value(row, 'character_first_name', '')} {value(row, 'character_last_name', '')}".strip()
    return (
        "📋 <b>анкета Le Nyan Paris</b>\n\n"
        f"<b>ID:</b> {value(row, 'id')}\n"
        f"<b>игрок:</b> {escape(str(username_line))}\n"
        f"<b>персонаж:</b> {escape(full_name)}\n"
        f"<b>возраст:</b> {value(row, 'character_age')}\n"
        f"<b>пол:</b> {escape(str(value(row, 'character_gender', '')))}\n"
        f"<b>ориентация:</b> {escape(str(value(row, 'character_orientation', '')))}\n"
        f"<b>желаемая роль:</b> {escape(str(value(row, 'role_preference', '')))}\n"
        f"<b>раздел:</b> {escape(str(value(row, 'affiliation', '')))}\n\n"
        f"<b>описание:</b>\n{escape(str(value(row, 'character_description', '')))}\n\n"
        f"<b>характер:</b>\n{escape(str(value(row, 'character_personality', '')))}\n\n"
        f"<b>опыт:</b>\n{escape(str(value(row, 'roleplay_experience', '')))}\n\n"
        f"<b>комментарий:</b>\n{escape(str(value(row, 'applicant_comment', '') or '—'))}"
    )


def application_decision_markup(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ принять", callback_data=f"app:accept:{application_id}"),
                InlineKeyboardButton("✏️ правки", callback_data=f"app:revise:{application_id}"),
            ],
            [InlineKeyboardButton("❌ отклонить", callback_data=f"app:reject:{application_id}")],
        ]
    )


def main_menu_markup(config: Config) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📜 подать анкету", web_app=WebAppInfo(config.mini_app_url))],
            [InlineKeyboardButton("🌐 открыть Mini App", url=config.mini_app_url)],
        ]
    )


def admin_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 новые анкеты", callback_data="admin:pending")],
        ]
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None:
        return
    text = (
        "📜 <b>Le Nyan Paris</b>\n\n"
        "это императорская канцелярия проекта.\n"
        "через бота можно подать анкету и получать важные уведомления по роли."
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_markup(config))


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return
    if user is None:
        await message.reply_text("не удалось определить Telegram ID")
        return
    await message.reply_text(f"ваш Telegram ID: <code>{user.id}</code>", parse_mode=ParseMode.HTML)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return
    user_id = user.id if user else None
    if not is_admin(config, user_id):
        await message.reply_text(admin_denied_text(user_id), parse_mode=ParseMode.HTML)
        return
    await message.reply_text("админ-панель Le Nyan Paris", reply_markup=admin_menu_markup())


async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    rows = await list_pending_applications(pool, limit=10)
    if not rows:
        await message.reply_text("новых анкет нет")
        return
    for row in rows:
        await message.reply_text(
            application_summary(row),
            parse_mode=ParseMode.HTML,
            reply_markup=application_decision_markup(row["id"]),
            disable_web_page_preview=True,
        )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    if not is_admin(config, user.id):
        await query.answer("нет доступа", show_alert=True)
        return

    data = query.data or ""
    if data == "admin:pending":
        await query.answer()
        rows = await list_pending_applications(pool, limit=10)
        if not rows:
            await query.message.reply_text("новых анкет нет")
            return
        for row in rows:
            await query.message.reply_text(
                application_summary(row),
                parse_mode=ParseMode.HTML,
                reply_markup=application_decision_markup(row["id"]),
                disable_web_page_preview=True,
            )
        return

    if not data.startswith("app:"):
        return
    _, action, application_id_raw = data.split(":", 2)
    try:
        application_id = int(application_id_raw)
    except ValueError:
        await query.answer("ошибка id", show_alert=True)
        return

    row = await get_application(pool, application_id)
    if row is None:
        await query.answer("анкета не найдена", show_alert=True)
        return

    if action == "accept":
        waiting_admin_actions[user.id] = ("accept", application_id)
        await query.answer()
        await query.message.reply_text(
            "введите назначенную роль для принятия анкеты.\n\n"
            "пример: посыльная при больнице"
        )
        return

    if action == "revise":
        waiting_admin_actions[user.id] = ("revise", application_id)
        await query.answer()
        await query.message.reply_text("напишите, что нужно исправить в анкете")
        return

    if action == "reject":
        waiting_admin_actions[user.id] = ("reject", application_id)
        await query.answer()
        await query.message.reply_text("напишите причину отказа")
        return


async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not is_admin(config, user.id):
        return
    pending = waiting_admin_actions.get(user.id)
    if pending is None:
        return

    text = (message.text or "").strip()
    if not text:
        await message.reply_text("нужно отправить текст")
        return
    action, application_id = pending
    row = await get_application(pool, application_id)
    if row is None:
        waiting_admin_actions.pop(user.id, None)
        await message.reply_text("анкета уже не найдена")
        return

    if action == "accept":
        decided = await decide_application(
            pool,
            application_id,
            status="accepted",
            reviewer_id=user.id,
            comment=text,
            assigned_role=text,
        )
        waiting_admin_actions.pop(user.id, None)
        await message.reply_text(f"анкета #{application_id} принята. роль: {text}")
        await context.bot.send_message(
            chat_id=decided["telegram_id"],
            text=(
                "📜 от императорской канцелярии\n\n"
                "многоуважаемый участник, ваша анкета одобрена.\n\n"
                f"назначенная роль:\n{text}\n\n"
                "вы приняты в Le Nyan Paris."
            ),
        )
        return

    if action == "revise":
        decided = await decide_application(
            pool,
            application_id,
            status="needs_changes",
            reviewer_id=user.id,
            comment=text,
        )
        waiting_admin_actions.pop(user.id, None)
        await message.reply_text(f"анкета #{application_id} отправлена на правки")
        await context.bot.send_message(
            chat_id=decided["telegram_id"],
            text=(
                "📜 от императорской канцелярии\n\n"
                "вашу анкету нужно немного исправить.\n\n"
                f"комментарий:\n{text}\n\n"
                "откройте Mini App и отправьте анкету заново."
            ),
            reply_markup=main_menu_markup(config),
        )
        return

    if action == "reject":
        decided = await decide_application(
            pool,
            application_id,
            status="rejected",
            reviewer_id=user.id,
            comment=text,
        )
        waiting_admin_actions.pop(user.id, None)
        await message.reply_text(f"анкета #{application_id} отклонена")
        await context.bot.send_message(
            chat_id=decided["telegram_id"],
            text=(
                "📜 от императорской канцелярии\n\n"
                "ваша анкета отклонена.\n\n"
                f"причина:\n{text}"
            ),
        )
        return


async def notify_admins_about_application(bot, config: Config, row: Any) -> None:
    for admin_id in config.admin_ids:
        await bot.send_message(
            chat_id=admin_id,
            text=application_summary(row),
            parse_mode=ParseMode.HTML,
            reply_markup=application_decision_markup(row["id"]),
            disable_web_page_preview=True,
        )


def build_application(config: Config, pool: asyncpg.Pool) -> Application:
    app = Application.builder().token(config.bot_token).build()
    app.bot_data["config"] = config
    app.bot_data["pool"] = pool
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler))
    return app
