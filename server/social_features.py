from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from telegram.constants import ParseMode

from .config import Config
from .db import get_user_application, upsert_user
from .main_base import _is_admin, _iso, _row_value, _username
from .security import validate_webapp_init_data

router = APIRouter()

LOCATIONS = [
    "дворец",
    "суд и канцелярия",
    "полиция",
    "тюрьма",
    "газета и слухи",
    "рынок",
    "кафе и салон",
    "больница",
    "театр",
    "армия и гарнизон",
    "подполье и катакомбы",
    "улицы Парижа",
    "другое",
]

SCENE_TYPES = {
    "any": "любая сцена",
    "meeting": "знакомство",
    "calm": "спокойная сцена",
    "plot": "сюжетная сцена",
    "conflict": "конфликт",
    "dialogue": "разговор",
}

NPC_TYPES = [
    "любой NPC",
    "врач",
    "солдат",
    "жандарм",
    "слуга",
    "чиновник",
    "торговец",
    "священник",
    "горожанин",
    "другой",
]

NPC_STATUSES = {
    "new": "новая",
    "in_work": "в работе",
    "done": "выполнена",
    "rejected": "отклонена",
    "cancelled": "отменена",
}


def _clean(value: Any, limit: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail="заполни обязательное поле")
    if len(text) > limit:
        raise HTTPException(status_code=400, detail="текст слишком длинный")
    return text


