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


_USER_COLUMNS = {
    "first_name", "username", "plan", "plan_expires_at",
    "is_active", "is_banned", "language", "last_seen_at", "phone_hash",
}


async def update_user(telegram_id: int, **kwargs) -> None:
    if not kwargs:
        return

    # فقط ستون‌های مجاز (نام ستون داخل کوئری نمی‌نشیند)
    cols = [k for k in kwargs if k in _USER_COLUMNS]
    if not cols:
        return

    pool = get_pool()
    sets = ", ".join(
        f"{k} = ${i+2}" for i, k in enumerate(cols)
    )
    vals = [telegram_id, *[kwargs[k] for k in cols]]
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


async def get_all_users(limit: int = 500) -> list[dict]:
    """لیست همه کاربران برای پنل مدیریت مشتریان"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]


async def get_user_by_db_id(user_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE id = $1", user_id
        )
        return dict(row) if row else None


async def count_users() -> dict:
    """آمار کلی برای پنل مدیریت"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              COUNT(*)                                                AS total,
              COUNT(*) FILTER (WHERE is_banned)                       AS banned,
              COUNT(*) FILTER (WHERE plan <> 'free'
                    AND plan_expires_at > NOW())                       AS premium
            FROM users
            """
        )
        sessions = await conn.fetchval(
            """SELECT COUNT(*) FROM account_sessions
               WHERE status IN ('active', 'connected')"""
        )
        pending = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_requests
               WHERE status = 'pending'"""
        )
        return {
            "total": row["total"] or 0,
            "banned": row["banned"] or 0,
            "premium": row["premium"] or 0,
            "sessions": sessions or 0,
            "pending": pending or 0,
        }


async def set_user_banned(telegram_id: int, banned: bool) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_banned = $2 WHERE telegram_id = $1",
            telegram_id, banned,
        )


async def set_user_plan(
    telegram_id: int,
    plan: str,
    expires_at=None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE users SET plan = $2, plan_expires_at = $3
               WHERE telegram_id = $1""",
            telegram_id, plan, expires_at,
        )


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
      async with conn.transaction():
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
                   u.plan_expires_at, u.is_banned, u.is_active
            FROM account_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.status IN ('active', 'connected')
            """
        )
        return [dict(r) for r in rows]

async def get_suspended_sessions() -> list[dict]:
    """session‌های معلق + وضعیت اشتراک صاحبشان (برای watchdog)"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.user_id, s.error_message,
                   u.telegram_id, u.plan, u.plan_expires_at,
                   u.is_banned, u.is_active
            FROM account_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.status = 'suspended'
            """
        )
        return [dict(r) for r in rows]


async def get_users_by_db_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM users WHERE id = ANY($1::int[])", list(ids)
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
) -> bool:
    """
    ذخیره پیام با جلوگیری قطعی از تکراری‌ها.
    خروجی:
      True  = رکورد جدید اضافه شد
      False = رکورد تکراری بود
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO saved_messages
                (user_id, source_type, source_chat_id, source_chat_title,
                 source_msg_id, original_text, media_type, media_path_enc, timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            ON CONFLICT (user_id, source_type, source_chat_id, source_msg_id)
            DO NOTHING
            RETURNING id
            """,
            user_id, source_type, chat_id, chat_title,
            msg_id, text, media_type, media_path or "",
        )
        return row is not None

# ═══════ Auto Response Rules ═══════


async def save_auto_response_rule(
    user_id: int, target_user_id: int, response_list: list,
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auto_response_rules
                (user_id, target_user_id, response_list)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT DO NOTHING
            """,
            user_id, target_user_id, json.dumps(response_list),
        )


async def get_auto_response_rules(user_id: int) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM auto_response_rules
               WHERE user_id = $1 AND is_active = TRUE""",
            user_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("response_list"), str):
                d["response_list"] = json.loads(d["response_list"])
            result.append(d)
        return result


async def delete_auto_response_rule(user_id: int, target_user_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM auto_response_rules WHERE user_id = $1 AND target_user_id = $2",
            user_id, target_user_id,
        )

