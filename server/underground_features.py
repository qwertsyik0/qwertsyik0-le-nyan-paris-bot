from __future__ import annotations

import re
import secrets
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
from .db import find_user_by_identifier, get_user_application, upsert_user
from .main_base import _row_value

STARTING_BALANCE = 100
ALLOWED_STAKES = (5, 10, 25, 50, 100)
RAID_CHANCE_PERCENT = 8

HEAT_LABELS = {
    0: "тихо",
    1: "заметно",
    2: "под наблюдением",
    3: "слишком заметно",
}


async def ensure_underground_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_underground_wallets (
                telegram_id BIGINT PRIMARY KEY REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                balance INT NOT NULL DEFAULT 100 CHECK (balance >= 0),
                net_profit INT NOT NULL DEFAULT 0,
                heat_level INT NOT NULL DEFAULT 0,
                gambling_rounds INT NOT NULL DEFAULT 0,
                gambling_wins INT NOT NULL DEFAULT 0,
                fights_played INT NOT NULL DEFAULT 0,
                fights_won INT NOT NULL DEFAULT 0,
                raids INT NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_underground_ledger (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                delta INT NOT NULL,
                balance_after INT NOT NULL,
                kind TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_underground_ledger_user_created_idx
            ON paris_underground_ledger (telegram_id, created_at DESC);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_underground_rounds (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                stake INT NOT NULL CHECK (stake > 0),
                choice TEXT CHECK (choice IN ('red', 'black')),
                result_color TEXT CHECK (result_color IN ('red', 'black')),
                result_number INT,
                delta INT,
                raid_triggered BOOLEAN NOT NULL DEFAULT FALSE,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'resolved', 'cancelled')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_underground_rounds_user_status_idx
            ON paris_underground_rounds (telegram_id, status, created_at DESC);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paris_underground_fights (
                id BIGSERIAL PRIMARY KEY,
                challenger_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                opponent_id BIGINT NOT NULL REFERENCES paris_users(telegram_id) ON DELETE CASCADE,
                stake INT NOT NULL CHECK (stake > 0),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'active', 'completed', 'declined', 'cancelled')),
                winner_claim_id BIGINT,
                winner_id BIGINT,
                disputed BOOLEAN NOT NULL DEFAULT FALSE,
                origin_chat_id BIGINT,
                origin_message_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS paris_underground_fights_status_idx
            ON paris_underground_fights (status, updated_at DESC);
            """
        )


def _heat_for_profit(profit: int) -> int:
    if profit >= 500:
        return 3
    if profit >= 250:
        return 2
    if profit >= 100:
        return 1
    return 0


async def _accepted(pool: asyncpg.Pool, telegram_id: int):
    await ensure_underground_schema(pool)
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


def _role(row: Any | None) -> str:
    if row is None:
        return "роль не указана"
    return str(
        _row_value(row, "assigned_role")
        or _row_value(row, "role_preference")
        or "роль не указана"
    )


async def _ensure_wallet_conn(conn: asyncpg.Connection, telegram_id: int) -> asyncpg.Record:
    await conn.execute(
        """
        INSERT INTO paris_underground_wallets (telegram_id, balance)
        VALUES ($1, $2)
        ON CONFLICT (telegram_id) DO NOTHING;
        """,
        telegram_id,
        STARTING_BALANCE,
    )
    return await conn.fetchrow(
        "SELECT * FROM paris_underground_wallets WHERE telegram_id = $1;",
        telegram_id,
    )


async def _wallet(pool: asyncpg.Pool, telegram_id: int) -> asyncpg.Record:
    await ensure_underground_schema(pool)
    async with pool.acquire() as conn:
        return await _ensure_wallet_conn(conn, telegram_id)


async def _active_fight(pool: asyncpg.Pool, telegram_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT *
        FROM paris_underground_fights
        WHERE status = 'active'
          AND (challenger_id = $1 OR opponent_id = $1)
        ORDER BY updated_at DESC
        LIMIT 1;
        """,
        telegram_id,
    )


