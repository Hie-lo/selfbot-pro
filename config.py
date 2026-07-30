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
MONTHLY_PRICE_TOMAN: int = 150_000