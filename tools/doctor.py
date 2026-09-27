#!/usr/bin/env python3
"""
🩺 دکتر SelfBot Pro — عیب‌یابی یک‌مرحله‌ای روی سرور.

اجرا (از پوشه‌ی پروژه، با venv فعال):
    python tools/doctor.py

چه چیزهایی را چک می‌کند:
  ۱) نسخه‌ی کد (کامیت فعلی) و وضعیت فایل‌های مهم
  ۲) نسخه‌ی پایتون + نصب بودن همه‌ی وابستگی‌ها
  ۳) خوانده شدن config و وجود کلیدهای .env (بدون چاپ مقدارها)
  ۴) اتصال به دیتابیس + وجود جدول‌ها (از جمله جدول‌های AI)
  ۵) ایمپورت و لود شدن همه‌ی پلاگین‌ها (همان کاری که موقع استارت ربات انجام می‌شود)
  ۶) ثبت هندلرهای ربات کنترلی + تست مسیریابی (باگ «ربات ساکت می‌شود»)
  ۷) سرویس‌های AI: تعداد providerهای تنظیم‌شده و آماده بودن httpx
  ۸) خطاهای مهم آخرین اجرا از logs/bot.log

با سوئیچ --init: اگر جدول‌ها ساخته نشده باشند، آن‌ها را می‌سازد
(همان کاری که موقع روشن شدن ربات انجام می‌شود). برای اجرای روان‌تر
ربات هم بی‌خطر است، چون همه‌ی CREATEها IF NOT EXISTS هستند.

خروجی: خلاصه‌ی ✅/❌ و در پایان، دستور پیشنهادی برای هر مشکل.
"""

import asyncio
import os
import re
import subprocess
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

GREEN, RED, YELLOW, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""

results: list[tuple[str, bool, str]] = []
advice: list[str] = []


def ok(name: str, extra: str = ""):
    results.append((name, True, extra))
    print(f"{GREEN}✅ {name}{RESET}" + (f"  {DIM}{extra}{RESET}" if extra else ""))


def bad(name: str, extra: str = "", hint: str = ""):
    results.append((name, False, extra))
    print(f"{RED}❌ {name}{RESET}" + (f"  {extra}" if extra else ""))
    if hint:
        advice.append(hint)


def warn(name: str, extra: str = "", hint: str = ""):
    results.append((name, True, extra))
    print(f"{YELLOW}⚠️  {name}{RESET}" + (f"  {extra}" if extra else ""))
    if hint:
        advice.append(hint)


def section(title: str):
    print(f"\n{DIM}── {title} ──{RESET}")


# ═══════════ ۱) کد ═══════════

def check_code():
    section("۱) نسخه‌ی کد")
    if not os.path.isdir(".git"):
        warn("این پوشه مخزن git نیست", hint="مطمئن شو در پوشه‌ی selfbot-pro هستی.")
        return
    try:
        commit = subprocess.run(["git", "log", "--oneline", "-1"],
                                capture_output=True, text=True, timeout=15).stdout.strip()
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=15).stdout.strip()
        ok("کد", f"{commit}  (branch: {branch})")
    except Exception as e:
        warn(f"خواندن git نشد: {e}")

    for f in ("main.py", "core/plugin_manager.py", "plugins/ai_reply.py",
              "core/ai_providers.py", "bot/handlers.py", ".env", "requirements.txt"):
        if not os.path.exists(f):
            if f == ".env":
                bad(f"فایل {f} وجود ندارد", hint="فایل .env را از .env.example بساز و پرش کن.")
            elif f in ("plugins/ai_reply.py", "core/ai_providers.py"):
                bad(f"فایل {f} وجود ندارد", hint="کد قدیمی است: git fetch && git reset --hard FETCH_HEAD")
            else:
                bad(f"فایل {f} وجود ندارد")
        else:
            ok(f"فایل {f} هست")
    if os.path.exists("logs/bot.log"):
        ok("logs/bot.log موجود است")
    else:
        warn("logs/bot.log نیست", hint="ربات حداقل یک بار اجرا نشده یا داخل پوشه‌ی دیگری اجرا می‌شود.")