async def _blocking_fight(pool: asyncpg.Pool, telegram_id: int) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT *
        FROM paris_underground_fights
        WHERE status IN ('pending', 'active')
          AND (challenger_id = $1 OR opponent_id = $1)
        ORDER BY updated_at DESC
        LIMIT 1;
        """,
        telegram_id,
    )


async def _apply_delta_conn(
    conn: asyncpg.Connection,
    telegram_id: int,
    delta: int,
    *,
    kind: str,
    note: str,
) -> tuple[asyncpg.Record, int]:
    wallet = await _ensure_wallet_conn(conn, telegram_id)
    wallet = await conn.fetchrow(
        "SELECT * FROM paris_underground_wallets WHERE telegram_id = $1 FOR UPDATE;",
        telegram_id,
    )
    old_heat = int(wallet["heat_level"] or 0)
    new_balance = int(wallet["balance"] or 0) + int(delta)
    if new_balance < 0:
        raise ValueError("недостаточно франков")
    new_profit = int(wallet["net_profit"] or 0) + int(delta)
    new_heat = max(old_heat, _heat_for_profit(new_profit))
    updated = await conn.fetchrow(
        """
        UPDATE paris_underground_wallets
        SET balance = $2,
            net_profit = $3,
            heat_level = $4,
            updated_at = NOW()
        WHERE telegram_id = $1
        RETURNING *;
        """,
        telegram_id,
        new_balance,
        new_profit,
        new_heat,
    )
    await conn.execute(
        """
        INSERT INTO paris_underground_ledger (telegram_id, delta, balance_after, kind, note)
        VALUES ($1, $2, $3, $4, $5);
        """,
        telegram_id,
        int(delta),
        new_balance,
        kind,
        note[:500],
    )
    return updated, old_heat


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    config: Config = context.application.bot_data["config"]
    for admin_id in config.admin_ids:
        try:
            await context.bot.send_message(chat_id=int(admin_id), text=text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


def _main_markup(*, active_fight: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎲 красное / чёрное", callback_data="ug:gamble")],
        [InlineKeyboardButton("🥊 подпольные бои", callback_data="ug:fights")],
        [InlineKeyboardButton("💰 кошелёк", callback_data="ug:wallet")],
    ]
    if not active_fight:
        rows.append([InlineKeyboardButton("🚪 уйти", callback_data="ug:leave")])
    return InlineKeyboardMarkup(rows)


def _stakes_markup() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("5", callback_data="ug:stake:5"),
            InlineKeyboardButton("10", callback_data="ug:stake:10"),
            InlineKeyboardButton("25", callback_data="ug:stake:25"),
        ],
        [
            InlineKeyboardButton("50", callback_data="ug:stake:50"),
            InlineKeyboardButton("100", callback_data="ug:stake:100"),
        ],
        [InlineKeyboardButton("← назад", callback_data="ug:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def _color_markup(round_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🔴 красное", callback_data=f"ug:play:{round_id}:red"),
            InlineKeyboardButton("⚫ чёрное", callback_data=f"ug:play:{round_id}:black"),
        ]]
    )


def _fight_active_markup(fight_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("заявить победу", callback_data=f"ug:fight_claim:{fight_id}")]]
    )


def _fight_claim_markup(fight_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("подтвердить", callback_data=f"ug:fight_confirm:{fight_id}"),
            InlineKeyboardButton("оспорить", callback_data=f"ug:fight_dispute:{fight_id}"),
        ]]
    )


async def _wallet_text(pool: asyncpg.Pool, telegram_id: int) -> str:
    wallet = await _wallet(pool, telegram_id)
    return (
        "💰 <b>кошелёк персонажа</b>\n\n"
        f"<b>средства:</b> {int(wallet['balance'])} франков\n"
        f"<b>чистый результат подполья:</b> {int(wallet['net_profit']):+d}\n"
        f"<b>азартных раундов:</b> {int(wallet['gambling_rounds'])}\n"
        f"<b>побед в играх:</b> {int(wallet['gambling_wins'])}\n"
        f"<b>боёв:</b> {int(wallet['fights_played'])}\n"
        f"<b>побед в боях:</b> {int(wallet['fights_won'])}\n"
        f"<b>облав:</b> {int(wallet['raids'])}\n"
        f"<b>внимание к деньгам:</b> {HEAT_LABELS.get(int(wallet['heat_level']), 'неизвестно')}"
    )


async def _menu_text(pool: asyncpg.Pool, telegram_id: int) -> str:
    wallet = await _wallet(pool, telegram_id)
    fight = await _active_fight(pool, telegram_id)
    extra = ""
    if fight is not None:
        extra = f"\n\n🥊 у тебя идёт бой <b>#{int(fight['id'])}</b>. уйти из подполья до его завершения нельзя."
    return (
        "🌑 <b>подполье</b>\n"
        "<i>ставки, драки и слишком много глаз в тёмных переулках.</i>\n\n"
        f"в кошельке: <b>{int(wallet['balance'])} франков</b>.\n"
        "во время начавшегося раунда или боя выйти нельзя. после окончания — можно.\n"
        "стража может нагрянуть без предупреждения."
        + extra
    )


async def underground_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    pool: asyncpg.Pool = context.application.bot_data["pool"]
    await upsert_user(pool, user.to_dict())
    row = await _accepted(pool, int(user.id))
    if row is None:
        await message.reply_text("подполье доступно только принятым участникам.")
        raise ApplicationHandlerStop
    fight = await _active_fight(pool, int(user.id))
    await message.reply_text(
        await _menu_text(pool, int(user.id)),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_markup(active_fight=fight is not None),
    )
    raise ApplicationHandlerStop


async def _start_round(pool: asyncpg.Pool, telegram_id: int, stake: int) -> asyncpg.Record:
    if stake not in ALLOWED_STAKES:
        raise ValueError("неверная ставка")
    if await _active_fight(pool, telegram_id):
        raise ValueError("сначала заверши текущий бой")
    async with pool.acquire() as conn:
        async with conn.transaction():
            wallet = await _ensure_wallet_conn(conn, telegram_id)
            if int(wallet["balance"]) < stake:
                raise ValueError("недостаточно франков")
            await conn.execute(
                """
                UPDATE paris_underground_rounds
                SET status = 'cancelled'
                WHERE telegram_id = $1 AND status = 'pending';
                """,
                telegram_id,
            )
            return await conn.fetchrow(
                """
                INSERT INTO paris_underground_rounds (telegram_id, stake)
                VALUES ($1, $2)
                RETURNING *;
                """,
                telegram_id,
                stake,
            )


async def _resolve_round(
    pool: asyncpg.Pool,
    telegram_id: int,
    round_id: int,
    choice: str,
) -> tuple[asyncpg.Record, asyncpg.Record, bool, int]:
    if choice not in {"red", "black"}:
        raise ValueError("неверный выбор")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT * FROM paris_underground_rounds
                WHERE id = $1 AND telegram_id = $2
                FOR UPDATE;
                """,
                round_id,
                telegram_id,
            )
            if row is None:
                raise ValueError("раунд не найден")
            if str(row["status"]) != "pending":
                raise ValueError("этот раунд уже завершён")

            stake = int(row["stake"])
            wallet = await _ensure_wallet_conn(conn, telegram_id)
            if int(wallet["balance"]) < stake:
                await conn.execute(
                    "UPDATE paris_underground_rounds SET status = 'cancelled' WHERE id = $1;",
                    round_id,
                )
                raise ValueError("недостаточно франков для этой ставки")

            draw = secrets.randbelow(20)
            result_color = "red" if draw < 10 else "black"
            result_number = (draw % 10) + 1
            win = choice == result_color
            delta = stake if win else -stake
            raid = secrets.randbelow(100) < RAID_CHANCE_PERCENT

            updated_wallet, old_heat = await _apply_delta_conn(
                conn,
                telegram_id,
                delta,
                kind="gambling",
                note=f"раунд #{round_id}: {choice} -> {result_color} {result_number}",
            )
            await conn.execute(
                """
                UPDATE paris_underground_wallets
                SET gambling_rounds = gambling_rounds + 1,
                    gambling_wins = gambling_wins + $2,
                    raids = raids + $3,
                    updated_at = NOW()
                WHERE telegram_id = $1;
                """,
                telegram_id,
                1 if win else 0,
                1 if raid else 0,
            )
            await conn.execute(
                """
                UPDATE paris_underground_rounds
                SET choice = $2,
                    result_color = $3,
                    result_number = $4,
                    delta = $5,
                    raid_triggered = $6,
                    status = 'resolved',
                    resolved_at = NOW()
                WHERE id = $1;
                """,
                round_id,
                choice,
                result_color,
                result_number,
                delta,
                raid,
            )
            final_wallet = await conn.fetchrow(
                "SELECT * FROM paris_underground_wallets WHERE telegram_id = $1;",
                telegram_id,
            )
            return row, final_wallet, raid, old_heat


