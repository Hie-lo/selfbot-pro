"""
تست قابلیت «مالک» — دیدن اطلاعات گروه/کانال و مالکش.

چه چیزی را تضمین می‌کند:
  ۱) پیدا کردن گروه/کانال با اسم، یوزرنیم، آیدی و لینک.
  ۲) پیدا کردن مالک: از فهرست مدیران (کانال/سوپرگروه) و از فهرست اعضا
     (گروه معمولی).
  ۳) وقتی مالک مخفی است، به‌جای خطا پیام روشن داده می‌شود.
  ۴) مالک در سابقه (user_directory) با access_hash ثبت می‌شود تا بعداً
     بدون گروه مشترک هم بتوان به او پیام داد.
  ۵) منوی لیست گروه/کانال‌ها + دکمه‌ها + صفحه‌بندی (پنل ربات).

اجرا:
    PYTHONPATH=. python tests/test_channel_owner.py
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _k, _v in (("BOT_TOKEN", "1:x"), ("ADMIN_TELEGRAM_ID", "42"),
               ("TELEGRAM_API_ID", "1"), ("TELEGRAM_API_HASH", "x"),
               ("DB_NAME", "sbt"), ("DB_USER", "sb"), ("DB_PASS", "sb"),
               ("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")):
    os.environ.setdefault(_k, _v)

from telethon.errors import ChatAdminRequiredError              # noqa: E402
from telethon.tl import types as tl                             # noqa: E402
from telethon.tl.functions.channels import (                    # noqa: E402
    GetFullChannelRequest, GetParticipantsRequest)
from telethon.tl.functions.messages import (                    # noqa: E402
    CheckChatInviteRequest, GetFullChatRequest)

import database.db as dbmod                                     # noqa: E402
from plugins.user_info import UserInfoPlugin                    # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


# ═══════════ ماکت دیتابیس ═══════════

SAVED = {}


async def _remember_user(user_id, tg_id, **kw):
    rec = SAVED.setdefault((user_id, tg_id), {"tg_id": tg_id})
    for k, v in kw.items():
        if k == "count_msg" or (k == "last_text" and v is None):
            continue
        if v is not None:
            rec[k] = v
    return rec


async def _nothing(*a, **kw):
    return None


async def _empty(*a, **kw):
    return []


for _name, _fn in (("remember_user", _remember_user), ("get_user_record", _nothing),
                   ("find_user_record", _nothing), ("search_directory", _empty),
                   ("list_directory", _empty), ("directory_count", _nothing),
                   ("prune_directory", _nothing), ("purge_group_rows", _nothing),
                   ("set_directory_link", _nothing), ("get_ai_profile", _nothing),
                   ("upsert_ai_profile", _nothing), ("get_features", _empty),
                   ("set_feature", _nothing), ("get_user_by_db_id", _nothing)):
    setattr(dbmod, _name, _fn)


# ═══════════ ماکت تلگرام ═══════════

def make_user(uid, first, last=None, username=None):
    return tl.User(id=uid, is_self=False, contact=False, mutual_contact=False,
                   deleted=False, bot=False, bot_chat_history=None, bot_nochats=None,
                   verified=False, restricted=False, min=False, bot_inline_geo=False,
                   support=False, scam=False, apply_min_photo=False, fake=False,
                   access_hash=uid * 7, first_name=first, last_name=last,
                   username=username, phone=None, status=tl.UserStatusRecently())


def make_channel(cid=1001, title="کانال خبری", username="news_ch",
                 broadcast=True, megagroup=False):
    return tl.Channel(id=cid, title=title, photo=None, date=None,
                      creator=None, left=False, broadcast=broadcast,
                      verified=True, megagroup=megagroup, restricted=False,
                      signatures=None, min=False, scam=False, has_link=None,
                      has_geo=None, slowmode_enabled=None, call_active=None,
                      call_not_empty=None, fake=False, gigagroup=None,
                      noforwards=None, join_to_send=None, join_request=None,
                      forum=None, stories_hidden=None, stories_hidden_min=None,
                      stories_unavailable=None, signature_profiles=None,
                      autotranslation=None, broadcast_messages_allowed=None,
                      monoforum=None, forum_tabs=None,
                      access_hash=cid * 3, username=username,
                      restriction_reason=None, admin_rights=None,
                      banned_rights=None, default_banned_rights=None,
                      participants_count=12500)


def make_basic_chat(cid=2002, title="گروه فامیل"):
    return tl.Chat(id=cid, title=title, photo=None, participants_count=42,
                   date=None, version=1, creator=None, left=False,
                   deactivated=False, call_active=None, call_not_empty=None,
                   noforwards=None, migrated_to=None, admin_rights=None,
                   default_banned_rights=None)


def make_channel_full(about="خبرهای مهم"):
    return tl.ChannelFull(id=1001, about=about, read_inbox_max_id=1,
                          read_outbox_max_id=1, unread_count=0, chat_photo=None,
                          notify_settings=None, bot_info=[], pts=1,
                          can_view_participants=True, can_set_username=None,
                          can_set_stickers=None, hidden_prehistory=None,
                          can_set_location=None, has_scheduled=None,
                          can_view_stats=None, blocked=False,
                          can_delete_channel=None, antispam=None,
                          participants_hidden=None, translations_disabled=None,
                          stories_pinned_available=None,
                          view_forum_as_messages=None,
                          restricted_sponsored=None, can_view_revenue=None,
                          paid_media_allowed=None, can_view_stars_revenue=None,
                          paid_reactions_available=None,
                          stargifts_available=None, participants_count=12500)


class FakeDialog:
    def __init__(self, entity):
        self.entity = entity


class FakeParticipants:
    def __init__(self, participants, users):
        self.participants = participants
        self.users = users


class FakeFullChat:
    def __init__(self, participants):
        self.full_chat = type("FC", (), {"about": "گروه خودمانی",
                                         "participants": participants})()
        self.users = []
        self.chats = []


class FakeClient:
    def __init__(self, dialogs=(), admin_creator=99, chat_creator=88,
                 hide_owner=False, invite_chat=None):
        self._dialogs = list(dialogs)
        self._admin_creator = admin_creator
        self._chat_creator = chat_creator
        self._hide_owner = hide_owner
        self._invite_chat = invite_chat
        self.calls = []
        self.handlers = []

    def add_event_handler(self, cb, ev):
        self.handlers.append((cb, ev))

    def remove_event_handler(self, cb, ev):
        pass

    async def get_me(self):
        return make_user(999, "من", username="me_acc")

    async def get_entity(self, target):
        if isinstance(target, str):
            for d in self._dialogs:
                if getattr(d.entity, "username", None) == target:
                    return d.entity
            raise ValueError("username not found")
        if isinstance(target, int):
            for d in self._dialogs:
                if d.entity.id == abs(target):
                    return d.entity
            raise ValueError("USER_ID_INVALID")
        return target

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
        self.calls.append(type(request).__name__)
        if isinstance(request, GetFullChannelRequest):
            return type("R", (), {"full_chat": make_channel_full(),
                                  "chats": [], "users": []})()
        if isinstance(request, GetFullChatRequest):
            parts = tl.ChatParticipants(
                chat_id=2002, participants=[
                    tl.ChatParticipant(user_id=5, inviter_id=None, date=None),
                    tl.ChatParticipantCreator(user_id=self._chat_creator),
                ], version=1)
            if self._hide_owner:
                return FakeFullChat(
                    tl.ChatParticipantsForbidden(chat_id=2002,
                                                 self_participant=None))
            return FakeFullChat(parts)
        if isinstance(request, GetParticipantsRequest):
            if self._hide_owner:
                raise ChatAdminRequiredError(request=request)
            admins = [tl.ChannelParticipant(user_id=7, date=None),
                      tl.ChannelParticipantCreator(
                          user_id=self._admin_creator,
                          admin_rights=tl.ChatAdminRights(
                              change_info=True, post_messages=None, edit_messages=None,
                              delete_messages=None, ban_users=None, invite_users=None,
                              pin_messages=None, add_admins=None, anonymous=None,
                              manage_call=None, other=None, manage_topics=None,
                              post_stories=None, edit_stories=None,
                              delete_stories=None))] if False else [
                tl.ChannelParticipantCreator(
                    user_id=self._admin_creator,
                    admin_rights=tl.ChatAdminRights(
                        change_info=True, post_messages=None, edit_messages=None,
                        delete_messages=None, ban_users=None, invite_users=None,
                        pin_messages=None, add_admins=None, anonymous=None,
                        manage_call=None, other=None, manage_topics=None,
                        post_stories=None, edit_stories=None,
                        delete_stories=None))]
            users = [make_user(self._admin_creator, "مدیر", "کل",
                               username="the_boss")]
            return FakeParticipants(admins, users)
        if isinstance(request, CheckChatInviteRequest):
            if self._invite_chat is None:
                return type("R", (), {"chat": None})()
            return type("R", (), {"chat": self._invite_chat})()
        return True


def make_plugin(client):
    plugin = UserInfoPlugin(client, user_id=1)
    plugin._owner_tg = None
    plugin._ensure_state()
    return plugin


# ═══════════ تست‌ها ═══════════

async def main():
    SAVED.clear()
    channel = make_channel()
    chat = make_basic_chat()
    client = FakeClient(dialogs=[FakeDialog(channel), FakeDialog(chat)])
    plugin = make_plugin(client)

    print("=" * 68)
    print("تست «مالک» — گروه/کانال + مالک")
    print("=" * 68)

    # ── ۱) پیدا کردن با یوزرنیم ──
    res = await plugin.chat_owner_card("@news_ch")
    check("کانال با یوزرنیم پیدا می‌شود", res.get("ok") is True,
          str(res.get("reason") or ""))
    text = res.get("text") or ""
    check("عنوان کانال در کارت می‌آید", "کانال خبری" in text)
    check("آیدی کانال نمایش داده می‌شود", "<code>1001</code>" in text, text[:60])
    check("تعداد اعضا نمایش داده می‌شود", "12,500" in text, text[:200])
    check("توضیحات (about) نمایش داده می‌شود", "خبرهای مهم" in text)
    check("نوع «کانال» مشخص است", "کانال" in text)
    check("برچسب تأییدشده (✅) می‌آید", "✅" in text)

    # ── ۲) مالک از فهرست مدیران ──
    check("مالک پیدا می‌شود", "مالک" in text and "99" in text, text[-220:])
    check("نام مالک در کارت است", "مدیر کل" in text)
    check("یوزرنیم مالک در کارت است", "@the_boss" in text)
    check("از فهرست مدیران پیدا شده", "مدیران" in text)

    rec = SAVED.get((1, 99))
    check("مالک در سابقه ثبت می‌شود (برای پیام بعدی)", bool(rec), str(SAVED))
    check("access_hash مالک ذخیره می‌شود",
          (rec or {}).get("access_hash") == 99 * 7, str((rec or {}).get("access_hash")))
    check("منبع سابقه «مالک گروه» است",
          (rec or {}).get("source") == "مالک گروه", str((rec or {}).get("source")))

    kb = res.get("kb")
    kb_s = str(kb)
    check("دکمه‌ی «پروفایل مالک» روی کارت هست", "u:card:99" in kb_s, kb_s[:130])
    check("دکمه‌ی پیام و پیش‌نویسِ مالک روی کارت هست",
          "u:msg:99" in kb_s and "u:draft:99" in kb_s, kb_s[:130])
    check("دکمه‌ی باز کردن کانال (لینک) هست", "t.me/news_ch" in kb_s, kb_s[:80])

    # ── ۳) پیدا کردن با آیدی عددی و اسم ──
    r2 = await plugin.chat_owner_card("1001")
    check("با آیدی عددی هم پیدا می‌شود", r2.get("ok") is True,
          str(r2.get("reason") or ""))
    r3 = await plugin.chat_owner_card("فامیل")
    check("با بخشی از اسم گروه پیدا می‌شود", r3.get("ok") is True,
          str(r3.get("reason") or ""))
    check("گروه معمولی: مالک از فهرست اعضا", "88" in (r3.get("text") or ""),
          (r3.get("text") or "")[-200:])
    r4 = await plugin.chat_owner_card("چیزی که نیست")
    check("هدف ناموجود → پیام روشن، بدون خطا",
          r4.get("ok") is False and bool(r4.get("reason")), str(r4.get("reason")))

    # ── ۴) مالک مخفی → خطا نمی‌دهد ──
    hidden = FakeClient(dialogs=[FakeDialog(make_channel())], hide_owner=True)
    p_hidden = make_plugin(hidden)
    r5 = await p_hidden.chat_owner_card("news_ch")
    check("مالک مخفی: کارت می‌آید ولی «مخفی» می‌گوید",
          r5.get("ok") is True and "مخفی" in (r5.get("text") or ""),
          (r5.get("text") or "")[-160:])

    # ── ۵) لینک دعوت ──
    inv_client = FakeClient(dialogs=[FakeDialog(make_channel())],
                            invite_chat=make_channel(cid=1001, title="کانال خبری",
                                                     username="news_ch"))
    p_inv = make_plugin(inv_client)
    r6 = await p_inv.chat_owner_card("https://t.me/+AbCdEfGh")
    check("لینک دعوت (عضو هستیم) → کارت می‌آید", r6.get("ok") is True,
          str(r6.get("reason") or ""))
    check("از مسیر «لینک دعوت» اعلام می‌شود", "لینک دعوت" in (r6.get("text") or ""))

    # ── ۶) منوی لیست گروه/کانال‌ها ──
    page = await plugin.member_page(0)
    check("لیست گروه/کانال ساخته می‌شود", bool(page.get("rows")), str(page)[:80])
    check("تعداد موارد در متن می‌آید", "مجموع 2" in page["text"], page["text"][:90])
    check("هر مورد دکمه دارد",
          page["rows"] and page["rows"][0][0] in (1001, 2002), str(page["rows"]))

    from bot.user_panel import member_kb
    kb2 = str(member_kb(page))
    check("دکمه‌ی هر چت به callback مالک وصل است", "u:mo:" in kb2, kb2[:100])
    check("ردیف صفحه‌بندی هست (حتی با یک صفحه)", "صفحه 1/1" in kb2, kb2[-90:])

    # کش: بار دوم نباید اسکن جدید بزند
    before = len(client.calls)
    await plugin.member_page(0)
    check("لیست کش می‌شود (اسکن تکراری نمی‌زند)", len(client.calls) == before,
          str(len(client.calls) - before))

    # ── ۷) دستور «.مالک» از مسیر واقعی ──
    sent = []

    async def fake_send(text, kb=None):
        sent.append(text)
        return True

    plugin._send_owner = fake_send
    plugin._owner_tg = None

    class CmdEvent:
        def __init__(self, text):
            self.raw_text = text
            self.deleted = False

        async def delete(self):
            self.deleted = True

    ev = CmdEvent(".مالک news_ch")
    await plugin._on_command(ev)
    check("دستور «.مالک ‹هدف›» بدون خطا اجرا می‌شود", ev.deleted is True)
    check("کارت مالک فرستاده می‌شود",
          sent and "مالک" in sent[-1] and "1001" in sent[-1],
          (sent[-1][:70] if sent else "—"))

    sent.clear()
    await plugin._on_command(CmdEvent(".مالک"))
    check("«.مالک» تنها، لیست گروه/کانال‌ها را می‌دهد",
          sent and "گروه‌ها و کانال‌های تو" in sent[-1],
          (sent[-1][:70] if sent else "—"))

    sent.clear()
    await plugin._on_command(CmdEvent(".مالک چیزی‌که‌نیست"))
    check("«.مالک» با هدف ناموجود، خطا نمی‌دهد و راهنما می‌دهد",
          sent and ("پیدا نشد" in sent[-1] or "لیست" in sent[-1]),
          (sent[-1][:70] if sent else "—"))

    # «.کاربر» هنوز کار می‌کند (رگرسیون مسیریابی)
    sent.clear()
    await plugin._on_command(CmdEvent(".کاربر"))
    check("دستور «.کاربر» بعد از تغییر الگو سالم است",
          sent and "کاربر با آیدی عددی" in sent[-1], (sent[-1][:60] if sent else "—"))

    print("\n" + "=" * 68)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
