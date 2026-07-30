"""
PostgreSQL operations with asyncpg
"""

import json
import logging
import asyncpg
from config import DB_DSN
from database.models import TABLES_SQL

logger = logging.getLogger("database")

_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    logger.info("Connecting to database...")
    _pool = await asyncpg.create_pool(
        DB_DSN, min_size=2, max_size=10, command_timeout=30
    )
    async with _pool.acquire() as conn:
        await conn.execute(TABLES_SQL)
    logger.info("Database ready")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database closed")


def get_pool() -> asyncpg.Pool:
    if not _pool:
        raise RuntimeError("Database not initialized")
    return _pool


# ═══════ Users ═══════


async def create_user(
    telegram_id: int, first_name: str = "", username: str = ""
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (telegram_id, first_name, username)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE
                SET first_name = $2, username = $3,
                    last_seen_at = NOW()
            RETURNING *
            """,
            telegram_id, first_name, username,
        )
        return dict(row)


async def get_user(telegram_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id,
        )
        return dict(row) if row else None


async def update_user(telegram_id: int, **kwargs) -> None:
    if not kwargs:
        return
    pool = get_pool()
    sets = ", ".join(
        f"{k} = ${i+2}" for i, k in enumerate(kwargs)
    )
    vals = [telegram_id, *kwargs.values()]
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {sets} WHERE telegram_id = $1",
            *vals,
        )


async def get_all_active_users() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM users
               WHERE is_active = TRUE AND is_banned = FALSE"""
        )
        return [dict(r) for r in rows]


# ═══════ Sessions ═══════


async def save_session(
    user_id: int,
    phone_hash: str,
    session_data_enc: str,
    api_id_enc: str,
    api_hash_enc: str,
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        # حذف سشن قبلی اگه بود
        await conn.execute(
            "DELETE FROM account_sessions WHERE user_id = $1",
            user_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO account_sessions
                (user_id, phone_hash, session_data_enc,
                 api_id_enc, api_hash_enc, status)
            VALUES ($1, $2, $3, $4, $5, 'active')
            RETURNING *
            """,
            user_id, phone_hash, session_data_enc,
            api_id_enc, api_hash_enc,
        )
        return dict(row) if row else {}


async def get_session(user_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM account_sessions WHERE user_id = $1",
            user_id,
        )
        return dict(row) if row else None


async def update_session_status(
    user_id: int, status: str, error_msg: str = None
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE account_sessions
            SET status = $2::varchar,
                error_message = $3::text,
                is_connected = CASE
                    WHEN $2::varchar = 'connected' THEN TRUE
                    ELSE FALSE
                END,
                last_connected_at = CASE
                    WHEN $2::varchar = 'connected' THEN NOW()
                    ELSE last_connected_at
                END
            WHERE user_id = $1
            """,
            user_id, status, error_msg,
        )


async def delete_session(user_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM account_sessions WHERE user_id = $1",
            user_id,
        )


async def get_all_active_sessions() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.*, u.telegram_id, u.plan,
                   u.plan_expires_at, u.is_banned
            FROM account_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.status IN ('active', 'connected')
              AND u.is_active = TRUE
              AND u.is_banned = FALSE
            """
        )
        return [dict(r) for r in rows]

# ═══════ Features ═══════


async def set_feature(
    user_id: int,
    feature_name: str,
    is_enabled: bool,
    config_json: dict = None,
) -> None:
    pool = get_pool()
    cfg = json.dumps(config_json or {})
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO feature_toggles
                (user_id, feature_name, is_enabled, config_json)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (user_id, feature_name) DO UPDATE
                SET is_enabled = $3,
                    config_json = $4::jsonb,
                    updated_at = NOW()
            """,
            user_id, feature_name, is_enabled, cfg,
        )


async def get_features(user_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM feature_toggles WHERE user_id = $1",
            user_id,
        )
        return [dict(r) for r in rows]


async def is_feature_enabled(
    user_id: int, feature_name: str
) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT is_enabled FROM feature_toggles
               WHERE user_id = $1 AND feature_name = $2""",
            user_id, feature_name,
        )
        return bool(row and row["is_enabled"])


# ═══════ Storage Targets ═══════


async def set_storage_target(
    user_id: int,
    feature_name: str,
    target_type: str,
    target_id: int,
    target_title: str = "",
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO storage_targets
                (user_id, feature_name, target_type,
                 target_id, target_title)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, feature_name) DO UPDATE
                SET target_type = $3, target_id = $4,
                    target_title = $5, updated_at = NOW()
            """,
            user_id, feature_name, target_type,
            target_id, target_title,
        )


async def get_storage_target(
    user_id: int, feature_name: str
) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM storage_targets
               WHERE user_id = $1 AND feature_name = $2""",
            user_id, feature_name,
        )
        return dict(row) if row else None


# ═══════ Audit ═══════


async def audit_log(
    user_id: int | None, action: str, detail: str = ""
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_logs (user_id, action, detail)
               VALUES ($1, $2, $3)""",
            user_id, action, detail,
        )

# ═══════ Channel Monitor Routes ═══════


async def set_channel_route(
    user_id: int,
    source_channel_id: int,
    source_title: str,
    dest_type: str,
    dest_id: int,
    dest_title: str = "",
    filter_type: str = "all",
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO channel_monitor_routes
                (user_id, source_channel_id, source_channel_title,
                 destination_type, destination_id, destination_title, filter_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, source_channel_id) DO UPDATE
                SET destination_type = $4, destination_id = $5,
                    destination_title = $6, filter_type = $7,
                    source_channel_title = $3
            """,
            user_id, source_channel_id, source_title,
            dest_type, dest_id, dest_title, filter_type,
        )


async def get_channel_routes(user_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM channel_monitor_routes WHERE user_id = $1 AND is_active = TRUE",
            user_id,
        )
        return [dict(r) for r in rows]


async def delete_channel_route(user_id: int, source_channel_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM channel_monitor_routes WHERE user_id = $1 AND source_channel_id = $2",
            user_id, source_channel_id,
        )

# ═══════ Banners ═══════


async def save_banner(user_id: int, chat_id: int, msg_id: int, interval: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO banners (user_id, chat_id, source_msg_id, interval_seconds)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
            """,
            user_id, chat_id, msg_id, interval,
        )


async def get_banners_for_chat(user_id: int, chat_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM banners
               WHERE user_id = $1 AND chat_id = $2 AND is_active = TRUE""",
            user_id, chat_id,
        )
        return [dict(r) for r in rows]


async def get_all_banners(user_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM banners WHERE user_id = $1 AND is_active = TRUE",
            user_id,
        )
        return [dict(r) for r in rows]


async def clear_banners(user_id: int, chat_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM banners WHERE user_id = $1 AND chat_id = $2",
            user_id, chat_id,
        )


# ═══════ Saved Messages Record ═══════


async def save_message_record(
    user_id: int, source_type: str, chat_id: int,
    chat_title: str, msg_id: int, text: str,
    media_type: str = None, media_path: str = None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO saved_messages
                (user_id, source_type, source_chat_id, source_chat_title,
                 source_msg_id, original_text, media_type, media_path_enc, timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            """,
            user_id, source_type, chat_id, chat_title,
            msg_id, text, media_type, media_path or "",
        )