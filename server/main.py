from __future__ import annotations

from . import main_base as _main_base
from .admin_ext import router as admin_ext_router
from .cleanup_router import router as cleanup_router
from .lenyan_ext import patch_lenyan_broadcast
from .main_base import app
from .player_features import patch_player_features, router as player_features_router

patch_lenyan_broadcast(_main_base)
patch_player_features(_main_base)

app.include_router(admin_ext_router)
app.include_router(cleanup_router)
app.include_router(player_features_router)

_test_application_cleanup_done = False


@app.middleware("http")
async def hide_test_application_once(request, call_next):
    global _test_application_cleanup_done
    if not _test_application_cleanup_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                UPDATE paris_applications AS a
                SET
                    status = 'rejected',
                    owner_comment = 'test removed',
                    assigned_role = '',
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND a.status <> 'rejected'
                  AND (
                    a.id = 1
                    OR a.telegram_id = 8252951161
                    OR lower(COALESCE(u.username, '')) = 'hwhejd'
                    OR lower(COALESCE(a.character_first_name, '')) LIKE '%test%'
                    OR lower(COALESCE(a.character_first_name, '')) LIKE '%тест%'
                    OR lower(COALESCE(a.character_last_name, '')) LIKE '%test%'
                    OR lower(COALESCE(a.character_last_name, '')) LIKE '%тест%'
                    OR lower(COALESCE(a.role_preference, '')) LIKE '%test%'
                    OR lower(COALESCE(a.role_preference, '')) LIKE '%тест%'
                    OR lower(COALESCE(a.assigned_role, '')) = 'тест'
                    OR lower(COALESCE(a.owner_comment, '')) = 'тест'
                  );
                """
            )
            _test_application_cleanup_done = True
    return await call_next(request)