async def _create_fight(
    pool: asyncpg.Pool,
    challenger_id: int,
    opponent_id: int,
    stake: int,
    chat_id: int | None,
) -> asyncpg.Record:
    if stake not in ALLOWED_STAKES:
        raise ValueError("ставка должна быть 5, 10, 25, 50 или 100 франков")
    if challenger_id == opponent_id:
        raise ValueError("нельзя вызвать на бой самого себя")
    if await _blocking_fight(pool, challenger_id):
        raise ValueError("у тебя уже есть незавершённый бой")
    if await _blocking_fight(pool, opponent_id):
        raise ValueError("у этого игрока уже есть незавершённый бой")

    async with pool.acquire() as conn:
        async with conn.transaction():
            challenger_wallet = await _ensure_wallet_conn(conn, challenger_id)
            opponent_wallet = await _ensure_wallet_conn(conn, opponent_id)
            if int(challenger_wallet["balance"]) < stake:
                raise ValueError("у тебя недостаточно франков")
            if int(opponent_wallet["balance"]) < stake:
                raise ValueError("у соперника недостаточно франков")
            return await conn.fetchrow(
                """
                INSERT INTO paris_underground_fights (
                    challenger_id, opponent_id, stake, origin_chat_id
                )
                VALUES ($1, $2, $3, $4)
                RETURNING *;
                """,
                challenger_id,
                opponent_id,
                stake,
                chat_id,
            )


