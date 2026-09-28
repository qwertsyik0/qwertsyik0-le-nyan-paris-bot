from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

InitDbFunc = Callable[[asyncpg.Pool], Awaitable[None]]


async def cleanup_test_applications(pool: asyncpg.Pool) -> None:
    result = await pool.execute(
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
          );
        """
    )
    print(f"test application cleanup: {result}")


def wrap_init_db(original_init_db: InitDbFunc) -> InitDbFunc:
    async def wrapped_init_db(pool: asyncpg.Pool) -> None:
        await original_init_db(pool)
        await cleanup_test_applications(pool)

    return wrapped_init_db
