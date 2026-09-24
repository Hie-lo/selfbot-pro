"""
کنترل دسترسی زنده — اشتراک، مسدودی و سلامت session

قبلاً اشتراک فقط هنگام «اتصال اکانت» و باز کردن منوها چک می‌شد؛ بعد از
انقضا، لغو اشتراک یا مسدودسازی، کلاینت و همه‌ی پلاگین‌ها روشن می‌ماندند
(حتی بعد از ری‌استارت). این ماژول مرجع واحد تصمیم‌گیری است:

  suspend_user  → توقف پلاگین‌ها + توقف فوروارد + قطع اتصال (session حفظ می‌شود)
  resume_user   → اتصال دوباره از session ذخیره‌شده + لود پلاگین‌ها
  revoke_user   → session در خود تلگرام باطل شده؛ پاک و به کاربر اطلاع داده می‌شود
  sync_user     → بر اساس وضعیت فعلی DB یکی از بالایی‌ها را انجام می‌دهد
                  (بعد از تایید/لغو اشتراک، مسدود/رفع مسدودی صدا زده می‌شود)

watchdog هر ACCESS_CHECK_INTERVAL ثانیه:
  • کاربران متصل بدون اشتراک معتبر یا مسدود → suspend
  • session‌های معلق که دوباره واجد شرایط شده‌اند → resume
  • سلامت اتصال + اعتبار session (هر AUTH_CHECK_EVERY دور) → revoke در صورت نیاز
  • هشدار نزدیک شدن انقضا
  • پاکسازی لاگین‌های نیمه‌کاره و rate limiter
"""

import asyncio
import logging
from datetime import datetime, timezone

from config import (
    ACCESS_CHECK_INTERVAL,
    AUTH_CHECK_EVERY,
    EXPIRY_WARN_DAYS,
    MAX_CLIENTS,
)
from core import client_manager, runtime
from core.crypto import decrypt
from core.security import is_subscription_active, prune_rate_limits
from database import db

logger = logging.getLogger("access")

# وضعیت‌های account_sessions.status
ST_CONNECTED = "connected"
ST_SUSPENDED = "suspended"      # معلق — با تمدید/رفع مسدودی خودکار برمی‌گردد
ST_EXPIRED = "expired"          # session نامعتبر — کاربر باید دوباره وصل شود
ST_REVOKED = "revoked"
ST_ERROR = "error"

# session‌هایی که دیگر قابل استفاده نیستند (اتصال مجدد لازم است)
DEAD_STATUSES = {ST_EXPIRED, ST_REVOKED, ST_ERROR, "inactive"}

# علت‌های تعلیق (در error_message ذخیره می‌شود)
R_SUB_EXPIRED = "sub_expired"
R_SUB_CANCELLED = "sub_cancelled"
R_BANNED = "banned"
R_CAPACITY = "capacity"
R_OFFLINE = "offline"           # خطای شبکه هنگام اتصال — خودکار دوباره تلاش می‌شود

REASON_LABELS = {
    R_SUB_EXPIRED: "⏳ اشتراک تمام شده",
    R_SUB_CANCELLED: "🚫 اشتراک لغو شده",
    R_BANNED: "⛔ حساب مسدود است",
    R_CAPACITY: "📈 ظرفیت سرور پر است (خودکار وصل می‌شود)",
    R_OFFLINE: "📡 خطای ارتباط با تلگرام (خودکار دوباره تلاش می‌شود)",
}

_NOTIFY_SUSPEND = {
    R_SUB_EXPIRED: (
        "⏳ <b>اشتراک شما به پایان رسید</b>\n\n"
        "سلف‌بات موقتاً متوقف شد. اطلاعات و تنظیمات شما حفظ شده و با "
        "تمدید اشتراک از «💎 اشتراک»، خودکار دوباره فعال می‌شود."
    ),
    R_SUB_CANCELLED: (
        "🚫 <b>اشتراک شما توسط مدیریت لغو شد</b>\n\n"
        "سلف‌بات متوقف شد. برای اطلاعات بیشتر با پشتیبانی در تماس باشید."
    ),
    R_BANNED: "⛔ <b>حساب شما مسدود شد</b> و سلف‌بات متوقف گردید.",
}

_NOTIFY_RESUMED = (
    "✅ <b>سلف‌بات دوباره فعال شد</b>\n\n"
    "اکانت شما وصل و قابلیت‌هایتان بارگذاری شد."
)

_NOTIFY_REVOKED = (
    "🔌 <b>اتصال اکانت شما قطع شد</b>\n\n"
    "session سلف‌بات در تلگرام باطل شده است (مثلاً از «Devices / دستگاه‌ها» "
    "خارج شده‌اید). برای ادامه، از منوی ربات دوباره «🔗 اتصال اکانت» را بزنید."
)

_locks: dict[int, asyncio.Lock] = {}
_warned: dict[int, str] = {}          # user_db_id → تاریخ انقضایی که برایش هشدار رفته
_task: asyncio.Task | None = None
_cycle = 0


