"""
مدیریت کلاینت‌های Telethon با StringSession
- هیچ فایل session روی دیسک ساخته نمی‌شود
- session به صورت رمزنگاری شده در DB ذخیره می‌شود
"""

import logging
import time
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    FloodWaitError,
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    SessionRevokedError,
    SessionExpiredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)
from config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    LOGIN_TIMEOUT,
    MAX_CLIENTS,
)

logger = logging.getLogger("client_manager")

# کلاینت‌های فعال (لاگین شده و آماده کار)
# key: user_db_id (int), value: TelegramClient
active_clients: dict[int, TelegramClient] = {}

# کلاینت‌های در حال لاگین (موقت)
# key: user_db_id (int), value: dict با اطلاعات لاگین
_pending: dict[int, dict] = {}

# تنظیمات کلاینت
_CLIENT_KWARGS = {
    "api_id": TELEGRAM_API_ID,
    "api_hash": TELEGRAM_API_HASH,
    "device_model": "iPhone 14 Pro",
    "app_version": "10.9.0",
    "system_version": "iOS 16.5",
    "lang_code": "en",
    "system_lang_code": "en",
    # خودمان FloodWait را مدیریت می‌کنیم (خواب با نمایش پیشرفت + قابل توقف)
    "flood_sleep_threshold": 0,
}


def _check_pending(info: dict | None) -> dict:
    """اطمینان از وجود و منقضی نشدن درخواست لاگین"""
    if not info:
        raise ValueError("درخواست لاگین منقضی شده. دوباره تلاش کنید.")
    created = info.get("created_at", 0)
    if created and (time.time() - created) > LOGIN_TIMEOUT:
        raise ValueError("زمان ورود کد تمام شد. دوباره تلاش کنید.")
    return info


def _make_client(session_string: str = "", user_db_id: int | None = None) -> TelegramClient:
    """ساخت کلاینت با StringSession"""
    session = StringSession(session_string)
    client = TelegramClient(session, **_CLIENT_KWARGS)
    client._sb_user = user_db_id
    # ثبت حذف/ویرایش‌های خود سلف‌بات (جلوگیری از گزارش کاذب ضدحذف/ضدویرایش)
    from core import self_actions
    self_actions.install(client)
    return client


# ═══════ Login Flow ═══════


async def request_login_code(user_db_id: int, phone: str) -> str:
    """
    مرحله ۱: ارسال کد تایید
    خروجی: phone_code_hash
    """
    await cleanup_pending(user_db_id)

    # قبل از فرستادن کد: اگر سرور جا ندارد وقت کاربر را نگیر
    from core import metrics
    ok, reason = metrics.admission(len(active_clients), new_login=True)
    if not ok:
        raise ValueError(metrics.admission_message(reason))

    client = _make_client(user_db_id=user_db_id)

    try:
        await client.connect()
        result = await client.send_code_request(phone)

    except FloodWaitError as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise ValueError(f"محدودیت تلگرام. {e.seconds} ثانیه صبر کنید.")

    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass

        logger.error(
            f"send_code failed user_db_id={user_db_id}: {type(e).__name__}: {e}"
        )

        msg = str(e).lower()
        if (
            "0 bytes read" in msg
            or "connection" in msg
            or "timed out" in msg
            or "timeout" in msg
        ):
            raise ValueError("ارتباط با تلگرام ناپایدار بود. دوباره تلاش کنید.")

        raise ValueError("ارسال کد با خطا مواجه شد.")

    _pending[user_db_id] = {
        "client": client,
        "phone": phone,
        "phone_code_hash": result.phone_code_hash,
        "created_at": time.time(),
    }

    logger.info(f"Code sent for user_db_id={user_db_id}")
    return result.phone_code_hash


async def complete_login(user_db_id: int, code: str) -> str:
    """
    مرحله ۲: ورود کد تایید
    خروجی: "success" یا "2fa_required"
    """
    info = _pending.get(user_db_id)
    try:
        _check_pending(info)
    except ValueError:
        await cleanup_pending(user_db_id)
        raise

    client = info["client"]

    try:
        await client.sign_in(
            phone=info["phone"],
            code=code,
            phone_code_hash=info["phone_code_hash"],
        )
        logger.info(f"Login success for user_db_id={user_db_id}")
        return "success"

    except SessionPasswordNeededError:
        logger.info(f"2FA required for user_db_id={user_db_id}")
        return "2fa_required"

    except PhoneCodeExpiredError:
        await cleanup_pending(user_db_id)
        raise ValueError("کد منقضی شده. دوباره تلاش کنید.")

    except PhoneCodeInvalidError:
        raise ValueError("کد اشتباه است.")

    except Exception:
        await cleanup_pending(user_db_id)
        raise


async def complete_2fa(user_db_id: int, password: str) -> str:
    """
    مرحله ۳: ورود رمز دوعاملی
    خروجی: "success"
    """
    info = _pending.get(user_db_id)
    try:
        _check_pending(info)
    except ValueError:
        await cleanup_pending(user_db_id)
        raise

    client = info["client"]

    try:
        await client.sign_in(password=password)
        logger.info(f"2FA success for user_db_id={user_db_id}")
        return "success"
    except Exception:
        await cleanup_pending(user_db_id)
        raise


