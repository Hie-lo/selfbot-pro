"""
تست کارت دکمه‌دار ضدحذف — «پیام بده» / «آماده‌کردن چت» زیر پیام حذف‌شده.

چرا مهم است: کاربر خواسته وقتی کسی چتش را پاک می‌کند، بتواند دوباره به او
پیام بدهد یا چت را (بی‌صدا) باز کند. این کارت را ربات کنترلی می‌فرستد
(سلف‌بات نمی‌تواند دکمه‌ی شیشه‌ای بفرستد) و به هیچ گروه مشترکی وابسته نیست.

اجرا:
    PYTHONPATH=. python tests/test_anti_delete_buttons.py
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "42")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault("DB_NAME", "sbt")
os.environ.setdefault("DB_USER", "sb")
os.environ.setdefault("DB_PASS", "sb")
os.environ.setdefault("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")

from core import runtime                             # noqa: E402
from plugins.anti_delete import AntiDeletePlugin      # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, **kw):
        self.sent.append((chat_id, text, kw.get("reply_markup")))
        return None


class FakeClient:
    def __init__(self):
        self.handlers = []

    def add_event_handler(self, cb, ev):
        self.handlers.append(cb)

    def remove_event_handler(self, cb, ev):
        pass


class FakeUserInfo:
    async def display_name(self, uid):
        return "سارا محمدی"


async def main():
    import database.db as dbmod

    async def features_on(user_id):
        return [{"feature_name": "anti_delete", "is_enabled": True,
                 "config_json": {}}]

    async def features_off(user_id):
        return [{"feature_name": "anti_delete", "is_enabled": True,
                 "config_json": {"quick_buttons": False}}]

    async def owner(uid):
        return {"telegram_id": 42}

    dbmod.get_user_by_db_id = owner               # قبل از استفاده
    dbmod.get_features = features_on

    bot = FakeBot()
    runtime.bot = bot
    plugin = AntiDeletePlugin(FakeClient(), 1)
    plugin._my_id = 999

    # ۱) پیوی → کارت با دکمه‌ها
    await plugin._quick_actions({"chat_id": 555123, "chat_name": "علی",
                                 "sender_name": "علی"})
    check("کارت ضدحذف برای پیوی فرستاده می‌شود", len(bot.sent) == 1,
          f"sent={len(bot.sent)}")
    if bot.sent:
        _, text, kb = bot.sent[-1]
        kb_s = str(kb)
        check("دکمه‌ی «✉️ پیام بده» روی کارت هست", "u:msg:555123" in kb_s)
        check("دکمه‌ی «📂 آماده‌کردن چت» روی کارت هست", "u:draft:555123" in kb_s)
        check("دکمه‌ی لینک و بستن هم هست",
              "u:link:555123" in kb_s and "u:close:555123" in kb_s)
        check("نام طرف در متن کارت می‌آید", "علی" in text)
        check("به پیوی خودِ کاربر (ربات) رفته", bot.sent[-1][0] == 42,
              str(bot.sent[-1][0]))

    # ۲) گروه/کانال → هیچ کارتی (دکمه لازم نیست)
    bot.sent.clear()
    await plugin._quick_actions({"chat_id": -100123456789, "chat_name": "گروه"})
    check("برای سوپرگروه کارت نمی‌فرستد", not bot.sent)
    await plugin._quick_actions({"chat_id": -12345, "chat_name": "گروه قدیمی"})
    check("برای گروه معمولی هم کارت نمی‌فرستد", not bot.sent)

    # ۳) پیام خودم (به خودم) → نه
    await plugin._quick_actions({"chat_id": 999, "chat_name": "خودم"})
    check("برای پیام خودم کارت نمی‌فرستد", not bot.sent)

    # ۴) خاموش با «.کاربر دکمه خاموش»
    dbmod.get_features = features_off
    await plugin._quick_actions({"chat_id": 555, "chat_name": "x"})
    check("با خاموش بودن دکمه‌ها کارت نمی‌آید", not bot.sent)
    dbmod.get_features = features_on

    # ۵) بدون ربات کنترلی → خطا نمی‌دهد
    runtime.bot = None
    try:
        await plugin._quick_actions({"chat_id": 555, "chat_name": "x"})
        check("بدون ربات کنترلی خطا نمی‌دهد", True)
    except Exception as e:                        # noqa: BLE001
        check("بدون ربات کنترلی خطا نمی‌دهد", False, f"{type(e).__name__}: {e}")
    runtime.bot = bot

    # ۶) نام واقعی از سابقه (وقتی کش نام ندارد و فقط آیدی است)
    import core.plugin_manager as pm
    pm._active_plugins[1] = {"user_info": FakeUserInfo()}
    bot.sent.clear()
    await plugin._quick_actions({"chat_id": 777, "chat_name": "777",
                                 "sender_name": "777"})
    check("اگر کش فقط آیدی داشت، نام از سابقه می‌آید",
          bool(bot.sent) and "سارا محمدی" in bot.sent[-1][1],
          (bot.sent[-1][1][:70] if bot.sent else "نرفت"))
    pm._active_plugins.pop(1, None)

    print("\n" + "=" * 60)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
