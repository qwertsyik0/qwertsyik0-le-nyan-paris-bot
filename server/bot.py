from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .db import (
    create_letter,
    decide_application,
    find_user_by_identifier,
    get_application,
    list_accepted_applications,
    list_admin_letters,
    list_pending_applications,
    update_letter_status,
)

WaitingAction = tuple[str, int]
waiting_admin_actions: dict[int, WaitingAction] = {}

EVENT_CHAT_URL = "https://t.me/+gB1sMZBd5Lo4YjQy"
CITY_SHEET_URL = "https://qwertsyik0.github.io/le-nyan-paris/"
MINIAPP_CACHE_TAG = "empire-rules-20261006-1"


def mini_app_url(config: Config) -> str:
    base = (config.mini_app_url or "https://qwertsyik0.github.io/le-nyan-paris/miniapp/").strip()
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}v={MINIAPP_CACHE_TAG}"

LETTER_STATUS_LABELS = {
    "new": "новое",
    "read": "прочитано",
    "in_work": "в работе",
    "closed": "закрыто",
    "hidden": "скрыто",
}
LETTER_STATUSES_TEXT = "new / read / in_work / closed / hidden"


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


def username_line(row: Any) -> str:
    username = value(row, "username")
    if username:
        username_text = str(username)
        return f"@{username_text}" if not username_text.startswith("@") else username_text
    return "без username"


def character_full_name(row: Any) -> str:
    return f"{value(row, 'character_first_name', '')} {value(row, 'character_last_name', '')}".strip()


def application_summary(row: Any) -> str:
    status = value(row, "status", "—")
    assigned_role = value(row, "assigned_role") or value(row, "owner_comment") or "—"
    return (
        "📋 <b>анкета L’Empire des Ombres</b>\n\n"
        f"<b>ID:</b> {value(row, 'id')}\n"
        f"<b>статус:</b> {escape(str(status))}\n"
        f"<b>игрок:</b> {escape(username_line(row))}\n"
        f"<b>персонаж:</b> {escape(character_full_name(row))}\n"
        f"<b>назначенная роль:</b> {escape(str(assigned_role))}\n"
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


def accepted_list_text(rows: list[asyncpg.Record]) -> str:
    if not rows:
        return "принятых анкет пока нет"
    lines = ["✅ <b>принятые участники</b>\n"]
    for row in rows:
        role = value(row, "assigned_role") or value(row, "owner_comment") or "—"
        lines.append(
            f"#{value(row, 'id')} — {escape(character_full_name(row))}\n"
            f"игрок: {escape(username_line(row))}\n"
            f"роль: {escape(str(role))}"
        )
    return "\n\n".join(lines)


def accepted_notification_text(role: str) -> str:
    return (
        "📜 <b>от императорской канцелярии</b>\n\n"
        "многоуважаемый участник,\n\n"
        "спешим уведомить вас, что поданная вами анкета была рассмотрена и одобрена.\n\n"
        "вы приняты в число участников <b>L’Empire des Ombres</b> и внесены в городской реестр.\n\n"
        f"<b>назначенная роль:</b>\n{escape(role)}\n\n"
        f"<b>чат события:</b>\n{EVENT_CHAT_URL}\n\n"
        f"<b>городской лист парижа:</b>\n{CITY_SHEET_URL}\n\n"
        "на городском листе будут размещаться указы, распоряжения, сведения о принятых ролях, городская газета и важные заметки для участников.\n\n"
        "по прибытии в чат просим ознакомиться с закрепленными правилами и начать игру с локации, соответствующей вашей роли.\n\n"
        "с уважением,\n"
        "императорская канцелярия"
    )


def letter_notification_text(body: str) -> str:
    safe_body = escape(body).strip()
    return (
        "📜 <b>вам доставлено письмо</b>\n\n"
        "<i>от императорской канцелярии L’Empire des Ombres</i>\n\n"
        f"<blockquote>{safe_body}</blockquote>\n\n"
        "<b>письмо сохранено в разделе «письма» вашего кабинета.</b>"
    )


def letter_status_label(status: str | None) -> str:
    return LETTER_STATUS_LABELS.get(str(status or ""), str(status or "—"))


def letter_admin_line(row: Any) -> str:
    username = username_line(row)
    status = letter_status_label(value(row, "status"))
    title = value(row, "title", "письмо из канцелярии")
    body = str(value(row, "body", "") or "")
    short_body = body[:220] + ("..." if len(body) > 220 else "")
    character = character_full_name(row) or "без персонажа"
    return (
        f"📨 <b>письмо #{value(row, 'id')}</b>\n"
        f"<b>статус:</b> {escape(status)}\n"
        f"<b>получатель:</b> {escape(username)} | <code>{value(row, 'telegram_id')}</code>\n"
        f"<b>персонаж:</b> {escape(character)}\n"
        f"<b>заголовок:</b> {escape(str(title))}\n"
        f"<b>текст:</b>\n{escape(short_body)}"
    )


def letters_list_text(rows: list[asyncpg.Record], status: str = "all") -> str:
    if not rows:
        return "писем с таким фильтром нет"
    lines = [f"📨 <b>письма</b> | фильтр: <code>{escape(status)}</code>\n"]
    for row in rows:
        lines.append(letter_admin_line(row))
    return "\n\n".join(lines)


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
            [InlineKeyboardButton("📜 подать анкету", web_app=WebAppInfo(mini_app_url(config)))],
            [InlineKeyboardButton("🌐 открыть Mini App", url=mini_app_url(config))],
        ]
    )