# ═══════ Channel Monitor (extended) ═══════


async def get_all_channel_routes(user_id: int) -> list[dict]:
    """همه مسیرها شامل غیرفعال‌ها"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM channel_monitor_routes WHERE user_id = $1 ORDER BY created_at",
            user_id,
        )
        return [dict(r) for r in rows]


async def toggle_channel_route(user_id: int, source_channel_id: int) -> bool:
    """تغییر وضعیت فعال/غیرفعال — خروجی: وضعیت جدید"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE channel_monitor_routes
            SET is_active = NOT is_active
            WHERE user_id = $1 AND source_channel_id = $2
            RETURNING is_active
            """,
            user_id, source_channel_id,
        )
        return row["is_active"] if row else False


# ═══════ Subscription Requests ═══════


async def create_subscription_request(
    user_id: int,
    plan_key: str,
    plan_title: str,
    price: int,
    days: int,
    receipt_chat_id: int,
    receipt_msg_id: int,
    receipt_file_id: str = None,
) -> dict | None:
    """
    ثبت درخواست خرید اشتراک.
    خروجی None یعنی کاربر از قبل یک درخواست در انتظار بررسی دارد.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO subscription_requests
                (user_id, plan_key, plan_title, price, days,
                 receipt_chat_id, receipt_msg_id, receipt_file_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (user_id) WHERE status = 'pending'
            DO NOTHING
            RETURNING *
            """,
            user_id, plan_key, plan_title, price, days,
            receipt_chat_id, receipt_msg_id, receipt_file_id,
        )
        return dict(row) if row else None


async def get_subscription_request(req_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.*, u.telegram_id, u.first_name, u.username,
                   u.plan, u.plan_expires_at
            FROM subscription_requests r
            JOIN users u ON r.user_id = u.id
            WHERE r.id = $1
            """,
            req_id,
        )
        return dict(row) if row else None


async def get_pending_requests(limit: int = 20) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.*, u.telegram_id, u.first_name, u.username
            FROM subscription_requests r
            JOIN users u ON r.user_id = u.id
            WHERE r.status = 'pending'
            ORDER BY r.created_at
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def get_last_request(user_id: int) -> dict | None:
    """آخرین درخواست کاربر (برای نمایش وضعیت)"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM subscription_requests
            WHERE user_id = $1
            ORDER BY created_at DESC LIMIT 1
            """,
            user_id,
        )
        return dict(row) if row else None


async def count_pending_requests() -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_requests
               WHERE status = 'pending'"""
        )
        return val or 0


async def set_request_message_id(req_id: int, admin_msg_id: int) -> None:
    """ذخیره آیدی پیام ادمین برای ویرایش بعدی"""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE subscription_requests SET admin_msg_id = $2
               WHERE id = $1""",
            req_id, admin_msg_id,
        )


async def resolve_subscription_request(
    req_id: int,
    status: str,
    reviewed_by: int,
    reject_reason: str = None,
) -> bool:
    """
    تایید/رد درخواست. خروجی False یعنی قبلاً بررسی شده بود
    (جلوگیری از تایید دوباره با دو کلیک).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE subscription_requests
            SET status = $2,
                reviewed_by = $3,
                reject_reason = $4,
                reviewed_at = NOW()
            WHERE id = $1 AND status = 'pending'
            RETURNING id
            """,
            req_id, status, reviewed_by, reject_reason,
        )
        return row is not None

# ═══════ Forward Jobs (فوروارد قابل ادامه) ═══════


