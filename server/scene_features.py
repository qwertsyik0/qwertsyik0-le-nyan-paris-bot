from __future__ import annotations

from html import escape
from typing import Any

import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Config
from .db import get_user_application, upsert_user
from .main_base import _row_value
from .presence_features import ensure_presence_schema


async def ensure_scene_schema(pool: asyncpg.Pool) -> None:
    await ensure_presence_schema(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_scenes (
                id BIGSERIAL PRIMARY KEY,
                creator_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                location TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                access_mode TEXT NOT NULL DEFAULT 'open'
                    CHECK (access_mode IN ('open', 'closed')),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'closed')),
                origin_chat_id BIGINT,
                origin_message_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at TIMESTAMPTZ
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_scene_members (
                scene_id BIGINT NOT NULL REFERENCES paris_scenes(id) ON DELETE CASCADE,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                member_role TEXT NOT NULL DEFAULT 'member'
                    CHECK (member_role IN ('owner', 'member')),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                left_at TIMESTAMPTZ,
                PRIMARY KEY (scene_id, telegram_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_scene_invites (
                scene_id BIGINT NOT NULL REFERENCES paris_scenes(id) ON DELETE CASCADE,
                target_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                invited_by BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (scene_id, target_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_scenes_status_created_idx
            ON paris_scenes (status, created_at DESC);
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_scene_members_user_active_idx
            ON paris_scene_members (telegram_id, is_active);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_scene_drafts (
                telegram_id BIGINT PRIMARY KEY REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                chat_id BIGINT NOT NULL,
                step TEXT NOT NULL DEFAULT 'title'
                    CHECK (step IN ('title', 'location', 'mode', 'description')),
                title TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                access_mode TEXT NOT NULL DEFAULT 'open'
                    CHECK (access_mode IN ('open', 'closed')),
                description TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )


async def _accepted(pool: asyncpg.Pool, telegram_id: int):
    await ensure_scene_schema(pool)
    row = await get_user_application(pool, telegram_id)
    if row is None or str(_row_value(row, "status") or "") != "accepted":
        return None
    if str(_row_value(row, "player_status", "active") or "active") == "left":
        return None
    return row


def _character_name(row: Any | None) -> str:
    if row is None:
        return "участник"
    first = str(_row_value(row, "character_first_name", "") or "")
    last = str(_row_value(row, "character_last_name", "") or "")
    return f"{first} {last}".strip() or "участник"


async def _active_scene_for_user(pool: asyncpg.Pool, telegram_id: int):
    return await pool.fetchrow(
        """
        SELECT s.*, m.member_role
        FROM paris_scene_members m
        JOIN paris_scenes s ON s.id = m.scene_id
        WHERE m.telegram_id = $1
          AND m.is_active = TRUE
          AND s.status = 'active'
        ORDER BY m.joined_at DESC
        LIMIT 1;
        """,
        telegram_id,
    )


async def _scene_members(pool: asyncpg.Pool, scene_id: int):
    return await pool.fetch(
        """
        SELECT m.telegram_id, m.member_role,
               a.character_first_name, a.character_last_name,
               u.username
        FROM paris_scene_members m
        JOIN paris_users u ON u.telegram_id = m.telegram_id
        LEFT JOIN paris_applications a ON a.telegram_id = m.telegram_id
        WHERE m.scene_id = $1 AND m.is_active = TRUE
        ORDER BY CASE WHEN m.member_role = 'owner' THEN 0 ELSE 1 END, m.joined_at ASC;
        """,
        scene_id,
    )


def _message_link(chat_id: int | None, message_id: int | None) -> str | None:
    if chat_id is None or message_id is None:
        return None
    chat_text = str(chat_id)
    if chat_text.startswith("-100"):
        internal = chat_text[4:]
        if internal:
            return f"https://t.me/c/{internal}/{int(message_id)}"
    return None


async def _scene_text(pool: asyncpg.Pool, scene: Any) -> str:
    members = await _scene_members(pool, int(scene["id"]))
    names = []
    for member in members:
        first = str(_row_value(member, "character_first_name", "") or "")
        last = str(_row_value(member, "character_last_name", "") or "")
        display = f"{first} {last}".strip()
        if not display:
            username = str(_row_value(member, "username", "") or "")
            display = f"@{username}" if username else f"id {member['telegram_id']}"
        names.append(display)

    mode = "открытая" if str(scene["access_mode"]) == "open" else "закрытая"
    members_text = "\n".join(f"• {escape(name)}" for name in names) or "• пока никого"

    return (
        f"🎭 <b>сцена #{int(scene['id'])}</b>\n\n"
        f"<b>{escape(str(scene['title']))}</b>\n"
        f"📍 {escape(str(scene['location']))}\n"
        f"🔐 {mode}\n\n"
        f"{escape(str(scene['description'] or 'без описания'))}\n\n"
        f"<b>участники:</b>\n{members_text}"
    )


async def _scene_markup(pool: asyncpg.Pool, scene: Any, viewer_id: int | None = None) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    scene_id = int(scene["id"])
    is_creator = viewer_id is not None and int(_row_value(scene, "creator_id")) == int(viewer_id)
    is_member = False
    if viewer_id is not None:
        is_member = bool(await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM paris_scene_members WHERE scene_id = $1 AND telegram_id = $2 AND is_active = TRUE);",
            scene_id,
            int(viewer_id),
        ))

    if str(scene["status"]) == "active" and str(scene["access_mode"]) == "open" and not is_member:
        buttons.append([InlineKeyboardButton("присоединиться", callback_data=f"scene:join:{scene_id}")])

    link = _message_link(_row_value(scene, "origin_chat_id"), _row_value(scene, "origin_message_id"))
    if link:
        buttons.append([InlineKeyboardButton("перейти к сцене", url=link)])

    if is_creator:
        buttons.append([InlineKeyboardButton("закрыть сцену", callback_data=f"scene:close:{scene_id}")])
    elif viewer_id is not None and is_member:
        buttons.append([InlineKeyboardButton("покинуть сцену", callback_data=f"scene:leave:{scene_id}")])

    return InlineKeyboardMarkup(buttons)


async def _set_in_scene(pool: asyncpg.Pool, telegram_id: int, location: str) -> None:
    await pool.execute(
        """
        UPDATE paris_applications
        SET character_status = 'in_scene',
            current_location = $2,
            presence_updated_at = NOW(),
            updated_at = NOW()
        WHERE telegram_id = $1;
        """,
        telegram_id,
        location,
    )


async def _set_free_if_in_scene(pool: asyncpg.Pool, telegram_id: int) -> None:
    await pool.execute(
        """
        UPDATE paris_applications
        SET character_status = CASE WHEN character_status = 'in_scene' THEN 'free' ELSE character_status END,
            presence_updated_at = NOW(),
            updated_at = NOW()
        WHERE telegram_id = $1;
        """,
        telegram_id,
    )


async def _join_scene(pool: asyncpg.Pool, scene_id: int, telegram_id: int, *, invited: bool = False):
    scene = await pool.fetchrow("SELECT * FROM paris_scenes WHERE id = $1;", scene_id)
    if scene is None or str(scene["status"]) != "active":
        raise ValueError("эта сцена уже завершена")

    if str(scene["access_mode"]) == "closed" and not invited and int(scene["creator_id"]) != telegram_id:
        raise PermissionError("это закрытая сцена — войти можно только по приглашению")

    current = await _active_scene_for_user(pool, telegram_id)
    if current is not None and int(current["id"]) != scene_id:
        raise ValueError(f"ты уже участвуешь в сцене #{int(current['id'])}")

    await pool.execute(
        """
        INSERT INTO paris_scene_members (scene_id, telegram_id, member_role, is_active, joined_at, left_at)
        VALUES ($1, $2, 'member', TRUE, NOW(), NULL)
        ON CONFLICT (scene_id, telegram_id) DO UPDATE SET
            is_active = TRUE,
            joined_at = NOW(),
            left_at = NULL;
        """,
        scene_id,
        telegram_id,
    )
    await _set_in_scene(pool, telegram_id, str(scene["location"]))
    return scene


async def _close_scene(pool: asyncpg.Pool, scene_id: int, requester_id: int):
    scene = await pool.fetchrow("SELECT * FROM paris_scenes WHERE id = $1;", scene_id)
    if scene is None:
        raise ValueError("сцена не найдена")
    if int(scene["creator_id"]) != requester_id:
        raise PermissionError("закрыть сцену может только её создатель")
    if str(scene["status"]) != "active":
        raise ValueError("сцена уже завершена")

    members = await pool.fetch(
        "SELECT telegram_id FROM paris_scene_members WHERE scene_id = $1 AND is_active = TRUE;",
        scene_id,
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE paris_scenes
                SET status = 'closed', closed_at = NOW(), updated_at = NOW()
                WHERE id = $1;
                """,
                scene_id,
            )
            await conn.execute(
                """
                UPDATE paris_scene_members
                SET is_active = FALSE, left_at = NOW()
                WHERE scene_id = $1 AND is_active = TRUE;
                """,
                scene_id,
            )
            await conn.execute(
                """
                UPDATE paris_scene_invites
                SET status = 'cancelled', updated_at = NOW()
                WHERE scene_id = $1 AND status = 'pending';
                """,
                scene_id,
            )

    for member in members:
        await _set_free_if_in_scene(pool, int(member["telegram_id"]))
    return scene


def _scene_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("создать сцену", callback_data="scene:new")],
            [
                InlineKeyboardButton("моя сцена", callback_data="scene:mine"),
                InlineKeyboardButton("открытые", callback_data="scene:open"),
            ],
        ]
    )


async def _send_scene_menu(message) -> None:
    await message.reply_text(
        "🎭 <b>сцены</b>\n\n"
        "создавай отыгрыш, находи открытые сцены и возвращайся к своей текущей сцене.",
        parse_mode=ParseMode.HTML,
        reply_markup=_scene_menu(),
    )


async def _start_draft(message, user_id: int, pool: asyncpg.Pool) -> None:
    if getattr(message.chat, "type", None) not in {"group", "supergroup"}:
        await message.reply_text("создавать сцену нужно в основной группе.")
        return
    await ensure_scene_schema(pool)
    await pool.execute(
        """
        INSERT INTO paris_scene_drafts (telegram_id, chat_id, step, title, location, access_mode, description)
        VALUES ($1, $2, 'title', '', '', 'open', '')
        ON CONFLICT (telegram_id) DO UPDATE SET
            chat_id = EXCLUDED.chat_id,
            step = 'title',
            title = '',
            location = '',
            access_mode = 'open',
            description = '',
            updated_at = NOW();
        """,
        user_id,
        int(message.chat_id),
    )
    await message.reply_text(
        "🎭 <b>создание сцены</b>\n\n"
        "1/4 · напиши название сцены.\n\n"
        "для отмены: <code>Отмена сцены</code>",
        parse_mode=ParseMode.HTML,
    )


async def _finish_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict[str, Any]) -> None:
    message = update.effective_message
    user = update.effective_user
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    if message is None or user is None:
        return

    current = await _active_scene_for_user(pool, int(user.id))
    if current is not None:
        await pool.execute("DELETE FROM paris_scene_drafts WHERE telegram_id = $1;", int(user.id))
        await message.reply_text(f"ты уже участвуешь в сцене #{int(current['id'])}. сначала выйди или закрой её.")
        return

    row = await pool.fetchrow(
        """
        INSERT INTO paris_scenes (
            creator_id, title, location, description, access_mode, origin_chat_id
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *;
        """,
        int(user.id),
        draft["title"],
        draft["location"],
        draft["description"],
        draft["access_mode"],
        int(message.chat_id),
    )

    scene_id = int(row["id"])
    await pool.execute(
        """
        INSERT INTO paris_scene_members (scene_id, telegram_id, member_role, is_active)
        VALUES ($1, $2, 'owner', TRUE)
        ON CONFLICT (scene_id, telegram_id) DO UPDATE SET
            member_role = 'owner', is_active = TRUE, joined_at = NOW(), left_at = NULL;
        """,
        scene_id,
        int(user.id),
    )
    await _set_in_scene(pool, int(user.id), str(row["location"]))

    card = await message.reply_text(
        await _scene_text(pool, row),
        parse_mode=ParseMode.HTML,
        reply_markup=await _scene_markup(pool, row, int(user.id)),
        disable_web_page_preview=True,
    )
    await pool.execute(
        """
        UPDATE paris_scenes
        SET origin_message_id = $2, updated_at = NOW()
        WHERE id = $1;
        """,
        scene_id,
        int(card.message_id),
    )
    await pool.execute("DELETE FROM paris_scene_drafts WHERE telegram_id = $1;", int(user.id))

    if str(row["access_mode"]) == "closed":
        await message.reply_text(
            "сцена создана как закрытая. чтобы пригласить человека, ответь на его сообщение текстом "
            "<code>Пригласить в сцену</code>.",
            parse_mode=ParseMode.HTML,
        )


async def scene_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = message.from_user if message is not None else None
    if message is None or user is None or not message.text:
        return

    text = " ".join(message.text.strip().split())
    lowered = text.casefold()
    user_id = int(user.id)
    pool: asyncpg.Pool = context.application.bot_data["pool"]

    await ensure_scene_schema(pool)
    draft = await pool.fetchrow(
        "SELECT * FROM paris_scene_drafts WHERE telegram_id = $1;",
        user_id,
    )
    if draft is not None:
        if int(draft["chat_id"]) != int(message.chat_id):
            return
        if lowered == "отмена сцены":
            await pool.execute("DELETE FROM paris_scene_drafts WHERE telegram_id = $1;", user_id)
            await message.reply_text("создание сцены отменено.")
            raise ApplicationHandlerStop

        step = str(draft["step"])
        if step == "title":
            if len(text) > 100:
                await message.reply_text("название слишком длинное. максимум 100 символов.")
                raise ApplicationHandlerStop
            await pool.execute(
                """
                UPDATE paris_scene_drafts
                SET title = $2, step = 'location', updated_at = NOW()
                WHERE telegram_id = $1;
                """,
                user_id,
                text,
            )
            await message.reply_text(
                "2/4 · где проходит сцена?\n\nнапример: <code>дворец</code>, <code>больница</code>, "
                "<code>улицы Парижа</code> или своя локация.",
                parse_mode=ParseMode.HTML,
            )
            raise ApplicationHandlerStop

        if step == "location":
            if len(text) > 80:
                await message.reply_text("локация слишком длинная. максимум 80 символов.")
                raise ApplicationHandlerStop
            await pool.execute(
                """
                UPDATE paris_scene_drafts
                SET location = $2, step = 'mode', updated_at = NOW()
                WHERE telegram_id = $1;
                """,
                user_id,
                text,
            )
            await message.reply_text(
                "3/4 · сцена будет <b>открытая</b> или <b>закрытая</b>?\n\n"
                "напиши: <code>Открытая</code> или <code>Закрытая</code>.",
                parse_mode=ParseMode.HTML,
            )
            raise ApplicationHandlerStop

        if step == "mode":
            if lowered not in {"открытая", "открытый", "open", "закрытая", "закрытый", "closed"}:
                await message.reply_text("напиши только: <code>Открытая</code> или <code>Закрытая</code>.", parse_mode=ParseMode.HTML)
                raise ApplicationHandlerStop
            access_mode = "open" if lowered in {"открытая", "открытый", "open"} else "closed"
            await pool.execute(
                """
                UPDATE paris_scene_drafts
                SET access_mode = $2, step = 'description', updated_at = NOW()
                WHERE telegram_id = $1;
                """,
                user_id,
                access_mode,
            )
            await message.reply_text(
                "4/4 · коротко опиши, что происходит в сцене.\n\n"
                "до 700 символов.",
                parse_mode=ParseMode.HTML,
            )
            raise ApplicationHandlerStop

        if step == "description":
            if len(text) > 700:
                await message.reply_text("описание слишком длинное. максимум 700 символов.")
                raise ApplicationHandlerStop
            await pool.execute(
                """
                UPDATE paris_scene_drafts
                SET description = $2, updated_at = NOW()
                WHERE telegram_id = $1;
                """,
                user_id,
                text,
            )
            final_draft = await pool.fetchrow(
                "SELECT * FROM paris_scene_drafts WHERE telegram_id = $1;",
                user_id,
            )
            if final_draft is None:
                await message.reply_text("черновик сцены потерян. напиши <code>Создать сцену</code> ещё раз.", parse_mode=ParseMode.HTML)
                raise ApplicationHandlerStop
            await _finish_draft(update, context, final_draft)
            raise ApplicationHandlerStop

    commands = {
        "сцена",
        "создать сцену",
        "моя сцена",
        "мои сцены",
        "открытые сцены",
        "покинуть сцену",
        "закрыть сцену",
        "пригласить в сцену",
    }
    if lowered not in commands:
        return

    try:
        from .bot import waiting_admin_actions
        if user_id in waiting_admin_actions:
            return
    except Exception:
        pass

    await upsert_user(pool, user.to_dict())
    application = await _accepted(pool, user_id)
    if application is None:
        await message.reply_text("сцены доступны только принятым участникам.")
        raise ApplicationHandlerStop

    if lowered == "сцена":
        await _send_scene_menu(message)
        raise ApplicationHandlerStop

    if lowered == "создать сцену":
        current = await _active_scene_for_user(pool, user_id)
        if current is not None:
            await message.reply_text(f"ты уже участвуешь в сцене #{int(current['id'])}.")
        else:
            await _start_draft(message, user_id, pool)
        raise ApplicationHandlerStop

    if lowered == "моя сцена":
        scene = await _active_scene_for_user(pool, user_id)
        if scene is None:
            await message.reply_text("сейчас у тебя нет активной сцены.")
        else:
            await message.reply_text(
                await _scene_text(pool, scene),
                parse_mode=ParseMode.HTML,
                reply_markup=await _scene_markup(pool, scene, user_id),
                disable_web_page_preview=True,
            )
        raise ApplicationHandlerStop

    if lowered == "мои сцены":
        rows = await pool.fetch(
            """
            SELECT DISTINCT s.*
            FROM paris_scenes s
            JOIN paris_scene_members m ON m.scene_id = s.id
            WHERE m.telegram_id = $1
            ORDER BY s.updated_at DESC
            LIMIT 8;
            """,
            user_id,
        )
        if not rows:
            await message.reply_text("у тебя ещё не было сцен.")
        else:
            for scene in rows:
                await message.reply_text(
                    await _scene_text(pool, scene),
                    parse_mode=ParseMode.HTML,
                    reply_markup=await _scene_markup(pool, scene, user_id),
                    disable_web_page_preview=True,
                )
        raise ApplicationHandlerStop

    if lowered == "открытые сцены":
        rows = await pool.fetch(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM paris_scene_members m WHERE m.scene_id = s.id AND m.is_active = TRUE) AS members_count
            FROM paris_scenes s
            WHERE s.status = 'active' AND s.access_mode = 'open'
            ORDER BY s.updated_at DESC
            LIMIT 12;
            """
        )
        if not rows:
            await message.reply_text("открытых сцен сейчас нет.")
        else:
            for scene in rows:
                await message.reply_text(
                    await _scene_text(pool, scene),
                    parse_mode=ParseMode.HTML,
                    reply_markup=await _scene_markup(pool, scene, user_id),
                    disable_web_page_preview=True,
                )
        raise ApplicationHandlerStop

    if lowered == "покинуть сцену":
        scene = await _active_scene_for_user(pool, user_id)
        if scene is None:
            await message.reply_text("ты сейчас не участвуешь в сцене.")
        elif int(scene["creator_id"]) == user_id:
            await message.reply_text("ты создатель сцены. используй <code>Закрыть сцену</code>.", parse_mode=ParseMode.HTML)
        else:
            await pool.execute(
                """
                UPDATE paris_scene_members
                SET is_active = FALSE, left_at = NOW()
                WHERE scene_id = $1 AND telegram_id = $2;
                """,
                int(scene["id"]),
                user_id,
            )
            await _set_free_if_in_scene(pool, user_id)
            await message.reply_text(f"ты покинул сцену #{int(scene['id'])}.")
        raise ApplicationHandlerStop

    if lowered == "закрыть сцену":
        scene = await _active_scene_for_user(pool, user_id)
        if scene is None:
            await message.reply_text("активной сцены нет.")
        else:
            try:
                await _close_scene(pool, int(scene["id"]), user_id)
                await message.reply_text(f"сцена #{int(scene['id'])} завершена.")
            except (ValueError, PermissionError) as exc:
                await message.reply_text(str(exc))
        raise ApplicationHandlerStop

    if lowered == "пригласить в сцену":
        scene = await _active_scene_for_user(pool, user_id)
        if scene is None or int(scene["creator_id"]) != user_id:
            await message.reply_text("приглашать может только создатель активной сцены.")
            raise ApplicationHandlerStop
        if str(scene["access_mode"]) != "closed":
            await message.reply_text("приглашения нужны только для закрытых сцен.")
            raise ApplicationHandlerStop
        reply = message.reply_to_message
        target = reply.from_user if reply is not None else None
        if target is None or target.is_bot or int(target.id) == user_id:
            await message.reply_text("ответь командой <code>Пригласить в сцену</code> на сообщение нужного участника.", parse_mode=ParseMode.HTML)
            raise ApplicationHandlerStop

        await upsert_user(pool, target.to_dict())
        target_app = await _accepted(pool, int(target.id))
        if target_app is None:
            await message.reply_text("этот пользователь не является принятым участником.")
            raise ApplicationHandlerStop

        target_current = await _active_scene_for_user(pool, int(target.id))
        if target_current is not None:
            await message.reply_text(f"этот участник уже находится в сцене #{int(target_current['id'])}.")
            raise ApplicationHandlerStop

        await pool.execute(
            """
            INSERT INTO paris_scene_invites (scene_id, target_id, invited_by, status)
            VALUES ($1, $2, $3, 'pending')
            ON CONFLICT (scene_id, target_id) DO UPDATE SET
                invited_by = EXCLUDED.invited_by,
                status = 'pending',
                updated_at = NOW();
            """,
            int(scene["id"]),
            int(target.id),
            user_id,
        )

        invite_markup = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("принять", callback_data=f"scene:accept:{int(scene['id'])}"),
                InlineKeyboardButton("отказаться", callback_data=f"scene:decline:{int(scene['id'])}"),
            ]]
        )
        try:
            await context.bot.send_message(
                chat_id=int(target.id),
                text=(
                    f"🎭 <b>приглашение в сцену #{int(scene['id'])}</b>\n\n"
                    f"<b>{escape(str(scene['title']))}</b>\n"
                    f"📍 {escape(str(scene['location']))}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=invite_markup,
            )
            await message.reply_text("приглашение отправлено.")
        except Exception:
            await message.reply_text(
                "не удалось отправить приглашение в личку. участнику нужно сначала открыть бота."
            )
        raise ApplicationHandlerStop


async def scene_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    await upsert_user(pool, user.to_dict())
    application = await _accepted(pool, int(user.id))
    if application is None:
        await message.reply_text("сцены доступны только принятым участникам.")
        raise ApplicationHandlerStop
    await _send_scene_menu(message)
    raise ApplicationHandlerStop


async def scene_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not query.data or not query.data.startswith("scene:"):
        return

    pool: asyncpg.Pool = context.application.bot_data["pool"]
    await upsert_user(pool, user.to_dict())
    application = await _accepted(pool, int(user.id))
    if application is None:
        await query.answer("сцены доступны только принятым участникам", show_alert=True)
        raise ApplicationHandlerStop

    parts = query.data.split(":")
    action = parts[1]
    user_id = int(user.id)

    if action == "new":
        current = await _active_scene_for_user(pool, user_id)
        if current is not None:
            await query.answer(f"ты уже в сцене #{int(current['id'])}", show_alert=True)
        else:
            await query.answer()
            await _start_draft(query.message, user_id, pool)
        raise ApplicationHandlerStop

    if action == "mine":
        scene = await _active_scene_for_user(pool, user_id)
        await query.answer()
        if scene is None:
            await query.message.reply_text("сейчас у тебя нет активной сцены.")
        else:
            await query.message.reply_text(
                await _scene_text(pool, scene),
                parse_mode=ParseMode.HTML,
                reply_markup=await _scene_markup(pool, scene, user_id),
                disable_web_page_preview=True,
            )
        raise ApplicationHandlerStop

    if action == "open":
        await query.answer()
        rows = await pool.fetch(
            "SELECT * FROM paris_scenes WHERE status = 'active' AND access_mode = 'open' ORDER BY updated_at DESC LIMIT 12;"
        )
        if not rows:
            await query.message.reply_text("открытых сцен сейчас нет.")
        else:
            for scene in rows:
                await query.message.reply_text(
                    await _scene_text(pool, scene),
                    parse_mode=ParseMode.HTML,
                    reply_markup=await _scene_markup(pool, scene, user_id),
                    disable_web_page_preview=True,
                )
        raise ApplicationHandlerStop

    if len(parts) < 3:
        await query.answer("неверная команда", show_alert=True)
        raise ApplicationHandlerStop

    try:
        scene_id = int(parts[2])
    except ValueError:
        await query.answer("неверный номер сцены", show_alert=True)
        raise ApplicationHandlerStop

    if action == "join":
        try:
            scene = await _join_scene(pool, scene_id, user_id)
            await query.answer("ты присоединился к сцене", show_alert=True)
            await query.message.reply_text(
                await _scene_text(pool, scene),
                parse_mode=ParseMode.HTML,
                reply_markup=await _scene_markup(pool, scene, user_id),
                disable_web_page_preview=True,
            )
        except (ValueError, PermissionError) as exc:
            await query.answer(str(exc), show_alert=True)
        raise ApplicationHandlerStop

    if action == "leave":
        scene = await _active_scene_for_user(pool, user_id)
        if scene is None or int(scene["id"]) != scene_id:
            await query.answer("ты не участвуешь в этой сцене", show_alert=True)
        elif int(scene["creator_id"]) == user_id:
            await query.answer("создателю нужно закрыть сцену", show_alert=True)
        else:
            await pool.execute(
                "UPDATE paris_scene_members SET is_active = FALSE, left_at = NOW() WHERE scene_id = $1 AND telegram_id = $2;",
                scene_id,
                user_id,
            )
            await _set_free_if_in_scene(pool, user_id)
            await query.answer("ты покинул сцену", show_alert=True)
        raise ApplicationHandlerStop

    if action == "close":
        try:
            await _close_scene(pool, scene_id, user_id)
            await query.answer("сцена завершена", show_alert=True)
            if query.message:
                await query.message.reply_text(f"сцена #{scene_id} завершена.")
        except (ValueError, PermissionError) as exc:
            await query.answer(str(exc), show_alert=True)
        raise ApplicationHandlerStop

    if action in {"accept", "decline"}:
        invite = await pool.fetchrow(
            """
            SELECT
                i.scene_id,
                i.target_id,
                i.status AS invite_status,
                s.status AS scene_status,
                s.creator_id,
                s.title,
                s.location,
                s.access_mode,
                s.origin_chat_id,
                s.origin_message_id
            FROM paris_scene_invites i
            JOIN paris_scenes s ON s.id = i.scene_id
            WHERE i.scene_id = $1 AND i.target_id = $2;
            """,
            scene_id,
            user_id,
        )
        if invite is None or str(invite["invite_status"]) != "pending" or str(invite["scene_status"]) != "active":
            await query.answer("приглашение уже неактивно", show_alert=True)
            raise ApplicationHandlerStop

        if action == "decline":
            await pool.execute(
                "UPDATE paris_scene_invites SET status = 'declined', updated_at = NOW() WHERE scene_id = $1 AND target_id = $2;",
                scene_id,
                user_id,
            )
            await query.answer("приглашение отклонено", show_alert=True)
            if query.message:
                await query.message.edit_reply_markup(reply_markup=None)
            raise ApplicationHandlerStop

        try:
            scene = await _join_scene(pool, scene_id, user_id, invited=True)
            await pool.execute(
                "UPDATE paris_scene_invites SET status = 'accepted', updated_at = NOW() WHERE scene_id = $1 AND target_id = $2;",
                scene_id,
                user_id,
            )
            await query.answer("ты вступил в сцену", show_alert=True)
            if query.message:
                await query.message.edit_reply_markup(reply_markup=None)
                await query.message.reply_text(
                    await _scene_text(pool, scene),
                    parse_mode=ParseMode.HTML,
                    reply_markup=await _scene_markup(pool, scene, user_id),
                    disable_web_page_preview=True,
                )
        except (ValueError, PermissionError) as exc:
            await query.answer(str(exc), show_alert=True)
        raise ApplicationHandlerStop


def patch_scene_features(main_base_module: Any) -> None:
    if getattr(main_base_module.app.state, "scene_patch_installed", False):
        return

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        app.add_handler(CommandHandler("scene", scene_command), group=-900)
        app.add_handler(CallbackQueryHandler(scene_callback_handler, pattern=r"^scene:"), group=-900)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, scene_text_handler), group=-900)
        return app

    main_base_module.build_application = wrapped_build_application
    main_base_module.app.state.scene_patch_installed = True
