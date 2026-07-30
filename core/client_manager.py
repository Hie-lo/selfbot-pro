"""
مدیریت کلاینت‌های Telethon با StringSession
- هیچ فایل session روی دیسک ساخته نمی‌شود
- session به صورت رمزنگاری شده در DB ذخیره می‌شود
"""

import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    FloodWaitError,
)
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH

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
}


def _make_client(session_string: str = "") -> TelegramClient:
    """ساخت کلاینت با StringSession"""
    session = StringSession(session_string)
    return TelegramClient(session, **_CLIENT_KWARGS)


# ═══════ Login Flow ═══════


async def request_login_code(user_db_id: int, phone: str) -> str:
    """
    مرحله ۱: ارسال کد تایید
    خروجی: phone_code_hash
    """
    await cleanup_pending(user_db_id)

    client = _make_client()

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
    }

    logger.info(f"Code sent for user_db_id={user_db_id}")
    return result.phone_code_hash


async def complete_login(user_db_id: int, code: str) -> str:
    """
    مرحله ۲: ورود کد تایید
    خروجی: "success" یا "2fa_required"
    """
    info = _pending.get(user_db_id)
    if not info:
        raise ValueError("درخواست لاگین منقضی شده. دوباره تلاش کنید.")

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
    if not info:
        raise ValueError("درخواست لاگین منقضی شده.")

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
    مرحله نهایی: گرفتن session string و فعال‌سازی کلاینت
    خروجی: session_string رمزنگاری نشده
    """
    info = _pending.get(user_db_id)
    if not info:
        raise ValueError("درخواست لاگین منقضی شده.")

    client = info["client"]

    # گرفتن session string
    session_string = client.session.save()

    # انتقال به active
    active_clients[user_db_id] = client

    # حذف از pending (بدون disconnect)
    del _pending[user_db_id]

    logger.info(f"Client finalized for user_db_id={user_db_id}")
    return session_string


# ═══════ Reconnect ═══════


async def reconnect_client(
    user_db_id: int, session_string: str
) -> TelegramClient | None:
    """اتصال مجدد از session string ذخیره شده"""
    client = _make_client(session_string)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            logger.warning(f"Session expired for user_db_id={user_db_id}")
            await client.disconnect()
            return None

        active_clients[user_db_id] = client
        logger.info(f"Reconnected user_db_id={user_db_id}")
        return client

    except Exception as e:
        logger.error(f"Reconnect failed user_db_id={user_db_id}: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return None


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