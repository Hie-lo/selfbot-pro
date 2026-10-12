"""
تست سرتاسری ضدحذف — از هندلرِ واقعی تا کارتِ دکمه‌دار.

چرا: تست‌های قبلی مستقیم `_quick_actions` را صدا می‌زدند؛ این‌جا مسیر واقعی
`start()` → هندلر کش → هندلر حذف → ذخیره‌سازی + کارت ربات کنترلی اجرا
می‌شود تا مطمئن شویم همه‌ی قطعات به هم وصل‌اند.

اجرا:
    PYTHONPATH=. python tests/test_anti_delete_flow.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _k, _v in (("BOT_TOKEN", "1:x"), ("ADMIN_TELEGRAM_ID", "42"),
               ("TELEGRAM_API_ID", "1"), ("TELEGRAM_API_HASH", "x"),
               ("DB_NAME", "sbt"), ("DB_USER", "sb"), ("DB_PASS", "sb"),
               ("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")):
    os.environ.setdefault(_k, _v)

from telethon import events                                  # noqa: E402
from telethon.tl import types as tl                          # noqa: E402

import database.db as dbmod                                  # noqa: E402
from core import runtime                                     # noqa: E402
from plugins.anti_delete import AntiDeletePlugin             # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


# ═══════════ ماکت‌ها ═══════════

class FakeUser(tl.User):
    def __init__(self, uid=555, first="مهسا", last="نوری", username="mahsa"):
        super().__init__(id=uid, is_self=False, contact=False, mutual_contact=False,
                         deleted=False, bot=False, bot_chat_history=None, bot_nochats=None,
                         verified=False, restricted=False, min=False, bot_inline_geo=False,
                         support=False, scam=False, apply_min_photo=False, fake=False,
                         access_hash=444555666, first_name=first, last_name=last,
                         username=username, phone=None, status=tl.UserStatusRecently())


class FakeMsg:
    def __init__(self, mid, text, sender_id, date=None):
        self.id = mid
        self.text = text
        self.message = text
        self.sender_id = sender_id
        self.media = None
        self.action = None
        self.date = date or datetime.now(timezone.utc)


class FakeNewMsgEvent:
    def __init__(self, chat_id, msg, is_private=True, sender=None):
        self.chat_id = chat_id
        self.message = msg
        self.raw_text = msg.text or ""
        self.is_private = is_private
        self._sender = sender

    async def get_sender(self):
        return self._sender


class FakeDeletedEvent:
    def __init__(self, chat_id, ids, is_private=True):
        self.chat_id = chat_id
        self.deleted_ids = list(ids)
        self.is_private = is_private


class FakeSBClient:
    """کلاینت سلف‌بات: هر ارسال را ثبت می‌کند"""

    def __init__(self, user=None):
        self.user = user or FakeUser()
        self.handlers = []
        self.sent = []           # (peer, text)
        self.entities = 0

    def add_event_handler(self, cb, ev):
        self.handlers.append((cb, ev))

    def remove_event_handler(self, cb, ev):
        pass

    async def get_me(self):
        return tl.User(id=999, is_self=True, contact=False, mutual_contact=False,
                       deleted=False, bot=False, bot_chat_history=None, bot_nochats=None,
                       verified=False, restricted=False, min=False, bot_inline_geo=False,
                       support=False, scam=False, apply_min_photo=False, fake=False,
                       first_name="من", username="me_acc")

    async def get_entity(self, target):
        self.entities += 1
        return self.user

    async def get_input_entity(self, ent):
        return tl.InputPeerUser(user_id=555, access_hash=444555666)

    async def send_message(self, peer, text, **kw):
        self.sent.append((peer, text))
        return type("M", (), {"id": 1})()

    def handler_for(self, event_type):
        for cb, ev in self.handlers:
            if ev is event_type:
                return cb
        return None


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self._mid = 500

    async def send_message(self, chat_id=None, text=None, **kw):
        self._mid += 1
        self.sent.append((chat_id, text, kw.get("reply_markup")))
        return type("M", (), {"message_id": self._mid})()

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kw):
        self.edits.append((chat_id, message_id, text))
        return type("M", (), {"message_id": message_id})()


remembered = []


async def main():
    async def features(user_id):
        return [{"feature_name": "anti_delete", "is_enabled": True,
                 "config_json": {}}]

    async def owner(uid):
        return {"telegram_id": 42}

    async def remember(user_id, tg_id, **kw):
        remembered.append((tg_id, kw))

    async def storage_target(user_id, feature):
        return {"target_id": None}          # = پیام‌های ذخیره‌شده خودم

    dbmod.get_features = features
    dbmod.get_user_by_db_id = owner
    dbmod.remember_user = remember
    dbmod.get_storage_target = storage_target

    bot = FakeBot()
    runtime.bot = bot
    sb = FakeSBClient()
    plugin = AntiDeletePlugin(sb, 1)
    await plugin.start()

    check("هندلر کش‌کردن پیام پیوی ثبت شد",
          any(ev is events.NewMessage for _, ev in sb.handlers))
    check("هندلر لحظه‌ی حذف ثبت شد",
          any(ev is events.MessageDeleted for _, ev in sb.handlers))

    cache_h = plugin._cache and None
    cache_h = sb.handler_for(events.NewMessage)
    del_h = sb.handler_for(events.MessageDeleted)

    # ── پیام‌ها در پیوی کش می‌شوند ──
    for mid, text in ((11, "سلام، خوبی؟"), (12, "بیا آشتی کنیم")):
        await cache_h(FakeNewMsgEvent(555, FakeMsg(mid, text, 555), True, sb.user))
    check("پیام‌های پیوی کش شدند", len(plugin._cache.get(555, {})) == 2,
          str(list(plugin._cache.get(555, {}))))

    # ── پیام گپ کش نمی‌شود ──
    await cache_h(FakeNewMsgEvent(-100123, FakeMsg(13, "چی می‌گی", 556),
                                  False, FakeUser(uid=556)))
    check("پیام گپ اصلاً کش نمی‌شود (فقط پیوی)",
          -100123 not in plugin._cache, str(list(plugin._cache)))

    # ── حذف: گزارش ذخیره‌سازی + یک کارت ──
    await del_h(FakeDeletedEvent(555, [11], True))
    check("گزارش ضدحذف در پیام‌های ذخیره‌شده رفت",
          any(p == 999 for p, _ in sb.sent), str([p for p, _ in sb.sent]))
    body = sb.sent[-1][1] if sb.sent else ""
    check("متن حذف‌شده در گزارش هست", "سلام، خوبی؟" in body, body[:80])
    check("یک کارت دکمه‌دار هم در پیوی ربات آمد", len(bot.sent) == 1,
          f"cards={len(bot.sent)}")
    check("دکمه‌ی «پیام بده» روی کارت", "u:msg:555" in str(bot.sent[-1][2]))
    check("نام واقعی طرف در کارت (نه آیدی)",
          "مهسا" in bot.sent[-1][1], bot.sent[-1][1][:60])
    check("طرف با یوزرنیم در سابقه ثبت شد",
          any(kw.get("username") == "mahsa" for _, kw in remembered),
          str(remembered[:1]))

    # ── دو حذف بعدی → همان کارت به‌روز می‌شود ──
    await del_h(FakeDeletedEvent(555, [12], True))
    check("حذف بعدی پیام جدید نمی‌سازد", len(bot.sent) == 1,
          f"cards={len(bot.sent)}")
    check("روی همان کارت به‌روز می‌شود", len(bot.edits) == 1,
          f"edits={len(bot.edits)}")
    check("گزارش ذخیره‌سازی برای هر پیام می‌رود (داده گم نمی‌شود)",
          len(sb.sent) == 2, str(len(sb.sent)))
    check("کش بعد از حذف پاک می‌شود", not plugin._cache.get(555))

    # ── تعداد درخواست get_entity برای دو پیام: فقط یکی ──
    before = sb.entities
    for mid in (21, 22):
        await cache_h(FakeNewMsgEvent(555, FakeMsg(mid, "تست", 555), True, sb.user))
    check("اطلاعات نام/یوزرنیم هر چت کش می‌شود (نه درخواست برای هر پیام)",
          sb.entities - before == 0, f"requests={sb.entities - before}")

    # ── حذف قدیمی: کارت نه، گزارش بله ──
    # ۳ روز پیش: هنوز در بازه‌ی گزارش (۷ روز) هست ولی از بازه‌ی کارت
    # (۲ روز) گذشته → گزارش بله، کارت نه
    old_date = datetime.now(timezone.utc) - timedelta(days=3)
    await cache_h(FakeNewMsgEvent(777, FakeMsg(31, "قدیمی", 777, old_date),
                                  True, sb.user))
    bot.sent.clear()
    sb.sent.clear()
    plugin._card_state.clear()
    await del_h(FakeDeletedEvent(777, [31], True))
    check("حذف ۳ روز پیش هنوز گزارش می‌شود (داده گم نمی‌شود)",
          len(sb.sent) == 1, str(len(sb.sent)))
    check("ولی کارت دکمه‌دار نمی‌گیرد (مزاحم نمی‌شود)", not bot.sent,
          str(len(bot.sent)))

    # ── بدون ربات: فقط گزارش، بدون خطا ──
    runtime.bot = None
    await cache_h(FakeNewMsgEvent(666, FakeMsg(41, "بدون ربات", 666), True, sb.user))
    try:
        await del_h(FakeDeletedEvent(666, [41], True))
        check("اگر ربات کنترلی وصل نباشد خطا نمی‌دهد", True)
    except Exception as e:                       # noqa: BLE001
        check("اگر ربات کنترلی وصل نباشد خطا نمی‌دهد", False, str(e))

    print("\n" + "=" * 62)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
