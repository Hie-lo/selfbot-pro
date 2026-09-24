"""
موتور اصلی
"""

import logging
from database import db
from core.crypto import decrypt
from core.client_manager import reconnect_client, disconnect_all
from core.plugin_manager import load_plugins_for_user, unload_all
from core import access
from config import MAX_CLIENTS

logger = logging.getLogger("engine")


async def startup():
    """اتصال مجدد + بارگذاری پلاگین‌ها"""
    logger.info("Starting engine...")

    sessions = await db.get_all_active_sessions()
    connected = 0

    for s in sessions:
        # اشتراک تمام‌شده/لغوشده → وصل نشود (قبلاً همه بعد از ری‌استارت وصل می‌شدند)
        ok, reason = access.eligible(s)
        if not ok:
            await db.update_session_status(s["user_id"], access.ST_SUSPENDED, reason)
            await db.audit_log(s["user_id"], "selfbot_suspended", f"{reason} (startup)")
            logger.info(f"User {s['user_id']} not reconnected: {reason}")
            continue

        from core.client_manager import active_clients
        if len(active_clients) >= MAX_CLIENTS:
            await db.update_session_status(s["user_id"], access.ST_SUSPENDED, access.R_CAPACITY)
            logger.warning(f"User {s['user_id']} deferred: server at capacity")
            continue

        try:
            session_string = decrypt(s["session_data_enc"])
        except Exception as e:
            # کلید رمزنگاری عوض شده یا داده خراب است → اتصال مجدد لازم است
            logger.error(f"Session decrypt failed user {s['user_id']}: {e}")
            await db.update_session_status(s["user_id"], access.ST_ERROR, "decrypt failed")
            continue

        try:
            client = await reconnect_client(
                user_db_id=s["user_id"],
                session_string=session_string,
            )

            if client:
                await db.update_session_status(s["user_id"], "connected")

                # بارگذاری پلاگین‌ها
                await load_plugins_for_user(s["user_id"], client)

                connected += 1
            else:
                await db.update_session_status(
                    s["user_id"], "expired", "Session expired",
                )
        except Exception as e:
            # خطای شبکه/موقت → session سالم است؛ watchdog دوباره تلاش می‌کند
            logger.error(f"Reconnect failed user {s['user_id']}: {e}")
            await db.update_session_status(
                s["user_id"], access.ST_SUSPENDED, access.R_OFFLINE,
            )

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
    await unload_all()
    await disconnect_all()
    await db.close_db()
    logger.info("Engine stopped")