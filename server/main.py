from __future__ import annotations

from . import main_base as _main_base
from .activity_ext import patch_activity_features
from .admin_ext import router as admin_ext_router
from .ban_ext import patch_banned_access
from .branding_ext import patch_shadow_empire_branding
from .cleanup_router import router as cleanup_router
from .empire_style_ext import patch_empire_style
from .lenyan_ext import patch_lenyan_broadcast
from .main_base import app
from .player_features import patch_player_features, router as player_features_router
from .public_profile_ext import patch_public_profile_features
from .warnings_ext import patch_warning_features, router as warnings_ext_router

patch_lenyan_broadcast(_main_base)
patch_player_features(_main_base)
patch_warning_features(_main_base)
patch_banned_access(_main_base)
patch_activity_features(_main_base)
patch_public_profile_features(_main_base)
patch_empire_style(_main_base)
patch_shadow_empire_branding(_main_base)

app.include_router(admin_ext_router)
app.include_router(cleanup_router)
app.include_router(player_features_router)
app.include_router(warnings_ext_router)

_test_application_cleanup_done = False
_dead_luka_cleanup_done = False
_inactive_exclusions_cleanup_done = False
_tvorog_restore_done = False
_leya_health_promotion_done = False
_lona_army_promotion_done = False
_kira_name_patch_done = False
_derek_role_patch_done = False

INACTIVE_EXCLUDED_USERNAMES = (
    "limeksvins",
    "leya_666",
    "luka_vo1d",
    "communityr34",
    "mimilset",
    "salamsister",
    "ilovekapebebra",
    "sofiysheva",
    "sofiyusheva",
    "pixel_are_you_okay",
)


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


@app.middleware("http")
async def remove_dead_luka_application_once(request, call_next):
    global _dead_luka_cleanup_done
    if not _dead_luka_cleanup_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                DELETE FROM paris_applications AS a
                USING paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND lower(COALESCE(u.username, '')) = 'pixel_are_you_okay'
                  AND (
                    (
                      lower(COALESCE(a.character_first_name, '')) = 'лука'
                      AND lower(COALESCE(a.character_last_name, '')) = 'нортвест'
                    )
                    OR lower(COALESCE(a.role_preference, '')) LIKE '%жандарм%'
                    OR lower(COALESCE(a.assigned_role, '')) LIKE '%жандарм%'
                  );
                """
            )
            _dead_luka_cleanup_done = True
    return await call_next(request)


@app.middleware("http")
async def exclude_inactive_users_once(request, call_next):
    global _inactive_exclusions_cleanup_done
    if not _inactive_exclusions_cleanup_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                UPDATE paris_applications AS a
                SET
                    status = 'rejected',
                    owner_comment = 'исключен за бездействие',
                    assigned_role = '',
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND lower(COALESCE(u.username, '')) = ANY($1::text[])
                  AND a.status <> 'rejected';
                """,
                list(INACTIVE_EXCLUDED_USERNAMES),
            )
            _inactive_exclusions_cleanup_done = True
    return await call_next(request)


@app.middleware("http")
async def restore_tvorog_application_once(request, call_next):
    global _tvorog_restore_done
    if not _tvorog_restore_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                UPDATE paris_applications AS a
                SET
                    status = 'accepted',
                    owner_comment = 'восстановлена после ошибочного исключения',
                    assigned_role = 'придворная музыкантка',
                    affiliation = 'двор',
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND lower(COALESCE(u.username, '')) = 'tvorog_t';
                """
            )
            _tvorog_restore_done = True
    return await call_next(request)


@app.middleware("http")
async def promote_leya_health_role_once(request, call_next):
    global _leya_health_promotion_done
    if not _leya_health_promotion_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                UPDATE paris_applications AS a
                SET
                    assigned_role = 'главная попечительница лекарской части Парижа',
                    affiliation = 'медицина',
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND lower(COALESCE(u.username, '')) = 'leya_666'
                  AND a.status = 'accepted';
                """
            )
            _leya_health_promotion_done = True
    return await call_next(request)


@app.middleware("http")
async def promote_lona_army_role_once(request, call_next):
    global _lona_army_promotion_done
    if not _lona_army_promotion_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                UPDATE paris_applications AS a
                SET
                    assigned_role = 'главная распорядительница военного ведомства Парижа',
                    affiliation = 'армия',
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND lower(COALESCE(u.username, '')) = 'ewq1k'
                  AND a.status = 'accepted';
                """
            )
            _lona_army_promotion_done = True
    return await call_next(request)


@app.middleware("http")
async def rename_kira_application_once(request, call_next):
    global _kira_name_patch_done
    if not _kira_name_patch_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                UPDATE paris_applications AS a
                SET
                    character_first_name = 'Кира',
                    character_last_name = '',
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND lower(COALESCE(u.username, '')) = 'mommytil'
                  AND a.status = 'accepted';
                """
            )
            _kira_name_patch_done = True
    return await call_next(request)


@app.middleware("http")
async def update_derek_role_once(request, call_next):
    global _derek_role_patch_done
    if not _derek_role_patch_done:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                UPDATE paris_applications AS a
                SET
                    assigned_role = 'рядовой солдат императорской армии',
                    affiliation = 'армия',
                    updated_at = NOW()
                FROM paris_users AS u
                WHERE a.telegram_id = u.telegram_id
                  AND lower(COALESCE(u.username, '')) = ('inf' || '2cted')
                  AND a.status = 'accepted';
                """
            )
            _derek_role_patch_done = True
    return await call_next(request)
