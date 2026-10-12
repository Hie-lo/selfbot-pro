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


class FakeSentMsg:
    def __init__(self, mid):
        self.message_id = mid


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self._mid = 100

    async def send_message(self, chat_id=None, text=None, **kw):
        self._mid += 1
        self.sent.append((chat_id, text, kw.get("reply_markup")))
        return FakeSentMsg(self._mid)

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kw):
        self.edits.append((chat_id, message_id, text, kw.get("reply_markup")))
        return FakeSentMsg(message_id)


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

    remembered = []

    async def remember(user_id, tg_id, **kw):
        remembered.append((tg_id, kw))

    dbmod.get_user_by_db_id = owner               # قبل از استفاده
    dbmod.get_features = features_on
    dbmod.remember_user = remember

    bot = FakeBot()
    runtime.bot = bot
    plugin = AntiDeletePlugin(FakeClient(), 1)
    plugin._my_id = 999

    def fresh(**extra):
        """پیام تازه‌حذف‌شده (چند لحظه پیش)"""
        from datetime import datetime, timezone
        d = dict({"chat_id": 555123, "chat_name": "علی", "sender_name": "علی",
                  "msg_id": 700, "date": datetime.now(timezone.utc)}, **extra)
        return d

    # ۱) پیوی → کارت با دکمه‌ها
    await plugin._quick_actions(fresh())
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
    bot.edits.clear()
    await plugin._quick_actions(fresh(chat_id=-100123456789, chat_name="گروه"))
    check("برای سوپرگروه کارت نمی‌فرستد", not bot.sent)
    await plugin._quick_actions(fresh(chat_id=-12345, chat_name="گروه قدیمی"))
    check("برای گروه معمولی هم کارت نمی‌فرستد", not bot.sent)

    # ۳) پیام خودم (به خودم) → نه
    await plugin._quick_actions(fresh(chat_id=999, chat_name="خودم"))
    check("برای پیام خودم کارت نمی‌فرستد", not bot.sent)

    # ۴) خاموش با «.کاربر دکمه خاموش»
    dbmod.get_features = features_off
    await plugin._quick_actions(fresh(chat_id=555, chat_name="x"))
    check("با خاموش بودن دکمه‌ها کارت نمی‌آید", not bot.sent)
    dbmod.get_features = features_on

    # ۵) بدون ربات کنترلی → خطا نمی‌دهد
    runtime.bot = None
    try:
        await plugin._quick_actions(fresh(chat_id=555, chat_name="x"))
        check("بدون ربات کنترلی خطا نمی‌دهد", True)
    except Exception as e:                        # noqa: BLE001
        check("بدون ربات کنترلی خطا نمی‌دهد", False, f"{type(e).__name__}: {e}")
    runtime.bot = bot

    # ۶) نام واقعی از سابقه (وقتی کش نام ندارد و فقط آیدی است)
    import core.plugin_manager as pm
    pm._active_plugins[1] = {"user_info": FakeUserInfo()}
    bot.sent.clear()
    bot.edits.clear()
    plugin._card_state.clear()
    await plugin._quick_actions(fresh(chat_id=777, chat_name="777",
                                      sender_name="777"))
    check("اگر کش فقط آیدی داشت، نام از سابقه می‌آید",
          bool(bot.sent) and "سارا محمدی" in bot.sent[-1][1],
          (bot.sent[-1][1][:70] if bot.sent else "نرفت"))
    pm._active_plugins.pop(1, None)

    # ── ۷) بهینه: ۱۰۰۰ حذف = یک کارت و ویرایش‌های محدود ──
    plugin._card_state.clear()
    bot.sent.clear()
    bot.edits.clear()
    for i in range(5):
        await plugin._quick_actions(fresh(chat_id=888001, chat_name="مهسا",
                                          msg_id=800 + i))
    check("۵ پیام حذف از یک نفر → فقط یک کارت فرستاده می‌شود",
          len(bot.sent) == 1, f"sent={len(bot.sent)} edits={len(bot.edits)}")
    check("ویرایش هم برای هر حذف تکرار نمی‌شود (حداکثر هر ۱۰ ثانیه)",
          len(bot.edits) == 0, f"edits={len(bot.edits)}")
    check("شمارنده در حافظه درست نگه داشته می‌شود",
          plugin._card_state[888001]["count"] == 5,
          str(plugin._card_state[888001]))
    # بعد از گذشتن بازه، ویرایش انجام می‌شود (شمارنده‌ی روی کارت به‌روز)
    plugin._card_state[888001]["edit_ts"] -= 11
    await plugin._quick_actions(fresh(chat_id=888001, chat_name="مهسا",
                                      msg_id=805))
    check("بعد از بازه، کارت با شمارنده‌ی تازه ویرایش می‌شود",
          bool(bot.edits) and "6 پیام حذف شد" in bot.edits[-1][2],
          (bot.edits[-1][2][:50] if bot.edits else "—"))
    check("دکمه‌ها روی کارتِ به‌روزشده هم هستند",
          bool(bot.edits) and "u:msg:888001" in str(bot.edits[-1][3]))
    check("بازه‌ی ویرایش در کد قابل تنظیم است",
          __import__("plugins.anti_delete", fromlist=["x"]).CARD_EDIT_GAP == 10.0)

    # ۷ب) بعد از پنجره‌ی تجمیع → کارت تازه
    plugin._card_state[888001]["ts"] -= 10_000
    await plugin._quick_actions(fresh(chat_id=888001, chat_name="مهسا"))
    check("بعد از تمام‌شدن پنجره، کارت تازه ساخته می‌شود",
          len(bot.sent) == 2, f"sent={len(bot.sent)}")

    # ── ۸) حذف‌های قدیمی کارت نمی‌گیرند ──
    from datetime import datetime, timezone, timedelta
    plugin._card_state.clear()
    bot.sent.clear()
    bot.edits.clear()
    await plugin._quick_actions(fresh(chat_id=999002,
        date=datetime.now(timezone.utc) - timedelta(days=10)))
    check("حذف قدیمی (۱۰ روز پیش) کارت دکمه‌دار نمی‌گیرد", not bot.sent)

    # ── ۹) طرفِ حذف‌کننده در سابقه ثبت می‌شود (با یوزرنیم) ──
    remembered.clear()
    plugin._card_state.clear()
    await plugin._quick_actions(fresh(chat_id=888003, chat_name="نیما",
                                      sender_username="nima",
                                      msg_link="tg://openmessage?user_id=888003"))
    check("طرفِ حذف در سابقه ثبت می‌شود", any(t == 888003 for t, _ in remembered),
          str(remembered))
    kw = dict(remembered[-1][1]) if remembered else {}
    check("یوزرنیم در سابقه ذخیره می‌شود (کلید پیدا کردن بعدی)",
          kw.get("username") == "nima", str(kw.get("username")))
    check("نوع سابقه «pv» است", kw.get("kind") == "pv", str(kw.get("kind")))
    check("آیدی پیام ذخیره می‌شود (برای لینک)", kw.get("last_msg_id"),
          str(kw.get("last_msg_id")))
    check("دکمه‌ی «باز کردن همان پیام» روی کارت هست",
          bool(bot.sent) and "888003" in str(bot.sent[-1][2]))

    print("\n" + "=" * 60)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
