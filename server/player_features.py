from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .bot import CITY_SHEET_URL, EVENT_CHAT_URL, admin_denied_text, is_admin, main_menu_markup, value
from .config import Config
from .db import count_unread_letters, create_letter, find_user_by_identifier, get_user_application, list_user_letters, upsert_user
from .main_base import LETTER_TYPE_LABELS, _compact_letter, _is_admin, _iso, _letter_notification_text, _row_value, _username
from .security import validate_webapp_init_data

router = APIRouter()

PLAYER_STATUS_LABELS = {
    "active": "активен",
    "low_activity": "малоактив",
    "frozen": "заморожен",
    "left": "выбыл",
    "watch": "под наблюдением",
}
AFFILIATIONS = ["двор", "суд", "полиция", "армия", "пресса", "медицина", "церковь", "город", "подполье", "рынок"]


def clean(value: Any, limit: int = 120, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail="пустое поле")
    if len(text) > limit:
        raise HTTPException(status_code=400, detail="поле слишком длинное")
    return text


def tags(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple)) else str(value or "").replace(";", ",").split(",")
    result: list[str] = []
    for item in raw:
        tag = str(item or "").strip()
        if tag and tag not in result:
            result.append(tag[:64])
    return result[:30]


def name(row: Any) -> str:
    return f"{_row_value(row, 'character_first_name', '') or ''} {_row_value(row, 'character_last_name', '') or ''}".strip() or "без имени"


def role(row: Any) -> str:
    return str(_row_value(row, "assigned_role") or _row_value(row, "owner_comment") or _row_value(row, "role_preference") or "—")


