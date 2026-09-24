"""
قفل «فقط یک نمونه» (Single-Instance Lock)

اگر دو نمونه از برنامه همزمان با یک session به تلگرام وصل شوند (ری‌استارتی
که نمونه‌ی قبلی هنوز بسته نشده، یا اجرای اشتباهی دو بار)، تلگرام ممکن است
AUTH_KEY_DUPLICATED بدهد و session را **باطل** کند → همه‌ی کاربران باید
دوباره وارد شوند.

راه‌حل بدون زیرساخت جدید: advisory lock خود PostgreSQL روی یک اتصال
اختصاصی (خارج از pool). تا وقتی این اتصال باز است قفل دست ماست؛ اگر
پروسه بمیرد، PostgreSQL خودش قفل را آزاد می‌کند.

- هنگام شروع: اگر قفل دست نمونه‌ی دیگری است تا INSTANCE_LOCK_WAIT ثانیه
  صبر می‌کند (برای ری‌استارت systemd که نمونه‌ی قبلی در حال بسته شدن است)
  و بعد با پیام واضح متوقف می‌شود — قبل از اینکه حتی یک session وصل شود.
- در حین کار: هر ۱۵ ثانیه اتصال بررسی می‌شود. اگر قطع شد دوباره قفل
  گرفته می‌شود؛ اگر نمونه‌ی دیگری قفل را گرفته بود، این نمونه فوراً
  همه‌ی اکانت‌ها را قطع و خودش را متوقف می‌کند.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import asyncpg

from config import DB_DSN, INSTANCE_LOCK, INSTANCE_LOCK_WAIT

logger = logging.getLogger("instance_lock")

# کلید ثابت قفل (عدد ۶۴ بیتی دلخواه، مخصوص این برنامه)
LOCK_KEY = 0x5E1FB07_0001
CHECK_INTERVAL = 15

_conn: asyncpg.Connection | None = None
_task: asyncio.Task | None = None


class AnotherInstanceRunning(RuntimeError):
    pass


# سرور PostgreSQL کلاینت مرده را (مثلاً kill -9 یا قطع شبکه) ظرف ~۶۰ ثانیه
# تشخیص می‌دهد و قفل را آزاد می‌کند؛ بدون این ممکن بود دقیقه‌ها طول بکشد
_SERVER_SETTINGS = {
    "application_name": "selfbot-instance-lock",
    "tcp_keepalives_idle": "30",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count": "3",
}


async def _connect():
    try:
        return await asyncpg.connect(DB_DSN, timeout=15, server_settings=_SERVER_SETTINGS)
    except asyncpg.PostgresError:
        # بعضی سرویس‌های مدیریت‌شده اجازه‌ی تنظیم keepalive نمی‌دهند
        return await asyncpg.connect(
            DB_DSN, timeout=15,
            server_settings={"application_name": _SERVER_SETTINGS["application_name"]},
        )


async def _try_lock(conn) -> bool:
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY))


async def _holder_info(conn) -> str:
    """چه کسی قفل را دارد؟ (برای پیام خطای قابل اقدام)"""
    try:
        row = await conn.fetchrow(
            """
            SELECT a.pid, a.client_addr::text AS addr, a.backend_start
            FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE l.locktype = 'advisory' AND l.granted
              AND l.classid = $1 AND l.objid = $2
            LIMIT 1
            """,
            (LOCK_KEY >> 32) & 0xFFFFFFFF, LOCK_KEY & 0xFFFFFFFF,
        )
        if row:
            return (
                f" [pid={row['pid']} addr={row['addr'] or 'local'} "
                f"since={row['backend_start']:%Y-%m-%d %H:%M:%S}] — اگر مطمئنید آن "
                f"نمونه دیگر وجود ندارد: SELECT pg_terminate_backend({row['pid']});"
            )
    except Exception:
        pass
    return ""


async def acquire(wait: int | None = None) -> None:
    """گرفتن قفل؛ در صورت شکست AnotherInstanceRunning"""
    global _conn, _task
    if not INSTANCE_LOCK:
        logger.warning("INSTANCE_LOCK خاموش است — مراقب اجرای دو نمونه باشید")
        return

    wait = INSTANCE_LOCK_WAIT if wait is None else wait
    conn = await _connect()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0, wait)
    announced = False
    while True:
        if await _try_lock(conn):
            break
        if loop.time() >= deadline:
            holder = await _holder_info(conn)
            await conn.close()
            raise AnotherInstanceRunning(
                "نمونه‌ی دیگری از ربات در حال اجراست (قفل PostgreSQL دست اوست). "
                "برای جلوگیری از باطل شدن sessionها این نمونه اجرا نمی‌شود." + holder
            )
        if not announced:
            logger.warning(
                f"قفل دست نمونه‌ی دیگری است؛ تا {wait} ثانیه صبر می‌کنم..."
            )
            announced = True
        await asyncio.sleep(2)

    _conn = conn
    logger.info("Instance lock acquired")
    _task = asyncio.create_task(_keepalive(), name="instance-lock")


async def _keepalive():
    global _conn
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            if _conn is None or _conn.is_closed():
                raise ConnectionError("closed")
            await _conn.fetchval("SELECT 1", timeout=10)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Instance lock connection lost: {e}")

        # اتصال قطع شد → قفل ممکن است آزاد شده باشد؛ دوباره بگیر
        try:
            if _conn is not None:
                _conn.terminate()
        except Exception:
            pass
        _conn = None
        while _conn is None:
            try:
                conn = await _connect()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # دیتابیس در دسترس نیست → نمونه‌ی دیگر هم نمی‌تواند قفل بگیرد
                logger.warning(f"Instance lock reconnect failed: {e}")
                await asyncio.sleep(5)
                continue
            if await _try_lock(conn):
                _conn = conn
                logger.info("Instance lock re-acquired")
                break
            await conn.close()
            logger.critical(
                "نمونه‌ی دیگری قفل را گرفت — قطع فوری همه‌ی اکانت‌ها و توقف این نمونه"
            )
            await _emergency_stop()
            return


async def _emergency_stop():
    try:
        from core.plugin_manager import unload_all
        from core.client_manager import disconnect_all
        await unload_all()
        await disconnect_all()
    except Exception as e:
        logger.error(f"emergency disconnect failed: {e}")
    # توقف تمیز PTB (post_shutdown اجرا می‌شود)
    os.kill(os.getpid(), signal.SIGTERM)


async def release() -> None:
    global _conn, _task
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
    if _conn is not None:
        try:
            await _conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        except Exception:
            pass
        try:
            await _conn.close()
        except Exception:
            pass
        _conn = None
        logger.info("Instance lock released")


def held() -> bool:
    return _conn is not None and not _conn.is_closed()