async def finalize_login(user_db_id: int) -> str:
    """
    نهایی کردن لاگین + بارگذاری پلاگین‌ها
    خروجی: session_string
    """
    info = _pending.get(user_db_id)
    _check_pending(info)

    # ظرفیت سرور (تعداد + RAM آزاد + بار فعلی)
    from core import metrics
    ok, reason = metrics.admission(len(active_clients), new_login=True)
    if not ok:
        await cleanup_pending(user_db_id)
        raise ValueError(metrics.admission_message(reason))

    client = info["client"]
    session_string = client.session.save()

    # انتقال به active
    active_clients[user_db_id] = client
    del _pending[user_db_id]

    # بارگذاری پلاگین‌ها
    from core.plugin_manager import load_plugins_for_user
    await load_plugins_for_user(user_db_id, client)

    logger.info(f"Client finalized for user_db_id={user_db_id}")
    return session_string


# ═══════ Reconnect ═══════


async def reconnect_client(
    user_db_id: int, session_string: str
) -> TelegramClient | None:
    """
    اتصال مجدد از session string ذخیره شده.

    خروجی None فقط یعنی «session واقعاً نامعتبر است».
    خطای شبکه/موقت → exception بالا می‌رود تا صدازننده session را
    (به‌اشتباه) باطل‌شده علامت نزند و بعداً دوباره تلاش شود.
    """
    client = _make_client(session_string, user_db_id)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            logger.warning(f"Session expired for user_db_id={user_db_id}")
            await client.disconnect()
            return None

        active_clients[user_db_id] = client
        logger.info(f"Reconnected user_db_id={user_db_id}")
        return client

    except SESSION_DEAD_ERRORS as e:
        logger.warning(f"Session dead user_db_id={user_db_id}: {type(e).__name__}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return None

    except Exception as e:
        logger.error(f"Reconnect failed user_db_id={user_db_id}: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        raise


# ═══════ Disconnect ═══════


async def disconnect_client(user_db_id: int):
    """قطع یک کلاینت فعال"""
    client = active_clients.pop(user_db_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception as e:
            logger.error(f"Disconnect error: {e}")
        logger.info(f"Disconnected user_db_id={user_db_id}")


async def get_client(user_db_id: int) -> TelegramClient | None:
    """دریافت کلاینت فعال"""
    return active_clients.get(user_db_id)


# ═══════ Health / Logout ═══════

# خطاهایی که یعنی session دیگر معتبر نیست (کاربر از تنظیمات تلگرام
# «Terminate session» زده، اکانت حذف/بن شده، یا کلید تکراری شده)
SESSION_DEAD_ERRORS = (
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    SessionRevokedError,
    SessionExpiredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)


async def check_client_health(user_db_id: int, deep: bool = False) -> str:
    """
    بررسی سلامت کلاینت فعال.

    خروجی: "ok" | "revoked" | "offline" | "missing"
      - اتصال افتاده باشد، یک بار تلاش به اتصال مجدد می‌شود
      - deep=True → از خود تلگرام می‌پرسد session هنوز معتبر است یا نه
    """
    client = active_clients.get(user_db_id)
    if client is None:
        return "missing"

    try:
        if not client.is_connected():
            await client.connect()
        if deep or not client.is_connected():
            if not await client.is_user_authorized():
                return "revoked"
        return "ok" if client.is_connected() else "offline"
    except SESSION_DEAD_ERRORS:
        return "revoked"
    except Exception as e:
        logger.warning(f"Health check user_db_id={user_db_id}: {type(e).__name__}: {e}")
        return "offline"


async def logout_session(user_db_id: int, session_string: str | None = None) -> bool:
    """
    خروج واقعی از تلگرام (باطل کردن session روی سرور تلگرام).

    قبلاً «قطع اکانت» فقط اتصال را می‌بست و session روی تلگرام معتبر
    می‌ماند؛ اگر رشته‌ی session جایی لو می‌رفت، هنوز قابل استفاده بود.
    """
    client = active_clients.pop(user_db_id, None)
    temp = False
    if client is None and session_string:
        client = _make_client(session_string)
        temp = True

    if client is None:
        return False

    ok = False
    try:
        if not client.is_connected():
            await client.connect()
        ok = bool(await client.log_out())
    except SESSION_DEAD_ERRORS:
        ok = True   # همین حالا هم باطل است
    except Exception as e:
        logger.warning(f"log_out failed user_db_id={user_db_id}: {type(e).__name__}: {e}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    logger.info(
        f"Logout user_db_id={user_db_id} ok={ok}{' (temp client)' if temp else ''}"
    )
    return ok


async def cleanup_stale_pending() -> int:
    """
    بستن لاگین‌های نیمه‌کاره‌ای که مهلتشان تمام شده.

    بدون این، هر «ارسال کد» که کاربر ادامه‌اش نمی‌داد یک اتصال باز
    Telethon را برای همیشه در حافظه نگه می‌داشت (نشت منابع / DoS).
    """
    now = time.time()
    stale = [
        uid for uid, info in _pending.items()
        if now - (info.get("created_at") or 0) > LOGIN_TIMEOUT + 30
    ]
    for uid in stale:
        await cleanup_pending(uid)
    return len(stale)


# ═══════ Cleanup ═══════


async def cleanup_pending(user_db_id: int):
    """پاکسازی login ناتمام"""
    info = _pending.pop(user_db_id, None)
    if info and info.get("client"):
        try:
            await info["client"].disconnect()
        except Exception:
            pass
        logger.info(f"Pending cleaned for user_db_id={user_db_id}")


async def disconnect_all():
    """قطع همه کلاینت‌ها"""
    for uid in list(active_clients.keys()):
        await disconnect_client(uid)

    for uid in list(_pending.keys()):
        await cleanup_pending(uid)

    logger.info("All clients disconnected")