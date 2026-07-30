"""
نقطه ورود اصلی
"""

import logging
import sys

from telegram import Update
from telegram.ext import Application, ContextTypes

from config import BOT_TOKEN
from database.db import init_db
from core.engine import startup, shutdown
from bot.handlers import register_handlers

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ],
)

# لاگ httpx را کم می‌کنیم تا توکن در URL چاپ نشود
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("main")


async def post_init(app: Application):
    await init_db()
    await startup()
    logger.info("Bot is running!")


async def post_shutdown(app: Application):
    await shutdown()
    logger.info("Bot stopped cleanly")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled bot exception", exc_info=context.error)


def main():
    logger.info("Starting SelfBot Pro...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    register_handlers(app)
    app.add_error_handler(error_handler)

    print("\n" + "=" * 50)
    print("🚀 SelfBot Pro")
    print("=" * 50 + "\n")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        logger.error(f"Fatal: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()