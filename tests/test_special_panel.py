"""
تست ⭐ «ویژگی‌های ویژه» — منوی پنل + شناسنامه (کانال/گروه) + دامپ + لیسن.

چه چیزی را تضمین می‌کند:
  ۱) منوی اصلی دکمه‌ی «⭐ ویژگی‌های ویژه» دارد و مسیرش درست است.
  ۲) منوی شناسنامه: دکمه‌های «📢 کانال‌ها»، «👥 گروه‌ها»، «🔙 بازگشت».
  ۳) لیست‌ها فقط کانال‌ها/گروه‌های خودِ کاربر را می‌آورند، با صفحه‌بندی
     (همان منبع و همان تجربه‌ی لیست دامپ).
  ۴) انتخاب هر چت → کارت اطلاعات + مالک (بدون درخواست اضافه‌ی پیدا کردن چت).
  ۵) دکمه‌ی «📥 دامپ این چت» → کاربر به همان جریان فوروارد وصل می‌شود
     (مبدأ از قبل انتخاب‌شده، مقصد انتخاب بعدی).
  ۶) دکمه‌ی «👂 لیسن (مانیتور)» → مسیر مانیتور واقعی در دیتابیس ساخته می‌شود
     و پلاگین در همان لحظه روشن/به‌روز می‌شود.
  ۷) لبه‌ها: اکانت قطع، لیست خالی، ایندکس کهنه، دکمه‌ی بی‌خاصیت.

اجرا:
    PYTHONPATH=. python tests/test_special_panel.py
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

from telethon.tl import types as tl                             # noqa: E402

import bot.special as SP                                        # noqa: E402
import core.forwarder as FW                                     # noqa: E402
import database.db as dbmod                                     # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


# ═══════════ ماکت تلگرام ═══════════

def make_channel(cid, title, username=None, megagroup=False):
    return tl.Channel(id=cid, title=title, photo=None, date=None, creator=None,
                      left=False, broadcast=not megagroup, verified=False,
                      megagroup=megagroup, restricted=False, signatures=None,
                      min=False, scam=False, has_link=None, has_geo=None,
                      slowmode_enabled=None, call_active=None, call_not_empty=None,
                      fake=False, gigagroup=None, noforwards=None, join_to_send=None,
                      join_request=None, forum=None, stories_hidden=None,
                      stories_hidden_min=None, stories_unavailable=None,
                      signature_profiles=None, autotranslation=None,
                      broadcast_messages_allowed=None, monoforum=None,
                      forum_tabs=None, access_hash=cid * 3, username=username,
                      restriction_reason=None, admin_rights=None,
                      banned_rights=None, default_banned_rights=None,
                      participants_count=100)


def make_user(uid, first, username=None):
    return tl.User(id=uid, is_self=False, contact=False, mutual_contact=False,
                   deleted=False, bot=False, bot_chat_history=None, bot_nochats=None,
                   verified=False, restricted=False, min=False, bot_inline_geo=False,
                   support=False, scam=False, apply_min_photo=False, fake=False,
                   access_hash=uid * 7, first_name=first, last_name=None,
                   username=username, phone=None, status=tl.UserStatusRecently())


class FakeDialog:
    def __init__(self, entity, name=None):
        self.entity = entity
        self.name = name or getattr(entity, "title", None) \
            or getattr(entity, "first_name", "")


class FakeClient:
    def __init__(self, dialogs=()):
        self._dialogs = list(dialogs)
        self.calls = []

    async def get_me(self):
        return make_user(999, "من", username="me_acc")

    def iter_dialogs(self, limit=None, ignore_migrated=False):
        client = self

        class _Iter:
            def __aiter__(self_inner):
                async def gen():
                    for d in client._dialogs[:limit or len(client._dialogs)]:
                        yield d
                return gen()
        return _Iter()

    async def get_entity(self, target):
        return self._dialogs[0].entity


# ═══════════ ماکت پنل تلگرام ═══════════

class FakeMsg:
    def __init__(self):
        self.chat_id = 42
        self.message_id = 777

    async def delete(self):
        return None

    async def reply_text(self, text, **kw):
        return None


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edits = []
        self.answers = []
        self.message = FakeMsg()

    async def answer(self, text=None, show_alert=False, **kw):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None, **kw):
        self.edits.append((text, reply_markup))


class FakeUpdate:
    def __init__(self, data):
        self.callback_query = FakeQuery(data)
        self.effective_chat = type("C", (), {"id": 42})()
        self.effective_user = type("U", (), {"id": 42})()


class FakeContext:
    def __init__(self):
        self.user_data = {}


# ═══════════ دیتابیس جعلی ═══════════

SAVED = {"routes": [], "features": {}}


async def _get_user_record(user_id, tg_id):
    return None


async def _nothing(*a, **kw):
    return None


async def _empty(*a, **kw):
    return []


async def _set_channel_route(user_id, source_channel_id, source_title,
                             dest_type, dest_id, dest_title="", filter_type="all"):
    SAVED["routes"].append({
        "user_id": user_id, "src": source_channel_id, "src_title": source_title,
        "dst": dest_id, "dst_title": dest_title,
    })


async def _set_feature(user_id, feature_name, is_enabled, config_json=None):
    SAVED["features"][feature_name] = is_enabled


async def _get_storage_target(user_id, feature):
    return SAVED.get("target")


for _name, _fn in (("get_user_record", _get_user_record),
                   ("remember_user", _nothing),
                   ("find_user_record", _nothing),
                   ("search_directory", _empty), ("list_directory", _empty),
                   ("directory_count", _nothing), ("prune_directory", _nothing),
                   ("purge_group_rows", _nothing), ("set_directory_link", _nothing),
                   ("get_ai_profile", _nothing), ("upsert_ai_profile", _nothing),
                   ("get_features", _empty), ("set_feature", _set_feature),
                   ("set_channel_route", _set_channel_route),
                   ("get_storage_target", _get_storage_target),
                   ("get_user_by_db_id", _nothing)):
    setattr(dbmod, _name, _fn)


# ═══════════ ماکت پلاگین کاربر (کارت مالک) ═══════════

class FakeUserInfo:
    def __init__(self):
        self.calls = 0

    async def chat_owner_from_entity(self, ent, how="پنل"):
        self.calls += 1
        title = getattr(ent, "title", "?")
        return {"ok": True, "owner_id": 555,
                "text": (f"🏷 <b>{title}</b>\n\n🆔 آیدی: "
                         f"<code>{getattr(ent, 'id', 0)}</code>\n\n"
                         "👑 <b>مالک:</b> مدیر کل\n    🆔 <code>555</code>")}


class FakeMonitor:
    def __init__(self):
        self.reloads = 0

    async def reload_routes(self):
        self.reloads += 1


# ═══════════ ابزار ═══════════

async def _client(user_id, client):
    return client


async def main():
    print("=" * 68)
    print("تست ⭐ ویژگی‌های ویژه — شناسنامه/دامپ/لیسن")
    print("=" * 68)

    ch1 = make_channel(101, "کانال خبری", username="news_ch")
    ch2 = make_channel(102, "کانال ورزشی", username="sport_ch")
    gr1 = make_channel(201, "گروه فامیل", megagroup=True)
    gr2 = make_channel(202, "گروه کار", megagroup=True)
    usr = make_user(301, "علی", username="ali")
    client = FakeClient([FakeDialog(ch1), FakeDialog(ch2), FakeDialog(gr1),
                         FakeDialog(gr2), FakeDialog(usr, "علی")])

    # پلاگین‌ها
    import core.plugin_manager as pm
    import core.client_manager as cm

    fake_ui = FakeUserInfo()
    fake_mon = FakeMonitor()
    pm._active_plugins[1] = {"user_info": fake_ui}

    # ⚠️ core.forwarder نام get_client را مستقیم import کرده؛ پس همان‌جا هم
    # باید جایگزین شود، وگرنه «account_not_connected» می‌گیریم.
    async def _get_client(uid):
        return client

    cm.get_client = _get_client
    FW.get_client = _get_client

    # enable_plugin ماکت
    async def _enable_plugin(uid, name, cl):
        pm._active_plugins[1]["channel_monitor"] = fake_mon
        return True

    pm.enable_plugin = _enable_plugin

    # get_or_create_user جعلی: پنل واقعی این را از bot.handlers می‌گیرد
    import bot.handlers as H

    async def _get_or_create(update):
        return {"id": 1, "telegram_id": 42}

    H.get_or_create_user = _get_or_create

    # کش چت‌ها را پر کن (مثل کاری که پنل می‌کند)
    items = await FW.load_dialogs(1, force=True)
    check("لیست چت‌ها ساخته می‌شود", len(items) == 5, str(len(items)))
    check("نوع‌ها درست تشخیص داده می‌شوند",
          [i["kind"] for i in items] == ["channel", "channel", "group", "group", "user"],
          str([i["kind"] for i in items]))

    # ── ۱) منوی اصلی ──
    from bot.keyboards import main_menu_kb
    kb_main = str(main_menu_kb(has_account=True, is_admin=False))
    flat = kb_main.replace("\u200c", "")          # نیم‌فاصله‌ها را نادیده بگیر
    check("دکمه‌ی «⭐ ویژگی‌های ویژه» در منوی اصلی هست",
          "sp:menu" in kb_main and "ویژگی" in flat, kb_main[:120])

    # ── ۲) خانه‌ی ویژگی‌های ویژه ──
    ctx = FakeContext()
    u = FakeUpdate("sp:menu")
    await SP.route_special_callbacks(u, ctx)
    text, kb = u.callback_query.edits[-1]
    check("منوی ویژگی‌های ویژه باز می‌شود", "ویژگی‌های ویژه" in text, text[:60])
    check("دکمه‌ی «🏷 شناسنامه» دارد", "sp:own" in str(kb), str(kb)[:100])
    check("دکمه‌ی بازگشت دارد", "back_main" in str(kb))

    # ── ۳) منوی شناسنامه: کانال‌ها / گروه‌ها / بازگشت ──
    u2 = FakeUpdate("sp:own")
    await SP.route_special_callbacks(u2, ctx)
    text2, kb2 = u2.callback_query.edits[-1]
    kbs = str(kb2)
    check("دکمه‌ی «📢 کانال‌ها» هست", "sp:l:ch:0" in kbs, kbs[:120])
    check("دکمه‌ی «👥 گروه‌ها» هست", "sp:l:gr:0" in kbs, kbs[:120])
    check("دکمه‌ی «🔙 بازگشت» هست", "sp:menu" in kbs, kbs[:160])

    # ── ۴) لیست کانال‌ها ──
    u3 = FakeUpdate("sp:l:ch:0")
    await SP.route_special_callbacks(u3, ctx)
    text3, kb3 = u3.callback_query.edits[-1]
    kb3s = str(kb3)
    check("لیست کانال‌ها ساخته می‌شود", "کانال‌ها" in text3, text3[:60])
    check("فقط کانال‌ها می‌آیند (گروه/کاربر نه)",
          "کانال خبری" in kb3s and "کانال ورزشی" in kb3s
          and "فامیل" not in kb3s and "علی" not in kb3s, kb3s[:200])
    check("شمارش کل درست است", "مجموع 2" in text3, text3[:80])
    check("دکمه‌ی رفتن به گروه‌ها هم هست", "sp:l:gr:0" in kb3s, kb3s[:200])
    check("صفحه‌بندی نمایش داده می‌شود", "1/1" in kb3s, kb3s[-160:])

    # ── ۵) لیست گروه‌ها ──
    u4 = FakeUpdate("sp:l:gr:0")
    await SP.route_special_callbacks(u4, ctx)
    kb4s = str(u4.callback_query.edits[-1][1])
    check("لیست گروه‌ها فقط گروه‌ها را می‌آورد",
          "فامیل" in kb4s and "کار" in kb4s and "خبری" not in kb4s, kb4s[:200])

    # ── ۶) انتخاب یک چت → کارت + مالک ──
    idx_ch1 = 0            # اولین چت لیست همان کانال خبری است
    u5 = FakeUpdate(f"sp:p:{idx_ch1}")
    await SP.route_special_callbacks(u5, ctx)
    text5, kb5 = u5.callback_query.edits[-1]
    kb5s = str(kb5)
    check("کارت اطلاعات کانال می‌آید", "کانال خبری" in text5 and "101" in text5,
          text5[:80])
    check("مالک در کارت هست", "مدیر کل" in text5 and "555" in text5, text5[-90:])
    check("از entity استفاده شد (بدون درخواست اضافه)", fake_ui.calls == 1,
          str(fake_ui.calls))
    check("دکمه‌ی «📥 دامپ این چت» روی کارت هست",
          f"sp:f:{idx_ch1}" in kb5s, kb5s[:140])
    check("دکمه‌ی «👂 لیسن (مانیتور)» روی کارت هست",
          f"sp:m:{idx_ch1}" in kb5s, kb5s[:160])
    check("دکمه‌ی «👤 پروفایل مالک» هست", "u:card:555" in kb5s, kb5s[:200])
    check("دکمه‌ی باز کردن (یوزرنیم) هست", "t.me/news_ch" in kb5s, kb5s[:200])
    check("بازگشت به لیست همان صفحه است", "sp:l:ch:0" in kb5s, kb5s[-120:])

    # ── ۷) دکمه‌ی دامپ → وصل شدن به جریان فوروارد ──
    rendered = {"called": 0, "target": None}

    async def fake_render(context, user, target, force=False):
        rendered["called"] += 1
        rendered["target"] = target

    import bot.forward as FWD
    FWD._render_dialogs = fake_render

    u6 = FakeUpdate(f"sp:f:{idx_ch1}")
    await SP.route_special_callbacks(u6, ctx)
    check("دامپ کاربر را به انتخاب مقصد می‌برد",
          rendered["called"] == 1 and rendered["target"] == "dst", str(rendered))
    fst = ctx.user_data.get("fwd") or {}
    src = fst.get("src") or {}
    check("مبدأ از قبل انتخاب شده (همان چت)", src.get("name") == "کانال خبری",
          str(src.get("name")))
    check("entity مبدأ هم پاس داده شده (بدون resolve مجدد)",
          src.get("entity") is ch1, type(src.get("entity")).__name__)
    check("chat_id/msg_id برای ویرایش پیام ست شده",
          fst.get("chat_id") == 42 and fst.get("msg_id") == 777, str(fst)[:80])

    # ── ۸) لیسن: انتخاب مقصد ──
    SAVED.pop("target", None)
    u7 = FakeUpdate(f"sp:m:{idx_ch1}")
    await SP.route_special_callbacks(u7, ctx)
    text7, kb7 = u7.callback_query.edits[-1]
    kb7s = str(kb7)
    check("منوی لیسن نام چت را نشان می‌دهد", "کانال خبری" in text7, text7[:80])
    check("گزینه‌ی Saved Messages هست", f"sp:mg:{idx_ch1}:saved" in kb7s, kb7s[:140])
    check("اگر مقصد ذخیره‌سازی تنظیم نشده باشد، گزینه‌اش نمی‌آید",
          "target" not in kb7s, kb7s[:160])

    # ── ۹) لیسن: ساخت مسیر ──
    SAVED["routes"].clear()
    u8 = FakeUpdate(f"sp:mg:{idx_ch1}:saved")
    await SP.route_special_callbacks(u8, ctx)
    text8, _ = u8.callback_query.edits[-1]
    check("مسیر مانیتور در دیتابیس ساخته می‌شود", len(SAVED["routes"]) == 1,
          str(SAVED["routes"]))
    route = SAVED["routes"][0] if SAVED["routes"] else {}
    check("منبع مسیر همان کانال است", route.get("src_title") == "کانال خبری",
          str(route))
    check("مقصد پیش‌فرض Saved Messages است", route.get("dst_title") == "Saved Messages",
          str(route.get("dst_title")))
    check("پلاگین مانیتور در لحظه روشن می‌شود",
          SAVED["features"].get("channel_monitor") is True, str(SAVED["features"]))
    check("مسیرهای پلاگین رفرش می‌شوند", fake_mon.reloads >= 1,
          str(fake_mon.reloads))
    check("پیام موفقیت به کاربر نشان داده می‌شود",
          "لیسن روشن شد" in text8 and "کانال خبری" in text8, text8[:120])

    # ── ۱۰) مقصد ذخیره‌سازی تنظیم شده ──
    SAVED["target"] = {"target_id": -100777, "target_title": "کانال آرشیو"}
    SAVED["routes"].clear()
    u9 = FakeUpdate(f"sp:mg:{idx_ch1}:target")
    await SP.route_special_callbacks(u9, ctx)
    route2 = SAVED["routes"][0] if SAVED["routes"] else {}
    check("گزینه‌ی «مقصد ذخیره‌سازی» به همان مقصد وصل می‌شود",
          route2.get("dst") == -100777 and route2.get("dst_title") == "کانال آرشیو",
          str(route2))
    SAVED.pop("target", None)

    # ── ۱۱) لبه‌ها ──
    u10 = FakeUpdate("sp:p:999")          # ایندکس کهنه
    await SP.route_special_callbacks(u10, ctx)
    check("ایندکس کهنه → پیام روشن، بدون خطا",
          any(a and "قدیمی" in str(a) for a in u10.callback_query.answers),
          str(u10.callback_query.answers))
    check("و کارتی هم ساخته نمی‌شود", not u10.callback_query.edits)

    u11 = FakeUpdate("sp:noop")           # دکمه‌ی بی‌خاصیت
    await SP.route_special_callbacks(u11, ctx)
    check("دکمه‌ی بی‌خاصیت بدون خطا مدیریت می‌شود", True)

    # اکانت قطع
    async def _no_client(uid):
        return None

    cm.get_client = _no_client
    FW.get_client = _no_client
    FW.clear_cache(1)
    u12 = FakeUpdate("sp:l:ch:0")
    await SP.route_special_callbacks(u12, ctx)
    check("اکانت قطع → پیام روشن بدون خطا",
          bool(u12.callback_query.edits)
          and "اتصال" in u12.callback_query.edits[-1][0],
          str(u12.callback_query.edits[-1][0])[:70] if u12.callback_query.edits else "—")

    # لیست خالی (اکانت وصل، ولی هیچ کانال/گروهی نیست)
    empty_client = FakeClient([FakeDialog(usr, "علی")])

    async def _empty_client(uid):
        return empty_client

    cm.get_client = _empty_client
    FW.get_client = _empty_client
    FW.clear_cache(1)
    u13 = FakeUpdate("sp:l:ch:0")
    await SP.route_special_callbacks(u13, ctx)
    check("وقتی کانالی نیست، پیام «چیزی پیدا نشد» می‌آید",
          "چیزی پیدا نشد" in u13.callback_query.edits[-1][0],
          u13.callback_query.edits[-1][0][:70])

    print("\n" + "=" * 68)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


async def _async_ret(value):
    return value


sys.exit(asyncio.run(main()))