def _lock(user_db_id: int) -> asyncio.Lock:
    lk = _locks.get(user_db_id)
    if lk is None:
        lk = _locks[user_db_id] = asyncio.Lock()
    return lk


def eligible(user: dict | None) -> tuple[bool, str | None]:
    """آیا کاربر مجاز به داشتن سلف‌بات فعال است؟ (خروجی: مجاز، علت عدم مجوز)"""
    if not user:
        return False, R_SUB_CANCELLED
    if user.get("is_banned"):
        return False, R_BANNED
    if not user.get("is_active", True):
        return False, R_SUB_CANCELLED
    if not is_subscription_active(user):
        return False, R_SUB_EXPIRED
    return True, None


def status_label(session: dict | None) -> str:
    """متن خوانای وضعیت session برای نمایش در ربات"""
    if not session:
        return "🔴 متصل نیست"
    st = session.get("status")
    if st in (ST_CONNECTED, "active"):
        return "🟢 متصل و فعال"
    if st == ST_SUSPENDED:
        return "⏸ معلق — " + REASON_LABELS.get(session.get("error_message") or "", "غیرفعال")
    if st in DEAD_STATUSES:
        return "🔴 session نامعتبر — دوباره «🔗 اتصال اکانت» را بزنید"
    return f"❔ {st}"


# ═══════════════════════════════════
# عملیات
# ═══════════════════════════════════


async def stop_forward_jobs(user_db_id: int, timeout: float = 15.0) -> None:
    """توقف فوروارد در جریان (قابل ادامه — وضعیت paused در DB)"""
    from core import cache_forward, forwarder

    for mod in (forwarder, cache_forward):
        try:
            job = mod.get_active_job(user_db_id)
            if not job:
                continue
            await mod.stop_job(job.id)
            try:
                await asyncio.wait_for(mod.wait_job(job), timeout=timeout)
            except asyncio.TimeoutError:
                if job.task:
                    job.task.cancel()
        except Exception as e:
            logger.warning(f"stop forward job user={user_db_id}: {e}")


async def _teardown(user_db_id: int) -> None:
    """خاموش کردن پلاگین‌ها، فوروارد و اتصال یک کاربر"""
    from core.plugin_manager import unload_all_for_user

    await stop_forward_jobs(user_db_id)
    try:
        await unload_all_for_user(user_db_id)
    except Exception as e:
        logger.warning(f"unload plugins user={user_db_id}: {e}")
    await client_manager.disconnect_client(user_db_id)
    try:
        from core.forwarder import clear_cache
        clear_cache(user_db_id)
    except Exception:
        pass


async def suspend_user(user_db_id: int, reason: str, notify: bool = True) -> bool:
    """تعلیق سلف‌بات کاربر — session برای ادامه‌ی بعدی حفظ می‌شود"""
    async with _lock(user_db_id):
        session = await db.get_session(user_db_id)
        was_running = user_db_id in client_manager.active_clients
        await _teardown(user_db_id)

        if not session or session.get("status") in DEAD_STATUSES:
            return was_running

        already = (
            session.get("status") == ST_SUSPENDED
            and session.get("error_message") == reason
        )
        await db.update_session_status(user_db_id, ST_SUSPENDED, reason)
        if not already:
            await db.audit_log(user_db_id, "selfbot_suspended", reason)
            logger.info(f"User {user_db_id} suspended ({reason})")

    if notify and not already and reason in _NOTIFY_SUSPEND:
        user = await db.get_user_by_db_id(user_db_id)
        if user:
            await runtime.notify_user(user["telegram_id"], _NOTIFY_SUSPEND[reason])
    return True


async def revoke_user(user_db_id: int, notify: bool = True) -> None:
    """session در تلگرام باطل شده — پاکسازی کامل و اطلاع به کاربر"""
    async with _lock(user_db_id):
        await _teardown(user_db_id)
        await db.update_session_status(user_db_id, ST_REVOKED, "Session revoked by Telegram")
        await db.audit_log(user_db_id, "session_revoked", "")
        logger.warning(f"User {user_db_id}: session revoked")

    if notify:
        user = await db.get_user_by_db_id(user_db_id)
        if user:
            await runtime.notify_user(user["telegram_id"], _NOTIFY_REVOKED)