async def create_forward_job(
    user_id: int,
    src: dict,
    dst: dict,
    mode: str,
    attributed: bool,
    media_only: bool,
    limit_count: int,
    chat_id: int = None,
    message_id: int = None,
    cache_mode: bool = False,
    capture_media: bool = False,
    speed: str = None,
) -> dict | None:
    """
    ساخت job فوروارد.
    خروجی None یعنی یک job در جریان برای این کاربر وجود دارد.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO forward_jobs
                (user_id, src_kind, src_id, src_hash, src_name,
                 dst_kind, dst_id, dst_hash, dst_name,
                 mode, attributed, media_only, limit_count,
                 chat_id, message_id, cache_mode, capture_media, speed,
                 phase, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                    CASE WHEN $16 THEN 'capture' ELSE 'send' END, 'running')
            ON CONFLICT (user_id) WHERE status IN ('running', 'paused')
            DO NOTHING
            RETURNING *
            """,
            user_id,
            src.get("kind"), src.get("id"), src.get("hash"), src.get("name", ""),
            dst.get("kind"), dst.get("id"), dst.get("hash"), dst.get("name", ""),
            mode, attributed, media_only, limit_count,
            chat_id, message_id, cache_mode, capture_media,
            speed or "balanced",
        )
        return dict(row) if row else None


async def update_forward_job(
    job_id: int,
    *,
    last_msg_id: int = None,
    sent: int = None,
    skipped: int = None,
    failed: int = None,
    copied: int = None,
    total: int = None,
    status: str = None,
    error: str = None,
) -> None:
    """ذخیره پیشرفت job (برای ادامه پس از ری‌استارت)"""
    sets, vals = [], []
    pairs = {
        "last_msg_id": last_msg_id, "sent": sent, "skipped": skipped,
        "failed": failed, "copied": copied, "total": total,
        "status": status, "error": error,
    }
    for col, val in pairs.items():
        if val is not None:
            vals.append(val)
            sets.append(f"{col} = ${len(vals)}")

    if not sets:
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""UPDATE forward_jobs
                SET {', '.join(sets)}, updated_at = NOW()
                WHERE id = ${len(vals) + 1}""",
            *vals, job_id,
        )


async def get_forward_job(job_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM forward_jobs WHERE id = $1", job_id
        )
        return dict(row) if row else None


async def get_resumable_jobs(limit: int = 20) -> list[dict]:
    """job های نیمه‌کاره برای ادامه خودکار"""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM forward_jobs
            WHERE status IN ('running', 'paused')
            ORDER BY updated_at
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def get_user_forward_job(user_id: int) -> dict | None:
    """job در جریان یا آخرین job کاربر"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM forward_jobs
            WHERE user_id = $1
            ORDER BY (status IN ('running', 'paused')) DESC,
                     updated_at DESC
            LIMIT 1
            """,
            user_id,
        )
        return dict(row) if row else None


async def get_blocking_forward_job(user_id: int) -> dict | None:
    """job نیمه‌کاره‌ای که جلوی شروع job جدید را می‌گیرد (running/paused)"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM forward_jobs
            WHERE user_id = $1 AND status IN ('running', 'paused')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            user_id,
        )
        return dict(row) if row else None


async def release_unfinished_jobs(user_id: int, note: str = None,
                                  status: str = "cancelled") -> list[dict]:
    """
    بستن job های نیمه‌کاره/گیرکرده کاربر (رفع قفل «شما فوروارد فعال دارید»).
    progress و کش دست‌نخورده می‌ماند؛ فقط قفل باز می‌شود.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE forward_jobs
               SET status = $2, error = COALESCE($3, error), updated_at = NOW()
             WHERE user_id = $1 AND status IN ('running', 'paused')
            RETURNING id, src_name, dst_name, sent, total, cache_mode, status
            """,
            user_id, status, note,
        )
        return [dict(r) for r in rows]


