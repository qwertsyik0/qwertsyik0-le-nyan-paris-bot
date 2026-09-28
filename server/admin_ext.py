from __future__ import annotations

import json
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from telegram.constants import ParseMode

from .bot import accepted_notification_text
from .config import Config
from .db import (
    create_letter,
    decide_application,
    find_user_by_identifier,
    get_admin_letter,
    get_application,
    get_user_application,
    list_admin_user_letters,
    update_letter_status,
    upsert_user,
)
from .main_base import (
    LETTER_TYPE_LABELS,
    _application_details,
    _compact_application,
    _compact_letter,
    _is_admin,
    _iso,
    _letter_notification_text,
    _row_value,
    _username,
)
from .security import validate_webapp_init_data

router = APIRouter()

APPLICATION_DECISION_ALIASES = {
    "accept": "accepted",
    "accepted": "accepted",
    "revise": "needs_changes",
    "needs_changes": "needs_changes",
    "reject": "rejected",
    "rejected": "rejected",
}

DEFAULT_TEMPLATES = [
    {
        "name": "повестка в суд",
        "letter_type": "summons",
        "title": "повестка в Императорский суд Парижа",
        "body": "Вам надлежит явиться в Императорский суд Парижа для дачи показаний. Неявка без уважительной причины будет передана жандармерии.",
    },
    {
        "name": "вызов к жандармерии",
        "letter_type": "summons",
        "title": "вызов от жандармерии Парижа",
        "body": "Вам надлежит явиться к представителю жандармерии Парижа для разбирательства по городскому делу.",
    },
    {
        "name": "тайное поручение",
        "letter_type": "task",
        "title": "тайное поручение",
        "body": "Это письмо предназначено только для вас. Полученные сведения не следует разглашать без разрешения администрации.",
    },
    {
        "name": "сюжетный слух",
        "letter_type": "rumor",
        "title": "слух, дошедший до ваших ушей",
        "body": "По Парижу ходит слух, который может оказаться полезным. Проверьте его осторожно и не раскрывайте источник без причины.",
    },
    {
        "name": "предупреждение игроку",
        "letter_type": "warning",
        "title": "предупреждение администрации",
        "body": "Администрация обращает ваше внимание на нарушение правил. Повторение ситуации может привести к ограничениям участия.",
    },
]


def _clean_text(value: Any, *, max_len: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail="required field is empty")
    if len(text) > max_len:
        raise HTTPException(status_code=400, detail="field is too long")
    return text


async def _admin_body(request: Request) -> tuple[Config, Any, dict[str, Any], dict[str, Any], int]:
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    admin_id = int(user["id"])
    if not _is_admin(config, admin_id):
        raise HTTPException(status_code=403, detail="нет доступа")
    await upsert_user(pool, user)
    await ensure_admin_ext_schema(pool)
    return config, pool, body, user, admin_id


async def ensure_admin_ext_schema(pool) -> None:
    async with pool.acquire() as conn:
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
            CREATE INDEX IF NOT EXISTS paris_admin_notes_user_idx
            ON paris_admin_notes (telegram_id, created_at DESC)
            WHERE deleted_at IS NULL;
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_letter_templates (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                letter_type TEXT NOT NULL DEFAULT 'letter',
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                created_by BIGINT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_letter_templates_active_idx
            ON paris_letter_templates (is_active, name);
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
            CREATE INDEX IF NOT EXISTS paris_action_log_created_idx
            ON paris_action_log (created_at DESC);
            """
        )
        for template in DEFAULT_TEMPLATES:
            await conn.execute(
                """
                INSERT INTO paris_letter_templates (name, letter_type, title, body, created_by)
                SELECT $1, $2, $3, $4, NULL
                WHERE NOT EXISTS (
                    SELECT 1 FROM paris_letter_templates WHERE lower(name) = lower($1) AND is_active = TRUE
                );
                """,
                template["name"],
                template["letter_type"],
                template["title"],
                template["body"],
            )


async def log_action(
    pool,
    *,
    admin_id: int,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    target_telegram_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO paris_action_log (admin_id, action, target_type, target_id, target_telegram_id, details)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb);
        """,
        admin_id,
        action,
        target_type,
        target_id,
        target_telegram_id,
        json.dumps(details or {}, ensure_ascii=False),
    )


