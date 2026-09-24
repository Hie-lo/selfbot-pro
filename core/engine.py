"""
موتور اصلی
"""

import logging
from database import db
from core.crypto import decrypt
from core.client_manager import reconnect_client, disconnect_all
from core.plugin_manager import load_plugins_for_user, unload_all

logger = logging.getLogger("engine")


async def startup():
    """اتصال مجدد + بارگذاری پلاگین‌ها"""
    logger.info("Starting engine...")

    sessions = await db.get_all_active_sessions()
    connected = 0

    for s in sessions:
        try:
            session_string = decrypt(s["session_data_enc"])
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
            logger.error(f"Reconnect failed user {s['user_id']}: {e}")
            await db.update_session_status(
                s["user_id"], "error", str(e)[:200],
            )

    logger.info(f"Engine: {connected}/{len(sessions)} clients connected")

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
    await unload_all()
    await disconnect_all()
    await db.close_db()
    logger.info("Engine stopped")