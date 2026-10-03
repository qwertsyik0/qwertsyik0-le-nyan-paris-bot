from __future__ import annotations

import json
from html import escape
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from telegram.constants import ParseMode

from .config import Config
from .db import (
    create_letter,
    find_user_by_identifier,
    get_user_application,
    list_admin_user_letters,
    upsert_user,
)
from .main_base import (
    LETTER_TYPE_LABELS,
    _compact_letter,
    _is_admin,
    _iso,
    _letter_notification_text,
    _row_value,
    _username,
)
from .security import validate_webapp_init_data
from .warnings_ext import ensure_warning_schema, list_user_warnings, warning_payload

router = APIRouter()

PLAYER_STATUS_LABELS = {
    "active": "активен",
    "low_activity": "малоактив",
    "frozen": "заморожен",
    "left": "выбыл",
    "watch": "под наблюдением",
}
AFFILIATIONS = ["двор", "суд", "полиция", "армия", "пресса", "медицина", "церковь", "город", "подполье", "рынок"]


def _clean(value: Any, limit: int = 160, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail="пустое поле")
    if len(text) > limit:
        raise HTTPException(status_code=400, detail="поле слишком длинное")
    return text


def _tags(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple)) else str(value or "").replace(";", ",").split(",")
    out: list[str] = []
    for item in raw:
        tag = str(item or "").strip()
        if tag and tag not in out:
            out.append(tag[:64])
    return out[:30]


async def _admin_body(request: Request) -> tuple[Config, asyncpg.Pool, dict[str, Any], int]:
    config: Config = request.app.state.config
    pool: asyncpg.Pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    admin_id = int(user["id"])
    if not _is_admin(config, admin_id):
        raise HTTPException(status_code=403, detail="нет доступа")
    await upsert_user(pool, user)
    await ensure_power_schema(pool)
    return config, pool, body, admin_id


async def ensure_power_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS player_status TEXT NOT NULL DEFAULT 'active';")
        await conn.execute("ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS story_tags TEXT[] NOT NULL DEFAULT '{}'::TEXT[];")
        await conn.execute("ALTER TABLE paris_applications ADD COLUMN IF NOT EXISTS status_reason TEXT NOT NULL DEFAULT '';")
        await conn.execute("ALTER TABLE paris_letter_templates ADD COLUMN IF NOT EXISTS is_favorite BOOLEAN NOT NULL DEFAULT FALSE;")
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
    await ensure_warning_schema(pool)


