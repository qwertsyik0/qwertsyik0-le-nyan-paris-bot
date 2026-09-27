from __future__ import annotations

from typing import Any

import asyncpg

APPLICATION_FIELDS = {
    "character_first_name",
    "character_last_name",
    "character_age",
    "character_gender",
    "character_orientation",
    "role_preference",
    "affiliation",
    "character_description",
    "character_personality",
    "roleplay_experience",
    "applicant_comment",
}


async def create_pool(database_url: str) -> asyncpg.Pool:
    # Render Postgres requires TLS. Keeping this in code avoids depending on
    # every DATABASE_URL value having sslmode=require appended manually.
    return await asyncpg.create_pool(database_url, min_size=1, max_size=5, ssl=True)


async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_users (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_applications (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'needs_changes')),
                character_first_name TEXT NOT NULL,
                character_last_name TEXT NOT NULL,
                character_age INT NOT NULL,
                character_gender TEXT NOT NULL,
                character_orientation TEXT NOT NULL,
                role_preference TEXT NOT NULL,
                affiliation TEXT NOT NULL,
                character_description TEXT NOT NULL,
                character_personality TEXT NOT NULL,
                roleplay_experience TEXT NOT NULL,
                applicant_comment TEXT NOT NULL DEFAULT '',
                owner_comment TEXT NOT NULL DEFAULT '',
                assigned_role TEXT NOT NULL DEFAULT '',
                reviewer_id BIGINT,
                reviewed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS paris_applications_one_current_per_user
            ON paris_applications (telegram_id);
            """
        )


async def upsert_user(pool: asyncpg.Pool, user: dict[str, Any]) -> None:
    await pool.execute(
        """
        INSERT INTO paris_users (telegram_id, username, first_name, last_name, language_code)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (telegram_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            language_code = EXCLUDED.language_code,
            updated_at = NOW();
        """,
        int(user["id"]),
        user.get("username"),
        user.get("first_name"),
        user.get("last_name"),
        user.get("language_code"),
    )


def _clean_text(value: Any, *, max_len: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError("required")
    if len(text) > max_len:
        raise ValueError("too_long")
    return text


def normalize_application(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["character_first_name"] = _clean_text(payload.get("character_first_name"), max_len=64)
    result["character_last_name"] = _clean_text(payload.get("character_last_name"), max_len=64)
    try:
        age = int(payload.get("character_age"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_age") from exc
    if age < 10 or age > 90:
        raise ValueError("invalid_age")
    result["character_age"] = age
    result["character_gender"] = _clean_text(payload.get("character_gender"), max_len=64)
    result["character_orientation"] = _clean_text(payload.get("character_orientation"), max_len=64)
    result["role_preference"] = _clean_text(payload.get("role_preference"), max_len=120)
    result["affiliation"] = _clean_text(payload.get("affiliation"), max_len=80)
    result["character_description"] = _clean_text(payload.get("character_description"), max_len=2500)
    result["character_personality"] = _clean_text(payload.get("character_personality"), max_len=1200)
    result["roleplay_experience"] = _clean_text(payload.get("roleplay_experience"), max_len=1200)
    result["applicant_comment"] = _clean_text(payload.get("applicant_comment"), max_len=1200, required=False)
    return result


async def submit_application(pool: asyncpg.Pool, telegram_id: int, payload: dict[str, Any]) -> asyncpg.Record:
    data = normalize_application(payload)
    existing = await pool.fetchrow("SELECT id, status FROM paris_applications WHERE telegram_id = $1", telegram_id)
    if existing and existing["status"] == "accepted":
        raise ValueError("already_accepted")

    return await pool.fetchrow(
        """
        INSERT INTO paris_applications (
            telegram_id,
            status,
            character_first_name,
            character_last_name,
            character_age,
            character_gender,
            character_orientation,
            role_preference,
            affiliation,
            character_description,
            character_personality,
            roleplay_experience,
            applicant_comment
        ) VALUES ($1, 'pending', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (telegram_id) DO UPDATE SET
            status = 'pending',
            character_first_name = EXCLUDED.character_first_name,
            character_last_name = EXCLUDED.character_last_name,
            character_age = EXCLUDED.character_age,
            character_gender = EXCLUDED.character_gender,
            character_orientation = EXCLUDED.character_orientation,
            role_preference = EXCLUDED.role_preference,
            affiliation = EXCLUDED.affiliation,
            character_description = EXCLUDED.character_description,
            character_personality = EXCLUDED.character_personality,
            roleplay_experience = EXCLUDED.roleplay_experience,
            applicant_comment = EXCLUDED.applicant_comment,
            owner_comment = '',
            assigned_role = '',
            reviewer_id = NULL,
            reviewed_at = NULL,
            updated_at = NOW()
        RETURNING *;
        """,
        telegram_id,
        data["character_first_name"],
        data["character_last_name"],
        data["character_age"],
        data["character_gender"],
        data["character_orientation"],
        data["role_preference"],
        data["affiliation"],
        data["character_description"],
        data["character_personality"],
        data["roleplay_experience"],
        data["applicant_comment"],
    )


async def get_application(pool: asyncpg.Pool, application_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT a.*, u.username, u.first_name AS tg_first_name, u.last_name AS tg_last_name
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE a.id = $1;
        """,
        application_id,
    )


async def get_user_application(pool: asyncpg.Pool, telegram_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow("SELECT * FROM paris_applications WHERE telegram_id = $1", telegram_id)


async def list_pending_applications(pool: asyncpg.Pool, limit: int = 10) -> list[asyncpg.Record]:
    rows = await pool.fetch(
        """
        SELECT a.*, u.username
        FROM paris_applications a
        JOIN paris_users u ON u.telegram_id = a.telegram_id
        WHERE a.status = 'pending'
        ORDER BY a.created_at ASC
        LIMIT $1;
        """,
        limit,
    )
    return list(rows)


async def decide_application(
    pool: asyncpg.Pool,
    application_id: int,
    *,
    status: str,
    reviewer_id: int,
    comment: str,
    assigned_role: str = "",
) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        UPDATE paris_applications
        SET
            status = $2,
            owner_comment = $3,
            assigned_role = $4,
            reviewer_id = $5,
            reviewed_at = NOW(),
            updated_at = NOW()
        WHERE id = $1
        RETURNING *;
        """,
        application_id,
        status,
        comment,
        assigned_role,
        reviewer_id,
    )
