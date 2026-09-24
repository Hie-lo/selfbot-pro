"""
موتور اصلی
"""

import asyncio
import logging
import random

from database import db
from core.crypto import decrypt
from core.client_manager import reconnect_client, disconnect_all
from core.plugin_manager import load_plugins_for_user, unload_all
from core import access, metrics, pv_cache
from config import STARTUP_CONCURRENCY

logger = logging.getLogger("engine")


async def _connect_one(s: dict, gate: asyncio.Lock, state: dict) -> bool:
    """اتصال یک session هنگام روشن شدن (با رزرو ظرفیت، ایمن در اجرای موازی)"""
    from core.client_manager import active_clients

    # اشتراک تمام‌شده/لغوشده → وصل نشود (قبلاً همه بعد از ری‌استارت وصل می‌شدند)
    ok, reason = access.eligible(s)
    if not ok:
        await db.update_session_status(s["user_id"], access.ST_SUSPENDED, reason)
        await db.audit_log(s["user_id"], "selfbot_suspended", f"{reason} (startup)")
        logger.info(f"User {s['user_id']} not reconnected: {reason}")
        return False

    # ظرفیت: اتصال‌های در جریان هم حساب می‌شوند (رزرو) تا اجرای موازی از سقف رد نشود
    async with gate:
        admit, _ = metrics.admission(len(active_clients) + state["reserved"])
        if admit:
            state["reserved"] += 1
    if not admit:
        await db.update_session_status(s["user_id"], access.ST_SUSPENDED, access.R_CAPACITY)
        logger.warning(f"User {s['user_id']} deferred: server at capacity")
        return False

    released = False

    async def _unreserve():
        nonlocal released
        if not released:
            released = True
            async with gate:
                state["reserved"] -= 1

    try:
        try:
            session_string = decrypt(s["session_data_enc"])
        except Exception as e:
            # کلید رمزنگاری عوض شده یا داده خراب است → اتصال مجدد لازم است
            logger.error(f"Session decrypt failed user {s['user_id']}: {e}")
            await db.update_session_status(s["user_id"], access.ST_ERROR, "decrypt failed")
            return False

        try:
            client = await reconnect_client(
                user_db_id=s["user_id"],
                session_string=session_string,
            )
            await _unreserve()   # از اینجا در active_clients شمرده می‌شود
            if client:
                await db.update_session_status(s["user_id"], "connected")
                await load_plugins_for_user(s["user_id"], client)
                return True
            await db.update_session_status(s["user_id"], "expired", "Session expired")
            return False
        except Exception as e:
            # خطای شبکه/موقت → session سالم است؛ watchdog دوباره تلاش می‌کند
            logger.error(f"Reconnect failed user {s['user_id']}: {e}")
            await db.update_session_status(
                s["user_id"], access.ST_SUSPENDED, access.R_OFFLINE,
            )
            return False
    finally:
        await _unreserve()


async def startup():
    """اتصال مجدد + بارگذاری پلاگین‌ها"""
    logger.info("Starting engine...")

    # فایل‌های گاوصندوق ضدحذف از اجرای قبلی بی‌استفاده‌اند (کش در حافظه بود)
    pv_cache.wipe_all_orphans()

    sessions = await db.get_all_active_sessions()

    # اتصال موازی با سقف (قبلاً یکی‌یکی → با ۱۰۰ اکانت روشن شدن چند دقیقه طول می‌کشید)
    # + فاصله‌ی تصادفی کوچک تا صدها اتصال همزمان به تلگرام نخورد
    sem = asyncio.Semaphore(STARTUP_CONCURRENCY)
    gate = asyncio.Lock()
    state = {"reserved": 0}

    async def _guarded(s):
        async with sem:
            await asyncio.sleep(random.uniform(0, 0.6))
            try:
                return await _connect_one(s, gate, state)
            except Exception as e:
                logger.error(f"startup user {s.get('user_id')}: {e}")
                return False

    results = await asyncio.gather(*(_guarded(s) for s in sessions))
    connected = sum(1 for r in results if r)

    logger.info(f"Engine: {connected}/{len(sessions)} clients connected")

    # معلق‌هایی که در این فاصله تمدید شده‌اند
    for row in await db.get_suspended_sessions():
        if access.eligible(row)[0]:
            await access.resume_user(row["user_id"], notify=False)

    # کنترل دسترسی زنده (انقضا، مسدودی، باطل شدن session)
    access.start_watchdog()

    # ادامه فورواردهای نیمه‌کاره (کم‌کم و با فاصله)
    try:
        from core.forwarder import resume_pending_jobs
        from bot.handlers import notify_job_progress
        await resume_pending_jobs(
            on_progress_factory=lambda uid, row: (
                lambda job: notify_job_progress(uid, job, row)
            ),
            delay=10.0,
        )
    except Exception as e:
        logger.warning(f"Forward jobs resume skipped: {e}")


async def shutdown():
    """خاموش کردن تمیز"""
    logger.info("Shutting down...")
    await access.stop_watchdog()
    await metrics.stop()
    await unload_all()
    await disconnect_all()
    await db.close_db()
    logger.info("Engine stopped")