async def _log(
    pool: asyncpg.Pool,
    *,
    admin_id: int,
    action: str,
    target_telegram_id: int | None = None,
    target_type: str = "player",
    target_id: int | None = None,
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


def _character_name(row: Any) -> str:
    first = str(_row_value(row, "character_first_name", "") or "")
    last = str(_row_value(row, "character_last_name", "") or "")
    return f"{first} {last}".strip() or "без имени"


def _player_item(row: Any) -> dict[str, Any]:
    player_status = str(_row_value(row, "player_status", "active") or "active")
    return {
        "telegram_id": int(_row_value(row, "telegram_id")),
        "username": _username(row),
        "character_name": _character_name(row),
        "application_status": _row_value(row, "status"),
        "player_status": player_status,
        "player_status_label": PLAYER_STATUS_LABELS.get(player_status, player_status),
        "status_reason": _row_value(row, "status_reason", "") or "",
        "assigned_role": _row_value(row, "assigned_role") or _row_value(row, "role_preference") or "",
        "affiliation": _row_value(row, "affiliation") or "",
        "story_tags": list(_row_value(row, "story_tags", []) or []),
        "letters_count": int(_row_value(row, "letters_count", 0) or 0),
        "notes_count": int(_row_value(row, "notes_count", 0) or 0),
        "warnings_count": int(_row_value(row, "warnings_count", 0) or 0),
    }


def _compact_template(row: Any) -> dict[str, Any]:
    return {
        "id": int(_row_value(row, "id")),
        "name": _row_value(row, "name"),
        "letter_type": _row_value(row, "letter_type"),
        "title": _row_value(row, "title"),
        "body": _row_value(row, "body"),
        "is_favorite": bool(_row_value(row, "is_favorite", False)),
        "created_by": _row_value(row, "created_by"),
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
    }


@router.post("/api/admin/dashboard")
async def api_admin_dashboard(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    counts = await pool.fetchrow(
        """
        SELECT
          (SELECT COUNT(*) FROM paris_applications WHERE status = 'pending') AS pending,
          (SELECT COUNT(*) FROM paris_applications WHERE status = 'accepted' AND player_status <> 'left') AS players,
          (SELECT COUNT(*) FROM paris_applications WHERE player_status = 'frozen') AS frozen,
          (SELECT COUNT(*) FROM paris_applications WHERE player_status = 'left') AS left_count,
          (SELECT COUNT(*) FROM paris_letters WHERE status IN ('new','in_work')) AS letters,
          (SELECT COUNT(*) FROM paris_admin_notes WHERE deleted_at IS NULL) AS notes,
          (SELECT COUNT(*) FROM paris_warnings WHERE revoked_at IS NULL) AS warnings,
          (SELECT COUNT(*) FROM paris_letter_templates WHERE is_active = TRUE) AS templates,
          (SELECT COUNT(*) FROM paris_action_log) AS logs;
        """
    )
    return {"ok": True, "counts": {key: int(value or 0) for key, value in dict(counts).items()}}


@router.post("/api/admin/search")
async def api_admin_search(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    search = _clean(body.get("search"), 120)
    affiliation = _clean(body.get("affiliation"), 80)
    player_status = _clean(body.get("player_status"), 40)
    rows = await pool.fetch(
        """
        SELECT a.*, u.username,
          (SELECT COUNT(*) FROM paris_letters l WHERE l.telegram_id = a.telegram_id AND l.status <> 'hidden') AS letters_count,
          (SELECT COUNT(*) FROM paris_admin_notes n WHERE n.telegram_id = a.telegram_id AND n.deleted_at IS NULL) AS notes_count,
          (SELECT COUNT(*) FROM paris_warnings w WHERE w.telegram_id = a.telegram_id AND w.revoked_at IS NULL) AS warnings_count
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE ($1::TEXT = '' OR a.affiliation = $1)
          AND ($2::TEXT = '' OR a.player_status = $2)
          AND (
            $3::TEXT = ''
            OR lower(COALESCE(u.username, '')) LIKE '%' || lower(trim(leading '@' from $3)) || '%'
            OR lower(a.character_first_name || ' ' || a.character_last_name) LIKE '%' || lower($3) || '%'
            OR lower(COALESCE(a.assigned_role, a.role_preference, '')) LIKE '%' || lower($3) || '%'
            OR a.telegram_id::TEXT LIKE '%' || $3 || '%'
          )
        ORDER BY
          CASE WHEN a.status = 'accepted' AND a.player_status <> 'left' THEN 0 ELSE 1 END,
          a.affiliation ASC,
          a.character_first_name ASC,
          a.character_last_name ASC
        LIMIT 300;
        """,
        affiliation,
        player_status,
        search,
    )
    return {
        "ok": True,
        "players": [_player_item(row) for row in rows],
        "player_statuses": PLAYER_STATUS_LABELS,
        "affiliations": AFFILIATIONS,
    }


async def _timeline(pool: asyncpg.Pool, target_id: int, application: Any | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def add(when: Any, event_type: str, title: str, text: str = "", meta: dict[str, Any] | None = None):
        if not when:
            return
        items.append({
            "_when": when,
            "created_at": _iso(when),
            "type": event_type,
            "title": title,
            "text": text,
            "meta": meta or {},
        })

    if application is not None:
        add(_row_value(application, "created_at"), "application", "анкета создана")
        if _row_value(application, "reviewed_at"):
            add(
                _row_value(application, "reviewed_at"),
                "application",
                "анкета рассмотрена",
                str(_row_value(application, "status") or ""),
            )

    letters = await list_admin_user_letters(pool, target_id, limit=100)
    for row in letters:
        add(
            _row_value(row, "created_at"),
            "letter",
            str(_row_value(row, "title") or "письмо"),
            str(_row_value(row, "body") or "")[:240],
            {"letter_type": _row_value(row, "letter_type"), "status": _row_value(row, "status")},
        )

    notes = await pool.fetch(
        """
        SELECT * FROM paris_admin_notes
        WHERE telegram_id = $1 AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 100;
        """,
        target_id,
    )
    for row in notes:
        add(_row_value(row, "created_at"), "note", "внутренняя заметка", str(_row_value(row, "body") or "")[:240])

    warnings = await list_user_warnings(pool, target_id, include_revoked=True)
    for row in warnings:
        payload = warning_payload(row)
        add(
            _row_value(row, "created_at"),
            "warning",
            str(payload.get("type_label") or "предупреждение"),
            str(payload.get("reason") or "")[:240],
            {"revoked_at": payload.get("revoked_at")},
        )

    logs = await pool.fetch(
        """
        SELECT * FROM paris_action_log
        WHERE target_telegram_id = $1
        ORDER BY created_at DESC LIMIT 120;
        """,
        target_id,
    )
    for row in logs:
        details = _row_value(row, "details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {"raw": details}
        add(
            _row_value(row, "created_at"),
            "admin",
            str(_row_value(row, "action") or "действие администрации"),
            "",
            details if isinstance(details, dict) else {},
        )

    items.sort(key=lambda item: item["_when"], reverse=True)
    for item in items:
        item.pop("_when", None)
    return items[:220]


@router.post("/api/admin/player/power")
async def api_admin_player_power(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    identifier = _clean(body.get("identifier") or body.get("target"), 128, True)
    target = await find_user_by_identifier(pool, identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")
    target_id = int(target["telegram_id"])
    application = await get_user_application(pool, target_id)
    if application is None:
        raise HTTPException(status_code=404, detail="анкета игрока не найдена")

    notes = await pool.fetch(
        """
        SELECT * FROM paris_admin_notes
        WHERE telegram_id = $1 AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 100;
        """,
        target_id,
    )
    warnings = await list_user_warnings(pool, target_id, include_revoked=True)
    letters = await list_admin_user_letters(pool, target_id, limit=100)

    player = {
        "telegram_id": target_id,
        "username": _username(target),
        "telegram_name": " ".join(filter(None, [_row_value(target, "first_name"), _row_value(target, "last_name")])) or "",
        "character_name": _character_name(application),
        "application_status": _row_value(application, "status"),
        "player_status": _row_value(application, "player_status") or "active",
        "player_status_label": PLAYER_STATUS_LABELS.get(str(_row_value(application, "player_status") or "active"), str(_row_value(application, "player_status") or "active")),
        "status_reason": _row_value(application, "status_reason", "") or "",
        "assigned_role": _row_value(application, "assigned_role") or "",
        "role_preference": _row_value(application, "role_preference") or "",
        "affiliation": _row_value(application, "affiliation") or "",
        "story_tags": list(_row_value(application, "story_tags", []) or []),
        "owner_comment": _row_value(application, "owner_comment") or "",
        "character_age": _row_value(application, "character_age"),
        "character_gender": _row_value(application, "character_gender"),
        "character_orientation": _row_value(application, "character_orientation"),
    }
    return {
        "ok": True,
        "player": player,
        "notes": [
            {
                "id": int(_row_value(row, "id")),
                "body": _row_value(row, "body"),
                "admin_id": _row_value(row, "admin_id"),
                "created_at": _iso(_row_value(row, "created_at")),
            }
            for row in notes
        ],
        "warnings": [warning_payload(row) for row in warnings],
        "letters": [_compact_letter(row, include_player=True) for row in letters],
        "timeline": await _timeline(pool, target_id, application),
        "affiliations": AFFILIATIONS,
        "player_statuses": PLAYER_STATUS_LABELS,
    }


@router.post("/api/admin/player/edit")
async def api_admin_player_edit(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    identifier = _clean(body.get("identifier") or body.get("target"), 128, True)
    target = await find_user_by_identifier(pool, identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")
    target_id = int(target["telegram_id"])
    before = await get_user_application(pool, target_id)
    if before is None:
        raise HTTPException(status_code=404, detail="анкета игрока не найдена")

    assigned_role = None if body.get("assigned_role") is None else _clean(body.get("assigned_role"), 160)
    affiliation = None if body.get("affiliation") is None else _clean(body.get("affiliation"), 80)
    if affiliation is not None and affiliation not in AFFILIATIONS:
        raise HTTPException(status_code=400, detail="неверный раздел")
    player_status = None if body.get("player_status") is None else _clean(body.get("player_status"), 40, True)
    if player_status is not None and player_status not in PLAYER_STATUS_LABELS:
        raise HTTPException(status_code=400, detail="неверный статус")
    status_reason = None if body.get("status_reason") is None else _clean(body.get("status_reason"), 300)
    story_tags = None if body.get("story_tags") is None else _tags(body.get("story_tags"))
    owner_comment = None if body.get("owner_comment") is None else _clean(body.get("owner_comment"), 1200)

    row = await pool.fetchrow(
        """
        UPDATE paris_applications
        SET assigned_role = COALESCE($2::TEXT, assigned_role),
            affiliation = COALESCE($3::TEXT, affiliation),
            player_status = COALESCE($4::TEXT, player_status),
            story_tags = COALESCE($5::TEXT[], story_tags),
            status_reason = COALESCE($6::TEXT, status_reason),
            owner_comment = COALESCE($7::TEXT, owner_comment),
            updated_at = NOW()
        WHERE telegram_id = $1
        RETURNING *;
        """,
        target_id,
        assigned_role,
        affiliation,
        player_status,
        story_tags,
        status_reason,
        owner_comment,
    )
    await _log(
        pool,
        admin_id=admin_id,
        action="player_profile_updated",
        target_telegram_id=target_id,
        details={
            "role_from": _row_value(before, "assigned_role") or "",
            "role_to": _row_value(row, "assigned_role") or "",
            "affiliation_from": _row_value(before, "affiliation") or "",
            "affiliation_to": _row_value(row, "affiliation") or "",
            "status_from": _row_value(before, "player_status") or "active",
            "status_to": _row_value(row, "player_status") or "active",
        },
    )
    return {"ok": True, "player": (await api_admin_player_power_payload(pool, target))["player"]}


async def api_admin_player_power_payload(pool: asyncpg.Pool, target: Any) -> dict[str, Any]:
    target_id = int(target["telegram_id"])
    application = await get_user_application(pool, target_id)
    if application is None:
        raise HTTPException(status_code=404, detail="анкета игрока не найдена")
    return {
        "player": {
            "telegram_id": target_id,
            "username": _username(target),
            "character_name": _character_name(application),
            "application_status": _row_value(application, "status"),
            "player_status": _row_value(application, "player_status") or "active",
            "player_status_label": PLAYER_STATUS_LABELS.get(str(_row_value(application, "player_status") or "active"), str(_row_value(application, "player_status") or "active")),
            "status_reason": _row_value(application, "status_reason", "") or "",
            "assigned_role": _row_value(application, "assigned_role") or "",
            "affiliation": _row_value(application, "affiliation") or "",
            "story_tags": list(_row_value(application, "story_tags", []) or []),
        }
    }


@router.post("/api/admin/player/status")
async def api_admin_player_status(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    identifier = _clean(body.get("identifier") or body.get("target"), 128, True)
    action = _clean(body.get("action") or body.get("player_status"), 40, True)
    target = await find_user_by_identifier(pool, identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="игрок не найден")
    target_id = int(target["telegram_id"])
    before = await get_user_application(pool, target_id)
    if before is None:
        raise HTTPException(status_code=404, detail="анкета игрока не найдена")

    if action == "restore":
        new_status = "active"
        application_status = "accepted"
        default_reason = "восстановлен"
    elif action in PLAYER_STATUS_LABELS:
        new_status = action
        application_status = None
        default_reason = "вышел по с/ж" if action == "left" else PLAYER_STATUS_LABELS[action]
    else:
        raise HTTPException(status_code=400, detail="неверное действие")

    reason = _clean(body.get("reason"), 300) or default_reason
    row = await pool.fetchrow(
        """
        UPDATE paris_applications
        SET player_status = $2,
            status_reason = $3,
            status = COALESCE($4::TEXT, status),
            updated_at = NOW()
        WHERE telegram_id = $1
        RETURNING *;
        """,
        target_id,
        new_status,
        reason,
        application_status,
    )

    await _log(
        pool,
        admin_id=admin_id,
        action=f"player_status_{action}",
        target_telegram_id=target_id,
        details={
            "from": _row_value(before, "player_status") or "active",
            "to": new_status,
            "reason": reason,
        },
    )

    notify_ok = None
    if bool(body.get("notify")):
        notify_ok = True
        try:
            label = PLAYER_STATUS_LABELS.get(new_status, new_status)
            await request.app.state.telegram_app.bot.send_message(
                chat_id=target_id,
                text=(
                    "📜 <b>изменение статуса участия</b>\n\n"
                    f"статус: <b>{escape(label)}</b>\n"
                    f"примечание: {escape(reason)}"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            notify_ok = False

    return {
        "ok": True,
        "notify_ok": notify_ok,
        "player_status": new_status,
        "player_status_label": PLAYER_STATUS_LABELS.get(new_status, new_status),
        "application_status": _row_value(row, "status"),
        "status_reason": reason,
    }


@router.post("/api/admin/letters/targets")
async def api_admin_letters_targets(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    raw_ids = body.get("target_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="выберите хотя бы одного игрока")
    try:
        target_ids = list(dict.fromkeys(int(item) for item in raw_ids))[:100]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="неверный список игроков") from exc

    letter_type = _clean(body.get("letter_type") or "letter", 40) or "letter"
    if letter_type not in LETTER_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="неверный тип письма")
    title = _clean(body.get("title"), 160) or LETTER_TYPE_LABELS[letter_type]
    text = _clean(body.get("body"), 3500, True)

    rows = await pool.fetch(
        """
        SELECT a.telegram_id, u.username
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE a.telegram_id = ANY($1::BIGINT[])
          AND a.status = 'accepted'
          AND a.player_status <> 'left'
        ORDER BY a.character_first_name ASC;
        """,
        target_ids,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="активные получатели не найдены")

    notified = 0
    failed = 0
    created = 0
    for row in rows:
        target_id = int(row["telegram_id"])
        await create_letter(
            pool,
            telegram_id=target_id,
            sender_admin_id=admin_id,
            title=title,
            letter_type=letter_type,
            body=text,
        )
        created += 1
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

    await _log(
        pool,
        admin_id=admin_id,
        action="targeted_bulk_letter_sent",
        target_type="letter",
        details={"target_ids": [int(row["telegram_id"]) for row in rows], "letter_type": letter_type, "title": title},
    )
    return {
        "ok": True,
        "recipients": len(rows),
        "letters_created": created,
        "notified": notified,
        "notify_failed": failed,
    }


@router.post("/api/admin/templates/power")
async def api_admin_templates_power(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    rows = await pool.fetch(
        """
        SELECT * FROM paris_letter_templates
        WHERE is_active = TRUE
        ORDER BY is_favorite DESC, lower(name) ASC;
        """
    )
    return {"ok": True, "templates": [_compact_template(row) for row in rows]}


@router.post("/api/admin/templates/favorite")
async def api_admin_template_favorite(request: Request):
    config, pool, body, admin_id = await _admin_body(request)
    try:
        template_id = int(body.get("template_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="неверный шаблон") from exc

    row = await pool.fetchrow(
        """
        UPDATE paris_letter_templates
        SET is_favorite = NOT is_favorite, updated_at = NOW()
        WHERE id = $1 AND is_active = TRUE
        RETURNING *;
        """,
        template_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="шаблон не найден")
    await _log(
        pool,
        admin_id=admin_id,
        action="template_favorite_toggled",
        target_type="template",
        target_id=template_id,
        details={"is_favorite": bool(row["is_favorite"])},
    )
    return {"ok": True, "template": _compact_template(row)}