async def _accept_fight(pool: asyncpg.Pool, fight_id: int, actor_id: int) -> asyncpg.Record:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fight = await conn.fetchrow(
                "SELECT * FROM paris_underground_fights WHERE id = $1 FOR UPDATE;",
                fight_id,
            )
            if fight is None:
                raise ValueError("бой не найден")
            if int(fight["opponent_id"]) != actor_id:
                raise PermissionError("принять вызов может только соперник")
            if str(fight["status"]) != "pending":
                raise ValueError("этот вызов уже неактивен")

            stake = int(fight["stake"])
            for uid in (int(fight["challenger_id"]), int(fight["opponent_id"])):
                wallet = await _ensure_wallet_conn(conn, uid)
                wallet = await conn.fetchrow(
                    "SELECT * FROM paris_underground_wallets WHERE telegram_id = $1 FOR UPDATE;",
                    uid,
                )
                if int(wallet["balance"]) < stake:
                    raise ValueError("у одного из участников уже недостаточно франков")

            for uid in (int(fight["challenger_id"]), int(fight["opponent_id"])):
                await _apply_delta_conn(
                    conn,
                    uid,
                    -stake,
                    kind="fight_stake",
                    note=f"ставка в бою #{fight_id}",
                )
                await conn.execute(
                    """
                    UPDATE paris_underground_wallets
                    SET fights_played = fights_played + 1, updated_at = NOW()
                    WHERE telegram_id = $1;
                    """,
                    uid,
                )

            return await conn.fetchrow(
                """
                UPDATE paris_underground_fights
                SET status = 'active', updated_at = NOW()
                WHERE id = $1
                RETURNING *;
                """,
                fight_id,
            )


async def _complete_fight(
    pool: asyncpg.Pool,
    fight_id: int,
    winner_id: int,
) -> tuple[asyncpg.Record, asyncpg.Record, int]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fight = await conn.fetchrow(
                "SELECT * FROM paris_underground_fights WHERE id = $1 FOR UPDATE;",
                fight_id,
            )
            if fight is None:
                raise ValueError("бой не найден")
            if str(fight["status"]) != "active":
                raise ValueError("бой уже завершён")
            participants = {int(fight["challenger_id"]), int(fight["opponent_id"])}
            if winner_id not in participants:
                raise ValueError("этот игрок не участвует в бою")

            stake = int(fight["stake"])
            winner_wallet, old_heat = await _apply_delta_conn(
                conn,
                winner_id,
                stake * 2,
                kind="fight_prize",
                note=f"приз за бой #{fight_id}",
            )
            await conn.execute(
                """
                UPDATE paris_underground_wallets
                SET fights_won = fights_won + 1, updated_at = NOW()
                WHERE telegram_id = $1;
                """,
                winner_id,
            )
            completed = await conn.fetchrow(
                """
                UPDATE paris_underground_fights
                SET status = 'completed',
                    winner_id = $2,
                    updated_at = NOW(),
                    completed_at = NOW()
                WHERE id = $1
                RETURNING *;
                """,
                fight_id,
                winner_id,
            )
            final_wallet = await conn.fetchrow(
                "SELECT * FROM paris_underground_wallets WHERE telegram_id = $1;",
                winner_id,
            )
            return completed, final_wallet, old_heat