def _compact_note(row: Any) -> dict[str, Any]:
    return {
        "id": _row_value(row, "id"),
        "telegram_id": _row_value(row, "telegram_id"),
        "admin_id": _row_value(row, "admin_id"),
        "body": _row_value(row, "body"),
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
    }


def _compact_template(row: Any) -> dict[str, Any]:
    return {
        "id": _row_value(row, "id"),
        "name": _row_value(row, "name"),
        "letter_type": _row_value(row, "letter_type"),
        "title": _row_value(row, "title"),
        "body": _row_value(row, "body"),
        "created_by": _row_value(row, "created_by"),
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
    }


def _compact_log(row: Any) -> dict[str, Any]:
    details = _row_value(row, "details") or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except Exception:
            details = {"raw": details}
    return {
        "id": _row_value(row, "id"),
        "admin_id": _row_value(row, "admin_id"),
        "action": _row_value(row, "action"),
        "target_type": _row_value(row, "target_type"),
        "target_id": _row_value(row, "target_id"),
        "target_telegram_id": _row_value(row, "target_telegram_id"),
        "details": details,
        "created_at": _iso(_row_value(row, "created_at")),
    }


async def _target_user(pool, identifier: str):
    target = await find_user_by_identifier(pool, identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")
    return target


async def _admin_player_payload(pool, target) -> dict[str, Any]:
    target_id = int(target["telegram_id"])
    application = await get_user_application(pool, target_id)
    letters = await list_admin_user_letters(pool, target_id, limit=80)
    notes = await pool.fetch(
        """
        SELECT * FROM paris_admin_notes
        WHERE telegram_id = $1 AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT 80;
        """,
        target_id,
    )
    logs = await pool.fetch(
        """
        SELECT * FROM paris_action_log
        WHERE target_telegram_id = $1
        ORDER BY created_at DESC
        LIMIT 40;
        """,
        target_id,
    )
    return {
        "player": {
            "telegram_id": target_id,
            "username": _username(target),
            "first_name": _row_value(target, "first_name"),
            "last_name": _row_value(target, "last_name"),
            "language_code": _row_value(target, "language_code"),
            "created_at": _iso(_row_value(target, "created_at")),
            "updated_at": _iso(_row_value(target, "updated_at")),
        },
        "application": _application_details(application),
        "letters": [_compact_letter(row, include_player=True) for row in letters],
        "notes": [_compact_note(row) for row in notes],
        "logs": [_compact_log(row) for row in logs],
    }


@router.post("/api/admin/applications/decision")
async def api_admin_application_decision(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    try:
        application_id = int(body.get("application_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid application id") from exc

    raw_status = str(body.get("status") or body.get("action") or "").strip()
    status = APPLICATION_DECISION_ALIASES.get(raw_status)
    if status is None:
        raise HTTPException(status_code=400, detail="invalid application status")

    comment = _clean_text(body.get("comment"), max_len=1200, required=False)
    assigned_role = _clean_text(body.get("assigned_role"), max_len=160, required=False)
    if status == "accepted" and not assigned_role:
        raise HTTPException(status_code=400, detail="assigned role is required")

    before = await get_application(pool, application_id)
    if before is None:
        raise HTTPException(status_code=404, detail="анкета не найдена")

    decided = await decide_application(
        pool,
        application_id,
        status=status,
        reviewer_id=admin_id,
        comment=comment,
        assigned_role=assigned_role if status == "accepted" else "",
    )
    if decided is None:
        raise HTTPException(status_code=404, detail="анкета не найдена")

    notify_ok = True
    try:
        telegram_app = request.app.state.telegram_app
        if status == "accepted":
            text = accepted_notification_text(assigned_role)
            await telegram_app.bot.send_message(
                chat_id=int(decided["telegram_id"]),
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        elif status == "needs_changes":
            await telegram_app.bot.send_message(
                chat_id=int(decided["telegram_id"]),
                text=(
                    "📜 <b>от императорской канцелярии</b>\n\n"
                    "вашу анкету нужно немного исправить.\n\n"
                    f"<b>комментарий:</b>\n{escape(comment or 'без комментария')}\n\n"
                    "откройте Mini App и отправьте анкету заново."
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        else:
            await telegram_app.bot.send_message(
                chat_id=int(decided["telegram_id"]),
                text=(
                    "📜 <b>от императорской канцелярии</b>\n\n"
                    "ваша анкета отклонена.\n\n"
                    f"<b>причина:</b>\n{escape(comment or 'без комментария')}"
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception as exc:
        notify_ok = False
        print(f"failed to notify application decision {application_id}: {exc}")

    full_row = await get_application(pool, application_id)
    await log_action(
        pool,
        admin_id=admin_id,
        action=f"application_{status}",
        target_type="application",
        target_id=application_id,
        target_telegram_id=int(decided["telegram_id"]),
        details={"from": _row_value(before, "status"), "to": status, "role": assigned_role, "comment": comment},
    )
    return {"ok": True, "notify_ok": notify_ok, "application": _compact_application(full_row or decided)}


@router.post("/api/admin/player/full")
async def api_admin_player_full(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    identifier = _clean_text(body.get("identifier") or body.get("target"), max_len=128)
    target = await _target_user(pool, identifier)
    payload = await _admin_player_payload(pool, target)
    return {"ok": True, **payload}


@router.post("/api/admin/notes/add")
async def api_admin_note_add(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    identifier = _clean_text(body.get("identifier") or body.get("target"), max_len=128)
    note_body = _clean_text(body.get("body"), max_len=2000)
    target = await _target_user(pool, identifier)
    target_id = int(target["telegram_id"])
    note = await pool.fetchrow(
        """
        INSERT INTO paris_admin_notes (telegram_id, admin_id, body)
        VALUES ($1, $2, $3)
        RETURNING *;
        """,
        target_id,
        admin_id,
        note_body,
    )
    await log_action(
        pool,
        admin_id=admin_id,
        action="admin_note_added",
        target_type="player",
        target_telegram_id=target_id,
        target_id=int(note["id"]),
        details={"body": note_body[:300]},
    )
    return {"ok": True, "note": _compact_note(note), **await _admin_player_payload(pool, target)}


@router.post("/api/admin/notes/delete")
async def api_admin_note_delete(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    try:
        note_id = int(body.get("note_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid note id") from exc
    note = await pool.fetchrow(
        """
        UPDATE paris_admin_notes
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE id = $1 AND deleted_at IS NULL
        RETURNING *;
        """,
        note_id,
    )
    if note is None:
        raise HTTPException(status_code=404, detail="заметка не найдена")
    await log_action(
        pool,
        admin_id=admin_id,
        action="admin_note_deleted",
        target_type="note",
        target_id=note_id,
        target_telegram_id=int(note["telegram_id"]),
    )
    return {"ok": True, "note": _compact_note(note)}


@router.post("/api/admin/templates")
async def api_admin_templates(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    rows = await pool.fetch(
        """
        SELECT * FROM paris_letter_templates
        WHERE is_active = TRUE
        ORDER BY name ASC;
        """
    )
    return {"ok": True, "templates": [_compact_template(row) for row in rows], "letter_types": LETTER_TYPE_LABELS}


@router.post("/api/admin/templates/save")
async def api_admin_template_save(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    template_id_raw = body.get("template_id")
    name = _clean_text(body.get("name"), max_len=120)
    letter_type = str(body.get("letter_type") or "letter").strip().lower()
    if letter_type not in LETTER_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="invalid letter type")
    title = _clean_text(body.get("title"), max_len=160, required=False) or LETTER_TYPE_LABELS[letter_type]
    template_body = _clean_text(body.get("body"), max_len=3500)

    if template_id_raw:
        try:
            template_id = int(template_id_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid template id") from exc
        row = await pool.fetchrow(
            """
            UPDATE paris_letter_templates
            SET name = $2, letter_type = $3, title = $4, body = $5, updated_at = NOW()
            WHERE id = $1 AND is_active = TRUE
            RETURNING *;
            """,
            template_id,
            name,
            letter_type,
            title,
            template_body,
        )
        action = "letter_template_updated"
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO paris_letter_templates (name, letter_type, title, body, created_by)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *;
            """,
            name,
            letter_type,
            title,
            template_body,
            admin_id,
        )
        action = "letter_template_created"
    if row is None:
        raise HTTPException(status_code=404, detail="шаблон не найден")
    await log_action(
        pool,
        admin_id=admin_id,
        action=action,
        target_type="template",
        target_id=int(row["id"]),
        details={"name": name, "letter_type": letter_type},
    )
    return {"ok": True, "template": _compact_template(row)}


@router.post("/api/admin/templates/delete")
async def api_admin_template_delete(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    try:
        template_id = int(body.get("template_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid template id") from exc
    row = await pool.fetchrow(
        """
        UPDATE paris_letter_templates
        SET is_active = FALSE, updated_at = NOW()
        WHERE id = $1 AND is_active = TRUE
        RETURNING *;
        """,
        template_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="шаблон не найден")
    await log_action(pool, admin_id=admin_id, action="letter_template_deleted", target_type="template", target_id=template_id)
    return {"ok": True}


@router.post("/api/admin/logs")
async def api_admin_logs(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    rows = await pool.fetch(
        """
        SELECT * FROM paris_action_log
        ORDER BY created_at DESC
        LIMIT 120;
        """
    )
    return {"ok": True, "logs": [_compact_log(row) for row in rows]}


@router.post("/api/admin/letters/send-template")
async def api_admin_letter_send_template(request: Request):
    config, pool, body, user, admin_id = await _admin_body(request)
    try:
        template_id = int(body.get("template_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid template id") from exc
    target_raw = _clean_text(body.get("target"), max_len=128)
    target = await _target_user(pool, target_raw)
    template = await pool.fetchrow(
        """
        SELECT * FROM paris_letter_templates
        WHERE id = $1 AND is_active = TRUE;
        """,
        template_id,
    )
    if template is None:
        raise HTTPException(status_code=404, detail="шаблон не найден")
    target_id = int(target["telegram_id"])
    letter = await create_letter(
        pool,
        telegram_id=target_id,
        sender_admin_id=admin_id,
        body=str(template["body"]),
        title=str(template["title"]),
        letter_type=str(template["letter_type"]),
    )
    notify_ok = True
    try:
        telegram_app = request.app.state.telegram_app
        await telegram_app.bot.send_message(
            chat_id=target_id,
            text=_letter_notification_text(str(template["title"]), str(template["letter_type"]), str(template["body"])),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        notify_ok = False
        print(f"failed to notify templated letter recipient {target_id}: {exc}")
    await log_action(
        pool,
        admin_id=admin_id,
        action="letter_sent_from_template",
        target_type="letter",
        target_id=int(letter["id"]),
        target_telegram_id=target_id,
        details={"template_id": template_id, "notify_ok": notify_ok},
    )
    full_row = await get_admin_letter(pool, int(letter["id"]))
    return {"ok": True, "notify_ok": notify_ok, "letter": _compact_letter(full_row or letter, include_player=True)}