# ═══════════ ۲) وابستگی‌ها ═══════════

def check_deps():
    section("۲) پایتون و وابستگی‌ها")
    ok("نسخه‌ی پایتون", sys.version.split()[0])
    if sys.version_info < (3, 10):
        bad("پایتون قدیمی است", "پروژه به ۳.۱۰+ نیاز دارد",
            hint="پایتون ۳.۱۱ یا بالاتر نصب کن.")
    for mod, name in [
        ("telethon", "telethon"), ("telegram", "python-telegram-bot"),
        ("asyncpg", "asyncpg"), ("dotenv", "python-dotenv"),
        ("cryptography", "cryptography"), ("psutil", "psutil"),
        ("PIL", "Pillow"), ("httpx", "httpx"),
    ]:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "")
            ok(f"پکیج {name}", ver)
        except Exception as e:
            bad(f"پکیج {name} نصب نیست", type(e).__name__,
                hint="cd " + ROOT + " && ./venv/bin/pip install -r requirements.txt")


# ═══════════ ۳) تنظیمات ═══════════

def check_config():
    section("۳) تنظیمات .env")
    try:
        import config
    except Exception as e:
        bad("خواندن config نشد", f"{type(e).__name__}: {e}",
            hint="پیام FATAL معمولاً می‌گوید کدام کلید .env خالی است.")
        return None
    ok("config خوانده شد")
    needed = ["BOT_TOKEN", "ADMIN_TELEGRAM_ID", "TELEGRAM_API_ID",
              "TELEGRAM_API_HASH", "DB_NAME", "DB_USER", "DB_PASS", "ENCRYPTION_KEY"]
    missing = [n for n in needed if not getattr(config, n, None)]
    if missing:
        bad("کلیدهای خالی در config", ", ".join(missing),
            hint="این کلیدها را در .env پر کن: " + ", ".join(missing))
    else:
        ok("همه‌ی کلیدهای ضروری config پر هستند")

    providers_env = os.getenv("AI_PROVIDERS", "").strip()
    if providers_env:
        import json
        try:
            data = json.loads(providers_env)
            ok(f"AI_PROVIDERS خوانده شد", f"{len(data)} سرویس")
        except Exception as e:
            bad("AI_PROVIDERS معتبر نیست (JSON خراب)", str(e)[:80],
                hint="مقدار AI_PROVIDERS را در .env درست کن (نمونه در .env.example).")
    else:
        if os.getenv("AI_API_KEY", "").strip():
            ok("حالت ساده‌ی AI (AI_API_KEY) تنظیم است")
        else:
            warn("هیچ سرویس AI تنظیم نشده (اختیاری)",
                 hint="برای قابلیت هوش مصنوعی، AI_PROVIDERS را در .env بگذار.")
    return config


# ═══════════ ۴) دیتابیس ═══════════