async def ensure_social_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_coplay_requests (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                location TEXT NOT NULL,
                scene_type TEXT NOT NULL DEFAULT 'any',
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at TIMESTAMPTZ
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_coplay_responses (
                id BIGSERIAL PRIMARY KEY,
                request_id BIGINT NOT NULL REFERENCES paris_coplay_requests(id) ON DELETE CASCADE,
                responder_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (request_id, responder_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_npc_requests (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                npc_type TEXT NOT NULL,
                location TEXT NOT NULL,
                when_text TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'in_work', 'done', 'rejected', 'cancelled')),
                admin_comment TEXT NOT NULL DEFAULT '',
                assigned_admin_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_coplay_open_idx
            ON paris_coplay_requests (status, created_at DESC);
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_npc_status_idx
            ON paris_npc_requests (status, created_at DESC);
            """
        )


async def _auth(
    request: Request,
    *,
    admin: bool = False,
    accepted: bool = False,
) -> tuple[Config, asyncpg.Pool, dict[str, Any], dict[str, Any], int]:
    config: Config = request.app.state.config
    pool: asyncpg.Pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    telegram_id = int(user["id"])
    if admin and not _is_admin(config, telegram_id):
        raise HTTPException(status_code=403, detail="нет доступа")
    await upsert_user(pool, user)
    await ensure_social_schema(pool)

    if accepted and not admin:
        app = await get_user_application(pool, telegram_id)
        if app is None or str(_row_value(app, "status") or "") != "accepted":
            raise HTTPException(status_code=403, detail="раздел доступен после принятия анкеты")
        if str(_row_value(app, "player_status", "active") or "active") == "left":
            raise HTTPException(status_code=403, detail="участие завершено")

    return config, pool, body, user, telegram_id


def _character(row: Any) -> str:
    first = str(_row_value(row, "character_first_name", "") or "")
    last = str(_row_value(row, "character_last_name", "") or "")
    return f"{first} {last}".strip() or "без имени"


def _coplay_item(row: Any, viewer_id: int) -> dict[str, Any]:
    return {
        "id": int(_row_value(row, "id")),
        "telegram_id": int(_row_value(row, "telegram_id")),
        "username": _username(row),
        "character_name": _character(row),
        "assigned_role": _row_value(row, "assigned_role") or _row_value(row, "role_preference") or "",
        "affiliation": _row_value(row, "affiliation") or "",
        "location": _row_value(row, "location") or "",
        "scene_type": _row_value(row, "scene_type") or "any",
        "scene_type_label": SCENE_TYPES.get(str(_row_value(row, "scene_type") or "any"), str(_row_value(row, "scene_type") or "")),
        "note": _row_value(row, "note") or "",
        "responses_count": int(_row_value(row, "responses_count", 0) or 0),
        "responded": bool(_row_value(row, "responded", False)),
        "own": int(_row_value(row, "telegram_id")) == viewer_id,
        "created_at": _iso(_row_value(row, "created_at")),
    }


def _npc_item(row: Any) -> dict[str, Any]:
    status = str(_row_value(row, "status") or "new")
    return {
        "id": int(_row_value(row, "id")),
        "telegram_id": int(_row_value(row, "telegram_id")),
        "username": _username(row),
        "character_name": _character(row),
        "affiliation": _row_value(row, "affiliation") or "",
        "npc_type": _row_value(row, "npc_type") or "",
        "location": _row_value(row, "location") or "",
        "when_text": _row_value(row, "when_text") or "",
        "description": _row_value(row, "description") or "",
        "status": status,
        "status_label": NPC_STATUSES.get(status, status),
        "admin_comment": _row_value(row, "admin_comment") or "",
        "created_at": _iso(_row_value(row, "created_at")),
        "updated_at": _iso(_row_value(row, "updated_at")),
    }


async def _notify_admins(request: Request, config: Config, text: str) -> None:
    bot = request.app.state.telegram_app.bot
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            print(f"failed to notify admin {admin_id}: {exc}")


@router.post("/api/game/coplay/list")
async def coplay_list(request: Request):
    config, pool, body, user, telegram_id = await _auth(request, accepted=True)
    rows = await pool.fetch(
        """
        SELECT r.*, u.username, a.character_first_name, a.character_last_name,
               a.assigned_role, a.role_preference, a.affiliation,
               (SELECT COUNT(*) FROM paris_coplay_responses pr WHERE pr.request_id = r.id) AS responses_count,
               EXISTS(
                   SELECT 1 FROM paris_coplay_responses pr
                   WHERE pr.request_id = r.id AND pr.responder_id = $1
               ) AS responded
        FROM paris_coplay_requests r
        JOIN paris_users u ON u.telegram_id = r.telegram_id
        JOIN paris_applications a ON a.telegram_id = r.telegram_id
        WHERE r.status = 'open'
          AND a.status = 'accepted'
          AND COALESCE(a.player_status, 'active') <> 'left'
        ORDER BY CASE WHEN r.telegram_id = $1 THEN 0 ELSE 1 END, r.created_at DESC
        LIMIT 100;
        """,
        telegram_id,
    )
    return {
        "ok": True,
        "requests": [_coplay_item(row, telegram_id) for row in rows],
        "locations": LOCATIONS,
        "scene_types": SCENE_TYPES,
    }


@router.post("/api/game/coplay/create")
async def coplay_create(request: Request):
    config, pool, body, user, telegram_id = await _auth(request, accepted=True)
    location = _clean(body.get("location"), 120, True)
    scene_type = _clean(body.get("scene_type") or "any", 40, True)
    note = _clean(body.get("note"), 800)

    if scene_type not in SCENE_TYPES:
        raise HTTPException(status_code=400, detail="неверный тип сцены")

    existing = await pool.fetchval(
        "SELECT id FROM paris_coplay_requests WHERE telegram_id = $1 AND status = 'open' LIMIT 1;",
        telegram_id,
    )
    if existing:
        raise HTTPException(status_code=409, detail="у тебя уже есть активный поиск — сначала закрой его")

    row = await pool.fetchrow(
        """
        INSERT INTO paris_coplay_requests (telegram_id, location, scene_type, note)
        VALUES ($1, $2, $3, $4)
        RETURNING *;
        """,
        telegram_id,
        location,
        scene_type,
        note,
    )
    return {"ok": True, "request_id": int(row["id"])}


@router.post("/api/game/coplay/respond")
async def coplay_respond(request: Request):
    config, pool, body, user, telegram_id = await _auth(request, accepted=True)
    try:
        request_id = int(body.get("request_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="неверный поиск") from exc

    row = await pool.fetchrow(
        """
        SELECT r.*, u.username, a.character_first_name, a.character_last_name
        FROM paris_coplay_requests r
        JOIN paris_users u ON u.telegram_id = r.telegram_id
        JOIN paris_applications a ON a.telegram_id = r.telegram_id
        WHERE r.id = $1;
        """,
        request_id,
    )
    if row is None or str(row["status"]) != "open":
        raise HTTPException(status_code=404, detail="поиск уже закрыт")
    owner_id = int(row["telegram_id"])
    if owner_id == telegram_id:
        raise HTTPException(status_code=400, detail="нельзя откликнуться на свой поиск")

    inserted = await pool.fetchrow(
        """
        INSERT INTO paris_coplay_responses (request_id, responder_id)
        VALUES ($1, $2)
        ON CONFLICT (request_id, responder_id) DO NOTHING
        RETURNING id;
        """,
        request_id,
        telegram_id,
    )
    if inserted is None:
        raise HTTPException(status_code=409, detail="ты уже откликнулся")

    responder_app = await get_user_application(pool, telegram_id)
    responder_name = _character(responder_app) if responder_app else str(user.get("first_name") or "участник")
    owner_name = _character(row)

    bot = request.app.state.telegram_app.bot
    try:
        await bot.send_message(
            chat_id=owner_id,
            text=(
                "🎭 <b>отклик на поиск соигрока</b>\n\n"
                f"<a href=\"tg://user?id={telegram_id}\">{escape(responder_name)}</a> хочет присоединиться к твоей сцене.\n"
                f"локация: <b>{escape(str(row['location']))}</b>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        print(f"failed to notify coplay owner {owner_id}: {exc}")

    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=(
                "🎭 <b>отклик отправлен</b>\n\n"
                f"ты откликнулся на поиск игрока "
                f"<a href=\"tg://user?id={owner_id}\">{escape(owner_name)}</a>."
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        print(f"failed to confirm coplay response {telegram_id}: {exc}")

    return {"ok": True}


@router.post("/api/game/coplay/close")
async def coplay_close(request: Request):
    config, pool, body, user, telegram_id = await _auth(request, accepted=True)
    try:
        request_id = int(body.get("request_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="неверный поиск") from exc

    row = await pool.fetchrow(
        """
        UPDATE paris_coplay_requests
        SET status = 'closed', closed_at = NOW(), updated_at = NOW()
        WHERE id = $1 AND telegram_id = $2 AND status = 'open'
        RETURNING id;
        """,
        request_id,
        telegram_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="активный поиск не найден")
    return {"ok": True}


@router.post("/api/game/npc/list")
async def npc_list(request: Request):
    config, pool, body, user, telegram_id = await _auth(request, accepted=True)
    rows = await pool.fetch(
        """
        SELECT n.*, u.username, a.character_first_name, a.character_last_name, a.affiliation
        FROM paris_npc_requests n
        JOIN paris_users u ON u.telegram_id = n.telegram_id
        JOIN paris_applications a ON a.telegram_id = n.telegram_id
        WHERE n.telegram_id = $1
        ORDER BY n.created_at DESC
        LIMIT 50;
        """,
        telegram_id,
    )
    return {
        "ok": True,
        "requests": [_npc_item(row) for row in rows],
        "locations": LOCATIONS,
        "npc_types": NPC_TYPES,
        "statuses": NPC_STATUSES,
    }


@router.post("/api/game/npc/create")
async def npc_create(request: Request):
    config, pool, body, user, telegram_id = await _auth(request, accepted=True)
    npc_type = _clean(body.get("npc_type"), 120, True)
    location = _clean(body.get("location"), 120, True)
    when_text = _clean(body.get("when_text"), 160)
    description = _clean(body.get("description"), 1400, True)

    active_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM paris_npc_requests
        WHERE telegram_id = $1 AND status IN ('new', 'in_work');
        """,
        telegram_id,
    )
    if int(active_count or 0) >= 3:
        raise HTTPException(status_code=409, detail="у тебя уже 3 активные NPC-заявки")

    row = await pool.fetchrow(
        """
        INSERT INTO paris_npc_requests (telegram_id, npc_type, location, when_text, description)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *;
        """,
        telegram_id,
        npc_type,
        location,
        when_text,
        description,
    )

    app = await get_user_application(pool, telegram_id)
    character_name = _character(app) if app else str(user.get("first_name") or "участник")
    await _notify_admins(
        request,
        config,
        (
            "🎭 <b>новая NPC-заявка</b>\n\n"
            f"игрок: <a href=\"tg://user?id={telegram_id}\">{escape(character_name)}</a>\n"
            f"NPC: <b>{escape(npc_type)}</b>\n"
            f"локация: <b>{escape(location)}</b>\n"
            + (f"когда: {escape(when_text)}\n" if when_text else "")
            + f"\n{escape(description)}\n\n"
            f"заявка #{int(row['id'])}"
        ),
    )
    return {"ok": True, "request_id": int(row["id"])}


@router.post("/api/game/npc/cancel")
async def npc_cancel(request: Request):
    config, pool, body, user, telegram_id = await _auth(request, accepted=True)
    try:
        request_id = int(body.get("request_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="неверная заявка") from exc

    row = await pool.fetchrow(
        """
        UPDATE paris_npc_requests
        SET status = 'cancelled', updated_at = NOW()
        WHERE id = $1 AND telegram_id = $2 AND status IN ('new', 'in_work')
        RETURNING id;
        """,
        request_id,
        telegram_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="активная заявка не найдена")
    return {"ok": True}


@router.post("/api/admin/npc/list")
async def admin_npc_list(request: Request):
    config, pool, body, user, admin_id = await _auth(request, admin=True)
    status = _clean(body.get("status") or "active", 30)
    if status == "active":
        condition = "n.status IN ('new', 'in_work')"
        args: list[Any] = []
    elif status == "all":
        condition = "TRUE"
        args = []
    elif status in NPC_STATUSES:
        condition = "n.status = $1"
        args = [status]
    else:
        raise HTTPException(status_code=400, detail="неверный статус")

    rows = await pool.fetch(
        f"""
        SELECT n.*, u.username, a.character_first_name, a.character_last_name, a.affiliation
        FROM paris_npc_requests n
        JOIN paris_users u ON u.telegram_id = n.telegram_id
        LEFT JOIN paris_applications a ON a.telegram_id = n.telegram_id
        WHERE {condition}
        ORDER BY
          CASE n.status WHEN 'new' THEN 0 WHEN 'in_work' THEN 1 ELSE 2 END,
          n.created_at ASC
        LIMIT 150;
        """,
        *args,
    )
    return {
        "ok": True,
        "requests": [_npc_item(row) for row in rows],
        "statuses": NPC_STATUSES,
    }


@router.post("/api/admin/npc/status")
async def admin_npc_status(request: Request):
    config, pool, body, user, admin_id = await _auth(request, admin=True)
    try:
        request_id = int(body.get("request_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="неверная заявка") from exc

    status = _clean(body.get("status"), 30, True)
    if status not in {"new", "in_work", "done", "rejected"}:
        raise HTTPException(status_code=400, detail="неверный статус")
    comment = _clean(body.get("admin_comment"), 800)

    row = await pool.fetchrow(
        """
        UPDATE paris_npc_requests
        SET status = $2, admin_comment = $3, assigned_admin_id = $4, updated_at = NOW()
        WHERE id = $1
        RETURNING *;
        """,
        request_id,
        status,
        comment,
        admin_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="заявка не найдена")

    try:
        await request.app.state.telegram_app.bot.send_message(
            chat_id=int(row["telegram_id"]),
            text=(
                "🎭 <b>NPC-заявка обновлена</b>\n\n"
                f"заявка #{request_id}\n"
                f"статус: <b>{escape(NPC_STATUSES.get(status, status))}</b>"
                + (f"\nкомментарий: {escape(comment)}" if comment else "")
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        print(f"failed to notify npc requester {row['telegram_id']}: {exc}")

    return {"ok": True, "status": status, "status_label": NPC_STATUSES.get(status, status)}
