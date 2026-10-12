"""
وضعیت سراسری زمان اجرا — برای ارتباط موتور سلف‌بات با ربات کنترلی.

`main.post_init` این مقادیر را پر می‌کند:
  bot           → نمونه‌ی telegram.Bot (برای ارسال اعلان به کاربر)
  bot_username  → یوزرنیم ربات کنترلی (برای راهنمای دکمه‌ای inline)
"""

import logging

logger = logging.getLogger("runtime")

bot = None
bot_username: str | None = None

# آیدی تلگرامی اکانت‌های سلف‌بات متصل → user_db_id
# (برای مجاز دانستن inline query راهنما از طرف همین اکانت‌ها)
selfbot_accounts: dict[int, int] = {}


async def notify_photo(telegram_id: int, photo: bytes, caption: str = "",
                       **kwargs) -> bool:
    """
    ارسال عکس از طرف ربات کنترلی (بی‌صدا در صورت خطا).
    برای کارت پروفایل «کاربر» لازم است؛ notify_user فقط متن می‌فرستد.
    """
    if bot is None or not telegram_id or not photo:
        return False
    try:
        kwargs.setdefault("parse_mode", "HTML")
        await bot.send_photo(chat_id=telegram_id, photo=photo,
                             caption=caption[:1024], **kwargs)
        return True
    except Exception as e:
        logger.debug(f"notify_photo {telegram_id} skipped: {e}")
        return False


async def notify_user(telegram_id: int, text: str, **kwargs) -> bool:
    """ارسال پیام از طرف ربات کنترلی به کاربر (بی‌صدا در صورت خطا)"""
    if bot is None or not telegram_id:
        return False
    try:
        kwargs.setdefault("parse_mode", "HTML")
        await bot.send_message(chat_id=telegram_id, text=text, **kwargs)
        return True
    except Exception as e:
        logger.debug(f"notify_user {telegram_id} skipped: {e}")
        return False