async def underground_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not query.data or not query.data.startswith("ug:"):
        return

    pool: asyncpg.Pool = context.application.bot_data["pool"]
    await upsert_user(pool, user.to_dict())
    application = await _accepted(pool, int(user.id))
    if application is None:
        await query.answer("подполье доступно только принятым участникам", show_alert=True)
        raise ApplicationHandlerStop

    parts = query.data.split(":")
    action = parts[1]
    uid = int(user.id)

    if action == "menu":
        fight = await _active_fight(pool, uid)
        await query.answer()
        await query.message.edit_text(
            await _menu_text(pool, uid),
            parse_mode=ParseMode.HTML,
            reply_markup=_main_markup(active_fight=fight is not None),
        )
        raise ApplicationHandlerStop

    if action == "wallet":
        await query.answer()
        fight = await _active_fight(pool, uid)
        await query.message.edit_text(
            await _wallet_text(pool, uid),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("← подполье", callback_data="ug:menu")]]
            ) if fight is None else _fight_active_markup(int(fight["id"])),
        )
        raise ApplicationHandlerStop

    if action == "gamble":
        if await _active_fight(pool, uid):
            await query.answer("сначала заверши текущий бой", show_alert=True)
            raise ApplicationHandlerStop
        wallet = await _wallet(pool, uid)
        await query.answer()
        await query.message.edit_text(
            "🎲 <b>красное / чёрное</b>\n\n"
            "20 исходов: 10 красных и 10 чёрных, номера от 1 до 10.\n"
            "угадал цвет — получаешь сумму ставки сверху. проиграл — ставка сгорает.\n\n"
            f"в кошельке: <b>{int(wallet['balance'])} франков</b>.\n"
            "выбери ставку:",
            parse_mode=ParseMode.HTML,
            reply_markup=_stakes_markup(),
        )
        raise ApplicationHandlerStop

    if action == "stake" and len(parts) >= 3:
        try:
            stake = int(parts[2])
            round_row = await _start_round(pool, uid, stake)
        except (ValueError, TypeError) as exc:
            await query.answer(str(exc), show_alert=True)
            raise ApplicationHandlerStop
        await query.answer()
        await query.message.edit_text(
            f"🎲 ставка: <b>{stake} франков</b>.\n\n"
            "раунд начался. сейчас выйти нельзя. выбери цвет:",
            parse_mode=ParseMode.HTML,
            reply_markup=_color_markup(int(round_row["id"])),
        )
        raise ApplicationHandlerStop

    if action == "play" and len(parts) >= 4:
        try:
            round_id = int(parts[2])
            choice = parts[3]
            round_row, wallet, raid, old_heat = await _resolve_round(pool, uid, round_id, choice)
        except (ValueError, TypeError) as exc:
            await query.answer(str(exc), show_alert=True)
            raise ApplicationHandlerStop

        resolved = await pool.fetchrow(
            "SELECT * FROM paris_underground_rounds WHERE id = $1;",
            round_id,
        )
        color_label = "🔴 красное" if str(resolved["result_color"]) == "red" else "⚫ чёрное"
        delta = int(resolved["delta"])
        result_line = f"выигрыш: <b>+{delta} франков</b>" if delta > 0 else f"проигрыш: <b>{delta} франков</b>"
        heat_now = int(wallet["heat_level"])

        text = (
            f"🎲 <b>раунд #{round_id}</b>\n\n"
            f"выпало: <b>{color_label} {int(resolved['result_number'])}</b>\n"
            f"{result_line}\n"
            f"кошелёк: <b>{int(wallet['balance'])} франков</b>"
        )

        if heat_now > old_heat:
            text += "\n\n👁 твои деньги начинают привлекать лишнее внимание."
            await _notify_admins(
                context,
                "👁 <b>подполье: заметный рост средств</b>\n\n"
                f"{escape(_character_name(application))}: "
                f"чистый результат {int(wallet['net_profit']):+d} франков.\n"
                f"уровень внимания: {escape(HEAT_LABELS.get(heat_now, str(heat_now)))}.",
            )

        if raid:
            text += (
                "\n\n🚨 <b>облава.</b>\n"
                "снаружи шум, команды и шаги стражи. раунд уже закончен, "
                "но просто исчезнуть теперь не получится — дальнейший исход отыгрывается."
            )
            await _notify_admins(
                context,
                "🚨 <b>подполье: сработала облава</b>\n\n"
                f"игрок: {escape(_character_name(application))}\n"
                f"раунд: #{round_id}\n"
                "можно подключать стражу и последствия.",
            )
            markup = None
        else:
            markup = _main_markup(active_fight=False)

        await query.answer()
        await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        raise ApplicationHandlerStop

    if action == "leave":
        fight = await _active_fight(pool, uid)
        if fight is not None:
            await query.answer("бой ещё не завершён — сейчас уйти нельзя", show_alert=True)
            raise ApplicationHandlerStop
        await query.answer()
        await query.message.edit_text(
            "🌑 ты покинул подполье. переулок снова выглядит совершенно обычным."
        )
        raise ApplicationHandlerStop

    if action == "fights":
        fight = await _active_fight(pool, uid)
        await query.answer()
        if fight is not None:
            await query.message.edit_text(
                "🥊 <b>подпольный бой</b>\n\n"
                f"у тебя уже идёт бой <b>#{int(fight['id'])}</b>.\n"
                f"ставка каждого: <b>{int(fight['stake'])} франков</b>.\n\n"
                "победа определяется отыгрышем: учитывайте профессию, физическую форму, "
                "ранения, окружение и уловки. бот не выбирает победителя случайно.",
                parse_mode=ParseMode.HTML,
                reply_markup=_fight_active_markup(int(fight["id"])),
            )
        else:
            await query.message.edit_text(
                "🥊 <b>подпольные бои</b>\n\n"
                "чтобы вызвать игрока, ответь на его сообщение:\n"
                "<code>Бой 25</code>\n\n"
                "доступные ставки: 5, 10, 25, 50 или 100 франков с каждого.\n"
                "после принятия вызова обе ставки замораживаются, и уйти до завершения боя нельзя.\n\n"
                "победа решается отыгрышем, а не рандомом.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("← подполье", callback_data="ug:menu")]]
                ),
            )
        raise ApplicationHandlerStop

    if action in {"fight_accept", "fight_decline", "fight_claim", "fight_confirm", "fight_dispute"}:
        if len(parts) < 3:
            await query.answer("неверный бой", show_alert=True)
            raise ApplicationHandlerStop
        try:
            fight_id = int(parts[2])
        except ValueError:
            await query.answer("неверный бой", show_alert=True)
            raise ApplicationHandlerStop

        fight = await pool.fetchrow(
            "SELECT * FROM paris_underground_fights WHERE id = $1;",
            fight_id,
        )
        if fight is None:
            await query.answer("бой не найден", show_alert=True)
            raise ApplicationHandlerStop

        if action == "fight_decline":
            if int(fight["opponent_id"]) != uid:
                await query.answer("отклонить вызов может только соперник", show_alert=True)
                raise ApplicationHandlerStop
            if str(fight["status"]) != "pending":
                await query.answer("вызов уже неактивен", show_alert=True)
                raise ApplicationHandlerStop
            await pool.execute(
                """
                UPDATE paris_underground_fights
                SET status = 'declined', updated_at = NOW()
                WHERE id = $1 AND status = 'pending';
                """,
                fight_id,
            )
            await query.answer("вызов отклонён")
            await query.message.edit_reply_markup(reply_markup=None)
            raise ApplicationHandlerStop

        if action == "fight_accept":
            try:
                active = await _accept_fight(pool, fight_id, uid)
            except (ValueError, PermissionError) as exc:
                await query.answer(str(exc), show_alert=True)
                raise ApplicationHandlerStop
            await query.answer("бой начался")
            challenger_app = await get_user_application(pool, int(active["challenger_id"]))
            opponent_app = await get_user_application(pool, int(active["opponent_id"]))
            await query.message.edit_text(
                f"🥊 <b>бой #{fight_id} начался</b>\n\n"
                f"{escape(_character_name(challenger_app))} — {escape(_role(challenger_app))}\n"
                f"{escape(_character_name(opponent_app))} — {escape(_role(opponent_app))}\n\n"
                f"банк боя: <b>{int(active['stake']) * 2} франков</b>.\n"
                "учитывайте профессию, силу, ранения, окружение и уловки. "
                "автоматического победителя нет.\n\n"
                "когда отыгрыш закончен, победитель нажимает «заявить победу», "
                "а второй участник подтверждает результат.",
                parse_mode=ParseMode.HTML,
                reply_markup=_fight_active_markup(fight_id),
            )
            raise ApplicationHandlerStop

        if str(fight["status"]) != "active":
            await query.answer("бой уже неактивен", show_alert=True)
            raise ApplicationHandlerStop
        participants = {int(fight["challenger_id"]), int(fight["opponent_id"])}
        if uid not in participants:
            await query.answer("ты не участвуешь в этом бою", show_alert=True)
            raise ApplicationHandlerStop

        if action == "fight_claim":
            claim = _row_value(fight, "winner_claim_id")
            if claim is not None and int(claim) != uid:
                await query.answer("другой участник уже заявил победу — подтверди или оспорь", show_alert=True)
                raise ApplicationHandlerStop
            await pool.execute(
                """
                UPDATE paris_underground_fights
                SET winner_claim_id = $2, updated_at = NOW()
                WHERE id = $1 AND status = 'active';
                """,
                fight_id,
                uid,
            )
            await query.answer("победа заявлена")
            await query.message.reply_text(
                f"🥊 в бою <b>#{fight_id}</b> один из участников заявил победу.\n"
                "второй участник должен подтвердить или оспорить результат.",
                parse_mode=ParseMode.HTML,
                reply_markup=_fight_claim_markup(fight_id),
            )
            raise ApplicationHandlerStop

        claim = _row_value(fight, "winner_claim_id")
        if claim is None:
            await query.answer("пока никто не заявил победу", show_alert=True)
            raise ApplicationHandlerStop
        claim_id = int(claim)
        if uid == claim_id:
            await query.answer("подтверждение должен дать второй участник", show_alert=True)
            raise ApplicationHandlerStop

        if action == "fight_dispute":
            await pool.execute(
                """
                UPDATE paris_underground_fights
                SET disputed = TRUE, updated_at = NOW()
                WHERE id = $1 AND status = 'active';
                """,
                fight_id,
            )
            await query.answer("результат оспорен")
            await query.message.edit_reply_markup(reply_markup=None)
            await _notify_admins(
                context,
                "🥊 <b>спор по подпольному бою</b>\n\n"
                f"бой: #{fight_id}\n"
                "участники не согласны с результатом.\n"
                f"для решения: <code>Бой #{fight_id} победитель @username</code>",
            )
            raise ApplicationHandlerStop

        if action == "fight_confirm":
            try:
                completed, winner_wallet, old_heat = await _complete_fight(pool, fight_id, claim_id)
            except ValueError as exc:
                await query.answer(str(exc), show_alert=True)
                raise ApplicationHandlerStop
            await query.answer("результат подтверждён")
            winner_app = await get_user_application(pool, claim_id)
            await query.message.edit_text(
                f"🥊 <b>бой #{fight_id} завершён</b>\n\n"
                f"победитель: <b>{escape(_character_name(winner_app))}</b>\n"
                f"банк: <b>{int(completed['stake']) * 2} франков</b>.\n"
                f"средства победителя: <b>{int(winner_wallet['balance'])} франков</b>.",
                parse_mode=ParseMode.HTML,
            )
            if int(winner_wallet["heat_level"]) > old_heat:
                await _notify_admins(
                    context,
                    "👁 <b>подполье: заметный рост средств</b>\n\n"
                    f"{escape(_character_name(winner_app))}: "
                    f"чистый результат {int(winner_wallet['net_profit']):+d} франков.",
                )
            raise ApplicationHandlerStop

    await query.answer()
    raise ApplicationHandlerStop