async def check_db(config):
    section("۴) دیتابیس")
    try:
        import asyncpg
    except Exception:
        bad("asyncpg نصب نیست")
        return
    dsn = getattr(config, "DB_DSN", "")
    if "--init" in sys.argv:
        try:
            from database import db as _db
            await _db.init_db()
            ok("جدول‌ها با --init ساخته/به‌روز شدند")
            await _db.close_db()
        except Exception as e:
            bad("اجرای init_db شکست خورد", f"{type(e).__name__}: {str(e)[:140]}",
                hint="این خطا معمولاً علت اصلی بالا نیامدن ربات است — کاملش را بفرست.")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=10)
    except Exception as e:
        bad("اتصال به دیتابیس برقرار نشد", f"{type(e).__name__}: {str(e)[:120]}",
            hint="پستگرس را چک کن: systemctl status postgresql — و DB_HOST/DB_PORT/DB_NAME در .env")
        return
    ok("اتصال به دیتابیس برقرار شد")
    try:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        names = {r["table_name"] for r in tables}
        ok(f"جدول‌ها: {len(names)}", ", ".join(sorted(names)[:8]) + (" …" if len(names) > 8 else ""))
        for t in ("users", "feature_toggles", "auto_response_rules"):
            if t not in names:
                bad(f"جدول {t} ساخته نشده",
                    hint="با «python tools/doctor.py --init» یا یک بار اجرای ربات، جدول‌ها ساخته می‌شوند.")
        for t in ("ai_profiles", "ai_memory"):
            if t not in names:
                warn(f"جدول {t} نیست (مخصوص هوش مصنوعی)",
                     hint="با اولین اجرای نسخه‌ی جدید خودکار ساخته می‌شود.")
        # خطاهای رایج: رکورد تکراری که ساخت index یکتا را می‌شکند
        if "auto_response_rules" in names:
            try:
                dup = await conn.fetchval(
                    """SELECT COUNT(*) FROM (
                           SELECT user_id, target_user_id FROM auto_response_rules
                           GROUP BY 1, 2 HAVING COUNT(*) > 1) d""")
                if dup:
                    warn(f"{dup} دشمن تکراری در دیتابیس (مهاجرت قدیمی)",
                         hint="نسخه‌ی جدید خودش پاک‌سازی می‌کند؛ یک بار ربات را اجرا کن.")
                else:
                    ok("رکورد تکراری در لیست دشمن نیست")
            except Exception as e:
                bad("بررسی رکوردهای تکراری خطا داد", str(e)[:100])
    finally:
        await conn.close()


# ═══════════ ۵) پلاگین‌ها ═══════════

def check_plugins():
    section("۵) پلاگین‌ها")
    try:
        from core import plugin_manager as pm
    except Exception as e:
        bad("ایمپورت plugin_manager شکست خورد", f"{type(e).__name__}: {e}",
            hint="خروجی کامل Traceback را بفرست.")
        traceback.print_exc()
        return
    failed = [n for n, cls in pm._loaded.items() if cls is None]
    if failed:
        bad("پلاگین‌هایی که ایمپورت نشدند", ", ".join(failed),
            hint="وابستگی‌های همان پلاگین نصب نیست یا فایل کد قدیمی است.")
    else:
        ok("همه‌ی پلاگین‌ها ایمپورت شدند")
    ok(f"همیشه‌روشن: {len(pm.ALWAYS_ON_PLUGINS)}",
       ", ".join(sorted(pm.ALWAYS_ON_PLUGINS)))
    ok(f"قابل‌تنظیم: {len(pm.TOGGLEABLE_PLUGINS)}",
       ", ".join(sorted(pm.TOGGLEABLE_PLUGINS)))


# ═══════════ ۶) هندلرهای ربات کنترلی ═══════════

