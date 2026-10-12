"""
تست «کاربر با آیدی عددی» — بدون شبکه و بدون دیتابیس.

مهم‌ترین چیزی که این تست تضمین می‌کند:
    تا وقتی خودت دکمه‌ی «✅ ارسال کن» را نزنی،
    هیچ پیامی برای طرف مقابل فرستاده نمی‌شود.
(خواسته‌ی صریح کاربر: «نمی‌خوام به کاربر چیزی ارسال بشه و مطلع بشه»)

اجرا:
    PYTHONPATH=. python tests/test_user_info.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon.tl import types as tl                       # noqa: E402
from telethon.tl.functions.messages import (              # noqa: E402
    GetPeerDialogsRequest, SaveDraftRequest)
from telethon.tl.functions.users import GetFullUserRequest  # noqa: E402

from plugins.user_info import UserInfoPlugin               # noqa: E402

# ── دیتابیس جعلی ──
# راند ۳۶ «سابقه‌ی کاربران» (دایرکتوری) اضافه شد و جست‌وجو اول از آن می‌خواند،
# پس این تست باید بدون پستگرس هم اجرا شود: توابع دیتابیس را در حافظه
# جایگزین می‌کنیم تا تست به سرور دیتابیس وابسته نباشد.
import database.db as dbmod                                # noqa: E402

_MEM: dict = {}
_AI: dict = {}
_FEATURES: dict = {}


async def _remember_user(user_id, tg_id, **kw):
    rec = _MEM.setdefault((user_id, tg_id), {"tg_id": tg_id, "msgs": 0})
    for k, v in kw.items():
        if k == "count_msg":
            if v:
                rec["msgs"] = rec.get("msgs", 0) + 1
            continue
        if v is not None:
            rec[k] = v
    return rec


async def _get_user_record(user_id, tg_id):
    return _MEM.get((user_id, tg_id))


async def _find_user_record(user_id, *, username=None, phone=None):
    for (u, _), rec in _MEM.items():
        if u != user_id:
            continue
        if username and (rec.get("username") or "").lower() == username.lower():
            return rec
        if phone and (rec.get("phone") or "") == phone:
            return rec
    return None


async def _search_directory(user_id, query, limit=20, kind=None,
                            exclude_kind=None):
    q = (query or "").lower()
    out = []
    for (u, _), rec in _MEM.items():
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


async def _list_directory(user_id, limit=30, kind=None, exclude_kind=None,
                          offset=0):
    rows = [r for (u, _), r in _MEM.items() if u == user_id]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if exclude_kind:
        rows = [r for r in rows if r.get("kind") != exclude_kind]
    rows.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
    return rows[int(offset):int(offset) + int(limit)]


async def _directory_count(user_id, kind=None, exclude_kind=None):
    rows = [(u, tg) for (u, tg) in _MEM if u == user_id]
    if kind:
        rows = [r for r in rows if _MEM[r].get("kind") == kind]
    if exclude_kind:
        rows = [r for r in rows if _MEM[r].get("kind") != exclude_kind]
    return len(rows)


async def _prune_directory(user_id, **kw):
    return {"groups": 0, "capped": 0, "total": 0}


async def _purge_group_rows(user_id):
    return 0


async def _set_directory_link(user_id, tg_id, last_msg_id):
    rec = _MEM.get((user_id, tg_id))
    if rec is not None:
        rec["last_msg_id"] = int(last_msg_id)


async def _get_ai_profile(user_id, target_id):
    return _AI.get((user_id, target_id))


async def _upsert_ai_profile(user_id, target_id, **fields):
    prof = _AI.setdefault((user_id, target_id), {})
    prof.update(fields)
    return prof


async def _get_features(user_id):
    return list(_FEATURES.values())


async def _set_feature(user_id, feature, enabled, config=None):
    _FEATURES[feature] = {"feature_name": feature, "is_enabled": enabled,
                          "config_json": config or {}}


for _name, _fn in (("remember_user", _remember_user),
                   ("get_user_record", _get_user_record),
                   ("find_user_record", _find_user_record),
                   ("search_directory", _search_directory),
                   ("list_directory", _list_directory),
                   ("directory_count", _directory_count),
                   ("prune_directory", _prune_directory),
                   ("purge_group_rows", _purge_group_rows),
                   ("set_directory_link", _set_directory_link),
                   ("get_ai_profile", _get_ai_profile),
                   ("upsert_ai_profile", _upsert_ai_profile),
                   ("get_features", _get_features),
                   ("set_feature", _set_feature)):
    setattr(dbmod, _name, _fn)

PASS, FAIL = [], []


def check(name: str, ok: bool, extra: str = ""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


# ═══════════ ماکت تلگرام ═══════════

class FakeUser(tl.User):
    def __init__(self, uid=555, first="علی", last="رضایی", username="ali",
                 phone=None, bot=False, verified=False):
        super().__init__(id=uid, is_self=False, contact=False, mutual_contact=False,
                         deleted=False, bot=bot, bot_chat_history=None, bot_nochats=None,
                         verified=verified, restricted=False, min=False, bot_inline_geo=False,
                         support=False, scam=False, apply_min_photo=False, fake=False,
                         access_hash=1, first_name=first, last_name=last, username=username,
                         phone=phone, status=tl.UserStatusRecently())


class FakeChannel:
    def __init__(self, cid=9001, title="کانال من", username="mychannel"):
        self.id = cid
        self.title = title
        self.username = username
        self.channel_id = cid


class FakeDialog:
    def __init__(self, entity):
        self.entity = entity


class FakeUserFull(tl.UserFull):
    def __init__(self, about="بیو من", common_chats=3, blocked=False, personal=None):
        super().__init__(id=None, settings=tl.PeerSettings(), notify_settings=None,
                         about=about, common_chats_count=common_chats, blocked=blocked,
                         profile_photo=None, folder_id=None, bot_info=None)
        if personal is not None:
            self.personal_chat_id = personal


class FakeFullRes:
    def __init__(self, full_user):
        self.full_user = full_user
        self.users = []
        self.chats = []


class FakePeerDialogsRes:
    def __init__(self, dialogs):
        self.dialogs = dialogs
        self.messages = []
        self.state = None
        self.users = []


class FakeContactsRes:
    def __init__(self, users):
        self.users = users
        self.contacts = []


class FakeSent:
    def __init__(self, mid=77):
        self.id = mid


class FakeClient:
    """کلاینت جعلی: رفتار تلگرام را با تنظیمات دلخواه تقلید می‌کند"""

    def __init__(self, user=None, dialogs=(), in_contacts=False,
                 has_dialog=True, photo=b"\x89PNG-fake", personal=None,
                 about="بیو من", get_entity_raises=()):
        self.user = user
        self._dialogs = list(dialogs)
        self._in_contacts = in_contacts
        self._has_dialog = has_dialog
        self._photo = photo
        self._personal = personal
        self._about = about
        self._raises = set(get_entity_raises)
        self.sent = []              # (peer, text) — ارسال‌های واقعی
        self.drafts = []            # (peer, text)
        self.handlers = {}
        self.calls = []

    # ── Telethon API ──
    async def get_me(self):
        return tl.User(id=999, is_self=True, contact=False, mutual_contact=False,
                       deleted=False, bot=False, bot_chat_history=None, bot_nochats=None,
                       verified=False, restricted=False, min=False, bot_inline_geo=False,
                       support=False, scam=False, apply_min_photo=False, fake=False,
                       first_name="من")

    async def get_entity(self, target):
        if isinstance(target, str):
            if "username" in self._raises:
                raise ValueError("username not found")
            return self.user
        if isinstance(target, int) or isinstance(target, tl.InputPeerUser) \
                or isinstance(target, tl.PeerChannel):
            if "id" in self._raises:
                raise ValueError("USER_ID_INVALID")
            if target == 9001 and self._personal is not None:
                return self._personal
            if isinstance(target, tl.PeerChannel):
                return self._personal or FakeChannel()
            return self.user
        if isinstance(target, tl.User):
            return target
        if hasattr(target, "title"):          # کانال شخصی (ماکت)
            return target
        return self.user

    async def get_input_entity(self, entity):
        uid = getattr(entity, "id", 555)
        return tl.InputPeerUser(user_id=uid, access_hash=1)

    def iter_dialogs(self, limit=None, ignore_migrated=False):
        client = self

        class _Iter:
            def __aiter__(self_inner):
                async def gen():
                    for d in client._dialogs[:limit]:
                        yield d
                return gen()
        return _Iter()

    async def __call__(self, request):
        self.calls.append(type(request).__name__)
        if isinstance(request, GetFullUserRequest):
            return FakeFullRes(FakeUserFull(about=self._about, personal=self._personal))
        if isinstance(request, GetPeerDialogsRequest):
            return FakePeerDialogsRes([object()] if self._has_dialog else [])
        if isinstance(request, SaveDraftRequest):
            self.drafts.append((request.peer, request.message))
            return True
        name = type(request).__name__
        if name == "GetContactsRequest":
            return FakeContactsRes([self.user] if self._in_contacts else [])
        return True

    async def download_profile_photo(self, entity, file=None):
        if self._photo and file is not None:
            file.write(self._photo)
        return self._photo

    async def send_message(self, peer, text, **kw):
        self.sent.append((peer, text))
        return FakeSent()


def make_plugin(client, monkeypatch_runtime=True):
    plugin = UserInfoPlugin(client, user_id=1)
    plugin._known = {}
    plugin._owner_tg = None          # هیچ ربات کنترلی → فالبک: پیام‌های ذخیره‌شده
    return plugin


def sent_to_others(client):
    """ارسال‌هایی که به کسی غیر از «me» (پیام‌های ذخیره‌شده) رفته‌اند"""
    return [(p, t) for p, t in client.sent if p != "me"]


# ═══════════ تست‌ها ═══════════



# ═══════════ ماکت ربات کنترلی (برای تست دکمه‌ها) ═══════════

class FakeQ:
    def __init__(self, data):
        self.data = data
        self.edits = []
        self.answers = []
        self.deleted = False
        self.message = self

    async def answer(self, text=None, show_alert=False, **kw):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None, **kw):
        self.edits.append((text, str(reply_markup)))

    async def reply_text(self, text, **kw):
        self.edits.append((text, str(kw.get("reply_markup"))))

    async def delete(self):
        self.deleted = True


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, **kw):
        self.sent.append((chat_id, text, str(kw.get("reply_markup"))))
        return None


class FakeUpdate:
    def __init__(self, data=None, text=None, chat_id=42):
        self.callback_query = FakeQ(data) if data is not None else None
        self.effective_chat = type("C", (), {"id": chat_id})()
        self.effective_user = type("U", (), {"id": chat_id})()
        self.message = type("M", (), {
            "text": text, "reply_text": self._reply, "delete": self._del})()
        self._replies = []

    async def _reply(self, text, **kw):
        self._replies.append((text, str(kw.get("reply_markup"))))

    async def _del(self):
        return None


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.bot = FakeBot()


async def flows(plugin_cls=FakeClient):
    """جریان دکمه‌های پنل: تا تأیید نکنی، هیچ پیامی نمی‌رود"""
    import bot.user_panel as panel

    client = FakeClient(user=FakeUser(uid=555))
    plugin = make_plugin(client)
    await plugin.lookup("555")
    panel._plugin = lambda user_id: plugin          # پلاگین جعلی
    ctx = FakeContext()

    # ۱) دکمه‌ی «آماده‌کردن چت» → فقط پیش‌نویس، هیچ پیامی
    u = FakeUpdate("u:draft:555")
    handled = await panel.cb_user(u, ctx, 1)
    check("دکمه‌ی آماده‌کردن چت مدیریت می‌شود", handled is True)
    check("دکمه‌ی آماده‌کردن، پیش‌نویس می‌سازد", len(client.drafts) == 1, str(client.drafts))
    check("🔒 دکمه‌ی آماده‌کردن هیچ پیامی نفرستاد", not sent_to_others(client),
          str(sent_to_others(client)))
    check("پیام تأیید به خودم نشان داده می‌شود",
          any("پیش‌نویس" in (t or "") for t, _ in u.callback_query.edits))

    # ۲) دکمه‌ی «نوشتن پیام» → فقط حالت انتظار، هیچ ارسالی
    u2 = FakeUpdate("u:msg:555")
    await panel.cb_user(u2, ctx, 1)
    check("دکمه‌ی نوشتن پیام حالت انتظار می‌سازد",
          ctx.user_data.get("awaiting_user_msg") == 555)
    check("🔒 دکمه‌ی نوشتن پیام چیزی نفرستاد", not sent_to_others(client))

    # ۳) متن نوشته می‌شود → فقط پیش‌نمایش
    u3 = FakeUpdate(text="سلام، خوبی؟")
    got = await panel.handle_user_text(u3, ctx, 1)
    check("متن ذخیره می‌شود (ولی نه ارسال)", got is True
          and ctx.user_data.get("user_msg_text") == "سلام، خوبی؟")
    check("🔒 بعد از نوشتن متن هم چیزی نرفته", not sent_to_others(client))
    check("پیش‌نمایش با دکمه‌ی تأیید می‌آید",
          any("u:send:555" in (kb or "") for *_, kb in ctx.bot.sent),
          str([kb[-60:] for *_, kb in ctx.bot.sent][:1]))

    # ۴) دکمه‌ی تأیید → ارسال واقعی (فقط همین‌جا)
    u4 = FakeUpdate("u:send:555")
    await panel.cb_user(u4, ctx, 1)
    check("دکمه‌ی ارسال، پیام را می‌فرستد", len(sent_to_others(client)) == 1,
          str(sent_to_others(client)))
    check("متن ارسالی همان متن تأییدشده است",
          sent_to_others(client)[0][1] == "سلام، خوبی؟")
    check("بعد از ارسال، متن از حافظه پاک می‌شود",
          not ctx.user_data.get("user_msg_text"))

    # ۵) لغو: بعدش دیگر ارسالی نمی‌شود
    u5 = FakeUpdate("u:msg:555")
    await panel.cb_user(u5, ctx, 1)
    u6 = FakeUpdate(text="این نباید برود")
    await panel.handle_user_text(u6, ctx, 1)
    u7 = FakeUpdate("u:abort:555")
    await panel.cb_user(u7, ctx, 1)
    u8 = FakeUpdate("u:send:555")
    await panel.cb_user(u8, ctx, 1)
    check("🔒 بعد از لغو، هیچ پیامی فرستاده نمی‌شود",
          len(sent_to_others(client)) == 1, str(sent_to_others(client)))

    # ۶) لینک: فقط لینک، نه پیام به طرف
    u9 = FakeUpdate("u:link:555")
    await panel.cb_user(u9, ctx, 1)
    check("دکمه‌ی لینک، لینک را نشان می‌دهد",
          any("tg://openmessage" in (t or "") for _, t, _ in ctx.bot.sent),
          str(ctx.bot.sent)[:80])
    check("🔒 لینک هم چیزی به طرف نفرستاد", len(sent_to_others(client)) == 1)

    # ۷) بستن کارت
    u10 = FakeUpdate("u:close:555")
    await panel.cb_user(u10, ctx, 1)
    check("دکمه‌ی بستن، کارت را حذف می‌کند", u10.callback_query.deleted is True)

    # ۸) دیتای غیرمرتبط را برنمی‌دارد
    check("دیتای غیر u: را نمی‌گیرد",
          await panel.cb_user(FakeUpdate("ai:menu"), ctx, 1) is False)

    # ۹) منوی تعاملی سابقه
    await menu_flow()


async def menu_flow():
    """منوی تعاملی سابقه: دکمه‌ها، صفحه‌بندی، باز کردن کارت هر نفر"""
    import time as _t

    import bot.user_panel as panel

    client = FakeClient(user=FakeUser(uid=6001))
    plugin = make_plugin(client)
    # چند نفر در سابقه (به‌ترتیب زمان) — از دیتابیس جعلی همین فایل
    _MEM.clear()
    for i in range(10):
        _MEM[(1, 6000 + i)] = {
            "tg_id": 6000 + i, "first_name": f"کاربر{i}", "kind": "pv",
            "msgs": i + 1, "last_seen": f"2026-10-{10 + i}:00:00"}
    panel._plugin = lambda user_id: plugin
    ctx = FakeContext()

    # ۱) صفحه‌ی دوم منو
    u = FakeUpdate("u:hist:1")
    ok = await panel.cb_user(u, ctx, 1)
    check("دکمه‌ی صفحه‌بندی منو کار می‌کند", ok is True and u.callback_query.edits,
          str(u.callback_query.answers))
    text, kb = u.callback_query.edits[-1]
    check("صفحه‌ی دوم در متن نشان داده می‌شود", "صفحه 2" in text, text[:60])
    check("دکمه‌ی هر کاربر در منو هست", "u:card:" in kb, kb[:60])

    # ۲) کلیک روی یک کاربر → کارت پروفایل
    u2 = FakeUpdate("u:card:6003")
    ok2 = await panel.cb_user(u2, ctx, 1)
    check("کلیک روی کاربر کارت پروفایل می‌آورد",
          ok2 is True and u2.callback_query.edits, str(u2.callback_query.answers))
    ctext, ckb2 = u2.callback_query.edits[-1]
    check("کارت پروفایل نام واقعی را نشان می‌دهد", "علی رضایی" in ctext,
          ctext[:60])
    check("دکمه‌ی بازگشت به سابقه روی کارت است", "u:hist:0" in ckb2, ckb2[:80])

    # ۳) دکمه‌ی بی‌اثر (شماره‌ی صفحه)
    u3 = FakeUpdate("u:noop:0")
    check("دکمه‌ی شماره‌ی صفحه بدون خطا مدیریت می‌شود",
          await panel.cb_user(u3, ctx, 1) is True)

    # ۴) «جست‌وجو در گزارش‌های ضدحذف» وقتی چیزی نیست
    class NoReportClient(FakeClient):
        def iter_messages(self, peer, limit=None, search=None):
            class _I:
                def __aiter__(self_inner):
                    async def gen():
                        if False:
                            yield None
                    return gen()
            return _I()

    plugin2 = make_plugin(NoReportClient(user=FakeUser(uid=6001)))
    panel._plugin = lambda user_id: plugin2
    plugin2._report_cache = {}
    u4 = FakeUpdate("u:scan:999999")
    ok4 = await panel.cb_user(u4, ctx, 1)
    check("دکمه‌ی جست‌وجو در گزارش‌ها خطا نمی‌دهد", ok4 is True)
    check("و وقتی چیزی نبود به کاربر خبر می‌دهد",
          any(ans and "گزارش" in str(ans) for ans in u4.callback_query.answers),
          str(u4.callback_query.answers))

    panel._plugin = lambda user_id: plugin


async def main():
    print("=" * 68)
    print("تست کاربر با آیدی عددی — تضمین «هیچ پیام ناخواسته‌ای نمی‌رود»")
    print("=" * 68)

    # ── ۱) پیدا کردن از حافظه/نشست ──
    client = FakeClient(user=FakeUser(uid=555))
    plugin = make_plugin(client)
    res = await plugin.lookup("555")
    check("کاربر با آیدی عددی پیدا می‌شود", res.get("ok") is True, str(res.get("reason") or ""))
    check("نام کامل درست است", res.get("name") == "علی رضایی", str(res.get("name")))
    check("یوزرنیم خوانده می‌شود", res.get("username") == "ali")
    check("بیو خوانده می‌شود", res.get("bio") == "بیو من", str(res.get("bio")))
    check("تعداد چت مشترک خوانده می‌شود", res.get("common_chats") == 3)
    check("عکس پروفایل گرفته می‌شود", bool(res.get("photo")))
    check("وضعیت چت مشخص می‌شود", res.get("has_dialog") is True)
    check("🔒 در حین جست‌وجو هیچ پیامی به کسی نرفت", not sent_to_others(client),
          str(sent_to_others(client)))
    check("🔒 هیچ پیش‌نویسی هم خودکار ساخته نشد", not client.drafts)

    # ── ۲) نقشه‌ی پیدا شدن: مخاطبین ──
    c2 = FakeClient(user=FakeUser(uid=777, first="سارا", username=None),
                    in_contacts=True, get_entity_raises=("id",))
    p2 = make_plugin(c2)
    r2 = await p2.lookup("777")
    check("اگر نشست نشناسد، از مخاطبین پیدا می‌شود",
          r2.get("ok") and r2.get("how") == "مخاطبینت", str(r2.get("how")))

    # ── ۳) نقشه‌ی پیدا شدن: چت‌ها ──
    c3 = FakeClient(user=FakeUser(uid=888, first="رضا"),
                    dialogs=[FakeDialog(FakeUser(uid=1)), FakeDialog(FakeUser(uid=888))],
                    get_entity_raises=("id",))
    p3 = make_plugin(c3)
    r3 = await p3.lookup("888")
    check("اگر آن دو نشد، از چت‌ها پیدا می‌شود",
          r3.get("ok") and r3.get("how") == "چت‌هایت", str(r3.get("how")))

    # ── ۴) پیدا نشد → پیام روشن، بدون خطا ──
    c4 = FakeClient(user=None, get_entity_raises=("id",))
    p4 = make_plugin(c4)
    r4 = await p4.lookup("999999999")
    check("آیدی ناشناس → «پیدا نشد» با دلیل",
          r4.get("ok") is False and bool(r4.get("reason")), str(r4.get("reason")))
    check("🔒 در حالت پیدا نشد هم چیزی فرستاده نشد", not sent_to_others(c4))

    # ── ۵) ورودی‌های مختلف ──
    c5 = FakeClient(user=FakeUser(uid=555), personal=FakeChannel())
    p5 = make_plugin(c5)
    check("آیدی با ارقام فارسی کار می‌کند", (await p5.lookup("۵۵۵")).get("ok") is True)
    p5._last_ok.clear()
    p5._last_fail.clear()
    check("یوزرنیم با @ کار می‌کند", (await p5.lookup("@ali")).get("ok") is True)
    r5c = await p5.lookup("https://t.me/ali")
    check("لینک t.me کار می‌کند", r5c.get("ok") is True)
    check("کانال شخصی پروفایل خوانده می‌شود",
          (r5c.get("personal_channel") or {}).get("username") == "mychannel",
          str(r5c.get("personal_channel")))
    r5d = await p5.lookup("https://t.me/+AbCdEf")
    check("لینک دعوت گروه رد می‌شود", r5d.get("ok") is False, str(r5d.get("reason")))

    # ── ۶) خودم و محدودیت سرعت ──
    c6 = FakeClient(user=FakeUser(uid=999))
    p6 = make_plugin(c6)
    p6._me_id = 999          # همان کاری که start() می‌کند
    r6 = await p6.lookup("999")
    check("آیدی خودم رد می‌شود", r6.get("ok") is False, str(r6.get("reason")))
    p6._last_ok.clear()
    p6._last_fail.clear()
    await p6.lookup("555")          # بار اول واقعاً از تلگرام خوانده شد
    before = len(c6.calls)
    r6b = await p6.lookup("555")
    check("بار دوم همان کاربر از «سابقه» می‌آید (نه از تلگرام)",
          r6b.get("ok") is True and "سابقه" in str(r6b.get("how")), str(r6b.get("how")))
    extra = [c for c in c6.calls[before:]
             if c not in ("GetFullUserRequest", "GetPeerDialogsRequest")]
    check("برای سابقه دنبال مخاطبین/چت‌های تلگرام نمی‌گردد",
          not extra, str(extra))

    # ضد فلود: جست‌وجوی ناموفقِ پشت‌سرهم محدود می‌شود (ولی قفل همیشگی نیست)
    _MEM.clear()
    c6b = FakeClient(user=None, get_entity_raises=("id", "username"))
    p6b = make_plugin(c6b)
    f1 = await p6b.lookup("123456")
    f2 = await p6b.lookup("123456")
    check("دو جست‌وجوی ناموفقِ پشت‌سرهم محدود می‌شود (ضد فلود)",
          f1.get("ok") is False and f2.get("ok") is False
          and "صبر" in str(f2.get("reason")), str(f2.get("reason")))
    p6b._last_fail.clear()
    f3 = await p6b.lookup("123456")
    check("ولی بعد از یک ثانیه دوباره تلاش می‌شود (قفل نمی‌ماند)",
          f3.get("ok") is False and "صبر" not in str(f3.get("reason")),
          str(f3.get("reason")))

    # همان بارِ موفق هم پشت‌سرهم دوباره اجرا نمی‌شود (۲ ثانیه)
    p6._last_ok.clear()
    _MEM.clear()
    c6c = FakeClient(user=FakeUser(uid=901))
    p6c = make_plugin(c6c)
    okc1 = await p6c.lookup("901")
    _MEM.clear()                    # سابقه را پاک کن تا کول‌داون تست شود
    p6c._known.clear()
    okc2 = await p6c.lookup("901")
    check("جست‌وجوی موفقِ پشت‌سرهم با فاصله‌ی کم رد می‌شود",
          okc1.get("ok") is True and okc2.get("ok") is False
          and "صبر" in str(okc2.get("reason")), str(okc2.get("reason")))

    # ── ۷) پیش‌نویس: چت آماده می‌شود، طرف مطلع نمی‌شود ──
    c7 = FakeClient(user=FakeUser(uid=555))
    p7 = make_plugin(c7)
    await p7.lookup("555")
    got = await p7.prepare_chat(555, "سلام")
    check("پیش‌نویس ساخته می‌شود", got.get("ok") is True, str(got.get("reason")))
    check("متن پیش‌نویس همان است که خواستی",
          c7.drafts and c7.drafts[0][1] == "سلام", str(c7.drafts))
    check("🔒 پیش‌نویس هیچ پیامی برای طرف نفرستاد", not sent_to_others(c7),
          str(sent_to_others(c7)))
    got2 = await p7.prepare_chat(555, None)
    check("پیش‌نویس پیش‌فرض هم بی‌خطر است",
          got2.get("ok") and c7.drafts[-1][1] == "🤍", str(c7.drafts[-1][1]))

    # ── ۸) ارسال واقعی: فقط وقتی خودت تأیید کنی ──
    c8 = FakeClient(user=FakeUser(uid=555))
    p8 = make_plugin(c8)
    await p8.lookup("555")
    check("🔒 تا قبل از تأیید، هیچ پیامی نرفته", not sent_to_others(c8))
    sent = await p8.send_message(555, "سلام از طرف من")
    check("بعد از تأیید، پیام می‌رود", sent.get("ok") is True, str(sent.get("reason")))
    check("متن پیام دقیقاً همان است",
          sent_to_others(c8) and sent_to_others(c8)[0][1] == "سلام از طرف من",
          str(sent_to_others(c8)))
    empty = await p8.send_message(555, "   ")
    check("متن خالی فرستاده نمی‌شود", empty.get("ok") is False)

    # ── ۹) لینک‌ها ──
    c9 = FakeClient(user=FakeUser(uid=555, username="ali"))
    p9 = make_plugin(c9)
    await p9.lookup("555")
    links = p9.chat_links(555)
    check("لینک باز کردن چت ساخته می‌شود",
          links.get("deep") == "tg://openmessage?user_id=555", str(links.get("deep")))
    check("لینک یوزرنیم ساخته می‌شود",
          links.get("username_url") == "https://t.me/ali", str(links.get("username_url")))
    check("منشن بدون ارسال پیام ساخته می‌شود",
          "tg://user?id=555" in (links.get("mention") or ""))
    check("🔒 ساختن لینک پیامی نمی‌فرستد", not sent_to_others(c9))

    # ── ۱۰) کارت و دکمه‌ها ──
    from bot.user_panel import card_kb, card_text, confirm_kb
    c10 = FakeClient(user=FakeUser(uid=555), personal=FakeChannel())
    p10 = make_plugin(c10)
    info = await p10.lookup("555")
    text = card_text(info)
    for piece in ("علی رضایی", "555", "@ali", "بیو من", "کانال", "پیش‌نویس"):
        check(f"کارت شامل «{piece}» است", piece in text)
    kb = str(card_kb(555, "ali"))
    for cb_data in ("u:draft:555", "u:msg:555", "u:link:555"):
        check(f"دکمه‌ی {cb_data} هست", cb_data in kb)
    check("دکمه‌ی باز کردن با یوزرنیم هست", "https://t.me/ali" in kb)
    check("دکمه‌ی تأیید ارسال جدا است", "u:send:555" in str(confirm_kb(555)))
    check("🔒 ساخت کارت هم پیامی نفرستاد", not sent_to_others(c10))

    # ── ۱۱) پیام «پیدا نشد» هم راهنمای درست دارد ──
    text2 = card_text({"ok": False, "reason": "آیدی را نشناخت", "target": "123"})
    check("کارت «پیدا نشد» راه یوزرنیم را نشان می‌دهد", "@username" in text2)

    # ── ۱۲) جریان دکمه‌های پنل ──
    print("\n" + "-" * 68)
    print("جریان دکمه‌ها (تضمین: تا تأیید نکنی چیزی نمی‌رود)")
    print("-" * 68)
    await flows()

    print("\n" + "=" * 68)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