async def underground_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = message.from_user if message is not None else None
    if message is None or user is None or not message.text:
        return

    raw = " ".join(message.text.strip().split())
    lowered = raw.casefold()

    is_menu = lowered == "подполье"
    is_wallet = lowered in {"кошелёк", "мой кошелёк"}
    is_fight = lowered == "бой" or lowered.startswith("бой ")
    is_admin_resolution = bool(re.match(r"^бой\s*#?\d+\s+победитель\s+\S+$", lowered))

    if not (is_menu or is_wallet or is_fight or is_admin_resolution):
        return

    pool: asyncpg.Pool = context.application.bot_data["pool"]
    config: Config = context.application.bot_data["config"]
    await upsert_user(pool, user.to_dict())

    if is_admin_resolution:
        if int(user.id) not in {int(x) for x in config.admin_ids}:
            return
        match = re.match(r"^бой\s*#?(\d+)\s+победитель\s+(\S+)$", raw, flags=re.IGNORECASE)
        if not match:
            return
        fight_id = int(match.group(1))
        target = await find_user_by_identifier(pool, match.group(2))
        if target is None:
            await message.reply_text("игрок не найден.")
            raise ApplicationHandlerStop
        try:
            completed, wallet, old_heat = await _complete_fight(pool, fight_id, int(target["telegram_id"]))
        except ValueError as exc:
            await message.reply_text(str(exc))
            raise ApplicationHandlerStop
        winner_app = await get_user_application(pool, int(target["telegram_id"]))
        await message.reply_text(
            f"🥊 бой <b>#{fight_id}</b> закрыт.\n"
            f"победитель: <b>{escape(_character_name(winner_app))}</b>.\n"
            f"банк {int(completed['stake']) * 2} франков выплачен.",
            parse_mode=ParseMode.HTML,
        )
        if int(wallet["heat_level"]) > old_heat:
            await _notify_admins(
                context,
                "👁 <b>подполье: заметный рост средств</b>\n\n"
                f"{escape(_character_name(winner_app))}: "
                f"чистый результат {int(wallet['net_profit']):+d} франков.",
            )
        raise ApplicationHandlerStop

    application = await _accepted(pool, int(user.id))
    if application is None:
        await message.reply_text("подполье доступно только принятым участникам.")
        raise ApplicationHandlerStop

    if is_menu:
        fight = await _active_fight(pool, int(user.id))
        await message.reply_text(
            await _menu_text(pool, int(user.id)),
            parse_mode=ParseMode.HTML,
            reply_markup=_main_markup(active_fight=fight is not None),
        )
        raise ApplicationHandlerStop

    if is_wallet:
        await message.reply_text(
            await _wallet_text(pool, int(user.id)),
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    if lowered == "бой":
        await message.reply_text(
            "🥊 чтобы вызвать игрока на подпольный бой, ответь на его сообщение:\n"
            "<code>Бой 25</code>\n\n"
            "ставка может быть 5, 10, 25, 50 или 100 франков.",
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    if is_fight:
        match = re.match(r"^бой\s+(\d+)$", lowered)
        if not match:
            return
        if message.reply_to_message is None or message.reply_to_message.from_user is None:
            await message.reply_text("команду <code>Бой 25</code> нужно отправить ответом на сообщение соперника.", parse_mode=ParseMode.HTML)
            raise ApplicationHandlerStop
        target_user = message.reply_to_message.from_user
        if target_user.is_bot:
            await message.reply_text("бота на подпольный бой вызвать нельзя.")
            raise ApplicationHandlerStop

        await upsert_user(pool, target_user.to_dict())
        target_app = await _accepted(pool, int(target_user.id))
        if target_app is None:
            await message.reply_text("этот игрок не является активным принятым участником.")
            raise ApplicationHandlerStop

        stake = int(match.group(1))
        try:
            fight = await _create_fight(
                pool,
                int(user.id),
                int(target_user.id),
                stake,
                int(message.chat_id) if message.chat_id is not None else None,
            )
        except ValueError as exc:
            await message.reply_text(str(exc))
            raise ApplicationHandlerStop

        sent = await message.reply_text(
            f"🥊 <b>вызов на подпольный бой #{int(fight['id'])}</b>\n\n"
            f"{escape(_character_name(application))} вызывает "
            f"{escape(_character_name(target_app))}.\n"
            f"ставка: <b>{stake} франков с каждого</b>.\n\n"
            "после принятия вызова уйти до завершения боя нельзя.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("принять", callback_data=f"ug:fight_accept:{int(fight['id'])}"),
                    InlineKeyboardButton("отказаться", callback_data=f"ug:fight_decline:{int(fight['id'])}"),
                ]]
            ),
        )
        await pool.execute(
            """
            UPDATE paris_underground_fights
            SET origin_message_id = $2, updated_at = NOW()
            WHERE id = $1;
            """,
            int(fight["id"]),
            int(sent.message_id),
        )
        raise ApplicationHandlerStop


def patch_underground_features(main_base_module: Any) -> None:
    if getattr(main_base_module.app.state, "underground_patch_installed", False):
        return

    original_build_application = main_base_module.build_application

    def wrapped_build_application(config: Config, pool: asyncpg.Pool) -> Application:
        app = original_build_application(config, pool)
        app.add_handler(CommandHandler("underground", underground_command), group=-850)
        app.add_handler(
            CallbackQueryHandler(underground_callback, pattern=r"^ug:"),
            group=-850,
        )
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, underground_text_handler),
            group=-850,
        )
        return app

    main_base_module.build_application = wrapped_build_application
    main_base_module.app.state.underground_patch_installed = True