def app_payload(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    status = str(_row_value(row, "player_status", "active") or "active")
    return {
        "id": _row_value(row, "id"),
        "telegram_id": _row_value(row, "telegram_id"),
        "status": _row_value(row, "status"),
        "player_status": status,
        "player_status_label": PLAYER_STATUS_LABELS.get(status, status),
        "story_tags": tags(_row_value(row, "story_tags")),
        "character_name": name(row),
        "character_first_name": _row_value(row, "character_first_name"),
        "character_last_name": _row_value(row, "character_last_name"),
        "character_age": _row_value(row, "character_age"),
        "character_gender": _row_value(row, "character_gender"),
        "character_orientation": _row_value(row, "character_orientation"),
        "affiliation": _row_value(row, "affiliation"),
        "role_preference": _row_value(row, "role_preference"),
        "assigned_role": role(row),
        "character_description": _row_value(row, "character_description"),
        "character_personality": _row_value(row, "character_personality"),
        "roleplay_experience": _row_value(row, "roleplay_experience"),
        "applicant_comment": _row_value(row, "applicant_comment"),
        "owner_comment": _row_value(row, "owner_comment"),
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
        "reviewed_at": _iso(_row_value(row, "reviewed_at")),
    }


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS player_status TEXT NOT NULL DEFAULT 'active';")
        await conn.execute("ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS story_tags TEXT[] NOT NULL DEFAULT '{}'::TEXT[];")
        await conn.execute("ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS last_invited_at TIMESTAMPTZ;")
        await conn.execute("CREATE INDEX IF NOT EXISTS paris_applications_player_status_idx ON paris_applications (player_status);")
        await conn.execute("CREATE INDEX IF NOT EXISTS paris_applications_status_affiliation_idx ON paris_applications (status, affiliation);")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_admin_notes (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                admin_id BIGINT NOT NULL,
                body TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMPTZ
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_action_log (
                id BIGSERIAL PRIMARY KEY,
                admin_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id BIGINT,
                target_telegram_id BIGINT,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            UPDATE paris_applications
            SET status = 'rejected', owner_comment = 'test removed', assigned_role = '', player_status = 'left', updated_at = NOW()
            WHERE status <> 'rejected'
              AND (id = 1 OR telegram_id = 8252951161 OR lower(COALESCE(assigned_role, '')) = 'тест' OR lower(COALESCE(owner_comment, '')) = 'тест');
            """
        )


async def admin_body(request: Request) -> tuple[Config, asyncpg.Pool, dict[str, Any], int]:
    config: Config = request.app.state.config
    pool: asyncpg.Pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    admin_id = int(user["id"])
    if not _is_admin(config, admin_id):
        raise HTTPException(status_code=403, detail="нет доступа")
    await upsert_user(pool, user)
    await ensure_schema(pool)
    return config, pool, body, admin_id


async def user_body(request: Request) -> tuple[Config, asyncpg.Pool, dict[str, Any], dict[str, Any], int]:
    config: Config = request.app.state.config
    pool: asyncpg.Pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    await upsert_user(pool, user)
    await ensure_schema(pool)
    return config, pool, body, user, telegram_id


async def log_action(pool: asyncpg.Pool, admin_id: int, action: str, target_id: int | None = None) -> None:
    await pool.execute(
        "INSERT INTO paris_action_log (admin_id, action, target_type, target_telegram_id) VALUES ($1, $2, 'player', $3);",
        admin_id,
        action,
        target_id,
    )


@router.post("/api/profile")
async def api_profile(request: Request):
    config, pool, body, user, telegram_id = await user_body(request)
    application = await get_user_application(pool, telegram_id)
    letters = await list_user_letters(pool, telegram_id, limit=20)
    return {
        "ok": True,
        "profile": {"telegram_id": telegram_id, "username": user.get("username"), "is_admin": _is_admin(config, telegram_id)},
        "application": app_payload(application),
        "letters": [_compact_letter(row) for row in letters],
        "unread_letters": await count_unread_letters(pool, telegram_id),
        "links": {"chat": EVENT_CHAT_URL, "citySheet": CITY_SHEET_URL},
    }


@router.post("/api/admin/players")
async def api_admin_players(request: Request):
    config, pool, body, admin_id = await admin_body(request)
    search = clean(body.get("search"), 120)
    affiliation = clean(body.get("affiliation"), 80)
    player_status = clean(body.get("player_status"), 40)
    rows = await pool.fetch(
        """
        SELECT a.*, u.username, u.first_name AS tg_first_name, u.last_name AS tg_last_name,
               (SELECT COUNT(*) FROM paris_letters l WHERE l.telegram_id = a.telegram_id AND l.status <> 'hidden') AS letters_count,
               (SELECT COUNT(*) FROM paris_admin_notes n WHERE n.telegram_id = a.telegram_id AND n.deleted_at IS NULL) AS notes_count
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE a.status = 'accepted'
          AND ($1::TEXT = '' OR a.affiliation = $1)
          AND ($2::TEXT = '' OR a.player_status = $2)
          AND ($3::TEXT = '' OR lower(COALESCE(u.username, '')) LIKE '%' || lower($3) || '%' OR lower(a.character_first_name || ' ' || a.character_last_name) LIKE '%' || lower($3) || '%' OR lower(COALESCE(a.assigned_role, a.role_preference, '')) LIKE '%' || lower($3) || '%')
        ORDER BY a.affiliation ASC, a.character_first_name ASC, a.character_last_name ASC
        LIMIT 300;
        """,
        affiliation,
        player_status,
        search,
    )
    players = []
    for row in rows:
        item = app_payload(row) or {}
        item.update({"username": _username(row), "letters_count": int(_row_value(row, "letters_count", 0) or 0), "notes_count": int(_row_value(row, "notes_count", 0) or 0)})
        players.append(item)
    return {"ok": True, "players": players, "player_statuses": PLAYER_STATUS_LABELS, "affiliations": AFFILIATIONS}


@router.post("/api/admin/player/update")
async def api_admin_player_update(request: Request):
    config, pool, body, admin_id = await admin_body(request)
    identifier = clean(body.get("identifier") or body.get("target"), 128, True)
    target = await find_user_by_identifier(pool, identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")
    target_id = int(target["telegram_id"])
    if await get_user_application(pool, target_id) is None:
        raise HTTPException(status_code=404, detail="анкета игрока не найдена")
    status = body.get("player_status")
    status_text = None
    if status is not None:
        status_text = clean(status, 40, True)
        if status_text not in PLAYER_STATUS_LABELS:
            raise HTTPException(status_code=400, detail="invalid player status")
    row = await pool.fetchrow(
        """
        UPDATE paris_applications
        SET assigned_role = COALESCE($2::TEXT, assigned_role),
            affiliation = COALESCE($3::TEXT, affiliation),
            player_status = COALESCE($4::TEXT, player_status),
            story_tags = COALESCE($5::TEXT[], story_tags),
            owner_comment = COALESCE($6::TEXT, owner_comment),
            updated_at = NOW()
        WHERE telegram_id = $1
        RETURNING *;
        """,
        target_id,
        None if body.get("assigned_role") is None else clean(body.get("assigned_role"), 160),
        None if body.get("affiliation") is None else clean(body.get("affiliation"), 80),
        status_text,
        None if body.get("story_tags") is None else tags(body.get("story_tags")),
        None if body.get("owner_comment") is None else clean(body.get("owner_comment"), 1200),
    )
    await log_action(pool, admin_id, "player_updated", target_id)
    return {"ok": True, "application": app_payload(row)}


@router.post("/api/admin/letters/group")
async def api_admin_letters_group(request: Request):
    config, pool, body, admin_id = await admin_body(request)
    group = clean(body.get("target_group") or body.get("group") or "all", 80, True)
    letter_type = clean(body.get("letter_type") or "letter", 40) or "letter"
    if letter_type not in LETTER_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="invalid letter type")
    title = clean(body.get("title"), 160) or LETTER_TYPE_LABELS[letter_type]
    text = clean(body.get("body"), 3500, True)
    rows = await pool.fetch(
        """
        SELECT a.*, u.username
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE a.status = 'accepted' AND a.player_status <> 'left' AND ($1::TEXT = 'all' OR a.affiliation = $1)
        ORDER BY a.affiliation ASC, a.character_first_name ASC
        LIMIT 300;
        """,
        group,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="получатели не найдены")
    notified = 0
    failed = 0
    for row in rows:
        target_id = int(row["telegram_id"])
        await create_letter(pool, telegram_id=target_id, sender_admin_id=admin_id, title=title, letter_type=letter_type, body=text)
        try:
            await request.app.state.telegram_app.bot.send_message(
                chat_id=target_id,
                text=_letter_notification_text(title, letter_type, text),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            notified += 1
        except Exception:
            failed += 1
    await log_action(pool, admin_id, "group_letter_sent")
    return {"ok": True, "recipients": len(rows), "letters_created": len(rows), "notified": notified, "notify_failed": failed}


@router.get("/api/public/accepted")
async def api_public_accepted(request: Request):
    pool: asyncpg.Pool = request.app.state.pool
    await ensure_schema(pool)
    rows = await pool.fetch(
        """
        SELECT a.*, u.username
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE a.status = 'accepted' AND a.player_status <> 'left'
        ORDER BY a.affiliation ASC, a.character_first_name ASC, a.character_last_name ASC;
        """
    )
    roles = []
    for row in rows:
        row_tags = tags(_row_value(row, "story_tags"))
        roles.append({"username": _username(row) or "без username", "name": name(row), "role": role(row), "group": str(_row_value(row, "affiliation") or "город"), "note": ", ".join(row_tags) if row_tags else role(row)})
    return {"ok": True, "count": len(roles), "roles": roles}


def profile_text(row: Any | None, unread: int = 0) -> str:
    if row is None:
        return "👤 <b>мой профиль</b>\n\nанкета пока не найдена. откройте кабинет и подайте анкету."
    player_status = str(value(row, "player_status", "active") or "active")
    row_tags = tags(value(row, "story_tags"))
    return (
        "👤 <b>мой профиль Le Nyan Paris</b>\n\n"
        f"<b>персонаж:</b> {escape(name(row))}\n"
        f"<b>роль:</b> {escape(role(row))}\n"
        f"<b>раздел:</b> {escape(str(value(row, 'affiliation', '—') or '—'))}\n"
        f"<b>статус:</b> {escape(str(value(row, 'status', '—')))}\n"
        f"<b>активность:</b> {escape(PLAYER_STATUS_LABELS.get(player_status, player_status))}\n"
        f"<b>письма:</b> {unread} непрочитанных\n\n"
        f"<b>возраст:</b> {escape(str(value(row, 'character_age', '—')))}\n"
        f"<b>пол:</b> {escape(str(value(row, 'character_gender', '—')))}\n"
        f"<b>ориентация:</b> {escape(str(value(row, 'character_orientation', '—')))}\n"
        f"<b>метки:</b> {escape(', '.join(row_tags) if row_tags else '—')}\n\n"
        f"<b>описание:</b>\n{escape(str(value(row, 'character_description', '—') or '—'))}\n\n"
        f"<b>характер:</b>\n{escape(str(value(row, 'character_personality', '—') or '—'))}"
    )


async def profile_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    await upsert_user(pool, user.to_dict())
    await ensure_schema(pool)
    row = await get_user_application(pool, user.id)
    unread = await count_unread_letters(pool, user.id)
    await message.reply_text(profile_text(row, unread), parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=main_menu_markup(config))


async def invite_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    await ensure_schema(pool)
    row = await get_user_application(pool, user.id)
    if row is None or value(row, "status") != "accepted":
        await message.reply_text("ссылка на чат выдается только принятым участникам.")
        return
    await message.reply_text(f"📜 <b>вход в чат события</b>\n\n{EVENT_CHAT_URL}\n\nгородской лист:\n{CITY_SHEET_URL}", parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def lettergroup_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await message.reply_text("формат:\n<code>/lettergroup all текст</code>\n<code>/lettergroup суд текст</code>", parse_mode=ParseMode.HTML)
        return
    await ensure_schema(pool)
    group = context.args[0].strip()
    text = " ".join(context.args[1:]).strip()
    rows = await pool.fetch("SELECT a.*, u.username FROM paris_applications a JOIN paris_users u ON u.telegram_id = a.telegram_id WHERE a.status = 'accepted' AND a.player_status <> 'left' AND ($1::TEXT = 'all' OR a.affiliation = $1) LIMIT 300;", group)
    if not rows:
        await message.reply_text("получатели не найдены")
        return
    ok = 0
    fail = 0
    for row in rows:
        target_id = int(row["telegram_id"])
        await create_letter(pool, telegram_id=target_id, sender_admin_id=int(user_id), body=text, title="письмо канцелярии", letter_type="letter")
        try:
            await context.bot.send_message(chat_id=target_id, text=_letter_notification_text("письмо канцелярии", "letter", text), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            ok += 1
        except Exception:
            fail += 1
    await message.reply_text(f"массовое письмо создано.\nполучателей: {len(rows)}\nуведомлено: {ok}\nошибок уведомления: {fail}")


def patch_player_features(main_base_module: Any) -> None:
    original_build_application = main_base_module.build_application

    def build_application_with_player_features(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        app.add_handler(CommandHandler("profile", profile_command), group=-2)
        app.add_handler(CommandHandler("myapp", profile_command), group=-2)
        app.add_handler(CommandHandler("invite", invite_command), group=-2)
        app.add_handler(CommandHandler("lettergroup", lettergroup_command), group=-2)
        return app

    async def set_bot_commands_with_player_features(app: Application) -> None:
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
            BotCommand("letters", "список писем"),
            BotCommand("letterstatus", "изменить статус письма"),
            BotCommand("lenyan", "объявление с отметками"),
        ])

    main_base_module.build_application = build_application_with_player_features
    main_base_module.set_bot_commands = set_bot_commands_with_player_features