async def finish_forward_job(job_id: int, status: str, error: str = None) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE forward_jobs
               SET status = $2, error = $3, updated_at = NOW()
               WHERE id = $1""",
            job_id, status, error,
        )


async def count_forward_jobs(user_id: int) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            """SELECT COUNT(*) FROM forward_jobs WHERE user_id = $1""",
            user_id,
        )
        return val or 0


# ═══════ Cache پیام‌ها (فوروارد دو مرحله‌ای) ═══════


async def add_cached_messages(job_id: int, user_id: int, rows: list[dict]) -> int:
    """ذخیره یک دسته پیام در کش — پیام‌های تکراری نادیده گرفته می‌شوند"""
    if not rows:
        return 0

    inserted = 0
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                res = await conn.fetchval(
                    """
                    INSERT INTO forward_cache
                        (job_id, user_id, src_msg_id, msg_date, text,
                         media_kind, media_path, media_size,
                         doc_id, doc_hash, file_ref,
                         sender_name, sender_label, msg_link)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    ON CONFLICT (job_id, src_msg_id) DO NOTHING
                    RETURNING id
                    """,
                    job_id, user_id, r.get("src_msg_id"), r.get("msg_date"),
                    r.get("text"), r.get("media_kind") or "",
                    r.get("media_path"), r.get("media_size") or 0,
                    r.get("doc_id"), r.get("doc_hash"), r.get("file_ref"),
                    r.get("sender_name") or "", r.get("sender_label") or "",
                    r.get("msg_link"),
                )
                if res:
                    inserted += 1
    return inserted


async def get_pending_cache(job_id: int, limit: int = 20,
                            include_failed: bool = False) -> list[dict]:
    """
    پیام‌های کش‌شده که هنوز ارسال نشده‌اند (به ترتیب).

    ردیف‌های خطاخورده (`send_error`) به‌طور پیش‌فرض برنمی‌گردند تا حلقه‌ی
    ارسال بی‌نهایت بار روی آن‌ها نچرخد؛ فقط در پاس تلاش دوباره می‌آیند.
    """
    pool = get_pool()
    where_failed = "" if include_failed else "AND send_error IS NULL"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT * FROM forward_cache
            WHERE job_id = $1 AND sent_at IS NULL {where_failed}
            ORDER BY src_msg_id
            LIMIT $2
            """,
            job_id, limit,
        )
        return [dict(r) for r in rows]


async def mark_cache_sent(cache_ids: list[int]) -> None:
    if not cache_ids:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE forward_cache SET sent_at = NOW(), send_error = NULL
               WHERE id = ANY($1::int[])""",
            cache_ids,
        )


async def mark_cache_failed(cache_id: int, error: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE forward_cache SET send_error = $2
               WHERE id = $1 AND sent_at IS NULL""",
            cache_id, (error or "")[:300],
        )


async def cache_stats(job_id: int) -> dict:
    """آمار کش: در انتظار / ارسال‌شده / حجم"""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              COUNT(*)                                        AS total,
              COUNT(*) FILTER (WHERE sent_at IS NOT NULL)      AS sent,
              COUNT(*) FILTER (WHERE sent_at IS NULL)          AS pending,
              COALESCE(SUM(media_size) FILTER (WHERE sent_at IS NULL), 0)
                                                              AS pending_bytes
            FROM forward_cache WHERE job_id = $1
            """,
            job_id,
        )
        return {
            "total": row["total"] or 0,
            "sent": row["sent"] or 0,
            "pending": row["pending"] or 0,
            "pending_bytes": row["pending_bytes"] or 0,
        }


async def clear_job_cache(job_id: int) -> list[str]:
    """
    پاک کردن کش یک job — خروجی: مسیر فایل‌های مدیا برای حذف از دیسک
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT media_path FROM forward_cache
               WHERE job_id = $1 AND media_path IS NOT NULL""",
            job_id,
        )
        await conn.execute(
            "DELETE FROM forward_cache WHERE job_id = $1", job_id
        )
    return [r["media_path"] for r in rows if r["media_path"]]


async def update_forward_job_phase(
    job_id: int,
    *,
    phase: str = None,
    captured: int = None,
    cache_bytes: int = None,
    capture_cursor: int = None,
    status: str = None,
) -> None:
    """به‌روزرسانی وضعیت فاز جمع‌آوری"""
    sets, vals = [], []
    for col, val in (
        ("phase", phase), ("captured", captured),
        ("cache_bytes", cache_bytes), ("capture_cursor", capture_cursor),
        ("status", status),
    ):
        if val is not None:
            vals.append(val)
            sets.append(f"{col} = ${len(vals)}")

    if not sets:
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""UPDATE forward_jobs SET {', '.join(sets)}, updated_at = NOW()
                WHERE id = ${len(vals) + 1}""",
            *vals, job_id,
        )