def admin_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 новые анкеты", callback_data="admin:pending")],
            [InlineKeyboardButton("✅ принятые", callback_data="admin:accepted")],
        ]
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None:
        return
    text = (
        "📜 <b>L’Empire des Ombres</b>\n\n"
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
    await message.reply_text("админ-панель L’Empire des Ombres", reply_markup=admin_menu_markup())


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


async def accepted_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    rows = await list_accepted_applications(pool, limit=20)
    await message.reply_text(accepted_list_text(rows), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if not context.args:
        await message.reply_text("укажите ID анкеты: /app 12")
        return
    try:
        application_id = int(context.args[0])
    except ValueError:
        await message.reply_text("ID анкеты должен быть числом")
        return
    row = await get_application(pool, application_id)
    if row is None:
        await message.reply_text("анкета не найдена")
        return
    markup = application_decision_markup(application_id) if value(row, "status") == "pending" else None
    await message.reply_text(
        application_summary(row),
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


async def letter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if len(context.args) < 2:
        await message.reply_text(
            "формат:\n"
            "<code>/letter @username текст письма</code>\n"
            "<code>/letter 123456789 текст письма</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target_raw = context.args[0]
    body = " ".join(context.args[1:]).strip()
    if not body:
        await message.reply_text("текст письма не может быть пустым")
        return
    if len(body) > 3500:
        await message.reply_text("письмо слишком длинное. максимум 3500 символов")
        return

    target = await find_user_by_identifier(pool, target_raw)
    if target is None:
        await message.reply_text(
            "получатель не найден.\n\n"
            "он должен хотя бы один раз открыть бота или Mini App, чтобы появиться в базе."
        )
        return

    letter = await create_letter(
        pool,
        telegram_id=int(target["telegram_id"]),
        sender_admin_id=int(user_id),
        body=body,
    )
    notify_ok = True
    try:
        await context.bot.send_message(
            chat_id=int(target["telegram_id"]),
            text=letter_notification_text(body),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        notify_ok = False
        print(f"failed to notify letter recipient {target['telegram_id']}: {exc}")

    username = target["username"] or target["telegram_id"]
    suffix = "" if notify_ok else "\n\n⚠️ письмо сохранено, но Telegram-уведомление не удалось отправить."
    await message.reply_text(
        f"письмо #{letter['id']} отправлено для {escape(str(username))}.{suffix}",
        parse_mode=ParseMode.HTML,
    )


async def letters_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    status = context.args[0] if context.args else "all"
    if status not in {"all", *LETTER_STATUS_LABELS.keys()}:
        await message.reply_text(
            f"неверный статус. доступно: all / {LETTER_STATUSES_TEXT}",
            parse_mode=ParseMode.HTML,
        )
        return

    rows = await list_admin_letters(pool, status=status, limit=20)
    await message.reply_text(
        letters_list_text(rows, status=status),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def letter_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if len(context.args) < 2:
        await message.reply_text(
            "формат:\n"
            "<code>/letterstatus ID status</code>\n\n"
            f"статусы: <code>{LETTER_STATUSES_TEXT}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        letter_id = int(context.args[0])
    except ValueError:
        await message.reply_text("ID письма должен быть числом")
        return

    status = context.args[1]
    if status not in LETTER_STATUS_LABELS:
        await message.reply_text(
            f"неверный статус. доступно: <code>{LETTER_STATUSES_TEXT}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    row = await update_letter_status(pool, letter_id, status)
    if row is None:
        await message.reply_text("письмо не найдено")
        return

    await message.reply_text(
        f"статус письма #{letter_id} изменен на <b>{escape(letter_status_label(status))}</b>",
        parse_mode=ParseMode.HTML,
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

    if data == "admin:accepted":
        await query.answer()
        rows = await list_accepted_applications(pool, limit=20)
        await query.message.reply_text(
            accepted_list_text(rows),
            parse_mode=ParseMode.HTML,
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
            text=accepted_notification_text(text),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
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


async def set_bot_commands(app: Application) -> None:
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
        ]
    )


def build_application(config: Config, pool: asyncpg.Pool) -> Application:
    app = Application.builder().token(config.bot_token).build()
    app.bot_data["config"] = config
    app.bot_data["pool"] = pool
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("accepted", accepted_command))
    app.add_handler(CommandHandler("app", app_command))
    app.add_handler(CommandHandler("letter", letter_command))
    app.add_handler(CommandHandler("letters", letters_command))
    app.add_handler(CommandHandler("letterstatus", letter_status_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler))
    return app
