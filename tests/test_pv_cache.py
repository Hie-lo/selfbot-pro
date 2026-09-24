"""
تست رفتاری ضدحذف/ضدویرایش روی کش مشترک (core/pv_cache.py) — بدون تلگرام.

سناریوها:
  • صفر درخواست API در مسیر دریافت پیام (۱۰۰ پیام از یک نفر → حداکثر ۱ get_entity)
  • قالب گزارش حذف دقیقاً همان قالب قبلی + ترتیب اصلی
  • حذف دسته‌ای → یک گزارش خلاصه + فایل متنی، مدیاها جدا
  • حذف در کانال نادیده گرفته می‌شود (شماره‌ی تکراری ≠ پیام پی‌وی)
  • Saved Messages کش نمی‌شود؛ پیام لینک‌دار «متن» حساب می‌شود
  • ضدویرایش: قالب قبلی؛ ضدحذف بعد از ویرایش متن «اصلی» را گزارش می‌دهد
  • سقف حجم کش رعایت می‌شود
  • ارجاع مدیا باطل شد → از گاوصندوق (رمزنگاری‌شده) بازیابی می‌شود و فایل پاک می‌شود
  • استیکر با ارجاع فشرده هم از Recents پاک می‌شود
  • FloodWait در صف ارسال → صبر و ارسال دوباره (نه دور ریختن)

اجرا:  python tests/test_pv_cache.py   یا   python -m pytest tests/
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault("DB_NAME", "x")
os.environ.setdefault("DB_USER", "x")
os.environ.setdefault("DB_PASS", "x")
os.environ.setdefault("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")

from telethon import events  # noqa: E402
from telethon.errors import FloodWaitError  # noqa: E402
from telethon.tl import types  # noqa: E402

from core import outbox, pv_cache, self_actions  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from database import db  # noqa: E402
import plugins.anti_delete as AD  # noqa: E402
import plugins.anti_edit as AE  # noqa: E402

pv_cache.WARMUP_DELAY = (0.0, 0.0)
pv_cache.WARMUP_GAP = 0.0
AD.DEBOUNCE = 0.05
AD.MAX_BATCH_WAIT = 0.2
outbox.MIN_GAP = 0.0

ME = 100
PEER = 200


async def _no_target(*a, **k):
    return None

db.get_storage_target = _no_target


# ═══════ Fakes ═══════

class FakeClient:
    def __init__(self):
        self.handlers = []
        self.sent = []
        self.calls = []
        self.rpc = {"get_entity": 0, "get_me": 0}
        self.fail_reference = False
        self.flood_once = False
        self.downloads = 0
        self.users = {
            PEER: types.User(id=PEER, first_name="Ali", last_name="Rezaei", username="ali_r"),
            300: types.User(id=300, first_name="Sara"),
        }
        self.dialogs = []            # برای بازخوانی تاریخچه
        self.history = {}            # chat_id → لیست پیام (جدید → قدیمی، مثل تلگرام)
        self.history_calls = []
        self.history_error = None
        self.server_deleted = []
        self.server_edited = []
        self_actions.install(self)

    async def get_dialogs(self, limit=None):
        return self.dialogs

    async def get_messages(self, entity, limit=None):
        cid = getattr(entity, "id", entity)
        self.history_calls.append(cid)
        if self.history_error:
            raise self.history_error
        return list(self.history.get(cid, []))[:limit]

    async def delete_messages(self, entity, message_ids, *a, **k):
        self.server_deleted.append(message_ids)

    async def edit_message(self, entity, message=None, text=None, *a, **k):
        self.server_edited.append((message, text))

    def add_event_handler(self, cb, ev):
        self.handlers.append((cb, ev))

    def remove_event_handler(self, cb, ev):
        self.handlers.remove((cb, ev))

    async def get_me(self):
        self.rpc["get_me"] += 1
        return types.User(id=ME, first_name="Me", username="meuser", is_self=True)

    async def get_entity(self, pid):
        self.rpc["get_entity"] += 1
        return self.users[pid]

    async def get_input_entity(self, pid):
        return pid

    async def send_message(self, dest, text, **kw):
        if self.flood_once:
            self.flood_once = False
            raise FloodWaitError(request=None, capture=1)
        self.sent.append(("msg", dest, text, kw))

    async def send_file(self, dest, file, **kw):
        if self.fail_reference and isinstance(file, (types.InputPhoto, types.InputDocument)):
            raise RuntimeError("FILE_REFERENCE_EXPIRED")
        data = file.getvalue() if hasattr(file, "getvalue") else None
        self.sent.append(("file", dest, file, kw, data))
        return None

    async def download_media(self, msg, file=bytes):
        self.downloads += 1
        return b"OGG-VOICE-DATA" * 20

    async def __call__(self, req):
        self.calls.append(req)
        return True


class Msg:
    _next = 1000

    def __init__(self, chat_id, sender_id, text="", media=None, out=False, sender=None, mid=None):
        Msg._next += 1
        self.id = mid or Msg._next
        self.sender_id = sender_id
        self.text = text
        self.media = media
        self.out = out
        self.date = datetime.now(timezone.utc)
        self._sender = sender
        self._chat = None


class NewEv:
    def __init__(self, msg, chat_id, private=True):
        self.message = msg
        self.chat_id = chat_id
        self.is_private = private


class DelEv:
    def __init__(self, ids, chat_id=None):
        self.deleted_ids = ids
        self.chat_id = chat_id


class EditEv:
    def __init__(self, msg):
        self.message = msg
        self.is_private = True


def voice_media(size=280):
    doc = types.Document(
        id=11, access_hash=22, file_reference=b"ref", date=None, mime_type="audio/ogg",
        size=size, dc_id=2, attributes=[types.DocumentAttributeAudio(duration=3, voice=True)],
    )
    return types.MessageMediaDocument(document=doc, voice=True)


def sticker_media():
    doc = types.Document(
        id=33, access_hash=44, file_reference=b"r2", date=None, mime_type="image/webp",
        size=1000, dc_id=2,
        attributes=[types.DocumentAttributeSticker(alt="🙂", stickerset=types.InputStickerSetEmpty())],
    )
    return types.MessageMediaDocument(document=doc)


def photo_media():
    p = types.Photo(id=5, access_hash=6, file_reference=b"pr", date=None,
                    sizes=[types.PhotoSize("x", 800, 600, 50000)], dc_id=2)
    return types.MessageMediaPhoto(photo=p)


def run(coro):
    return asyncio.run(coro)


async def make(plugins=("anti_delete",), uid=None):
    c = FakeClient()
    uid = uid or (9000 + Msg._next)
    started = []
    if "anti_delete" in plugins:
        p = AD.AntiDeletePlugin(c, uid)
        await p.start()
        started.append(p)
    if "anti_edit" in plugins:
        p = AE.AntiEditPlugin(c, uid)
        await p.start()
        started.append(p)
    new_handler = next(cb for cb, ev in c.handlers if isinstance(ev, events.NewMessage))
    return c, started, new_handler


async def settle(c):
    await asyncio.sleep(0.35)
    await outbox.for_client(c).drain()
    await asyncio.sleep(0.05)


# ═══════ Tests ═══════

def test_zero_rpc_on_receive():
    async def go():
        c, ps, on_new = await make()
        base_me = c.rpc["get_me"]
        for i in range(100):
            await on_new(NewEv(Msg(PEER, PEER, f"hi {i}"), PEER))
        await asyncio.sleep(0.05)       # warm در پس‌زمینه
        assert c.rpc["get_entity"] <= 1, c.rpc
        assert c.rpc["get_me"] == base_me, c.rpc
        # بعد از یادگیری، دیگر هیچ درخواستی
        for i in range(50):
            await on_new(NewEv(Msg(PEER, ME, f"me {i}", out=True), PEER))
        await asyncio.sleep(0.05)
        assert c.rpc["get_entity"] <= 1, c.rpc
        for p in ps:
            await p.stop()
    run(go())


def test_delete_report_format_and_order():
    async def go():
        c, ps, on_new = await make()
        m1 = Msg(PEER, PEER, "اول")
        m2 = Msg(PEER, ME, "دوم", out=True)
        await on_new(NewEv(m1, PEER))
        await on_new(NewEv(m2, PEER))
        await ps[0]._on_delete(DelEv([m2.id, m1.id]))
        await settle(c)
        texts = [s[2] for s in c.sent if s[0] == "msg"]
        assert len(texts) == 2, c.sent
        t1, t2 = texts
        assert t1.startswith("🗑 **پیام حذف شده (ضدحذف)**\n📥 از: Ali Rezaei (@ali_r)\n")
        assert "👤 فرستنده: Ali Rezaei — @ali_r (200)" in t1
        assert f"tg://" not in t1 and f"🔗 https://t.me/ali_r/{m1.id}" in t1
        assert "📌 پیامِ طرف مقابل حذف شد\n──────────────\n\n📝 متن:\nاول" in t1
        assert "👤 فرستنده: شما — @meuser (100)" in t2
        assert "📌 پیامِ خودت حذف شد" in t2 and t2.endswith("📝 متن:\nدوم")
        for p in ps:
            await p.stop()
    run(go())


def test_burst_summary():
    async def go():
        c, ps, on_new = await make()
        ids = []
        for i in range(12):
            m = Msg(PEER, PEER, f"پیام {i}")
            ids.append(m.id)
            await on_new(NewEv(m, PEER))
        vm = Msg(PEER, PEER, "", media=voice_media())
        ids.append(vm.id)
        await on_new(NewEv(vm, PEER))
        await asyncio.sleep(0.05)
        await ps[0]._on_delete(DelEv(ids[:7]))
        await ps[0]._on_delete(DelEv(ids[7:]))
        await settle(c)
        files = [s for s in c.sent if s[0] == "file"]
        msgs = [s for s in c.sent if s[0] == "msg"]
        assert not msgs, "burst نباید ۱۳ پیام جدا بفرستد"
        txt = [f for f in files if getattr(f[2], "name", "").endswith(".txt")]
        assert len(txt) == 1
        body = txt[0][4].decode()
        assert "پیام 0" in body and "پیام 11" in body and "[ویس]" in body
        assert "13 پیام حذف شد" in txt[0][3]["caption"]
        # ویس جدا و با قالب همیشگی
        media = [f for f in files if f is not txt[0]]
        assert len(media) == 1 and "🗑 **پیام حذف شده (ضدحذف)**" in media[0][3]["caption"]
        for p in ps:
            await p.stop()
    run(go())


def test_channel_delete_ignored_and_saved_not_cached():
    async def go():
        c, ps, on_new = await make()
        m = Msg(PEER, PEER, "پی‌وی")
        await on_new(NewEv(m, PEER))
        await ps[0]._on_delete(DelEv([m.id], chat_id=-1001234))   # حذف در کانال با همان شماره
        s = Msg(ME, ME, "یادداشت", out=True)
        await on_new(NewEv(s, ME))                                  # Saved Messages
        await ps[0]._on_delete(DelEv([s.id]))
        await settle(c)
        assert c.sent == [], c.sent
        assert ps[0]._cache.get(m.id) is not None   # هنوز در کش است
        for p in ps:
            await p.stop()
    run(go())


def test_webpage_is_text():
    async def go():
        c, ps, on_new = await make()
        m = Msg(PEER, PEER, "ببین https://x.com",
                media=types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1)))
        await on_new(NewEv(m, PEER))
        await ps[0]._on_delete(DelEv([m.id]))
        await settle(c)
        assert len(c.sent) == 1 and c.sent[0][0] == "msg"
        assert "ببین https://x.com" in c.sent[0][2]
        for p in ps:
            await p.stop()
    run(go())


def test_edit_then_delete():
    async def go():
        c, ps, on_new = await make(("anti_delete", "anti_edit"))
        ad, ae = ps
        m = Msg(PEER, PEER, "سلام")
        await on_new(NewEv(m, PEER))
        await ae._on_edit(EditEv(Msg(PEER, PEER, "سلام خوبی؟", mid=m.id)))
        await settle(c)
        t = c.sent[-1][2]
        assert t.startswith("✏️ **پیام ویرایش شده**\n💬 چت: Ali Rezaei\n👤 ویرایش‌کننده: Ali Rezaei (`200`)")
        assert "📝 **متن قبلی:**\nسلام\n\n📝 **متن جدید:**\nسلام خوبی؟" in t
        # ری‌اکشن (متن یکسان) → گزارشی نیست
        n = len(c.sent)
        await ae._on_edit(EditEv(Msg(PEER, PEER, "سلام خوبی؟", mid=m.id)))
        await settle(c)
        assert len(c.sent) == n
        # حذف بعد از ویرایش → متن اصلی (مثل قبل)
        await ad._on_delete(DelEv([m.id]))
        await settle(c)
        assert c.sent[-1][2].endswith("📝 متن:\nسلام")
        for p in ps:
            await p.stop()
    run(go())


def test_memory_cap():
    async def go():
        old = pv_cache.PV_CACHE_MAX_MB
        pv_cache.PV_CACHE_MAX_MB = 0.05          # ~52KB
        try:
            c, ps, on_new = await make()
            cache = ps[0]._cache
            for i in range(2000):
                await on_new(NewEv(Msg(PEER, PEER, "x" * 200), PEER))
            assert cache.bytes <= 0.05 * 1024 * 1024
            assert 0 < len(cache) < 2000
            for p in ps:
                await p.stop()
            assert len(cache) == 0 and cache.bytes == 0
        finally:
            pv_cache.PV_CACHE_MAX_MB = old
    run(go())


def test_vault_fallback_encrypted_and_cleaned():
    async def go():
        c, ps, on_new = await make()
        cache = ps[0]._cache
        m = Msg(PEER, PEER, "ویس", media=voice_media())
        await on_new(NewEv(m, PEER))
        await asyncio.sleep(0.1)                   # دانلود پس‌زمینه
        assert c.downloads == 1
        path = cache.vault._files[m.id][0]
        raw = open(path, "rb").read()
        assert b"OGG-VOICE-DATA" not in raw        # رمزنگاری‌شده روی دیسک
        c.fail_reference = True                    # ارجاع بعد از حذف باطل است
        await ps[0]._on_delete(DelEv([m.id]))
        await settle(c)
        f = [s for s in c.sent if s[0] == "file"][-1]
        assert f[4] == b"OGG-VOICE-DATA" * 20
        assert f[3].get("voice_note") is True and "🗑" in f[3]["caption"]
        assert not os.path.exists(path)            # بعد از ارسال پاک شد
        # پیام خودم دانلود نمی‌شود (پهنای باند)
        await on_new(NewEv(Msg(PEER, ME, "", media=voice_media(), out=True), PEER))
        await asyncio.sleep(0.05)
        assert c.downloads == 1
        for p in ps:
            await p.stop()
        assert not os.path.exists(cache.vault.dir)
    run(go())


def test_unrecoverable_media_still_reports_text():
    async def go():
        c, ps, on_new = await make()
        c.fail_reference = True
        m = Msg(PEER, PEER, "کپشن", media=photo_media())
        pv_cache.PV_VAULT_ENABLED, old = False, pv_cache.PV_VAULT_ENABLED
        try:
            await on_new(NewEv(m, PEER))
        finally:
            pv_cache.PV_VAULT_ENABLED = old
        await ps[0]._on_delete(DelEv([m.id]))
        await settle(c)
        t = c.sent[-1][2]
        assert "⚠️ عکس قابل بازیابی نبود" in t and t.endswith("📝 متن:\nکپشن")
        for p in ps:
            await p.stop()
    run(go())


def test_sticker_reference_cleans_recents():
    async def go():
        c, ps, on_new = await make()
        m = Msg(PEER, PEER, "", media=sticker_media())
        await on_new(NewEv(m, PEER))
        await ps[0]._on_delete(DelEv([m.id]))
        await settle(c)
        names = [type(r).__name__ for r in c.calls]
        assert "SaveRecentStickerRequest" in names, names
        for p in ps:
            await p.stop()
    run(go())


def test_outbox_floodwait_retry():
    async def go():
        c, ps, on_new = await make()
        m = Msg(PEER, PEER, "مهم")
        await on_new(NewEv(m, PEER))
        c.flood_once = True
        await ps[0]._on_delete(DelEv([m.id]))
        await asyncio.sleep(0.3)
        await outbox.for_client(c).drain(timeout=10)
        await asyncio.sleep(2.2)                     # FloodWait 1s + 1
        await outbox.for_client(c).drain(timeout=10)
        assert any("مهم" in s[2] for s in c.sent if s[0] == "msg"), c.sent
        for p in ps:
            await p.stop()
    run(go())


def test_old_messages_skipped():
    async def go():
        c, ps, on_new = await make()
        m = Msg(PEER, PEER, "قدیمی")
        m.date = datetime.now(timezone.utc) - timedelta(days=8)
        cache = ps[0]._cache
        cache.add_message(m, PEER)
        await ps[0]._on_delete(DelEv([m.id]))
        await settle(c)
        assert c.sent == []
        for p in ps:
            await p.stop()
    run(go())


def test_disable_anti_delete_frees_media_keeps_edit():
    async def go():
        c, ps, on_new = await make(("anti_delete", "anti_edit"))
        ad, ae = ps
        cache = ad._cache
        t = Msg(PEER, PEER, "متن")
        v = Msg(PEER, PEER, "", media=voice_media())
        cap = Msg(PEER, PEER, "کپشن", media=photo_media())
        for m in (t, v, cap):
            await on_new(NewEv(m, PEER))
        await asyncio.sleep(0.1)
        before = cache.bytes
        await ad.stop()                       # فقط ضدحذف خاموش شد
        assert cache.get(v.id) is None        # بدون متن → دیگر لازم نیست
        assert cache.get(cap.id).ref is None  # ارجاع آزاد شد، متن برای ضدویرایش ماند
        assert cache.get(t.id) is not None
        assert cache.bytes < before and len(cache.vault) == 0
        assert any(isinstance(ev, events.NewMessage) for _, ev in c.handlers)  # هندلر مشترک ماند
        await ae.stop()
        assert not any(isinstance(ev, events.NewMessage) for _, ev in c.handlers)
    run(go())


# ═══════ مثبت کاذب ═══════

def test_selfbot_deleting_its_command_is_not_reported():
    async def go():
        c, ps, on_new = await make()
        cmd = Msg(PEER, ME, ".راهنما", out=True)
        real = Msg(PEER, ME, "پیام واقعی", out=True)
        await on_new(NewEv(cmd, PEER))
        await on_new(NewEv(real, PEER))
        await c.delete_messages(PEER, [cmd.id])        # مثل event.delete() در پلاگین
        await ps[0]._on_delete(DelEv([cmd.id]))
        await ps[0]._on_delete(DelEv([real.id]))       # کاربر خودش در اپ پاک کرد
        await settle(c)
        texts = [x[2] for x in c.sent if x[0] == "msg"]
        assert len(texts) == 1 and "پیام واقعی" in texts[0], texts
        assert not any(".راهنما" in t for t in texts)
        for p in ps:
            await p.stop()
    run(go())


def test_selfbot_edits_are_not_reported():
    async def go():
        c, ps, on_new = await make(("anti_delete", "anti_edit"))
        ae = ps[1]
        heart = Msg(PEER, ME, ".قلب", out=True)
        await on_new(NewEv(heart, PEER))
        for frame in ("❤️", "🧡", "💛"):               # انیمیشن = ویرایش پشت‌سرهم
            await c.edit_message(PEER, heart.id, frame)
            await ae._on_edit(EditEv(Msg(PEER, ME, frame, out=True, mid=heart.id)))
        await settle(c)
        assert c.sent == [], c.sent
        # ویرایش واقعی کاربر (در اپ) همچنان گزارش می‌شود
        m = Msg(PEER, PEER, "قبل")
        await on_new(NewEv(m, PEER))
        await ae._on_edit(EditEv(Msg(PEER, PEER, "بعد", mid=m.id)))
        await settle(c)
        assert len(c.sent) == 1 and "📝 **متن قبلی:**\nقبل" in c.sent[0][2]
        for p in ps:
            await p.stop()
    run(go())


def test_auto_delete_timer_is_not_a_deletion():
    async def go():
        c, ps, on_new = await make()
        expired = Msg(PEER, PEER, "با تایمر")
        expired.ttl_period = 60
        expired.date = datetime.now(timezone.utc) - timedelta(seconds=70)
        early = Msg(PEER, PEER, "زود حذف شد")
        early.ttl_period = 86400                        # تایمر یک‌روزه، ولی همین الان حذف شد
        await on_new(NewEv(expired, PEER))
        await on_new(NewEv(early, PEER))
        await ps[0]._on_delete(DelEv([expired.id, early.id]))
        await settle(c)
        texts = [x[2] for x in c.sent]
        assert len(texts) == 1 and "زود حذف شد" in texts[0], texts
        for p in ps:
            await p.stop()
    run(go())


def test_view_once_media_not_cached():
    async def go():
        c, ps, on_new = await make()
        media = photo_media()
        media.ttl_seconds = 10
        m = Msg(PEER, PEER, "", media=media)
        await on_new(NewEv(m, PEER))
        assert ps[0]._cache.get(m.id) is None
        await ps[0]._on_delete(DelEv([m.id]))           # خودتخریبی بعد از دیدن
        await settle(c)
        assert c.sent == []
        for p in ps:
            await p.stop()
    run(go())


# ═══════ بازخوانی تاریخچه (قبل از ری‌استارت) ═══════

def _dialog(ent, hours_ago=1):
    return SimpleNamespace(entity=ent, date=datetime.now(timezone.utc) - timedelta(hours=hours_ago))


async def make_with_history(plugins=("anti_delete",), setup=None):
    """کلاینت را قبل از start پلاگین‌ها آماده می‌کند (مثل ری‌استارت واقعی)"""
    c = FakeClient()
    if setup:
        setup(c)
    uid = 9000 + Msg._next
    ps = []
    for name in plugins:
        P = AD.AntiDeletePlugin if name == "anti_delete" else AE.AntiEditPlugin
        p = P(c, uid)
        await p.start()
        ps.append(p)
    cache = ps[0]._cache
    if cache._warm_task:
        await asyncio.wait_for(cache._warm_task, 10)
    on_new = next(cb for cb, ev in c.handlers if isinstance(ev, events.NewMessage))
    return c, ps, on_new, cache


def _history_setup(c):
    old1 = Msg(PEER, PEER, "قبل از ری‌استارت ۱")
    old2 = Msg(PEER, ME, "قبل از ری‌استارت ۲", out=True)
    svc = Msg(PEER, PEER, "")
    svc.action = object()                                    # پیام سرویس
    ancient = Msg(PEER, PEER, "خیلی قدیمی")
    ancient.date = datetime.now(timezone.utc) - timedelta(days=5)
    c.history[PEER] = [svc, old2, old1, ancient]             # جدید → قدیمی
    bot = types.User(id=400, first_name="SomeBot", bot=True)
    me = types.User(id=ME, first_name="Me", is_self=True)
    stale = types.User(id=300, first_name="Sara")
    c.dialogs = [_dialog(me), _dialog(bot), _dialog(c.users[PEER]), _dialog(stale, hours_ago=200)]
    c._ids = (old1.id, old2.id, svc.id, ancient.id)


def test_warmup_covers_messages_from_before_restart():
    async def go():
        c, ps, on_new, cache = await make_with_history(setup=_history_setup)
        old1, old2, svc, ancient = c._ids
        assert c.history_calls == [PEER], c.history_calls   # نه ربات، نه خودم، نه چت قدیمی
        assert cache.get(old1) and cache.get(old2)
        assert cache.get(svc) is None and cache.get(ancient) is None
        assert c.rpc["get_entity"] == 0                      # نام‌ها از خود دیالوگ‌ها
        await ps[0]._on_delete(DelEv([old1]))
        await settle(c)
        t = c.sent[-1][2]
        assert "📥 از: Ali Rezaei (@ali_r)" in t and t.endswith("📝 متن:\nقبل از ری‌استارت ۱")
        # پیام‌هایی که حذف نشده‌اند گزارش نمی‌شوند
        assert len(c.sent) == 1
        assert cache.warm_stats["state"] == "انجام شد"
        for p in ps:
            await p.stop()
    run(go())


def test_warmup_never_overrides_live_record():
    async def go():
        c = FakeClient()
        uid = 9000 + Msg._next
        ad = AD.AntiDeletePlugin(c, uid)
        ae = AE.AntiEditPlugin(c, uid)
        # بازخوانی را عقب می‌اندازیم تا پیام زنده اول برسد
        old_delay = pv_cache.WARMUP_DELAY
        pv_cache.WARMUP_DELAY = (0.3, 0.3)
        try:
            await ad.start()
            await ae.start()
            on_new = next(cb for cb, ev in c.handlers if isinstance(ev, events.NewMessage))
            live = Msg(PEER, PEER, "متن اصلی")
            await on_new(NewEv(live, PEER))
            await ae._on_edit(EditEv(Msg(PEER, PEER, "متن ویرایش‌شده", mid=live.id)))
            c.history[PEER] = [Msg(PEER, PEER, "متن ویرایش‌شده", mid=live.id)]
            c.dialogs = [_dialog(c.users[PEER])]
            await asyncio.wait_for(ad._cache._warm_task, 10)
        finally:
            pv_cache.WARMUP_DELAY = old_delay
        await settle(c)
        c.sent.clear()
        await ad._on_delete(DelEv([live.id]))
        await settle(c)
        assert c.sent[-1][2].endswith("📝 متن:\nمتن اصلی")   # متن قبل از ویرایش حفظ شد
        await ad.stop()
        await ae.stop()
    run(go())


def test_warmup_stops_on_long_floodwait():
    async def go():
        def setup(c):
            c.dialogs = [_dialog(c.users[PEER]), _dialog(c.users[300])]
            c.history_error = FloodWaitError(request=None, capture=120)
        c, ps, on_new, cache = await make_with_history(setup=setup)
        assert c.history_calls == [PEER]                     # بعد از اولی متوقف شد
        assert "محدودیت" in cache.warm_stats["state"]
        # کش زنده همچنان کار می‌کند
        m = Msg(PEER, PEER, "زنده")
        await on_new(NewEv(m, PEER))
        await ps[0]._on_delete(DelEv([m.id]))
        await settle(c)
        assert "زنده" in c.sent[-1][2]
        for p in ps:
            await p.stop()
    run(go())


def test_warmup_vault_budget_newest_first():
    async def go():
        old_budget = pv_cache.PV_WARMUP_VAULT_FILES
        pv_cache.PV_WARMUP_VAULT_FILES = 2
        try:
            def setup(c):
                voices = [Msg(PEER, PEER, "", media=voice_media()) for _ in range(5)]
                c.history[PEER] = list(reversed(voices))
                c.dialogs = [_dialog(c.users[PEER])]
                c._voices = voices
            c, ps, on_new, cache = await make_with_history(setup=setup)
            await asyncio.sleep(0.2)
            assert c.downloads == 2, c.downloads
            assert set(cache.vault._files) == {c._voices[-1].id, c._voices[-2].id}
            for p in ps:
                await p.stop()
        finally:
            pv_cache.PV_WARMUP_VAULT_FILES = old_budget
    run(go())


def test_warmup_adds_media_when_anti_delete_enabled_later():
    async def go():
        def setup(c):
            c.history[PEER] = [Msg(PEER, PEER, "کپشن", media=photo_media())]
            c.dialogs = [_dialog(c.users[PEER])]
        c, ps, on_new, cache = await make_with_history(("anti_edit",), setup=setup)
        mid = c.history[PEER][0].id
        assert cache.get(mid).ref is None                     # فقط ضدویرایش → بدون مدیا
        ad = AD.AntiDeletePlugin(c, ps[0].user_id)
        await ad.start()                                      # حالا ضدحذف روشن شد
        await asyncio.wait_for(cache._warm_task, 10)
        assert isinstance(cache.get(mid).ref, types.InputPhoto)
        await ad.stop()
        for p in ps:
            await p.stop()
    run(go())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