async def resume_user(user_db_id: int, notify: bool = True) -> bool:
    """اتصال دوباره‌ی کاربر معلق (پس از تمدید/رفع مسدودی)"""
    from core.plugin_manager import load_plugins_for_user

    async with _lock(user_db_id):
        if user_db_id in client_manager.active_clients:
            return True

        user = await db.get_user_by_db_id(user_db_id)
        ok, reason = eligible(user)
        if not ok:
            return False

        session = await db.get_session(user_db_id)
        if not session or session.get("status") in DEAD_STATUSES:
            return False

        from core import metrics
        if not metrics.admission(len(client_manager.active_clients))[0]:
            await db.update_session_status(user_db_id, ST_SUSPENDED, R_CAPACITY)
            logger.warning(f"User {user_db_id} resume deferred: server at capacity")
            return False

        try:
            session_string = decrypt(session["session_data_enc"])
        except Exception as e:
            await db.update_session_status(user_db_id, ST_ERROR, f"decrypt: {e}"[:200])
            return False

        try:
            client = await client_manager.reconnect_client(user_db_id, session_string)
        except Exception as e:
            await db.update_session_status(user_db_id, ST_SUSPENDED, R_OFFLINE)
            logger.warning(f"User {user_db_id} resume failed (network): {e}")
            return False
        if not client:
            await db.update_session_status(user_db_id, ST_EXPIRED, "Session expired")
            if notify:
                await runtime.notify_user(user["telegram_id"], _NOTIFY_REVOKED)
            return False

        try:
            await load_plugins_for_user(user_db_id, client)
        except Exception as e:
            logger.error(f"load plugins on resume user={user_db_id}: {e}")

        await db.update_session_status(user_db_id, ST_CONNECTED)
        await db.audit_log(user_db_id, "selfbot_resumed", "")
        logger.info(f"User {user_db_id} resumed")

    if notify:
        await runtime.notify_user(user["telegram_id"], _NOTIFY_RESUMED)
    return True


async def sync_user(user_db_id: int, notify: bool = True) -> str:
    """
    هماهنگ کردن وضعیت واقعی با DB — بعد از هر تغییر اشتراک/مسدودی صدا بزنید.
    خروجی: "running" | "suspended" | "none"
    """
    user = await db.get_user_by_db_id(user_db_id)
    ok, reason = eligible(user)
    session = await db.get_session(user_db_id)
    if not session:
        # حتی بدون session، اگر کلاینتی مانده باشد (نباید) قطعش کن
        if not ok and user_db_id in client_manager.active_clients:
            await _teardown(user_db_id)
        return "none"

    if not ok:
        await suspend_user(user_db_id, reason, notify=notify)
        return "suspended"

    if user_db_id in client_manager.active_clients:
        return "running"
    return "running" if await resume_user(user_db_id, notify=notify) else "suspended"


# ═══════════════════════════════════
# Watchdog
# ═══════════════════════════════════


async def _warn_expiry(user: dict) -> None:
    if EXPIRY_WARN_DAYS <= 0:
        return
    expires = user.get("plan_expires_at")
    if not expires:
        return
    if getattr(expires, "tzinfo", None) is None:
        expires = expires.replace(tzinfo=timezone.utc)
    left = expires - datetime.now(timezone.utc)
    if left.total_seconds() <= 0 or left.days >= EXPIRY_WARN_DAYS:
        return
    key = expires.isoformat()
    if _warned.get(user["id"]) == key:
        return
    _warned[user["id"]] = key
    hours = int(left.total_seconds() // 3600)
    when = f"{left.days} روز" if left.days >= 1 else f"{max(1, hours)} ساعت"
    await runtime.notify_user(
        user["telegram_id"],
        f"⏰ <b>یادآوری:</b> اشتراک شما حدود <b>{when}</b> دیگر تمام می‌شود.\n"
        f"برای جلوگیری از توقف سلف‌بات، از «💎 اشتراک» تمدید کنید.",
    )


async def run_checks() -> None:
    """یک دور کامل بررسی (برای تست هم مستقیم قابل صدا زدن است)"""
    global _cycle
    _cycle += 1
    deep = (_cycle % AUTH_CHECK_EVERY) == 0

    # 1) کاربران متصل
    uids = list(client_manager.active_clients.keys())
    users = {u["id"]: u for u in await db.get_users_by_db_ids(uids)} if uids else {}
    for uid in uids:
        user = users.get(uid)
        ok, reason = eligible(user)
        if not ok:
            await suspend_user(uid, reason)
            continue

        await _warn_expiry(user)

        health = await client_manager.check_client_health(uid, deep=deep)
        if health == "revoked":
            await revoke_user(uid)
        elif health == "offline":
            logger.info(f"User {uid}: client offline, will retry next cycle")

    # 2) معلق‌هایی که دوباره واجد شرایط شده‌اند (تمدید/رفع مسدودی/ظرفیت آزاد)
    for row in await db.get_suspended_sessions():
        uid = row["user_id"]
        if uid in client_manager.active_clients:
            continue
        ok, _ = eligible(row)
        if ok:
            # برگشت از قطعی شبکه/ظرفیت نیازی به اعلان ندارد
            quiet = row.get("error_message") in (R_OFFLINE, R_CAPACITY)
            await resume_user(uid, notify=not quiet)

    # 3) خانه‌تکانی
    await client_manager.cleanup_stale_pending()
    prune_rate_limits()


async def _loop() -> None:
    logger.info(f"Access watchdog started (every {ACCESS_CHECK_INTERVAL}s)")
    while True:
        try:
            await asyncio.sleep(ACCESS_CHECK_INTERVAL)
            await run_checks()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"watchdog cycle failed: {type(e).__name__}: {e}", exc_info=True)


def start_watchdog() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop_watchdog() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