async def check_handlers():
    section("۶) هندلرهای ربات کنترلی (باگ «ربات ساکت»)")
    try:
        from telegram.ext import Application
        from bot.handlers import register_handlers
    except Exception as e:
        bad("ایمپورت هندلرها شکست خورد", f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return
    try:
        app = Application.builder().token("1:x").build()
        await register_handlers(app)
    except Exception as e:
        bad("ثبت هندلرها شکست خورد", f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return

    total = sum(len(g) for g in app.handlers.values())
    ok(f"هندلرها ثبت شدند: {total}")

    # هندلر همه‌گیر وسط گروه = بقیه هرگز اجرا نمی‌شوند
    caught = []
    for group, handlers in app.handlers.items():
        if group < 0:
            continue
        for h in handlers[:-1]:
            cb = getattr(h, "callback", None)
            name = getattr(cb, "__name__", type(h).__name__)
            if name == "route_ai_callbacks":
                continue
            try:
                checks = [h.check_update(_fake_update(kind)) for kind in ("callback", "message")]
            except Exception:
                continue
            if all(c is not None and c is not False for c in checks):
                caught.append(f"گروه {group}: {name}")
    if caught:
        bad("هندلر همه‌گیر پیدا شد (بقیه‌ی دکمه‌ها کار نمی‌کنند)", ", ".join(caught),
            hint="کد قدیمی است → git fetch && git reset --hard FETCH_HEAD")
    else:
        ok("هیچ هندلر همه‌گیری جلوی بقیه را نمی‌گیرد")

    try:
        from bot.keyboards import main_menu_kb
        kb = str(main_menu_kb(has_account=True, is_admin=False))
        ok("دکمه‌های منو ساخته می‌شوند",
           "ai:menu" in kb and "features" in kb)
    except Exception as e:
        bad("ساخت کیبورد منو خطا داد", f"{type(e).__name__}: {e}")


def _fake_update(kind: str):
    from telegram import CallbackQuery, Chat, Message, Update, User
    user = User(id=1, first_name="T", is_bot=False)
    chat = Chat(id=1, type=Chat.PRIVATE)
    if kind == "callback":
        q = CallbackQuery(id="1", from_user=user, chat_instance="ci", data="panel")
        q._unfreeze()
        q.message = Message(message_id=1, date=None, chat=chat)
        q.message._unfreeze()
        u = Update(update_id=1, callback_query=q)
    else:
        m = Message(message_id=1, date=None, chat=chat, from_user=user, text="سلام")
        m._unfreeze()
        u = Update(update_id=2, message=m)
    u._unfreeze()
    return u


# ═══════════ ۷) سرویس‌های AI ═══════════

def check_ai():
    section("۷) سرویس‌های هوش مصنوعی")
    try:
        from core import ai_providers
    except Exception as e:
        bad("ایمپورت ai_providers شکست خورد", f"{type(e).__name__}: {e}")
        return
    hx = getattr(ai_providers, "_http", lambda: None)()
    if hx is None:
        warn("httpx در دسترس نیست — قابلیت AI کار نمی‌کند",
             hint="./venv/bin/pip install -r requirements.txt")
    else:
        ok("httpx آماده است", getattr(hx, "__version__", ""))
    providers = ai_providers.load_providers()
    if not providers:
        warn("هیچ provider تنظیم نشده (قابلیت AI غیرفعال می‌ماند)",
             hint="AI_PROVIDERS را در .env بگذار — نمونه در .env.example")
    else:
        ok(f"provider های تنظیم‌شده: {len(providers)}")
        for p in providers:
            ok(f"  • {p.name}", f"kind={p.kind} model={p.model or '?'} keys={len(p.keys)}")


# ═══════════ ۸) لاگ ═══════════

def check_log():
    section("۸) خطاهای آخرین اجرا (logs/bot.log)")
    path = os.path.join(ROOT, "logs", "bot.log")
    if not os.path.exists(path):
        warn("logs/bot.log پیدا نشد")
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-400:]
    except Exception as e:
        warn(f"خواندن لاگ نشد: {e}")
        return
    pat = re.compile(r"(Traceback|CRITICAL|FATAL|ERROR)", re.I)
    found = [ln.rstrip() for ln in lines if pat.search(ln)]
    important = [ln for ln in found if "telethon" not in ln.lower()][-8:]
    if not important:
        ok("خطای مهمی در لاگ نبود")
    else:
        print(f"{YELLOW}آخرین خطاها:{RESET}")
        for ln in important:
            print(f"   {DIM}{ln[:190]}{RESET}")
        if any("Traceback" in ln for ln in important):
            advice.append("Traceback لاگ را کامل بفرست.")


# ═══════════ اجرا ═══════════

async def main():
    print(f"{DIM}🩺 دکتر SelfBot Pro — پوشه: {ROOT}{RESET}")
    check_code()
    check_deps()
    config = check_config()
    if config:
        await check_db(config)
    check_plugins()
    await check_handlers()
    check_ai()
    check_log()

    good = sum(1 for _, o, _ in results if o)
    print(f"\n{'─' * 46}")
    print(f"نتیجه: {good}/{len(results)} چک سالم")
    if advice:
        print(f"\n{YELLOW}کارهایی که پیشنهاد می‌شود:{RESET}")
        for a in dict.fromkeys(advice):
            print(f"  • {a}")
    else:
        print(f"{GREEN}همه‌چیز سالم است.{RESET}")
    print(f"\n{DIM}خروجی کامل همین متن را برای پشتیبانی بفرست.{RESET}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nمتوقف شد.")
