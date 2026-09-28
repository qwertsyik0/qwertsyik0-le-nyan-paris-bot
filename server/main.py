from __future__ import annotations

from . import main_base as _main_base
from .admin_ext import router as admin_ext_router
from .cleanup_router import router as cleanup_router
from .lenyan_ext import patch_lenyan_broadcast
from .main_base import app

patch_lenyan_broadcast(_main_base)

app.include_router(admin_ext_router)
app.include_router(cleanup_router)

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
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND a.status <> 'accepted'
                  AND a.id = 1
                  AND (
                    lower(COALESCE(u.username, '')) = 'hwhejd'
                    OR lower(COALESCE(a.character_first_name, '')) LIKE '%test%'
                    OR lower(COALESCE(a.character_first_name, '')) LIKE '%тест%'
                    OR lower(COALESCE(a.character_last_name, '')) LIKE '%test%'
                    OR lower(COALESCE(a.character_last_name, '')) LIKE '%тест%'
                    OR lower(COALESCE(a.role_preference, '')) LIKE '%test%'
                    OR lower(COALESCE(a.role_preference, '')) LIKE '%тест%'
                  );
                """
            )
            _test_application_cleanup_done = True
    return await call_next(request)
