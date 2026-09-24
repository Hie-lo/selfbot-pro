import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    """خواندن متغیر محیطی اجباری"""
    val = os.getenv(name, "").strip()
    if not val:
        print(f"FATAL: {name} is not set in .env", flush=True)
        sys.exit(1)
    return val


# ── Bot ──
BOT_TOKEN: str = _require("BOT_TOKEN")

# ── Admin ──
ADMIN_TELEGRAM_ID: int = int(_require("ADMIN_TELEGRAM_ID"))

# ── Telegram API ──
TELEGRAM_API_ID: int = int(_require("TELEGRAM_API_ID"))
TELEGRAM_API_HASH: str = _require("TELEGRAM_API_HASH")

# ── Database ──
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
DB_NAME: str = _require("DB_NAME")
DB_USER: str = _require("DB_USER")
DB_PASS: str = _require("DB_PASS")
DB_DSN: str = (
    f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ── Encryption ──
ENCRYPTION_KEY: str = _require("ENCRYPTION_KEY")

# ── Security ──
MAX_LOGIN_ATTEMPTS: int = int(os.getenv("MAX_LOGIN_ATTEMPTS", "3"))
LOGIN_TIMEOUT: int = int(os.getenv("LOGIN_TIMEOUT_SECONDS", "120"))
MAX_CLIENTS: int = int(os.getenv("MAX_CLIENTS_PER_SERVER", "20"))

# ── Paths ──
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR: str = os.path.join(BASE_DIR, "sessions")
DOWNLOADS_DIR: str = os.path.join(BASE_DIR, "downloads")
LOGS_DIR: str = os.path.join(BASE_DIR, "logs")

for _d in [SESSIONS_DIR, DOWNLOADS_DIR, LOGS_DIR]:
    os.makedirs(_d, exist_ok=True)

# ── Subscription ──

# پلن‌های قابل خرید — key: کلید داخلی
PLANS: dict[str, dict] = {
    "1m": {"title": "۱ ماهه", "days": 30, "price": 150_000},
    "3m": {"title": "۳ ماهه", "days": 90, "price": 400_000},
    "6m": {"title": "۶ ماهه", "days": 180, "price": 750_000},
    "12m": {"title": "۱۲ ماهه", "days": 365, "price": 1_300_000},
}

# برای سازگاری با نسخه قبلی
MONTHLY_PRICE_TOMAN: int = PLANS["1m"]["price"]

# ── پرداخت کارت به کارت ──
CARD_NUMBER: str = os.getenv("CARD_NUMBER", "6037-0000-0000-0000")
CARD_HOLDER: str = os.getenv("CARD_HOLDER", "نام صاحب کارت")
BANK_NAME: str = os.getenv("BANK_NAME", "")

# حداکثر حجم عکس رسید (مگابایت)
RECEIPT_MAX_MB: int = int(os.getenv("RECEIPT_MAX_MB", "10"))

# ── Recents (استیکر و گیف‌های اخیر) ──
# document → ارسال به‌صورت فایل (هیچ‌وقت به Recents اضافه نمی‌شود) — پیش‌فرض
# cleanup  → ارسال به‌صورت استیکر/گیف واقعی + حذف از Recents با API
# off      → رفتار قبلی (ممکن است Recents را پر کند)
CLEAN_RECENTS_MODE: str = os.getenv("CLEAN_RECENTS_MODE", "document").strip().lower()
# ── فوروارد: سرعت و مقاومت در برابر محدودیت تلگرام ──
# هیچ سقفی روی تعداد پیام نیست؛ job تا پایان ادامه می‌دهد و پس از
# ری‌استارت سرور هم از همان‌جا ادامه پیدا می‌کند.
FORWARD_BATCH_SIZE: int = int(os.getenv("FORWARD_BATCH_SIZE", "20"))
FORWARD_BATCH_PAUSE: float = float(os.getenv("FORWARD_BATCH_PAUSE", "1.0"))

# ── سرعت (پریست): safe | balanced | fast | max ──
# خود موتور با بازخورد تلگرام تنظیمش می‌کند (Pacer): FloodWait خورد → کندتر،
# چند پیام بی‌مشکل رفت → تندتر. پس لازم نیست دستی وسواس کنی.
FORWARD_SPEED: str = os.getenv("FORWARD_SPEED", "balanced").strip().lower()

# مقدار دستی (اختیاری) — اگر ست شود، جای pause پریست را می‌گیرد.
# ⚠️ اگر مقدار قدیمی و کند باشد (مثل ۰.۸) سرعت پایین می‌ماند؛ برای همین
# هشدار می‌دهیم تا از .env حذفش کنی و با پریست‌ها کار کنی.
FORWARD_MSG_PAUSE_ENV: str | None = os.getenv("FORWARD_MSG_PAUSE")
FORWARD_MSG_PAUSE: float = (
    float(FORWARD_MSG_PAUSE_ENV) if FORWARD_MSG_PAUSE_ENV else 0.2
)
FORWARD_SPEED_ENV: str | None = os.getenv("FORWARD_SPEED")

_PRESET_PAUSE = {"safe": 0.8, "balanced": 0.2, "fast": 0.08, "max": 0.0}
_SLOW_ENV_WARNING = bool(
    os.getenv("FORWARD_MSG_PAUSE")
    and not os.getenv("FORWARD_SPEED")
    and FORWARD_MSG_PAUSE > _PRESET_PAUSE.get(FORWARD_SPEED, 0.2)
)

# چند پیام همزمان (0 = از پریست)
FORWARD_CONCURRENCY: int = int(os.getenv("FORWARD_CONCURRENCY", "0"))

# ── فاز جمع‌آوری کش (خواندن تاریخچه، سبک‌تر از ارسال) ──
FORWARD_CACHE_BATCH: int = int(os.getenv("FORWARD_CACHE_BATCH", "100"))
FORWARD_CACHE_PAUSE: float = float(os.getenv("FORWARD_CACHE_PAUSE", "0.15"))
FORWARD_MAX_FLOOD_WAIT: int = int(os.getenv("FORWARD_MAX_FLOOD_WAIT", "86400"))

# ── حالت کش (ذخیره روی سرور قبل از ارسال) ──
# وقتی روشن باشد، اول همه پیام‌ها روی سرور ذخیره می‌شوند و بعد ارسال
# می‌شوند؛ اگر وسط کار دسترسی به مبدأ از دست برود، ارسال ادامه می‌یابد.
FORWARD_CACHE_MODE: bool = os.getenv("FORWARD_CACHE_MODE", "1").strip().lower() in (
    "1", "true", "yes", "on",
)
FORWARD_CACHE_CAPTURE_MEDIA: bool = os.getenv(
    "FORWARD_CACHE_CAPTURE_MEDIA", "0"
).strip().lower() in ("1", "true", "yes", "on")
FORWARD_CACHE_MAX_GB: float = float(os.getenv("FORWARD_CACHE_MAX_GB", "5"))
FORWARD_CACHE_MEDIA_MAX_MB: int = int(os.getenv("FORWARD_CACHE_MEDIA_MAX_MB", "100"))

# ── کنترل دسترسی زنده (watchdog) ──
# هر چند ثانیه وضعیت اشتراک/مسدودی/سلامت session کاربران متصل بررسی شود
ACCESS_CHECK_INTERVAL: int = max(15, int(os.getenv("ACCESS_CHECK_INTERVAL", "60")))
# هر چند دور، اعتبار session از خود تلگرام پرسیده شود (سبک ولی یک درخواست API)
AUTH_CHECK_EVERY: int = max(1, int(os.getenv("AUTH_CHECK_EVERY", "5")))
# چند روز قبل از انقضا به کاربر هشدار داده شود (0 = خاموش)
EXPIRY_WARN_DAYS: int = int(os.getenv("EXPIRY_WARN_DAYS", "3"))


def _env_bool(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ── Performance / Capacity ──
# قفل «فقط یک نمونه» (جلوگیری از AUTH_KEY_DUPLICATED و باطل شدن sessionها)
INSTANCE_LOCK: bool = _env_bool("INSTANCE_LOCK", "1")
INSTANCE_LOCK_WAIT: int = int(os.getenv("INSTANCE_LOCK_WAIT", "30"))
# کنترل پذیرش اکانت جدید بر اساس منابع واقعی (0 = خاموش)
MIN_FREE_MEM_MB: int = int(os.getenv("MIN_FREE_MEM_MB", "150"))
MAX_RSS_MB: int = int(os.getenv("MAX_RSS_MB", "0"))
LAG_LIMIT_MS: int = int(os.getenv("LAG_LIMIT_MS", "500"))
# چند اکانت همزمان هنگام روشن شدن وصل شوند
STARTUP_CONCURRENCY: int = max(1, int(os.getenv("STARTUP_CONCURRENCY", "4")))
# حلقه‌ی سریع‌تر asyncio (در صورت نصب بودن uvloop)
USE_UVLOOP: bool = _env_bool("USE_UVLOOP", "1")
# چرخش فایل لاگ
LOG_MAX_MB: int = max(1, int(os.getenv("LOG_MAX_MB", "10")))
LOG_BACKUPS: int = max(1, int(os.getenv("LOG_BACKUPS", "5")))

# ── ضدحذف / ضدویرایش (کش مشترک پی‌وی) ──
PV_CACHE_MAX_MB: float = float(os.getenv("PV_CACHE_MAX_MB", "8"))
PV_CACHE_MAX_AGE_HOURS: int = int(os.getenv("PV_CACHE_MAX_AGE_HOURS", "168"))
# حذف‌های بیشتر از این تعداد در یک لحظه → یک گزارش خلاصه + فایل
PV_BURST_THRESHOLD: int = max(1, int(os.getenv("PV_BURST_THRESHOLD", "5")))
# گاوصندوق مدیا: مدیای کوچکِ طرف مقابل همان لحظه دانلود و رمزنگاری‌شده
# نگه داشته می‌شود تا بعد از حذف هم قابل بازیابی باشد
PV_VAULT_ENABLED: bool = _env_bool("PV_VAULT_ENABLED", "1")
PV_VAULT_TYPES: set = {
    t.strip() for t in os.getenv("PV_VAULT_TYPES", "photo,voice,round").split(",")
    if t.strip()
}
PV_VAULT_MAX_MB: float = float(os.getenv("PV_VAULT_MAX_MB", "5"))
PV_VAULT_QUOTA_MB: float = float(os.getenv("PV_VAULT_QUOTA_MB", "50"))
PV_VAULT_TTL_HOURS: int = int(os.getenv("PV_VAULT_TTL_HOURS", "48"))
# بازخوانی هنگام روشن شدن: پیام‌های اخیر پی‌وی‌های فعال از خود تلگرام خوانده
# می‌شود تا پیام‌هایی که قبل از ری‌استارت (یا قبل از روشن کردن ضدحذف) رسیده‌اند
# و بعداً حذف می‌شوند هم پوشش داده شوند
PV_WARMUP_ENABLED: bool = _env_bool("PV_WARMUP_ENABLED", "1")
PV_WARMUP_HOURS: int = max(1, int(os.getenv("PV_WARMUP_HOURS", "48")))
PV_WARMUP_CHATS: int = max(1, int(os.getenv("PV_WARMUP_CHATS", "50")))
PV_WARMUP_MESSAGES: int = max(1, min(100, int(os.getenv("PV_WARMUP_MESSAGES", "100"))))
PV_WARMUP_VAULT_FILES: int = max(0, int(os.getenv("PV_WARMUP_VAULT_FILES", "20")))
