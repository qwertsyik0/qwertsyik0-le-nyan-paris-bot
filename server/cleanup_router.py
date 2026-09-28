from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .config import Config
from .db import upsert_user
from .main_base import _is_admin
from .security import validate_webapp_init_data

router = APIRouter()


@router.post("/api/admin/cleanup/test-applications")
async def api_cleanup_test_applications(request: Request):
    config: Config = request.app.state.config
    pool = request.app.state.pool
    body: dict[str, Any] = await request.json()
    user = validate_webapp_init_data(str(body.get("initData") or ""), config.bot_token)
    admin_id = int(user["id"])
    if not _is_admin(config, admin_id):
        raise HTTPException(status_code=403, detail="нет доступа")
    await upsert_user(pool, user)

    rows = await pool.fetch(
        """
        DELETE FROM paris_applications a
        USING paris_users u
        WHERE u.telegram_id = a.telegram_id
          AND a.status <> 'accepted'
          AND (
            lower(coalesce(u.username, '')) IN ('hwhejd', 'test', 'tester')
            OR lower(coalesce(a.character_first_name, '')) IN ('test', 'тест')
            OR lower(coalesce(a.character_last_name, '')) IN ('test', 'тест')
            OR lower(coalesce(a.role_preference, '')) LIKE '%test%'
            OR lower(coalesce(a.character_description, '')) LIKE '%test%'
            OR lower(coalesce(a.applicant_comment, '')) LIKE '%test%'
          )
        RETURNING a.id, a.telegram_id, u.username, a.character_first_name, a.character_last_name;
        """
    )
    return {
        "ok": True,
        "deleted_count": len(rows),
        "deleted": [dict(row) for row in rows],
    }
