"""
تست راند ۳۶ — «شماره‌ی تلفن»، «سابقه‌ی کاربران» و «دکمه‌های ضدحذف».

چه چیزی را تضمین می‌کند:
  ۱) با شماره‌ی تلفن هم می‌شود کاربر را پیدا کرد (مثل آیدی)، با سهمیه‌ی
     محافظه‌کارانه (۴۵ ثانیه فاصله، ۲۵ بار در روز) و پاک‌کردن مخاطب موقت.
  ۲) سابقه‌ی کاربران (دایرکتوری) با `access_hash` ذخیره می‌شود؛ یعنی
     کار با کاربر **هیچ گروه/چت مشترکی لازم ندارد** و بعد از پاک‌شدن چت هم
     قابل پیدا شدن و پیام دادن است.
  ۳) هر پیام ورودی در سابقه ثبت می‌شود و نام‌های «فقط آیدی عددی» خودکار
     اصلاح می‌شوند.
  ۴) `.کاربر سابقه` و `.کاربر دکمه روشن/خاموش` بدون خطا کار می‌کنند
     (رگرسیون: قبلاً متغیر قبل از تعریف استفاده می‌شد → NameError).

اجرا:
    PYTHONPATH=. python tests/test_phone_history.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# این تست به پستگرس نیاز ندارد (دیتابیس جعلی)، ولی ماژول‌ها مقدار می‌خواهند
for _k, _v in (("BOT_TOKEN", "1:x"), ("ADMIN_TELEGRAM_ID", "42"),
               ("TELEGRAM_API_ID", "1"), ("TELEGRAM_API_HASH", "x"),
               ("DB_NAME", "sbt"), ("DB_USER", "sb"), ("DB_PASS", "sb"),
               ("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")):
    os.environ.setdefault(_k, _v)

from telethon.errors import FloodWaitError                      # noqa: E402
from telethon.tl import types as tl                             # noqa: E402
from telethon.tl.functions.contacts import (                    # noqa: E402
    DeleteContactsRequest, ImportContactsRequest)
from telethon.tl.functions.users import GetFullUserRequest      # noqa: E402

import plugins.user_info as UI                                  # noqa: E402
from plugins.user_info import (                                 # noqa: E402
    PHONE_COOLDOWN, PHONE_DAILY_LIMIT, UserInfoPlugin,
    looks_like_phone, normalize_phone)

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


# ═══════════ ماکت دیتابیس (دایرکتوری) ═══════════

class FakeDB:
    def __init__(self):
        self.records = {}          # (user_id, tg_id) → dict
        self.ai_profiles = {}
        self.features = {}
        self.remember_calls = []

    async def remember_user(self, user_id, tg_id, **kw):
        self.remember_calls.append((tg_id, kw))
        rec = self.records.setdefault((user_id, tg_id), {"tg_id": tg_id, "msgs": 0})
        for k, v in kw.items():
            if k == "count_msg":
                if v:
                    rec["msgs"] = rec.get("msgs", 0) + 1
                continue
            if k == "last_text" and v is None:
                continue
            if k == "kind" and v is not None:
                # «پیوی» از «گپ» بالاتر است
                if v == "pv" or rec.get("kind") is None:
                    rec["kind"] = v
                continue
            if v is not None:
                rec[k] = v
        rec["last_seen"] = "2026-10-13 10:00"
        rec["_ts"] = time.time()          # برای تست پاک‌سازی گپ‌های قدیمی

    async def prune_directory(self, user_id, *, group_keep_days=7, max_rows=1500):
        """مثل دیتابیس واقعی: فقط گپ‌های قدیمی حذف می‌شوند"""
        now = time.time()
        cutoff = group_keep_days * 86400
        before = len(self.records)
        self.records = {
            (u, t): r for (u, t), r in self.records.items()
            if not (u == user_id and r.get("kind") == "group"
                    and (now - r.get("_ts", 0)) > cutoff)
        }
        return {"groups": before - len(self.records), "capped": 0,
                "total": len(self.records)}

    async def get_user_record(self, user_id, tg_id):
        return self.records.get((user_id, tg_id))

    async def find_user_record(self, user_id, *, username=None, phone=None):
        for (u, _), rec in self.records.items():
            if u != user_id:
                continue
            if username and (rec.get("username") or "").lower() == username.lower():
                return rec
            if phone and (rec.get("phone") or "").replace(" ", "") == phone:
                return rec
        return None

    async def search_directory(self, user_id, query, limit=20, kind=None,
                               exclude_kind=None):
        q = (query or "").lower()
        out = []
        for (u, _), rec in self.records.items():
            if u != user_id:
                continue
            name = f"{rec.get('first_name') or ''} {rec.get('last_name') or ''}".lower()
            if q in name or q in str(rec.get("tg_id")):
                out.append(rec)
        if kind:
            out = [r for r in out if r.get("kind") == kind]
        if exclude_kind:
            out = [r for r in out if r.get("kind") != exclude_kind]
        return out[:limit]

    async def list_directory(self, user_id, limit=30, kind=None,
                             exclude_kind=None, offset=0):
        rows = [r for (u, _), r in self.records.items() if u == user_id]
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        if exclude_kind:
            rows = [r for r in rows if r.get("kind") != exclude_kind]
        rows.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
        return rows[int(offset):int(offset) + int(limit)]

    async def purge_group_rows(self, user_id):
        before = len(self.records)
        self.records = {(u, t): r for (u, t), r in self.records.items()
                        if not (u == user_id and r.get("kind") == "group")}
        return before - len(self.records)

    async def set_directory_link(self, user_id, tg_id, last_msg_id):
        rec = self.records.get((user_id, tg_id))
        if rec is not None:
            rec["last_msg_id"] = int(last_msg_id)

    async def directory_count(self, user_id, kind=None, exclude_kind=None):
        rows = [r for (u, _), r in self.records.items() if u == user_id]
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        if exclude_kind:
            rows = [r for r in rows if r.get("kind") != exclude_kind]
        return len(rows)

    async def get_ai_profile(self, user_id, target_id):
        return self.ai_profiles.get((user_id, target_id))

    async def upsert_ai_profile(self, user_id, target_id, **fields):
        prof = self.ai_profiles.setdefault((user_id, target_id), {})
        prof.update(fields)
        return prof

    async def get_features(self, user_id):
        return list(self.features.values())

    async def set_feature(self, user_id, feature, enabled, config=None):
        self.features[feature] = {"feature_name": feature, "is_enabled": enabled,
                                  "config_json": config or {}}


import database.db as dbmod                                  # noqa: E402

_CUR = {"db": None}          # دیتابیس جعلیِ فعال


def use_db(db):
    """تعیین این‌که توابع دیتابیس به کدام ماکت بروند"""
    _CUR["db"] = db


def _install():
    """توابع موقت `database.db` که کار را به ماکت فعال می‌سپارند"""
    def wrap(meth):
        async def _f(*a, **kw):
            cur = _CUR["db"]
            if cur is None:
                raise RuntimeError("FakeDB ست نشده")
            return await getattr(cur, meth)(*a, **kw)
        _f.__name__ = meth
        return _f

    for meth in ("remember_user", "get_user_record", "find_user_record",
                 "search_directory", "list_directory", "directory_count",
                 "prune_directory", "purge_group_rows", "set_directory_link",
                 "get_ai_profile", "upsert_ai_profile",
                 "get_features", "set_feature"):
        setattr(dbmod, meth, wrap(meth))

    # توابعی که ماکت لازم ندارند تا ماژول بی‌دردسر import شود
    async def _noop(*a, **kw):
        return None
    dbmod.init = _noop
    dbmod.close = _noop


_install()


# ═══════════ ماکت تلگرام ═══════════

class FakeUser(tl.User):
    def __init__(self, uid=555, first="سارا", last="محمدی", username="sara",
                 phone="+989121234567", bot=False):
        super().__init__(id=uid, is_self=False, contact=False, mutual_contact=False,
                         deleted=False, bot=bot, bot_chat_history=None, bot_nochats=None,
                         verified=False, restricted=False, min=False, bot_inline_geo=False,
                         support=False, scam=False, apply_min_photo=False, fake=False,
                         access_hash=987654321, first_name=first, last_name=last,
                         username=username, phone=phone,
                         status=tl.UserStatusRecently())


class FakeUserFull(tl.UserFull):
    def __init__(self, about="سلام"):
        super().__init__(id=None, settings=tl.PeerSettings(), notify_settings=None,
                         about=about, common_chats_count=2, blocked=False,
                         profile_photo=None, folder_id=None, bot_info=None)


class FakeFullRes:
    def __init__(self):
        self.full_user = FakeUserFull()
        self.users = []
        self.chats = []


class FakeClient:
    def __init__(self, user=None, phone_users=(), dialogs=(), raise_id=False,
                 flood_on_import=False, input_peer_ok=True):
        self.user = user or FakeUser()
        self._phone_users = list(phone_users)
        self._dialogs = list(dialogs)
        self._raise_id = raise_id
        self._flood = flood_on_import
        self._input_peer_ok = input_peer_ok
        self.calls = []                     # نام درخواست‌ها به ترتیب
        self.sent = []
        self.drafts = []
        self.deleted_contacts = 0
        self.import_contacts = 0

    async def get_me(self):
        return tl.User(id=999, is_self=True, contact=False, mutual_contact=False,
                       deleted=False, bot=False, bot_chat_history=None, bot_nochats=None,
                       verified=False, restricted=False, min=False, bot_inline_geo=False,
                       support=False, scam=False, apply_min_photo=False, fake=False,
                       first_name="من")

    async def get_entity(self, target):
        if isinstance(target, tl.InputPeerUser):
            if not self._input_peer_ok:
                raise ValueError("hash نامعتبر")
            return self.user
        if isinstance(target, int):
            if self._raise_id:
                raise ValueError("USER_ID_INVALID")     # شبیه «گروه مشترک نداری»
            return self.user
        if isinstance(target, str):
            return self.user
        return self.user

    async def get_input_entity(self, entity):
        return tl.InputPeerUser(user_id=getattr(entity, "id", 555), access_hash=987654321)

    def iter_dialogs(self, limit=None, ignore_migrated=False):
        client = self

        class _Iter:
            def __aiter__(self_inner):
                async def gen():
                    for d in client._dialogs[:limit or len(client._dialogs)]:
                        yield d
                return gen()
        return _Iter()

    async def __call__(self, request):
        name = type(request).__name__
        self.calls.append(name)
        if isinstance(request, ImportContactsRequest):
            self.import_contacts += 1
            if self._flood:
                raise _flood()
            class _Res:
                users = self._phone_users
                contacts = []
            return _Res()
        if isinstance(request, DeleteContactsRequest):
            self.deleted_contacts += 1
            return True
        if isinstance(request, GetFullUserRequest):
            return FakeFullRes()
        return True

    def iter_messages(self, peer, limit=None, search=None):
        """ماکت خواندن چت ذخیره‌سازی (گزارش‌های ضدحذف)"""
        client = self

        class _Iter:
            def __aiter__(self_inner):
                async def gen():
                    out = list(getattr(client, "storage_msgs", []))
                    if search:
                        out = [m for m in out if str(search) in (m.raw_text or "")]
                    for m in out[:limit or len(out)]:
                        yield m
                return gen()
        return _Iter()

    async def download_profile_photo(self, entity, file=None):
        return None

    async def send_message(self, peer, text, **kw):
        self.sent.append((peer, text))
        return type("M", (), {"id": 1})()


def _flood(seconds=1800):
    """خطای FloodWait تلگرام (پیام خطا لازم است)"""
    return FloodWaitError(request=None, capture=seconds)


def make_plugin(client, db=None):
    """
    پلاگین با دیتابیس جعلی.

    نکته‌ی مهم: توابع دیتابیس روی «نمونه» (نه کلاس) تعریف شده‌اند و پلاگین
    هم آن‌ها را به‌صورت `db.<name>` صدا می‌زند، پس همان‌جا در ماژول جایگزین
    می‌کنیم تا تست بدون پستگرس اجرا شود.
    """
    if db is not None:
        use_db(db)
    plugin = UserInfoPlugin(client, user_id=1)
    plugin._owner_tg = None          # فالبک: پیام‌های ذخیره‌شده
    plugin._ensure_state()           # مثل وقتی که start() صدا زده شده
    return plugin


# ═══════════ تست‌ها ═══════════

async def main():
    db = FakeDB()

    print("=" * 68)
    print("راند ۳۶ — شماره‌ی تلفن، سابقه‌ی کاربران، دکمه‌های ضدحذف")
    print("=" * 68)

    # ── ۱) تشخیص و یکسان‌سازی شماره ──
    check("شماره‌ی ۰۹… (فارسی) شناسایی می‌شود", looks_like_phone("۰۹۱۲۱۲۳۴۵۶۷"))
    check("۰۹… → +989121234567", normalize_phone("۰۹۱۲۱۲۳۴۵۶۷") == "+989121234567",
          normalize_phone("۰۹۱۲۱۲۳۴۵۶۷"))
    check("0098… → +98…", normalize_phone("00989121234567") == "+989121234567",
          normalize_phone("00989121234567"))
    check("+98 912 123 4567 → +989121234567",
          normalize_phone("+98 912 123 4567") == "+989121234567",
          normalize_phone("+98 912 123 4567"))
    check("9121234567 → +989121234567", normalize_phone("9121234567") == "+989121234567",
          normalize_phone("9121234567"))
    check("شماره‌ی خارجی (+49…) دست‌نخورده می‌ماند",
          normalize_phone("+491701234567") == "+491701234567",
          normalize_phone("+491701234567"))
    check("آیدی عددی ۸ رقمی «شماره» حساب نمی‌شود",
          not looks_like_phone("12345678") and not looks_like_phone("987654321"))
    check("تلفن ثابت بدون صفر اول (8904799614) → +988904799614",
          normalize_phone("8904799614") == "+988904799614",
          normalize_phone("8904799614"))

    # ── ۱ب) فالبک شماره فقط برای شکل واقعی شماره ──
    # (راند ۳۸: آیدی‌های تلگرام ۹-۱۰ رقمی‌اند و نباید سهمیه‌ی شماره را بخورند)
    cfb_a = FakeClient(user=None, raise_id=True, phone_users=[])
    pfb_a = make_plugin(cfb_a, FakeDB())
    await pfb_a.lookup("8904799614")          # این یک آیدی است، نه شماره
    check("عددی مثل 8904799614 (شکل آیدی) سهمیه‌ی شماره را مصرف نمی‌کند",
          cfb_a.import_contacts == 0, str(cfb_a.import_contacts))
    cfb_b = FakeClient(user=None, raise_id=True, phone_users=[])
    pfb_b = make_plugin(cfb_b, FakeDB())
    await pfb_b.lookup("7410222506")
    check("عددی مثل 7410222506 هم «شماره» فرض نمی‌شود",
          cfb_b.import_contacts == 0, str(cfb_b.import_contacts))
    cfb_c = FakeClient(user=None, raise_id=True,
                       phone_users=[FakeUser(uid=4242, first="امیر")])
    pfb_c = make_plugin(cfb_c, FakeDB())
    rfb = await pfb_c.lookup("9123456789")    # موبایل بدون صفر اول
    check("موبایل بدون صفر اول (9123456789) به‌عنوان شماره امتحان می‌شود",
          cfb_c.import_contacts == 1, str(cfb_c.import_contacts))
    check("و کاربر پیدا می‌شود", rfb.get("ok") is True, str(rfb.get("reason") or ""))

    # ── ۲) پیدا کردن با شماره ──
    user = FakeUser(uid=555)
    client = FakeClient(user=user, phone_users=[user])
    plugin = make_plugin(client, db)
    res = await plugin.lookup("۰۹۱۲۱۲۳۴۵۶۷")
    check("جست‌وجو با شماره موفق می‌شود", res.get("ok") is True, str(res.get("reason") or ""))
    check("نام درست خوانده می‌شود", res.get("name") == "سارا محمدی", str(res.get("name")))
    check("ImportContacts صدا زده شد", "ImportContactsRequest" in client.calls)
    check("مخاطب موقت بلافاصله حذف شد", client.deleted_contacts == 1)
    check("ترتیب: import قبل از delete",
          client.calls.index("ImportContactsRequest")
          < client.calls.index("DeleteContactsRequest"), str(client.calls[:6]))
    rec = db.records.get((1, 555))
    check("کاربر در سابقه ثبت شد", bool(rec))
    check("منبع سابقه «شماره» است",
          ((rec or {}).get("source") or "").startswith("شماره"),
          str((rec or {}).get("source")))
    check("access_hash ذخیره شد (بدون گروه مشترک هم کار می‌کند)",
          (rec or {}).get("access_hash") == 987654321,
          str((rec or {}).get("access_hash")))
    check("🔒 در جست‌وجوی شماره هم چیزی برای طرف فرستاده نشد", not client.sent,
          str(client.sent))

    # ── ۳) سهمیه: ۴۵ ثانیه فاصله ──
    client2 = FakeClient(phone_users=[FakeUser(uid=556, first="رضا", username=None)])
    plugin2 = make_plugin(client2, db)
    r1 = await plugin2.lookup("+989120000001")
    r2 = await plugin2.lookup("+989120000002")
    check("اولین جست‌وجوی شماره می‌رود", r1.get("ok") is True, str(r1.get("reason") or ""))
    check(f"دومین جست‌وجو با فاصله‌ی کمتر از {PHONE_COOLDOWN:.0f} ثانیه رد می‌شود",
          r2.get("ok") is False and r2.get("cooldown") is True, str(r2.get("reason")))
    check("پیام «صبر کن» به کاربر گفته می‌شود",
          "صبر" in (r2.get("reason") or ""), str(r2.get("reason")))
    check("جست‌وجوی دوم به تلگرام نرفت", client2.import_contacts == 1)

    # ── ۴) سقف روزانه ──
    plugin2._phone_count = PHONE_DAILY_LIMIT
    r3 = await plugin2.lookup("+989120000003")
    check("سقف روزانه (۲۵ بار) رعایت می‌شود",
          r3.get("ok") is False and "سقف" in (r3.get("reason") or ""),
          str(r3.get("reason")))
    plugin2._phone_count = 0
    plugin2._phone_last = time.monotonic() - PHONE_COOLDOWN - 1
    r4 = await plugin2.lookup("+989120000004")
    check("بعد از پر شدن سقف، دیگر به تلگرام نمی‌زند", client2.import_contacts == 2,
          str(client2.import_contacts))

    # ── ۵) FloodWait → قفل روز ──
    client3 = FakeClient(phone_users=[])
    client3._flood = True
    client3.__call__ = _make_flood_call(client3)
    plugin3 = make_plugin(client3, db)
    r5 = await plugin3.lookup("+989120000005")
    check("خطای FloodWait ملایم مدیریت می‌شود",
          r5.get("ok") is False, str(r5.get("reason")))
    check("بعد از FloodWait تا فردا قفل می‌شود",
          plugin3._phone_count >= PHONE_DAILY_LIMIT, str(plugin3._phone_count))

    # ── ۶) بدون گروه مشترک: فقط با access_hash ──
    db.records[(1, 777)] = {"tg_id": 777, "access_hash": 123456789,
                            "first_name": "نگین", "last_name": "",
                            "username": None, "phone": None, "msgs": 4,
                            "source": "پیام ورودی", "last_seen": "2026-10-12 09:00"}
    client4 = FakeClient(user=FakeUser(uid=777, first="نگین", last=None),
                         raise_id=True)          # آیدی خالی → مثل «چت پاک شده»
    plugin4 = make_plugin(client4, db)
    r6 = await plugin4.lookup("777")
    check("چت پاک‌شده با hash سابقه پیدا می‌شود",
          r6.get("ok") is True, str(r6.get("reason") or ""))
    check("نشان می‌دهد از سابقه آمده", "سابقه" in (r6.get("how") or ""),
          str(r6.get("how")))
    check("پیام‌های ثبت‌شده هم نمایش داده می‌شود", r6.get("msgs") == 4, str(r6.get("msgs")))
    check("🔒 ذخیره‌ی hash باعث ارسال چیزی نشد", not client4.sent)

    # ۶ب) اگر تلگرام hash را نپذیرد و چتی هم نباشد → پیام قابل‌فهم
    client5 = FakeClient(user=FakeUser(uid=778), input_peer_ok=False,
                         raise_id=True)      # نه hash، نه چت، نه نشست
    plugin5 = make_plugin(client5, db)
    db.records[(1, 778)] = {"tg_id": 778, "access_hash": 5, "first_name": "x"}
    r7 = await plugin5.lookup("778")
    check("hash نامعتبر + نبود چت → پیام روشن می‌دهد",
          r7.get("ok") is False and r7.get("reason"), str(r7.get("reason")))

    # ── ۷) ثبت هر پیام ورودی ──
    client6 = FakeClient()
    plugin6 = make_plugin(client6, db)

    class MemEvent:
        chat_id = 555
        raw_text = "سلام، خوبی؟"
        is_private = True
        message = type("M", (), {"action": None, "id": 15})()

        async def get_sender(self):
            return FakeUser(uid=555, first="سارا", last="محمدی", username="sara")

    await plugin6._remember_event(MemEvent())
    rec555 = db.records.get((1, 555))
    check("پیام ورودی در سابقه ثبت می‌شود", bool(rec555))
    check("hash از فرستنده‌ی پیام گرفته شد",
          (rec555 or {}).get("access_hash") == 987654321)
    check("متن آخرین پیام ذخیره شد",
          "سلام" in ((rec555 or {}).get("last_text") or ""),
          str((rec555 or {}).get("last_text")))
    check("منبع «پیام پیوی» است",
          (rec555 or {}).get("source") == "پیام پیوی",
          str((rec555 or {}).get("source")))
    check("نوع سابقه «pv» است", (rec555 or {}).get("kind") == "pv",
          str((rec555 or {}).get("kind")))
    check("آیدی آخرین پیام ذخیره شد (برای لینک مستقیم)",
          (rec555 or {}).get("last_msg_id") == 15,
          str((rec555 or {}).get("last_msg_id")))

    class BotEvent(MemEvent):
        async def get_sender(self):
            return FakeUser(uid=999, bot=True)

    class GroupEvent(MemEvent):
        async def get_sender(self):
            return type("Chan", (), {"id": 1, "title": "گروه"})()   # کاربر نیست

    before = len(db.remember_calls)
    await plugin6._remember_event(BotEvent())
    await plugin6._remember_event(GroupEvent())
    check("ربات/گروه در سابقه‌ی «کاربران» ثبت نمی‌شود",
          len(db.remember_calls) == before, str(len(db.remember_calls) - before))

    # ── ۸) اصلاح نامِ «فقط آیدی عددی» ──
    db.ai_profiles[(1, 555)] = {"target_name": "555", "summary": "قبلاً"}
    await plugin6._fix_numeric_name(555, FakeUser(uid=555, first="سارا", last="محمدی"))
    check("نامِ آیدی‌عددی با نام واقعی اصلاح می‌شود",
          db.ai_profiles[(1, 555)].get("target_name") == "سارا محمدی",
          str(db.ai_profiles[(1, 555)].get("target_name")))
    check("بقیه‌ی اطلاعات پروفایل پاک نمی‌شود",
          db.ai_profiles[(1, 555)].get("summary") == "قبلاً")
    db.ai_profiles[(1, 556)] = {"target_name": "رضا"}
    await plugin6._fix_numeric_name(556, FakeUser(uid=556, first="رضا", last=None))
    check("نامِ درست دست‌نخورده می‌ماند", db.ai_profiles[(1, 556)]["target_name"] == "رضا")

    # ── ۹) کارت سابقه ──
    empty_db = FakeDB()
    p_empty = make_plugin(FakeClient(), empty_db)
    check("سابقه‌ی خالی پیام راهنما می‌دهد",
          "سابقه خالی" in await p_empty.history_card())
    make_plugin(client6, db)         # برگرد به دیتابیس پر (همان قبلی)
    card = await plugin6.history_card()
    check("کارت سابقه نام‌ها را نشان می‌دهد", "سارا محمدی" in card)
    check("کارت سابقه آیدی عددی را نشان می‌دهد", "555" in card)
    check("تعداد کل در کارت هست", "از" in card)
    check("جست‌وجو در سابقه کار می‌کند",
          "سارا" in await plugin6.history_card("سارا"))
    wide = await plugin6.history_card("نگین")
    check("جست‌وجو برای کسی که چتش پاک شده هم نتیجه می‌دهد", "نگین" in wide, wide[:60])

    # ── ۱۰) دستور «.کاربر دکمه» و «.کاربر سابقه» (رگرسیون NameError) ──
    plugin6._owner_tg = None
    cmd_client = FakeClient()
    plugin7 = make_plugin(cmd_client, db)
    plugin7._owner_tg = None

    class CmdEvent:
        def __init__(self, text):
            self.raw_text = text
            self.deleted = False

        async def delete(self):
            self.deleted = True

    ev = CmdEvent(".کاربر دکمه خاموش")
    await plugin7._on_command(ev)
    check("دستور «.کاربر دکمه خاموش» بدون خطا اجرا می‌شود", ev.deleted is True)
    check("واقعاً دکمه‌ها را خاموش می‌کند",
          db.features.get("anti_delete", {}).get("config_json", {}).get("quick_buttons")
          is False, str(db.features.get("anti_delete")))
    await plugin7._on_command(CmdEvent(".کاربر دکمه روشن"))
    check("«.کاربر دکمه روشن» دوباره روشن می‌کند",
          db.features["anti_delete"]["config_json"]["quick_buttons"] is True)
    await plugin7._on_command(CmdEvent(".کاربر دکمه"))
    check("«.کاربر دکمه» وضعیت را نشان می‌دهد",
          any("دکمه‌های کارت ضدحذف" in t for _, t in cmd_client.sent),
          str(cmd_client.sent[-1:])[:90])
    await plugin7._on_command(CmdEvent(".کاربر سابقه"))
    check("«.کاربر سابقه» کارت سابقه را می‌فرستد",
          any("سابقه‌ی کاربران" in t for _, t in cmd_client.sent),
          str(cmd_client.sent[-1:])[:90])
    await plugin7._on_command(CmdEvent(".کاربر"))
    check("«.کاربر» تنها، راهنما را می‌فرستد",
          any("کاربر با آیدی عددی" in t for _, t in cmd_client.sent))
    check("🔒 هیچ‌کدام از دستورها به کسی پیام نداد",
          all(p == "me" for p, _ in cmd_client.sent), str(cmd_client.sent[:2]))

    # ── ۱۱) نام نمایشی برای کارت ضدحذف ──
    check("نام از سابقه برای کارت ضدحذف می‌آید",
          await plugin6.display_name(555) == "سارا محمدی",
          await plugin6.display_name(555))
    check("اگر نام نبود، یوزرنیم برمی‌گردد",
          await plugin6.display_name(777) in ("نگین", "777"))
    check("کاربر ناشناس → آیدی عددی",
          await plugin6.display_name(424242) == "424242")

    # ── ۱۲) کول‌داون جست‌وجو: ۲ ثانیه موفق / ۰.۸ ثانیه ناموفق ──
    class NoSaveDB(FakeDB):
        """سابقه ذخیره نمی‌کند → هر جست‌وجو به تلگرام می‌زند (بدترین حالت)"""
        async def remember_user(self, user_id, tg_id, **kw):
            return None

    fresh = NoSaveDB()
    c_cool = FakeClient(user=FakeUser(uid=900))
    p_cool = make_plugin(c_cool, fresh)
    ok1 = await p_cool.lookup("900")
    ok2 = await p_cool.lookup("900")
    check("جست‌وجوی موفقِ پشت‌سرهم با فاصله‌ی کم رد می‌شود",
          ok1.get("ok") is True and ok2.get("ok") is False, str(ok2.get("reason")))
    check("کول‌داونِ موفقیت ۲ ثانیه است", UI.LOOKUP_COOLDOWN == 2.0,
          str(UI.LOOKUP_COOLDOWN))
    p_cool._last_ok[901] = time.monotonic() - UI.LOOKUP_COOLDOWN - 0.1
    ok3 = await p_cool.lookup("901")            # ۲.۱ ثانیه گذشته → آزاد
    check("خیلی زود قفل نمی‌ماند (۵ ثانیه‌ی قبلی حذف شد)", ok3.get("ok") is not False
          or "صبر" not in (ok3.get("reason") or ""), str(ok3.get("reason")))
    c_fail = FakeClient(raise_id=True)
    p_fail = make_plugin(c_fail, NoSaveDB())
    bad1 = await p_fail.lookup("901")
    bad2 = await p_fail.lookup("901")           # همین حالا خطا خورد → کوتاه
    check("بعد از خطا، ۰.۸ ثانیه صبر می‌خواهد (نه ۵ ثانیه)",
          bad1.get("ok") is False and bad2.get("ok") is False
          and "صبر" in (bad2.get("reason") or ""),
          str(bad2.get("reason")))
    p_fail._last_fail[901] = time.monotonic() - 1.0     # ۱ ثانیه پیش ناموفق بود
    bad3 = await p_fail.lookup("901")
    check("بعد از ۱ ثانیه، خطای قبلی سدّ راه نمی‌شود (شکایت کاربر)",
          bad3.get("ok") is False and "صبر" not in (bad3.get("reason") or ""),
          str(bad3.get("reason")))
    check("با وجود سابقه، جست‌وجو اصلاً کول‌داون نمی‌خورد",
          (await make_plugin(FakeClient(user=FakeUser(uid=777),
                                       raise_id=True), db).lookup("777")).get("ok")
          is True)

    # ── ۱۳) راند ۳۷: گپ‌ها سابقه را شلوغ نمی‌کنند ──
    gdb = FakeDB()
    gc = FakeClient()
    gp = make_plugin(gc, gdb)

    class PvEvent:
        chat_id = 555
        raw_text = "سلام"
        is_private = True
        message = type("M", (), {"action": None, "id": 20})()

        async def get_sender(self):
            return FakeUser(uid=555, first="سارا", last="محمدی")

    class GroupEvent:
        chat_id = -100123
        raw_text = "چی می‌گی"
        is_private = False
        message = type("M", (), {"action": None, "id": 21,
                                 "reply_to": type("R", (), {"reply_to_msg_id": 99})()})()

        async def get_sender(self):
            return FakeUser(uid=556, first="رضا", last="کریمی")

    class GroupPlain(GroupEvent):
        message = type("M", (), {"action": None, "id": 22, "reply_to": None})()

    await gp._remember_event(PvEvent())
    await gp._remember_event(GroupEvent())        # ریپلای، ولی به پیام من نیست
    await gp._remember_event(GroupPlain())        # بدون ریپلای
    check("پیوی ثبت می‌شود ولی پیام‌های گپ نه",
          (1, 555) in gdb.records and (1, 556) not in gdb.records,
          str(list(gdb.records)))

    gp._my_msgs[-100123] = {99: 0.0}              # پیام خودم در گپ
    await gp._remember_event(GroupEvent())
    check("ریپلای به پیام خودم در گپ ثبت می‌شود (kind=گپ)",
          gdb.records.get((1, 556), {}).get("kind") == "group",
          str(gdb.records.get((1, 556))))

    gdb.records[(1, 777)] = {"tg_id": 777, "kind": "group",
                             "first_name": "قاسم", "_ts": time.time()}
    gdb.records[(1, 888)] = {"tg_id": 888, "kind": "group",
                             "first_name": "گرگین", "_ts": time.time()}
    gdb.records[(1, 999)] = {"tg_id": 999, "kind": "group", "first_name": "قدیمی",
                             "_ts": time.time() - 10 * 86400}
    gcard = await gp.history_card()
    check("آدم‌های گپ در کارت سابقه نمی‌آیند",
          "رضا" not in gcard and "قاسم" not in gcard and "گرگین" not in gcard,
          gcard[:120])
    check("کارت سابقه تعداد گپ‌های نگه‌داشته‌شده را جدا می‌گوید",
          "گپ" in gcard, gcard[-120:])
    check("پیوی در کارت سابقه هست", "سارا محمدی" in gcard)
    check("پاکسازی، گپِ قدیمی (۱۰ روز پیش) را حذف می‌کند",
          (1, 999) not in gdb.records, str(list(gdb.records)))
    check("گپِ تازه (که همین چند وقت پیش ریپلای زده) می‌ماند",
          (1, 556) in gdb.records)

    # ── ۱۴) فالبک شماره فقط برای شکل شماره‌ی محلی ──
    heur = FakeDB()
    hc = FakeClient(user=None, raise_id=True, phone_users=[])
    hp = make_plugin(hc, heur)
    await hp.lookup("8904799614")
    check("۸۹۰۴۷۹۹۶۱۴ (شکل آیدی، نه شماره) سهمیه‌ی شماره را مصرف نمی‌کند",
          hc.import_contacts == 0, str(hc.import_contacts))
    hc2 = FakeClient(user=None, raise_id=True, phone_users=[])
    hp2 = make_plugin(hc2, FakeDB())
    await hp2.lookup("7410222506")               # شکل آیدی، نه شماره
    check("آیدیِ ۱۰ رقمی که شکل شماره ندارد، سهمیه‌ی شماره را مصرف نمی‌کند",
          hc2.import_contacts == 0, str(hc2.import_contacts))

    # ── ۱۵) فالبک یوزرنیم از سابقه ──
    udb = FakeDB()
    udb.records[(1, 999999)] = {"tg_id": 999999, "username": "ali_gh",
                                "first_name": "علی"}
    uc = FakeClient(user=None, raise_id=True)    # آیدی و hash جواب نمی‌دهد
    uc.user = FakeUser(uid=999999, first="علی", last=None, username="ali_gh")
    up = make_plugin(uc, udb)
    # فقط مسیر یوزرنیم جواب می‌دهد: get_entity(int) خطا می‌دهد ولی str نه
    r_u = await up.lookup("999999")
    check("اگر آیدی/هش جواب نداد، با یوزرنیمِ سابقه پیدا می‌شود",
          r_u.get("ok") is True, str(r_u.get("reason") or ""))

    # ── ۱۶) مسیر بازیابی: پیامِ خودم در پیوی، طرف را با hash ذخیره می‌کند ──
    rdb = FakeDB()
    rc = FakeClient()
    rp = make_plugin(rc, rdb)

    class MyPvEvent:
        chat_id = 3210
        is_private = True
        raw_text = "سلام از طرف من"
        message = type("M", (), {"action": None, "id": 55})()

        async def get_chat(self):
            return FakeUser(uid=3210, first="نیلوفر", last="رستمی")

        async def get_sender(self):
            return FakeUser(uid=999, first="من", is_self=True) if False else None

    await rp._remember_own(MyPvEvent())
    rec_r = rdb.records.get((1, 3210))
    check("پیام خودم در پیوی، طرف را در سابقه ثبت می‌کند", bool(rec_r))
    check("و access_hash را ذخیره می‌کند (مسیر بازیابی)",
          (rec_r or {}).get("access_hash") == 987654321,
          str((rec_r or {}).get("access_hash")))
    check("منبع «پیام خودم» است", (rec_r or {}).get("source") == "پیام خودم",
          str((rec_r or {}).get("source")))
    before_cnt = len(rdb.remember_calls)
    await rp._remember_own(MyPvEvent())          # پشت‌سرهم → بدون نوشتن دوباره
    check("برای پیام‌های پشت‌سرهم خودم هر بار نمی‌نویسد",
          len(rdb.remember_calls) == before_cnt, str(len(rdb.remember_calls)))

    # ── ۱۷) حالت «پیدا نشد» ولی سابقه لینک دارد → لینک بازیابی بده ──
    ndb = FakeDB()
    ndb.records[(1, 31415)] = {"tg_id": 31415, "last_msg_id": 1747403,
                               "first_name": None, "last_name": None,
                               "kind": "pv"}
    nc = FakeClient(user=None, raise_id=True, phone_users=[])
    np_ = make_plugin(nc, ndb)
    nr = await np_.lookup("31415")
    check("پیدا نشد ولی لینک آخرین پیامش برگردانده می‌شود",
          nr.get("ok") is False and (nr.get("msg_link") or "").endswith("1747403"),
          str(nr.get("msg_link")))
    from bot.user_panel import card_kb
    kb_s = str(card_kb(None, None, nr.get("msg_link")))
    check("و دکمه‌ی بازیابی روی کارت می‌آید", "1747403" in kb_s, kb_s[:80])

    # ── ۱۸) ماین کردن گزارش‌های ضدحذف (راند ۳۸) ──
    from plugins.user_info import UserInfoPlugin as _UIP

    rep_text = (
        "🗑 **پیام حذف شده (ضدحذف)**\n"
        "📥 از: 8904799614 (8904799614)\n"
        "👤 فرستنده: مهسا (8904799614)\n"
        "🕒 2026-10-12 17:15:17 (UTC)\n"
        "tg://openmessage?user_id=8904799614&message_id=1747403\n"
        "📌 پیامِ طرف مقابل حذف شد\n──────────────\n\n📝 متن:\n"
        "بیا آشتی کنیممم")
    parsed = _UIP.parse_report(rep_text)
    check("آیدی کاربر از گزارش ضدحذف خوانده می‌شود",
          parsed and parsed["uid"] == 8904799614, str(parsed))
    check("آیدی همان پیام هم خوانده می‌شود",
          parsed and parsed["msg_id"] == 1747403, str(parsed))
    check("نام فرستنده از گزارش استخراج می‌شود",
          parsed and parsed["name"] == "مهسا", str(parsed))
    check("لینک کامل ساخته می‌شود",
          parsed and parsed["link"].endswith("user_id=8904799614&message_id=1747403"),
          str(parsed))
    check("گزارش نامرتبط نادیده گرفته می‌شود",
          _UIP.parse_report("سلام، این پیام معمولی است") is None)

    class FakeStoredMsg:
        def __init__(self, text):
            self.raw_text = text
            self.id = 1

    rdb2 = FakeDB()
    rc2 = FakeClient()
    rc2.storage_msgs = [FakeStoredMsg(rep_text)]
    rp2 = make_plugin(rc2, rdb2)

    info = await rp2.find_in_reports(8904799614)
    check("کاربر در گزارش‌های ضدحذف پیدا می‌شود",
          bool(info) and info["msg_id"] == 1747403, str(info))
    check("و لینکِ پیامش در سابقه ثبت می‌شود",
          rdb2.records.get((1, 8904799614), {}).get("last_msg_id") == 1747403
          or bool(info), str(rdb2.records.get((1, 8904799614), {})))
    # بار دوم → از کش، بدون درخواست
    calls_before = len(rc2.calls)
    await rp2.find_in_reports(8904799614)
    check("جست‌وجوی تکراری در گزارش‌ها از کش می‌آید",
          len(rc2.calls) == calls_before, str(len(rc2.calls) - calls_before))

    n_rec = await rp2.recover_reports(limit=50)
    check("«.کاربر بازیابی» کاربران گزارش‌ها را برمی‌گرداند", n_rec == 1, str(n_rec))
    rec_rp = rdb2.records.get((1, 8904799614), {})
    check("منبع در سابقه «گزارش ضدحذف» است",
          rec_rp.get("source") == "گزارش ضدحذف", str(rec_rp.get("source")))
    check("آیدی پیام برای لینک ذخیره شد",
          rec_rp.get("last_msg_id") == 1747403, str(rec_rp.get("last_msg_id")))
    check("نوع «pv» است (در سابقه می‌آید)", rec_rp.get("kind") == "pv")

    # ── ۱۹) کاربری که تلگرام نمی‌شناسد: کارت با لینک، نه «هیچ» ──
    ldb = FakeDB()
    lc = FakeClient(user=None, raise_id=True, phone_users=[])
    lc.storage_msgs = [FakeStoredMsg(rep_text)]
    lp = make_plugin(lc, ldb)
    lres = await lp.lookup("8904799614")
    check("کاربر ناشناس ولی با گزارش → لینک بازیابی برمی‌گردد",
          lres.get("ok") is False and "1747403" in str(lres.get("msg_link")),
          str(lres.get("msg_link")))
    from bot.user_panel import card_kb, card_text
    ltxt = card_text({**lres, "target": "8904799614"})
    check("لینک در متن کارت قابل‌لمس است",
          "tg://openmessage?user_id=8904799614" in ltxt, ltxt[-160:])
    check("و دکمه‌ی «باز کردن» هم ساخته می‌شود",
          "1747403" in str(card_kb(None, None, lres.get("msg_link"))))

    # کاربری که حتی گزارش هم ندارد → حداقل لینک خودِ چت
    ndb2 = FakeDB()
    nc2 = FakeClient(user=None, raise_id=True, phone_users=[])
    nc2.storage_msgs = []
    np2 = make_plugin(nc2, ndb2)
    nres = await np2.lookup("8904799614")
    check("بدون گزارش هم لینک «باز کردن چت» داده می‌شود",
          nres.get("msg_link") == "tg://openmessage?user_id=8904799614",
          str(nres.get("msg_link")))

    # ── ۲۰) منوی تعاملی سابقه: صفحه‌بندی + ترتیب ──
    mdb = FakeDB()
    mc = FakeClient()
    mp = make_plugin(mc, mdb)
    for i in range(21):
        mdb.records[(1, 5000 + i)] = {
            "tg_id": 5000 + i, "first_name": f"نفر{i}", "kind": "pv",
            "last_seen": f"2026-10-{10 + (i % 4):02d} {i:02d}:00", "msgs": i + 1,
            "_ts": time.time()}
    page1 = await mp.history_page(0)
    check("صفحه‌ی اول منو ساخته می‌شود", bool(page1.get("text")) and page1["rows"])
    check("مجموع کاربران در متن می‌آید", "21" in page1["text"], page1["text"][:80])
    check("تعداد صفحه‌ها درست است (۲۱ نفر / ۸ در صفحه = ۳)",
          page1["pages"] == 3, str(page1["pages"]))
    check("تعداد دکمه‌های کاربران در یک صفحه ۸ است",
          len(page1["rows"]) == 8, str(len(page1["rows"])))
    page3 = await mp.history_page(2)
    check("صفحه‌ی آخر بقیه را نشان می‌دهد", len(page3["rows"]) == 5,
          str(len(page3["rows"])))
    check("صفحه‌ها کاربر تکراری ندارند",
          not ({u for u, _ in page1["rows"]} & {u for u, _ in page3["rows"]}))
    check("مرتب‌سازی بر اساس آخرین پیام (نزولی)",
          [r.get("last_seen") for r in
           await mdb.list_directory(1, limit=3, exclude_kind="group")]
          == sorted([r["last_seen"] for r in mdb.records.values()],
                    reverse=True)[:3],
          "ترتیب نزولی بر اساس last_seen")
    check("صفحه‌ی خارج از محدوده، خطا نمی‌دهد",
          (await mp.history_page(99))["ok"] is True)
    from bot.user_panel import history_kb
    kb_str = str(history_kb(page1))
    check("دکمه‌های صفحه‌بندی در منو هست",
          "u:hist:1" in kb_str and "u:card:" in kb_str, kb_str[:120])

    # ── ۲۱) جست‌وجو در منو و پاک‌سازی گپ‌ها ──
    spage = await mp.history_page(0, "نفر7")
    check("جست‌وجو در سابقه یک نتیجه می‌دهد",
          len(spage["rows"]) == 1, str(spage["rows"]))
    mdb.records[(1, 9001)] = {"tg_id": 9001, "kind": "group", "first_name": "گپی",
                              "_ts": time.time()}
    g1 = await mp.history_page(0)
    check("گپ‌ها در منو نمی‌آیند", all(u != 9001 for u, _ in g1["rows"]))
    purged = await mdb.purge_group_rows(1)
    check("دستور پاک‌سازی گپ‌ها کار می‌کند", purged == 1, str(purged))

    print("\n" + "=" * 68)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


def _make_flood_call(client):
    async def _call(request):
        client.calls.append(type(request).__name__)
        raise FloodWaitError(request=None)
    return _call


sys.exit(asyncio.run(main()))
