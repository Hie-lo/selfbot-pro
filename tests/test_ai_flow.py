"""
تست جریان کامل پاسخ هوشمند (AI) با پلاگین و دیتابیس واقعی.

اجرا:
    PYTHONPATH=. python tests/test_ai_flow.py

نیاز: دیتابیس Postgres مطابق .env (در محیط تست، همان .env پروژه کافی است).
اگر دیتابیس در دسترس نباشد، تست‌ها با پیام «پرش» رد می‌شوند.
"""

import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_sim as sim                                    # noqa: E402
from ai_sim import GROUP, ME, PEER, PV, SENT                   # noqa: E402

from core import ai_providers                           # noqa: E402
from core import plugin_manager as pm                   # noqa: E402
from database import db                                 # noqa: E402
from plugins import ai_engine as E                      # noqa: E402
from plugins.ai_reply import DRAFTS                     # noqa: E402

CALLS = []
RESULTS = []


def check(name: str, ok: bool, extra: str = ""):
    RESULTS.append((name, ok))
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


def reset_limits(uid):
    """سقف مصرف و «فعال بودن خودم» را صفر می‌کند تا پیام بعدی قطعاً پردازش شود"""
    from core import plugin_manager as _pm
    # دشمن PEER را هم پاک کن (بخش همزیستی با دشمن آن را ثبت کرده بود و از
    # این‌جا به بعد جلوی پاسخ‌ها را می‌گرفت)
    other = _pm.get_active_plugins(uid).get("auto_response")
    if other is not None:
        getattr(other, "_enemies", {}).pop(PEER, None)
    ar = _pm.get_active_plugins(uid).get("ai_reply")
    if ar:
        ar._usage.clear()
        ar._usage_day.clear()
        ar._last_activity.clear()
        ar._paused.discard(PEER)
    return ar


def fake_chat(reply="سلام خوبم، تو چطوری؟", fail=False,
              summary='{"summary":"درباره فوتبال حرف زدیم","facts":["فوتبال دوست داره"]}'):
    async def _chat(messages, **kw):
        CALLS.append(messages)
        if "خلاصه‌ساز" in messages[0]["content"]:
            return summary, "fake"
        if fail:
            return None, None
        return reply, "fake-provider"
    return _chat


async def main():
    sim.install()
    logging.basicConfig(level=logging.CRITICAL)
    cap = sim.LogCapture()
    logging.getLogger("telethon.client.updates").addHandler(cap)

    try:
        uid, client = await sim.setup_user()
    except Exception as e:
        print(f"⚠️ پرش تست‌های AI — دیتابیس در دسترس نیست: {type(e).__name__}: {e}")
        return

    await db.set_feature(uid, "ai_reply", True, {
        "persona": "من کم‌حرفم، کوتاه جواب می‌دم، شکسته می‌نویسم.",
        "mode": "playfull", "tone_level": 2, "draft_only": True,
        "quiet_hours": "", "rpm": 50, "rph": 100, "rpd": 500,
        "raw_limit": 30, "keep_after_compact": 10,
    })
    await pm.load_plugins_for_user(uid, client)
    AI = pm.get_active_plugins(uid).get("ai_reply")
    check("پلاگین ai_reply لود شد", AI is not None)
    GENUINE_CHAT = ai_providers.chat          # مرجع تابع واقعی (تست‌ها جعل می‌کنند)
    GENUINE_CALL = ai_providers._call_provider

    ai_providers.chat = fake_chat()

    # ── ۱) فعال‌سازی در چت ──
    n, p = len(SENT), len(PV)
    await sim.fire(client, ".ai روشن", out=True)
    await asyncio.sleep(0.3)
    prof = await db.get_ai_profile(uid, PEER)
    check("‏.ai روشن → پروفایل ساخته شد", bool(prof and prof.get("enabled")))
    check("پیام راهنما به پیوی ربات رفت", any("روشن شد" in t for _, t in PV[p:]))
    check("در همان چت چیزی فرستاده نشد",
          not [s for s in SENT[n:] if s[0] == "send" and s[1] != "me"])

    # ── ۲) جایگاه شخص ──
    await sim.fire(client, ".ai شخص رفیق نزدیک", out=True)
    await asyncio.sleep(0.2)
    prof = await db.get_ai_profile(uid, PEER)
    check("جایگاه ذخیره شد (رفیق نزدیک)", prof.get("relationship") == "close_friend")

    # ── ۳) مود و سطح ──
    await sim.fire(client, ".تنظیم مود playfull", out=True)
    await asyncio.sleep(0.2)
    await sim.fire(client, ".تنظیم سطح 3", out=True)
    await asyncio.sleep(0.2)
    from bot.ai_panel import _ai_config, _save_ai_config
    cfg = await _ai_config(uid)
    check("مود playfull ذخیره شد", cfg.get("mode") == "playfull")
    prof3 = await db.get_ai_profile(uid, PEER)
    check("سطح ۳ برای همین چت ذخیره شد", int(prof3.get("tone_level", -1)) == 3)

    # ── ۴) پیام ورودی → پیش‌نویس ──
    CALLS.clear(); p = len(PV); n = len(SENT)
    await sim.fire(client, "سلام، خوبی؟", out=False)
    await asyncio.sleep(1.0)
    check("پیش‌نویس به پیوی ربات آمد", any("پیشنهاد پاسخ" in t for _, t in PV[p:]))
    check("در چت جواب خودکار نرفت",
          not [s for s in SENT[n:] if s[0] == "send" and s[1] != "me"])
    from core import runtime as _rt
    check("دکمه‌های پیش‌نویس ساخته شدند",
          "reply_markup" in (_rt.bot.last_kwargs or {}))
    check("پیش‌نویس در رجیستری هست", len(DRAFTS) == 1)

    prompt = CALLS[-1][0]["content"]
    check("پرسونا در پرامپت هست", "کم‌حرفم" in prompt)
    check("جایگاه در پرامپت هست", "رفیق نزدیک" in prompt)
    check("سطح آزادی در پرامپت هست", "بی‌سقف" in prompt)
    check("مود در پرامپت هست", "playfull" in prompt)
    check("قاعده‌ی «ربات نیستم» در پرامپت هست", "ربات" in prompt and "هرگز نگو" in prompt)

    # ── ۵) حافظه ──
    counts = await db.count_ai_memory(uid, PEER)
    check("پیام ورودی در حافظه ثبت شد", counts["msg"] >= 1, str(counts))

    # ── ۶) هوش مصنوعی چیزی که یادش نیست را تایید نمی‌کند ──
    CALLS.clear()
    await sim.fire(client, "یادت هست اون روز راجع‌به پروژه چی گفتی؟", out=False)
    await asyncio.sleep(1.0)
    check("هشدار «حافظه‌ای ندارم» به پرامپت اضافه شد",
          "هیچ نشانی از آن نیست" in CALLS[-1][0]["content"])
    check("قاعده‌ی تایید نکردن در پرامپت هست", "تایید نکن" in CALLS[-1][0]["content"])

    # ── ۷) ادعای طرف مقابل → pending، نه فکت ──
    await db.add_ai_fact(uid, PEER, "ادعا: قول پول داده", status="pending")
    CALLS.clear()
    await sim.fire(client, "خب پس کی پول رو میدی؟", out=False)
    await asyncio.sleep(1.0)
    check("ادعای تاییدنشده با برچسب «ادعا» به مدل داده شد",
          "ادعای او" in CALLS[-1][0]["content"])
    approved = await db.get_ai_facts(uid, PEER)
    check("ادعا در لیست فکت‌های تأییدشده نیست",
          all("قول پول" not in f["content"] for f in approved))

    # ── ۸) هیچ‌وقت بی‌جواب نمی‌ماند — ولی بدون اجازه هم چیزی نمی‌فرستد ──
    # «پاسخ جانشین» پیش‌فرض خاموش است: کاربر گفت جایگزین بدون اجازه ممنوع.
    await _save_ai_config(uid, {"fallback_replies": False})
    ar8 = pm.get_active_plugins(uid)["ai_reply"]
    await ar8._load_config()
    ar8._pending_retry.clear()
    ai_providers.chat = fake_chat(fail=True)
    p = len(PV)
    n_before8 = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "کجایی؟", out=False, mid=9110)
    await asyncio.sleep(1.2)
    drafts = [t for _, t in PV[p:] if "پیشنهاد پاسخ" in t]
    check("با شکست همه‌ی سرویس‌ها، پاسخ جانشین خودکار ساخته نمی‌شود", not drafts)
    check("و به طرف مقابل هم چیزی فرستاده نمی‌شود",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) == n_before8)
    check("به من هشدار داده شد که سرویس خطا داد",
          any("هیچ سرویس AI جواب نداد" in t for _, t in PV[p:]))

    # اگر خودم «پاسخ جانشین» را روشن کنم، آن وقت می‌رود
    await _save_ai_config(uid, {"fallback_replies": True})
    await ar8._load_config()
    ai_providers.chat = fake_chat(fail=True)
    p = len(PV)
    await sim.fire(client, "بازم کجایی؟", out=False, mid=9111)
    await asyncio.sleep(1.5)
    drafts_on = [t for _, t in PV[p:] if "پیشنهاد پاسخ" in t]
    check("با روشن کردن «پاسخ جانشین»، به‌عنوان پیشنهاد نشان داده می‌شود",
          bool(drafts_on), str(drafts_on)[:80])
    await _save_ai_config(uid, {"fallback_replies": False})
    await ar8._load_config()
    ar8._pending_retry.clear()

    # ── ۹) حالت خودکار: ارسال به چت با تایپینگ ──
    ai_providers.chat = fake_chat(reply="دارم میام\nآماده شو")
    await db.set_ai_auto_mode(uid, PEER, True)
    cfg = await _ai_config(uid)
    cfg["draft_only"] = False
    await db.set_feature(uid, "ai_reply", True, cfg)
    await pm.unload_all_for_user(uid)
    await pm.load_plugins_for_user(uid, client)
    await db.set_ai_auto_mode(uid, PEER, True)

    n = len(SENT)
    await sim.fire(client, "دیر نکنی", out=False)
    await asyncio.sleep(3.0)
    sent = [s[2] for s in SENT[n:] if s[0] == "send" and s[1] != "me"]
    typing = [s for s in SENT[n:] if s[0] == "action"]
    check("حالت خودکار در چت پاسخ داد", len(sent) >= 1, str(sent))
    check("پاسخ به پیام‌های کوتاه شکسته شد", len(sent) >= 2, str(sent))
    check("تایپینگ قبل از ارسال انجام شد", any(a[2] == "typing" for a in typing))
    check("پاسخ خودکار در حافظه ثبت شد",
          (await db.count_ai_memory(uid, PEER))["msg"] >= 3)

    # ── ۱۰) سبک‌سازی حافظه (پیام خام → خلاصه + فکت) ──
    ai_providers.chat = fake_chat()
    for i in range(40):
        await db.add_ai_message(uid, PEER, f"پیام تست {i}", is_out=(i % 2 == 0))
    before = await db.count_ai_memory(uid, PEER)
    p = len(PV)
    await sim.fire(client, ".ai سبک‌سازی", out=True)
    await asyncio.sleep(1.5)
    after = await db.count_ai_memory(uid, PEER)
    check("سبک‌سازی پیام‌های خام را کم کرد", after["msg"] < before["msg"],
          f"{before['msg']} → {after['msg']}")
    check("خلاصه ساخته شد", after["note"] >= 1)
    check("فکت‌ها استخراج شدند", after["fact"] >= 1)
    check("به من اطلاع داد که سبک‌سازی شد",
          any("سبک‌سازی" in t for _, t in PV[p:]))

    # ── ۱۱) جست‌وجو در حافظه ──
    p = len(PV)
    await sim.fire(client, ".ai سرچ فوتبال", out=True)
    await asyncio.sleep(0.4)
    check("جست‌وجو نتیجه داد", any("نتیجه" in t for _, t in PV[p:]))

    # ── ۱۲) کلمه‌ی توقف ──
    p = len(PV)
    await sim.fire(client, "#ساکت", out=False)
    await asyncio.sleep(0.2)
    await sim.fire(client, "هستی؟", out=False)
    await asyncio.sleep(0.8)
    check("کلمه‌ی توقف جواب نمی‌دهد",
          not [t for _, t in PV[p:] if "پیشنهاد" in t])

    # ── ۱۳) همزیستی با «دشمن» ──
    await sim.fire(client, ".روشن دشمن", out=True)
    await asyncio.sleep(0.3)
    ar = pm.get_active_plugins(uid).get("auto_response")
    if ar:
        ar._enemies[PEER] = {"name": "Ali", "responses": ["x"]}
    await sim.fire(client, ".ai روشن", out=True)      # از حالت ساکت در بیاید
    await asyncio.sleep(0.3)
    p = len(PV)
    await sim.fire(client, "علی سلام", out=False)
    await asyncio.sleep(1.0)
    check("برای «دشمن» AI مداخله نمی‌کند",
          not [t for _, t in PV[p:] if "پیشنهاد پاسخ" in t])

    # ── ۱۴) چند سرویسی: بارگذاری از تنظیمات ──
    providers = ai_providers.load_providers({
        "providers": [
            {"name": "a", "kind": "openai", "base_url": "https://x/v1",
             "model": "m1", "keys": ["k1", "k2"], "priority": 1},
            {"name": "b", "kind": "gemini", "model": "gemini-1.5-flash",
             "keys": ["k3"], "priority": 0},
        ]
    })
    check("چند سرویس بارگذاری شد", len(providers) == 2)
    check("مرتب‌سازی بر اساس اولویت", providers[0].name == "b")
    check("چرخش کلیدها کار می‌کند", len(providers[1].available_keys()) == 2)
    p1 = providers[1]
    p1.cooldown(p1.keys[0], seconds=60)
    check("کلید خطادار از چرخه خارج شد", len(p1.available_keys()) == 1)

    # ── ۱۵) پنل پیوی ربات: پرسونا ──
    from bot import ai_panel
    from bot.ai_panel import (ai_menu_kb, build_emoji_text, build_modes_text,
                             contacts_kb, emoji_kb, modes_kb, tones_kb)
    check("کیبورد مودها ساخته می‌شود", "ai:mode:playfull" in str(modes_kb("playfull")))
    check("کیبورد سطح‌ها ساخته می‌شود", "ai:tone:3" in str(tones_kb(3)))
    check("کیبورد مخاطبین ساخته می‌شود", "ai:chat:" in str(contacts_kb(
        await db.list_ai_chats(uid))))
    cfg = await _ai_config(uid)
    check("پرسونا در تنظیمات ذخیره است", "کم‌حرفم" in (cfg.get("persona") or ""))

    # ── ۱۵.۵) ویرایش فکت (باید کامل قابل ویرایش باشد) ──
    await db.add_ai_fact(uid, PEER, "فکت اولیه برای ویرایش")
    facts_list = await db.get_ai_facts(uid, PEER, limit=50)
    target_fact = next(f for f in facts_list if "ویرایش" in f["content"])
    ok = await db.update_ai_memory(target_fact["id"], uid, content="فکت ویرایش‌شده")
    edited = await db.get_ai_facts(uid, PEER, limit=50)
    check("فکت قابل ویرایش است", ok and any(f["content"] == "فکت ویرایش‌شده" for f in edited))
    await db.update_ai_memory(target_fact["id"], uid, pinned=True)
    pinned = await db.get_ai_facts(uid, PEER, limit=50)
    check("فکت قابل سنجاق‌کردن است",
          any(f["content"] == "فکت ویرایش‌شده" and f["pinned"] for f in pinned))
    await db.delete_ai_memory(uid, PEER, mem_id=target_fact["id"])
    after_del = await db.get_ai_facts(uid, PEER, limit=50)
    check("فکت قابل حذف است",
          not any(f["content"] == "فکت ویرایش‌شده" for f in after_del))
    from bot.ai_panel import _esc as _panel_esc   # اطمینان از ایمن بودن متن‌ها
    check("متن فکت‌ها ایمن (escape) می‌شود", "&lt;" in _panel_esc("<b>"))

    # ── ۱۵.۷) عیب‌یابی سرویس‌ها (پنل) ──
    from bot.ai_panel import build_diag_text
    good = build_diag_text({"providers": [{
        "name": "سالم", "kind": "openai", "base_url": "https://api.example.com/v1",
        "model": "m", "keys": ["sk-abcdef123456"]}]})
    check("عیب‌یابی آدرس سالم را ✅ نشان می‌دهد",
          "api.example.com" in good and "✅" in good)
    check("عیب‌یابی کلید را ماسک می‌کند", "sk-abc" in good and "sk-abcdef123456" not in good)
    check("عیب‌یابی راهنمای تست سرور را دارد", "ai_check.py" in good)

    bad_cfg = {"providers": [{
        "name": "خراب", "kind": "openai",
        "base_url": "openrouter.ai/api/v1", "model": "m", "keys": ["k"]}]}
    providers_bad = ai_providers.load_providers(bad_cfg)
    ai_providers.set_last_error("خراب", "HTTP 401: bad key")
    bad_txt = build_diag_text(bad_cfg)
    check("عیب‌یابی آدرس نامعتبر را ❌ نشان می‌دهد", "نامعتبر" in bad_txt, bad_txt[:120])
    check("عیب‌یابی آخرین خطا را نشان می‌دهد", "401" in bad_txt, bad_txt[:200])
    check("عیب‌یابی بدون سرویس، راهنمای .env می‌دهد",
          "AI_BASE_URL" in build_diag_text({"providers": []}))

    # ── ۱۵.۸) ایموجی: سطح‌بندی + محدود کردن به ایموجی‌های خودم ──
    check("سطح ایموجی: عدد و کلمه قبول است",
          [E.norm_emoji_level(v) for v in ("۰", "هیچ", 2, "زیاد", "کم")] == [0, 0, 2, 3, 1])
    check("ایموجی‌های چسبیده جدا می‌شوند",
          E.parse_emoji_list("😂❤️🙏") == ["😂", "❤️", "🙏"], str(E.parse_emoji_list("😂❤️🙏")))

    sample = "سلام چطوری 😂 خوبی ❤️😍"
    check("سطح ۰: هیچ ایموجی نمی‌ماند", E.emoji_count(E.strip_emoji(sample, 0)) == 0)
    check("سطح ۱: فقط یک ایموجی", E.emoji_count(E.strip_emoji(sample, 1)) == 1)
    check("سطح ۲: حداکثر دو ایموجی", E.emoji_count(E.strip_emoji(sample, 2)) == 2)
    check("متن بدون ایموجی دست‌نخورده می‌ماند",
          E.strip_emoji("سلام چطوری", 0) == "سلام چطوری")
    check("با لیست مجاز: ایموجی غیرمجاز حذف می‌شود",
          E.strip_emoji("باشه 😍", 3, ["😂"]) == "باشه")
    check("با لیست مجاز: ایموجی مجاز می‌ماند",
          E.strip_emoji("باشه 😂", 3, ["😂"]) == "باشه 😂")
    check("فاصله‌ی اضافه بعد از حذف ایموجی جمع می‌شود",
          "  " not in E.strip_emoji("سلام  😂  خوبی", 0))

    # پرامپت: قانون ایموجی بر اساس سطح و لیست
    prof = {"relationship": "friend", "tone_level": 2, "emoji_level": 0,
            "allowed_emojis": "", "length": "کوتاه"}
    st = E.effective_settings(prof, "عادی", 2, global_emoji=1, allowed_emojis="")
    check("سطح ایموجی چت بر سطح کلی اولویت دارد", st["emoji_level"] == 0)
    st2 = E.effective_settings({}, "عادی", 2, global_emoji=3, allowed_emojis="😂❤️")
    check("سطح کلی وقتی چت تنظیمی ندارد اعمال می‌شود", st2["emoji_level"] == 3)
    check("لیست ایموجی‌های مجاز در تنظیمات می‌آید", st2["allowed_emojis"] == ["😂", "❤️"])
    msgs = E.build_messages(my_name="من", persona="", mode="عادی", settings=st2,
                            notes=[], facts=[], pending=[], history=[])
    check("پرامپت سطح ۳ را می‌گوید", "ایموجی: آزاد" in msgs[0]["content"], msgs[0]["content"][-300:])
    check("پرامپت لیست ایموجی‌های من را می‌گوید",
          "فقط و فقط از این ایموجی‌ها" in msgs[0]["content"] and "😂" in msgs[0]["content"])
    msgs0 = E.build_messages(my_name="من", persona="", mode="عادی",
                             settings={**st2, "emoji_level": 0, "allowed_emojis": []},
                             notes=[], facts=[], pending=[], history=[])
    check("پرامپت سطح ۰ صریح «هیچ ایموجی» می‌گوید",
          "ایموجی: هیچ" in msgs0[0]["content"], msgs0[0]["content"][-200:])

    # دستور و ذخیره‌سازی
    await sim.fire(client, ".تنظیم ایموجی 0", out=True)
    await asyncio.sleep(0.3)
    cfg_e = await _ai_config(uid)
    check("«.تنظیم ایموجی 0» ذخیره می‌شود", E.norm_emoji_level(cfg_e.get("emoji_level"), -1) == 0,
          str(cfg_e.get("emoji_level")))
    await sim.fire(client, ".تنظیم ایموجی 😂❤️🙏", out=True)
    await asyncio.sleep(0.3)
    cfg_e = await _ai_config(uid)
    check("لیست ایموجی‌های خودم ذخیره می‌شود",
          cfg_e.get("allowed_emojis") == "😂❤️🙏", str(cfg_e.get("allowed_emojis")))
    await sim.fire(client, ".تنظیم ایموجی هیچ", out=True)
    await asyncio.sleep(0.3)
    cfg_e = await _ai_config(uid)
    check("«ایموجی هیچ» محدودیت را برمی‌دارد", not cfg_e.get("allowed_emojis"),
          str(cfg_e.get("allowed_emojis")))
    pv_before = len(PV)
    await sim.fire(client, ".تنظیم ایموجی", out=True)
    await asyncio.sleep(0.4)
    check("«.تنظیم ایموجی» بدون آرگومان راهنما می‌دهد",
          any("سطح ایموجی" in t for _, t in PV[pv_before:]),
          str([t[:60] for _, t in PV[pv_before:]]))

    # ── ۱۵.۸۵) رگرسیون باگ: مقدار خالی پروفایل نباید تنظیم کلی را بی‌اثر کند ──
    rel_prof = {"relationship": "close_friend", "emoji_level": "",
                "tone_level": "", "allowed_emojis": ""}
    check("تنظیم کلی ایموجی روی پروفایل خالی اثر می‌کند",
          E.effective_settings(rel_prof, "عادی", 2, global_emoji=3)["emoji_level"] == 3)
    check("سطح صفر چت بر تنظیم کلی اولویت دارد",
          E.effective_settings({**rel_prof, "emoji_level": 0}, "عادی", 2,
                               global_emoji=3)["emoji_level"] == 0)
    check("«هیچ» به‌عنوان سطح کلی درست تفسیر می‌شود",
          E.effective_settings(rel_prof, "عادی", 2, global_emoji="هیچ")["emoji_level"] == 0)
    st_empty = E.effective_settings({"tone_level": "", "intimacy": "", "relationship": "close_friend"},
                                    "عادی", 2)
    check("سطح خالی/نامعتبر خطا نمی‌دهد و از جایگاه می‌آید",
          st_empty["tone"] == 3 and st_empty["intimacy"] == 5, str(st_empty["tone"]))
    check("لیست ایموجی چت بر کلی اولویت دارد",
          E.effective_settings({**rel_prof, "allowed_emojis": "😂"}, "عادی", 2,
                               global_emoji=1, allowed_emojis="")["allowed_emojis"] == ["😂"])
    check("شمارش ایموجی‌های تکراری درست است",
          E.strip_emoji("😂😂😂", 1) == "😂" and E.emoji_count(E.strip_emoji("😂😂😂", 2)) == 2)

    # ── ۱۵.۹) پنل: دکمه‌ی بازگشت + کلید draft ──
    kb_menu = str(ai_menu_kb())
    check("دکمه‌ی بازگشت در منوی AI هست", "back_main" in kb_menu)
    check("دکمه‌ی ایموجی در منو هست", "ai:emoji" in kb_menu)
    check("دکمه‌ی حالت ارسال (draft) در منو هست", "ai:draft" in kb_menu)
    check("وضعیت ارسال در برچسب دکمه دیده می‌شود",
          "پیشنهاد به من" in kb_menu or "خودکار" in kb_menu)
    kb_emoji = str(emoji_kb(2, "😂"))
    check("کیبورد ایموجی سطح‌ها را دارد", all(f"ai:setemoji:{i}" in kb_emoji for i in range(4)))
    check("کیبورد ایموجی دکمه‌ی برداشتن محدودیت دارد", "allow_clear" in kb_emoji)
    check("متن ایموجی لیست مجاز را نشان می‌دهد",
          "😂" in build_emoji_text({"emoji_level": 2, "allowed_emojis": "😂"}))

    before = bool((await _ai_config(uid)).get("draft_only", True))
    await _save_ai_config(uid, {"draft_only": not before})
    check("حالت draft از پنل قابل تغییر است",
          bool((await _ai_config(uid)).get("draft_only", True)) is (not before))
    await _save_ai_config(uid, {"draft_only": before})
    n_off = await db.set_ai_auto_mode_all(uid, False)
    check("خاموش‌کردن خودکار برای همه‌ی چت‌ها", n_off >= 1, str(n_off))
    n_auto = await db.set_ai_auto_mode_all(uid, True)
    check("خودکار کردن همه‌ی چت‌ها کار می‌کند", n_auto >= 1, str(n_auto))
    prof_now = await db.get_ai_profile(uid, PEER)
    check("چت هم خودکار شد", bool(prof_now.get("auto_mode")))

    # «.ai خودکار» باید کلید کلی draft را هم باز کند وگرنه اثری ندارد
    await _save_ai_config(uid, {"draft_only": True})
    await db.set_ai_auto_mode(uid, PEER, False)
    await sim.fire(client, ".ai خودکار", out=True)
    await asyncio.sleep(0.4)
    cfg_auto = await _ai_config(uid)
    check("«.ai خودکار» کلید کلی را هم خودکار می‌کند",
          cfg_auto.get("draft_only") is False, str(cfg_auto.get("draft_only")))
    check("در همان چت هم خودکار شد",
          bool((await db.get_ai_profile(uid, PEER)).get("auto_mode")))
    await _save_ai_config(uid, {"draft_only": True})
    await db.set_ai_auto_mode(uid, PEER, False)

    # ── ۱۵.۹۵) نام‌ها: نام من، نام مخاطب، اصلاح دستی ──  # noqa: F811
    # نام خودم (از اکانت تلگرام، نه «من»)
    ar = pm.get_active_plugins(uid).get("ai_reply")
    my_name = await ar._my_name()
    check("نام واقعی من از اکانت خوانده می‌شود", my_name and my_name != "من", my_name)

    prof_n = await db.get_ai_profile(uid, PEER)
    check("نام مخاطب خودکار از تلگرام گرفته می‌شود",
          (prof_n.get("target_name") or "") == "Ali", str(prof_n.get("target_name")))

    await sim.fire(client, ".ai نام رضایی", out=True)
    await asyncio.sleep(0.4)
    check("«.ai نام ‹اسم›» نام را عوض می‌کند",
          (await db.get_ai_profile(uid, PEER)).get("target_name") == "رضایی"
          and (await _ai_config(uid)).get("name_overrides", {}).get(str(PEER)) == "رضایی")
    await sim.fire(client, ".ai نام خودکار", out=True)
    await asyncio.sleep(0.4)
    check("«.ai نام خودکار» به نام تلگرام برمی‌گردد",
          not (await _ai_config(uid)).get("name_overrides")
          and (await db.get_ai_profile(uid, PEER)).get("target_name") == "Ali")

    # جایگزینی نام عددی با نام واقعی
    await db.upsert_ai_profile(uid, PEER, target_name=str(PEER))
    reset_limits(uid)
    await sim.fire(client, "سلام", out=False)
    await asyncio.sleep(1.2)
    check("نام عددی (id) با نام واقعی جایگزین می‌شود",
          (await db.get_ai_profile(uid, PEER)).get("target_name") == "Ali")

    # نام دستی نباید با نام تلگرام بازنویسی شود
    await sim.fire(client, ".ai نام رضایی", out=True)
    await asyncio.sleep(0.4)
    reset_limits(uid)
    await sim.fire(client, "سلام دوباره", out=False)
    await asyncio.sleep(1.2)
    check("نام دستی من با نام تلگرام بازنویسی نمی‌شود",
          (await db.get_ai_profile(uid, PEER)).get("target_name") == "رضایی")
    await sim.fire(client, ".ai نام خودکار", out=True)
    await asyncio.sleep(0.4)

    # ── ۱۵.۹۶) مودها: ناراحت و خشمگین + اعمال واقعی ──
    check("مود «ناراحت» وجود دارد", "ناراحت" in E.MODES)
    check("مود «خشمگین» وجود دارد", "خشمگین" in E.MODES)
    check("«دلگیر» به «ناراحت» و «عصبانی» به «خشمگین» می‌رود",
          E.norm_mode("دلگیر") == "ناراحت" and E.norm_mode("عصبانی") == "خشمگین")
    check("هر مود قاعده‌ی رفتاری دارد",
          all(m in E.MODE_RULES for m in E.MODES),
          str([m for m in E.MODES if m not in E.MODE_RULES]))
    kb_all = str(modes_kb("عادی"))
    check("همه‌ی مودها در پنل هستند",
          all(f"ai:mode:{m}" in kb_all for m in E.MODES),
          str([m for m in E.MODES if f"ai:mode:{m}" not in kb_all]))
    check("متن پنل مودها همه را توضیح می‌دهد",
          all(m in build_modes_text({}) for m in E.MODES))

    for m in ("ناراحت", "خشمگین"):
        await sim.fire(client, f".تنظیم مود {m}", out=True)
        await asyncio.sleep(0.35)
        check(f"مود {m} ذخیره می‌شود", (await _ai_config(uid)).get("mode") == m)
        n = len(PV)
        reset_limits(uid)
        await sim.fire(client, "چرا اینکارو کردی؟", out=False)
        await asyncio.sleep(1.2)
        last_prompt = CALLS[-1][0]["content"] if CALLS else ""
        check(f"قاعده‌ی مود {m} در پرامپت می‌آید",
              E.MODE_RULES[m][:20] in last_prompt, last_prompt[-200:])
        check(f"یادآوری مود {m} در پایان پرامپت هست",
              f"مود الان «{m}»" in last_prompt)

    # ── ۱۵.۹۷) ریپلای روی پیام طرف ──
    await sim.fire(client, ".تنظیم مود عادی", out=True)
    await asyncio.sleep(0.3)
    ar = pm.get_active_plugins(uid).get("ai_reply")
    reset_limits(uid)
    await sim.fire(client, ".ai خودکار", out=True)
    await asyncio.sleep(0.4)
    reset_limits(uid)
    n_sent = len(SENT)
    await sim.fire(client, "کجایی تو؟", out=False, mid=4242)
    await asyncio.sleep(2.5)
    sends = [x for x in SENT[n_sent:] if x[0] == "send" and x[1] != "me"]
    check("پاسخ خودکار ارسال می‌شود", bool(sends), str(sends))
    check("پاسخ ریپلای روی همان پیام طرف است",
          bool(sends) and len(sends[0]) > 3 and sends[0][3] == 4242,
          str(sends[0] if sends else None))

    # پیش‌نویس هم شناسه‌ی پیام را نگه می‌دارد
    DRAFTS.clear()
    reset_limits(uid)
    await sim.fire(client, ".ai دستی", out=True)
    await asyncio.sleep(0.4)
    reset_limits(uid)
    await sim.fire(client, "خب؟", out=False, mid=4343)
    await asyncio.sleep(1.5)
    d = list(DRAFTS.values())[-1] if DRAFTS else {}
    check("پیش‌نویس شناسه‌ی پیام طرف را نگه می‌دارد", d.get("reply_to") == 4343, str(d.get("reply_to")))
    check("پیش‌نویس نام مخاطب را دارد", bool(d.get("name")), str(d.get("name")))

    # ── ۱۵.۹۸) دقت: قواعد ضد بی‌ربطی و غلط املایی ──
    msgs_rel = E.build_messages(my_name="علی", persona="", mode="عادی",
                                settings=st2, notes=[], facts=[], pending=[],
                                history=[{"content": "کجایی؟", "is_out": False}],
                                incoming_text="کجایی؟")
    sys_txt = msgs_rel[0]["content"]
    check("قاعده‌ی «مستقیماً جواب همان پیام» در پرامپت هست",
          "مستقیماً به همان پیام" in sys_txt)
    check("قاعده‌ی ممنوعیت اطلاعات ساختگی هست",
          "هیچ اطلاعات، خاطره، اسم، عدد" in sys_txt)
    check("قاعده‌ی پیام مبهم/کوتاه هست", "پیام کوتاه یا مبهم" in sys_txt)
    check("قاعده‌ی غلط املایی اصلاح شده",
          "غلط املایی واضح" in sys_txt and "غلط تایپی طبیعی اشکالی ندارد" not in sys_txt)
    check("قاعده‌ی نام‌بردن هست", "اسم طرف را فقط همان‌طور" in sys_txt)
    check("پیام طرف در یادآوری پایان پرامپت می‌آید", "کجایی؟" in sys_txt.split("یادآوری آخر")[-1])

    # ── ۱۵.۹۹) ماندگاری داده (رگرسیون باگ ریست‌شدن تنظیمات) ──
    await _save_ai_config(uid, {"persona": "پرسونای ماندگار", "mode": "شوخ",
                                "emoji_level": 2,
                                "name_overrides": {str(PEER): "رضایی"}})
    snapshot = {k: (await _ai_config(uid)).get(k)
                for k in ("persona", "mode", "emoji_level", "name_overrides")}
    # روشن/خاموش کردن قابلیت (هم دستور، هم دکمه‌ی پنل) نباید تنظیمات را پاک کند
    await sim.fire(client, ".خاموش هوش مصنوعی", out=True)
    await asyncio.sleep(0.4)
    await sim.fire(client, ".روشن هوش مصنوعی", out=True)
    await asyncio.sleep(0.4)
    await db.set_feature(uid, "ai_reply", False)          # مسیر دکمه‌ی پنل
    await db.set_feature(uid, "ai_reply", True)
    after_toggle = {k: (await _ai_config(uid)).get(k) for k in snapshot}
    check("خاموش/روشن کردن قابلیت تنظیمات را پاک نمی‌کند",
          after_toggle == snapshot, f"{snapshot} → {after_toggle}")

    # شبیه‌سازی ری‌استارت: نمونه‌ی تازه‌ی پلاگین همان تنظیمات را می‌خواند
    from plugins.ai_reply import AiReplyPlugin
    fresh = AiReplyPlugin(client, uid)
    await fresh._load_config()
    check("بعد از ری‌استارت تنظیمات همان می‌ماند",
          all(fresh._cfg.get(k) == snapshot[k] for k in snapshot),
          str({k: fresh._cfg.get(k) for k in snapshot}))
    fresh.stop()
    # تنظیمات سایر قابلیت‌ها هم نباید پاک شود
    await db.set_feature(uid, "banner", True, {"seconds": 300})
    await db.set_feature(uid, "banner", True)
    feats = {f["feature_name"]: f for f in await db.get_features(uid)}
    banner_cfg = feats["banner"]["config_json"] or {}
    if isinstance(banner_cfg, str):
        import json as _json
        banner_cfg = _json.loads(banner_cfg or "{}")
    check("تنظیمات قابلیت‌های دیگر هم حفظ می‌شود",
          banner_cfg.get("seconds") == 300, str(banner_cfg))

    # ── ۱۵.۱۰) مخاطبین: هر چتِ روشن‌شده + نام درست ──
    await db.upsert_ai_profile(uid, 55555, target_name="حسن", enabled=True)
    chats_all = await db.list_ai_chats(uid)
    ids = {c["target_id"] for c in chats_all}
    check("مخاطب جدید (بدون هیچ پیامی) در فهرست می‌آید", 55555 in ids, str(sorted(ids)))
    check("چت‌های دارای حافظه هم می‌مانند", PEER in ids, str(sorted(ids)))
    hassan = next((c for c in chats_all if c["target_id"] == 55555), {})
    check("نام مخاطب در فهرست درست است", hassan.get("name") == "حسن", str(hassan))

    # نام مخاطب نباید نام من باشد
    me_name = await pm.get_active_plugins(uid)["ai_reply"]._my_name()
    prof_self = next((c for c in chats_all if c["target_id"] == PEER), {})
    check("نام مخاطب با نام خودم اشتباه نشده", prof_self.get("name") != me_name,
          f"{prof_self.get('name')} vs {me_name}")

    # نام خراب (id خام یا نام خودم) خودکار اصلاح می‌شود
    await db.upsert_ai_profile(uid, PEER, target_name=me_name)
    await _save_ai_config(uid, {"name_overrides": {}})
    ar2 = pm.get_active_plugins(uid)["ai_reply"]
    await ar2._repair_contact_names()
    check("نام خرابِ ذخیره‌شده خودکار اصلاح می‌شود",
          (await db.get_ai_profile(uid, PEER)).get("target_name") == "Ali",
          str((await db.get_ai_profile(uid, PEER)).get("target_name")))
    # نامِ گروه (که در شبیه‌ساز قابل حل است) به‌جای id خام
    await db.upsert_ai_profile(uid, GROUP, target_name=str(abs(GROUP)))
    await ar2._repair_contact_names()
    fixed_group = (await db.get_ai_profile(uid, GROUP)).get("target_name")
    check("نام عددی (id خام) خودکار با نام واقعی جایگزین می‌شود",
          fixed_group == "گروه تست", str(fixed_group))

    # نام دستی با اصلاح خودکار عوض نمی‌شود
    await _save_ai_config(uid, {"name_overrides": {str(PEER): "رضایی"}})
    await db.upsert_ai_profile(uid, PEER, target_name="رضایی")
    hassan2 = await ar2._repair_contact_names()
    check("نام دستی با اصلاح خودکار تغییر نمی‌کند",
          (await db.get_ai_profile(uid, PEER)).get("target_name") == "رضایی")
    await _save_ai_config(uid, {"name_overrides": {}, "persona": "من کم‌حرفم",
                                "mode": "عادی", "emoji_level": 1})

    # ── ۱۵.۱۱) رگرسیون «گاهی جواب نمی‌دهد» ──
    await _save_ai_config(uid, {"quiet_hours": "", "auto_mode": None,
                                "draft_only": False, "all_private": False})
    await db.set_ai_auto_mode(uid, PEER, True)
    ar3 = reset_limits(uid)
    if ar3:
        ar3._skip_notice.clear()

    # الف) سکوت ۹۰ ثانیه‌ای بعد از هر پاسخ (باگ اصلی)
    answered = 0
    for i in range(3):
        reset_limits(uid)
        before_n = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
        await sim.fire(client, f"پیام پشت‌سرهم {i}", out=False, mid=9100 + i)
        await asyncio.sleep(2.2)
        if len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > before_n:
            answered += 1
    check("در گفت‌وگوی دوطرفه به پیام‌های پشت‌سرهم جواب می‌دهد", answered == 3,
          f"{answered}/3")
    if ar3:
        check("پاسخ AI چت را ۹۰ ثانیه ساکت نمی‌کند",
              time.time() - ar3._last_activity.get(PEER, 0) > 60,
              str(ar3._last_activity.get(PEER)))

    # ب) چند مخاطب همزمان
    others = [7001, 7002, 7003]
    for cid in others:
        await db.upsert_ai_profile(uid, cid, target_name=f"مخاطب{cid}",
                                   enabled=True, auto_mode=True)
    got = 0
    for cid in others:
        reset_limits(uid)
        before_n = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
        await sim.fire(client, "سلام", out=False, sender_id=cid, chat=cid, mid=9200 + cid)
        await asyncio.sleep(2.2)
        if len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > before_n:
            got += 1
    check("به چند مخاطب همزمان جواب می‌دهد", got == 3, f"{got}/3")

    # ج) سقف‌های واقعی‌تر + مهاجرت مقادیر قدیمی
    from plugins.ai_reply import DEFAULT_CONFIG as DC
    check("سقف دقیقه‌ای پیش‌فرض واقعی است", int(DC["rpm"]) >= 20, str(DC["rpm"]))
    check("سقف روزانه پیش‌فرض واقعی است", int(DC["rpd"]) >= 1000, str(DC["rpd"]))
    await db.set_feature(uid, "ai_reply", True, {"rpm": 6, "rph": 60, "rpd": 300})
    ar4 = pm.get_active_plugins(uid)["ai_reply"]
    await ar4._load_config()
    check("سقف‌های قدیمی ذخیره‌شده خودکار مهاجرت می‌کنند",
          int(ar4._cfg.get("rpm")) >= 20, str(ar4._cfg.get("rpm")))

    # د) سقف که پر شد، دلیلش را می‌گوید (نه سکوت بی‌دلیل)
    ar5 = pm.get_active_plugins(uid)["ai_reply"]
    now_t = time.time()
    ar5._usage_chat[PEER] = [now_t] * int(ar5._cfg.get("rpm", 20))
    ar5._usage = [now_t] * int(ar5._cfg.get("rpm", 20))
    ok_q, why_q = ar5._quota_ok(PEER)
    check("سقف پر شده تشخیص داده می‌شود", not ok_q and "سقف" in why_q, why_q)
    # سقف کل اکانت لایه‌ی جداگانه‌ای است
    ar5._usage = [now_t] * int(ar5._cfg.get("global_rpm", 60))
    ar5._usage_chat.clear()
    ok_all, why_all = ar5._quota_ok(PEER)
    check("سقف کل اکانت هم جداگانه تشخیص داده می‌شود",
          (not ok_all) and "کل اکانت" in why_all, why_all)
    check("وضعیت مصرف خوانا است", "دقیقه" in ar5._quota_state(), ar5._quota_state())
    reset_limits(uid)

    # ه) ساعت سکوت به وقت خودم، نه سرور
    ar5._cfg["timezone"] = "Asia/Tehran"
    ar5._cfg["quiet_hours"] = ""
    check("ساعت سکوت خاموش قابل تشخیص است", ar5._in_quiet_hours() is False)
    ar5._cfg["quiet_hours"] = f"{ar5._local_now().hour}-{(ar5._local_now().hour + 1) % 24}"
    check("ساعت سکوت فعال درست تشخیص داده می‌شود", ar5._in_quiet_hours() is True)
    check("ساعت محلی از منطقه‌ی زمانی می‌آید",
          ar5._local_now() is not None)
    ar5._cfg["quiet_hours"] = ""

    # و) دستورها
    pv_before = len(PV)
    await sim.fire(client, ".ai چرا", out=True, mid=9300)
    await asyncio.sleep(0.6)
    why_txt = "\n".join(t for _, t in PV[pv_before:])
    check("«.ai چرا» وضعیت کامل می‌دهد", "وضعیت پاسخ‌دهی" in why_txt, why_txt[:100])
    check("«.ai چرا» نسخه‌ی کد را نشان می‌دهد", "نسخه‌ی کد" in why_txt)
    check("«.ai چرا» ساعت محلی را نشان می‌دهد", "ساعت من" in why_txt)

    pv_before = len(PV)
    await sim.fire(client, ".ai سکوت 23-7", out=True, mid=9301)
    await asyncio.sleep(0.5)
    check("«.ai سکوت ‹بازه›» ذخیره می‌شود",
          (await _ai_config(uid)).get("quiet_hours") == "23-7",
          str((await _ai_config(uid)).get("quiet_hours")))
    await sim.fire(client, ".ai سکوت خاموش", out=True, mid=9302)
    await asyncio.sleep(0.5)
    check("«.ai سکوت خاموش» ساعت سکوت را برمی‌دارد",
          not (await _ai_config(uid)).get("quiet_hours"))

    await sim.fire(client, ".ai سقف 30 400 3000", out=True, mid=9303)
    await asyncio.sleep(0.5)
    cfg_lim = await _ai_config(uid)
    check("«.ai سقف» سقف‌ها را عوض می‌کند",
          (cfg_lim.get("rpm"), cfg_lim.get("rph"), cfg_lim.get("rpd")) == (30, 400, 3000),
          str((cfg_lim.get("rpm"), cfg_lim.get("rph"), cfg_lim.get("rpd"))))

    # ز) «همه‌ی پیوی‌ها» → چت جدید خودکار به مخاطبین اضافه می‌شود
    await sim.fire(client, ".ai همه روشن", out=True, mid=9304)
    await asyncio.sleep(0.5)
    check("«.ai همه روشن» ذخیره می‌شود",
          bool((await _ai_config(uid)).get("all_private")))
    reset_limits(uid)
    fresh_peer = 8080
    await sim.fire(client, "سلام تازه", out=False, sender_id=fresh_peer,
                   chat=fresh_peer, mid=9400)
    await asyncio.sleep(2.0)
    ids_now = {c["target_id"] for c in await db.list_ai_chats(uid)}
    check("چت پیوی جدید خودکار در مخاطبین می‌آید", fresh_peer in ids_now, str(sorted(ids_now))[:200])
    await sim.fire(client, ".ai همه خاموش", out=True, mid=9305)
    await asyncio.sleep(0.4)

    # ح) میان‌برهای پنل: ساعت سکوت و همه‌ی پیوی‌ها
    kb2 = str(ai_menu_kb(all_private=True, quiet="1-8"))
    check("دکمه‌ی «همه‌ی پیوی‌ها» در پنل هست", "ai:allpvt" in kb2)
    check("دکمه‌ی ساعت سکوت در پنل هست", "ai:quiet" in kb2)
    check("وضعیت دکمه‌ها در برچسب دیده می‌شود",
          "روشن" in kb2 and "1-8" in kb2)

    # ط) نسخه‌ی کد در دسترس است
    from core.version import code_version, feature_summary
    check("نسخه‌ی کد خوانده می‌شود", bool(code_version()), code_version())
    check("خلاصه‌ی قابلیت‌ها تعداد مودها را می‌گوید",
          "مود" in feature_summary(), feature_summary())

    # ── ۱۵.۱۲) اکانت دومِ خودم (سلف‌بات جداگانه) ──
    # کاربر: «از اکانت دوم که سلف‌بات هم رویش هست، نه جواب می‌گیرم نه در
    # مخاطبین می‌آید، هرچقدر .ai را روشن/خاموش می‌کنم.»
    # علت: پلاگین AI وقتی قابلیت خاموش باشد اصلاً لود نمی‌شود → دستور `.ai`
    # هیچ‌وقت اجرا نمی‌شد (پیام پاک می‌شد و فقط یک هشدار می‌آمد).
    uid2, client2 = await sim.setup_second_user()
    reset_limits(uid2)
    check("اکانت دوم با قابلیت AI خاموش شروع می‌شود",
          "ai_reply" not in pm.get_active_plugins(uid2))

    sent_before = len(SENT)
    await sim.fire(client2, ".ai وضعیت", out=True, mid=9500)
    await asyncio.sleep(0.8)
    hint = "\n".join(x[2] for x in SENT[sent_before:] if isinstance(x[2], str))
    check("با قابلیت خاموش، دستور .ai راهنما می‌دهد (سکوت نمی‌کند)",
          "خاموش است" in hint, hint[:110])

    await sim.fire(client2, ".ai روشن", out=True, mid=9501)
    await asyncio.sleep(1.2)
    check("«.ai روشن» روی اکانت دوم، خودِ قابلیت را روشن می‌کند",
          "ai_reply" in pm.get_active_plugins(uid2),
          str(sorted(pm.get_active_plugins(uid2))))
    prof2 = await db.get_ai_profile(uid2, PEER)
    check("و همان چت در مخاطبین اکانت دوم ثبت می‌شود", prof2 is not None, str(prof2))
    ids2 = {c["target_id"] for c in await db.list_ai_chats(uid2)}
    check("چت در فهرست مخاطبین اکانت دوم دیده می‌شود", PEER in ids2, str(sorted(ids2))[:150])

    # مخاطبین دو اکانت قاطی نمی‌شوند
    await db.upsert_ai_profile(uid2, 7777, target_name="مخصوص‌اکانت۲", enabled=True)
    ids1 = {c["target_id"] for c in await db.list_ai_chats(uid)}
    ids2 = {c["target_id"] for c in await db.list_ai_chats(uid2)}
    check("مخاطبین دو اکانت جدا هستند", 7777 not in ids1 and 7777 in ids2,
          f"اکانت۱={sorted(ids1)[:6]} اکانت۲={sorted(ids2)[:6]}")

    # پاسخ‌دهی روی اکانت دوم
    await _save_ai_config(uid2, {"draft_only": False, "quiet_hours": "",
                                 "rpm": 20, "rph": 240, "rpd": 1500})
    ar2 = pm.get_active_plugins(uid2)["ai_reply"]
    await ar2._load_config()
    ar2._usage.clear(); ar2._usage_day.clear(); ar2._last_activity.clear()
    await db.set_ai_auto_mode(uid2, PEER, True)
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client2, "سلام از اکانت دوم", out=False, sender_id=PEER, mid=9510)
    await asyncio.sleep(2.2)
    check("اکانت دوم هم پاسخ AI می‌گیرد",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)

    # ── ۱۵.۱۳) نگهبان حلقه: دو سلف‌بات که بی‌وقفه جواب هم را می‌دهند ──
    await _save_ai_config(uid, {"draft_only": False, "quiet_hours": ""})
    ar5b = pm.get_active_plugins(uid)["ai_reply"]
    await ar5b._load_config()
    await db.upsert_ai_profile(uid, PEER, enabled=True)
    await db.set_ai_auto_mode(uid, PEER, True)
    reset_limits(uid)
    ar5b._streak[PEER] = int(ar5b._cfg.get("max_streak", 25))
    ar5b._streak_cool[PEER] = time.time()      # دوره‌ی خنک‌شدن فعال
    ar5b._skip_notice.clear()
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "بازم سلام", out=False, mid=9600)
    await asyncio.sleep(1.5)
    check("بعد از پاسخ‌های بی‌پایان، نگهبان حلقه جلوی پاسخ را می‌گیرد",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) == n_before)
    pv_n = "\n".join(t for _, t in PV[-4:])
    check("و دلیلش را اطلاع می‌دهد", "پشت‌سرهم" in pv_n, pv_n[:120])
    # پایان دوره‌ی خنک‌شدن → خودکار ادامه می‌دهد (هرگز برای همیشه ساکت نمی‌ماند)
    ar5b._streak_cool[PEER] = time.time() - 9999
    reset_limits(uid)
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "بعد از خنک‌شدن", out=False, mid=9610)
    await asyncio.sleep(2.0)
    check("بعد از دوره‌ی خنک‌شدن، نگهبان حلقه خودکار رها می‌کند",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)
    ar5b._last_activity[PEER] = 0
    await sim.fire(client, "خودم دارم حرف می‌زنم", out=True, mid=9601)
    await asyncio.sleep(0.6)
    check("با حرف زدن خودم، شمارنده‌ی حلقه صفر می‌شود",
          ar5b._streak.get(PEER, 0) == 0, str(ar5b._streak.get(PEER)))
    reset_limits(uid)

    # ── ۱۵.۱۴) تلاش دوباره با فاصله (کاربر: «۲ بار تلاش کن، ۳ ثانیه فاصله») ──
    real_call = ai_providers._call_provider
    tries = []

    async def _flaky(pv, key, messages, max_tokens, temperature):
        tries.append(1)
        if len(tries) == 1:
            pv.last_error = "HTTP 503 — خطای موقت سرویس"
            return None
        return "سلام، خوبم"

    class _FakeProvider:
        name, model, kind, temperature, max_tokens = "fake", "m", "openai", 0.8, 200
        blocked, last_error, keys = False, "", [""]

        def available_keys(self):
            return [""]

        def cooldown(self, key, seconds=0):
            pass

    ai_providers._call_provider = _flaky
    import time as _time
    _t0 = _time.time()
    text, name = await GENUINE_CHAT(
        [{"role": "user", "content": "سلام"}], providers=[_FakeProvider()],
        attempts=2, delay=0.6)
    check("سرویس با تلاش دوباره جواب می‌دهد", text == "سلام، خوبم", str(text))
    check("بین دو دور تلاش فاصله هست", len(tries) == 2 and _time.time() - _t0 >= 0.5,
          f"tries={len(tries)}")
    ai_providers._call_provider = GENUINE_CALL
    real_chat = GENUINE_CHAT
    ai_providers.chat = GENUINE_CHAT

    _seen = {}

    async def _spy(messages, **kw):
        _seen.update(kw)
        return ("باشه", "fake")

    ai_providers.chat = _spy
    await sim.fire(client, "خوبی خوشی سلامتی ؟ کجایی نیستی ؟ دلم واست تنگ شده بود منم",
                   out=False, mid=9750)
    await asyncio.sleep(2.0)
    check("پلاگین ۲ تلاش با فاصله‌ی ۳ ثانیه درخواست می‌کند",
          int(_seen.get("attempts", 0)) == 2 and float(_seen.get("delay", 0)) == 3.0,
          f"attempts={_seen.get('attempts')} delay={_seen.get('delay')}")

    # ── ۱۵.۱۵) پاسخ جانشین هرگز بدون اجازه فرستاده نمی‌شود ──

    async def _dead(messages, **kw):
        return (None, None)

    ai_providers.chat = _dead
    ar._cfg["no_reply_retry_delay"] = 1
    ar._pending_retry.clear()
    ar._skip_notice.clear()
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    pv0 = len(PV)
    await sim.fire(client, "یکی هستی؟", out=False, mid=9760)
    await asyncio.sleep(2.5)
    check("وقتی همه‌ی سرویس‌ها خطا دادند، پاسخ جانشین به طرف مقابل نمی‌رود",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) == n_before)
    notice = "\n".join(t for _, t in PV[pv0:])
    check("و علتش را با جزئیات به من می‌گوید",
          "هیچ سرویس AI جواب نداد" in notice and "تلاش" in notice, notice[:80])
    check("و می‌گوید پیامی بدون اجازه فرستاده نشد",
          "بدون جواب تو" in notice or "جایگزین" in notice)

    # تلاش دومِ زمان‌دار وقتی سرویس برگشت، پاسخ را می‌رساند

    async def _back(messages, **kw):
        return ("ببخشید، اینجام", "fake")

    ai_providers.chat = _back
    ar._pending_retry.clear()
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "دوباره سلام", out=False, mid=9761)
    await asyncio.sleep(1.0)
    ai_providers.chat = _dead
    await asyncio.sleep(2.6)
    ai_providers.chat = _back
    await asyncio.sleep(4.0)
    check("تلاش زمان‌دار بعدی پاسخ را می‌رساند",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)
    ai_providers.chat = real_chat

    # ── ۱۵.۱۶) هرگز برای همیشه متوقف نمی‌شود ──
    import time as _tt
    # ارسال خودکار در چت (پیش‌نویس خاموش) + سرویس سالم
    await _save_ai_config(uid, {"draft_only": False, "fallback_replies": False})
    ar = pm.get_active_plugins(uid)["ai_reply"]     # نمونه‌ی زنده (ممکن است عوض شده باشد)
    await ar._load_config()
    await db.set_ai_auto_mode(uid, PEER, True)
    ai_providers.chat = _back
    ar._pending_retry.clear()
    ar._streak[PEER] = 999
    ar._streak_cool[PEER] = 0
    reset_limits(uid)
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "بازم سلام", out=False, mid=9770)
    await asyncio.sleep(2.2)
    check("بعد از پایان دوره‌ی خنک‌شدن، خودکار ادامه می‌دهد",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)

    ar._busy.add(PEER)
    ar._busy_since[PEER] = _tt.time() - 999
    reset_limits(uid)
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "قفل گیرکرده", out=False, mid=9771)
    await asyncio.sleep(2.2)
    check("قفل گیرکرده‌ی چت خودکار آزاد می‌شود (بدون ری‌استارت)",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)
    for _ in range(20):                 # تا پایان ارسال صبر کن
        if PEER not in ar._busy:
            break
        await asyncio.sleep(0.5)
    check("قفل در پایان پاسخ پاک می‌شود", PEER not in ar._busy)

    # سقف دقیقه‌ای: مکث می‌کند ولی جواب می‌دهد
    ar._cfg.update({"rpm": 1, "quota_wait_max": 3})
    ar._usage = [_tt.time()]
    ar._usage_day = [_tt.time()]
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "سقف پر", out=False, mid=9772)
    await asyncio.sleep(4.5)
    check("با پر بودن سقف دقیقه‌ای، بعد از مکث جواب می‌دهد",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)
    ar._cfg.update({"rpm": 20, "quota_wait_max": 45})
    reset_limits(uid)

    # ── ۱۵.۱۶.۵) دکمه‌ی «پاسخ جانشین» در پنل ──
    kb_fb = str(ai_menu_kb(fallback=False))
    check("دکمه‌ی «پاسخ جانشین» در پنل هست", "ai:fallback" in kb_fb)
    check("حالت خاموش آن دیده می‌شود", "خاموش" in kb_fb)
    check("حالت روشن آن دیده می‌شود", "روشن" in str(ai_menu_kb(fallback=True)))

    # ── ۱۵.۱۷) تغییر مود وسط گفت‌وگو پاسخ‌دهی را قطع نمی‌کند ──
    ar = pm.get_active_plugins(uid)["ai_reply"]
    await sim.fire(client, ".تنظیم مود دعوایی", out=True, mid=9780)
    await asyncio.sleep(0.8)
    ar._cfg = ar._cfg  # noqa: B018
    await ar._load_config()
    check("مود «دعوایی» ذخیره و فعال می‌شود", ar._cfg.get("mode") == "دعوایی",
          str(ar._cfg.get("mode")))
    check("مود «دعوایی» در موتور هست", "دعوایی" in E.MODES)
    check("قاعده‌ی مود دعوایی نوشته شده", "دعواطلب" in E.MODE_RULES.get("دعوایی", ""))
    check("عاشقانه رمانتیک‌تر شد",
          "رمانتیک" in E.MODE_RULES["عاشقانه"] and "یک خط کامل" in E.LENGTH_HINT["بلند"])
    check("طول پاسخ رمانتیک بلندتر است", E.MODES["عاشقانه"].get("length") == "بلند")
    reset_limits(uid)
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "خب چی می‌گی؟", out=False, mid=9781)
    await asyncio.sleep(2.2)
    check("بعد از تغییر مود، جواب دادن ادامه دارد",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)
    kb_modes = str(modes_kb("دعوایی"))
    check("دکمه‌ی «دعوایی» در پنل هست", "دعوایی" in kb_modes)
    await sim.fire(client, ".تنظیم مود عادی", out=True, mid=9782)
    await asyncio.sleep(0.6)
    await ar._load_config()
    ai_providers.chat = real_chat
    ar._pending_retry.clear()

    # ── ۱۵.۱۸) خطای غیرمنتظره‌ی سرویس هم به پاسخ جانشین می‌رسد ──
    await _save_ai_config(uid, {"draft_only": False, "quiet_hours": ""})
    ar6 = pm.get_active_plugins(uid)["ai_reply"]
    await ar6._load_config()
    ar6._last_activity.clear(); ar6._streak.clear(); ar6._pending_retry.clear()
    reset_limits(uid)
    await db.upsert_ai_profile(uid, PEER, enabled=True)
    await db.set_ai_auto_mode(uid, PEER, True)
    async def _boom(*a, **k):
        raise RuntimeError("simulated provider crash")

    ai_providers.chat = _boom
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "سلام، خطای سرویس", out=False, mid=9700)
    await asyncio.sleep(2.2)
    check("خطای غیرمنتظره‌ی سرویس هم هشدار می‌دهد (بدون پاسخ جانشین)",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) == n_before)
    ai_providers.chat = real_chat

    # ── ۱۵.۱۹) پیام دستور «.ai روشن» بعد از اجرا در چت نمی‌ماند ──
    ar = pm.get_active_plugins(uid)["ai_reply"]
    await db.upsert_ai_profile(uid, PEER, enabled=False)
    before_del = len([x for x in SENT if x[0] == "delete"])
    await sim.fire(client, ".ai روشن", out=True, mid=9790)
    await asyncio.sleep(1.0)
    check("پیام دستور بعد از ارسال جواب پاک می‌شود (چت شلوغ/لو نرفتن)",
          len([x for x in SENT if x[0] == "delete"]) > before_del,
          str([x for x in SENT if x[0] == "delete"][-1:]))

    # ── ۱۵.۲۰) مکث بعد از پیام خودم (۱۵ ثانیه، نه ۹۰) ──
    await _save_ai_config(uid, {"draft_only": False, "quiet_hours": "",
                                "owner_idle_seconds": 15, "rpm": 20,
                                "rph": 240, "rpd": 1500})
    check("مکث پیش‌فرض قابل تغییر است (اینجا ۱۵ برای تست)",
          int(((await _ai_config(uid)).get("owner_idle_seconds")) or 0) == 15)
    ar = pm.get_active_plugins(uid)["ai_reply"]
    await ar._load_config()
    await db.set_ai_auto_mode(uid, PEER, True)
    ai_providers.chat = _back          # سرویس جعلیِ موفق (نه تابع واقعی بدون provider)

    from plugins.ai_reply import DEFAULT_CONFIG as _DC2
    check("مکث پیش‌فرض کد ۹۰ ثانیه است",
          int(_DC2.get("owner_idle_seconds", 0)) == 90,
          str(_DC2.get("owner_idle_seconds")))

    ar._usage_chat.clear(); ar._usage.clear()
    await sim.fire(client, "خودم جوابش را می‌دهم", out=True, mid=9810)
    await asyncio.sleep(0.5)
    ar._usage_chat.clear(); ar._usage.clear()
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "پیام فوری بعد از پیام من", out=False, mid=9811)
    await asyncio.sleep(1.5)
    check("بلافاصله بعد از پیام خودم وارد نمی‌شود",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) == n_before)
    ar._last_activity[PEER] = time.time() - 20        # ۲۰ ثانیه گذشته
    ar._usage_chat.clear(); ar._usage.clear()
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "بعد از مکث", out=False, mid=9812)
    await asyncio.sleep(2.0)
    check("بعد از گذشت مکث، دوباره جواب می‌دهد",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)

    # دستور مکث
    await sim.fire(client, ".ai مکث 5", out=True, mid=9813)
    await asyncio.sleep(0.7)
    _pm = await db.get_ai_profile(uid, PEER)
    check("«.ai مکث ‹ثانیه›» برای همین مخاطب ذخیره می‌شود",
          int(_pm.get("idle_seconds", -99)) == 5, str(_pm.get("idle_seconds")))
    await sim.fire(client, ".ai مکث خاموش", out=True, mid=9814)
    await asyncio.sleep(0.7)
    _pm = await db.get_ai_profile(uid, PEER)
    check("«.ai مکث خاموش» برای همین مخاطب مکث را برمی‌دارد",
          int(_pm.get("idle_seconds", -99)) == 0, str(_pm.get("idle_seconds")))
    await sim.fire(client, ".ai مکث پیش‌فرض", out=True, mid=9817)
    await asyncio.sleep(0.7)
    _pm = await db.get_ai_profile(uid, PEER)
    check("«.ai مکث پیش‌فرض» برمی‌گرداند به پیش‌فرض کلی",
          int(_pm.get("idle_seconds", -99)) == -1, str(_pm.get("idle_seconds")))
    await _save_ai_config(uid, {"owner_idle_seconds": 90})
    await ar._load_config()

    # ── ۱۵.۲۰.۵) مکث اختصاصی هر مخاطب (جدا از پیش‌فرض کلی) ──
    await db.upsert_ai_profile(uid, PEER, idle_seconds=-1)
    await ar._load_config()
    await sim.fire(client, ".ai مکث 5", out=True, mid=9815)
    await asyncio.sleep(0.7)
    _p5 = await db.get_ai_profile(uid, PEER)
    check("«.ai مکث ‹ثانیه›» برای همین مخاطب ذخیره می‌شود",
          int(_p5.get("idle_seconds", -99)) == 5, str(_p5.get("idle_seconds")))
    _eff, _src = ar._idle_for(PEER, _p5)
    check("مکث مؤثر این مخاطب اختصاصی است", _eff == 5 and "اختصاصی" in _src,
          f"{_eff} / {_src}")
    _eff2, _src2 = ar._idle_for(4242, {"idle_seconds": -1})
    _def_now = int(ar._cfg.get("owner_idle_seconds", 0) or 0)
    check("بقیه‌ی مخاطبین روی پیش‌فرض کلی می‌مانند",
          _eff2 == _def_now and "پیش‌فرض" in _src2, f"{_eff2} / {_src2}")
    await sim.fire(client, ".ai مکث پیش‌فرض", out=True, mid=9816)
    await asyncio.sleep(0.7)
    _p6 = await db.get_ai_profile(uid, PEER)
    check("«.ai مکث پیش‌فرض» برگشت به پیش‌فرض کلی",
          int(_p6.get("idle_seconds", -99)) == -1, str(_p6.get("idle_seconds")))

    # ═══ ۳) صف پیام‌ها: پیام‌های وسط پاسخ دور ریخته نشوند ═══
    ar._last_activity.clear()          # پیام‌های قبلیِ خودم باعث مکث نشوند
    ar._usage_chat.clear(); ar._usage.clear()
    _before_q = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "پیام صف یک", out=False, mid=9850)
    await asyncio.sleep(0.05)
    await sim.fire(client, "پیام صف دو", out=False, mid=9851)
    await asyncio.sleep(0.05)
    await sim.fire(client, "پیام صف سه", out=False, mid=9852)
    await asyncio.sleep(9.0)
    _after_q = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    check("پیام‌های رسیده وسط پاسخ دور ریخته نمی‌شوند",
          _after_q - _before_q >= 2, f"{_before_q} → {_after_q}")
    check("صف پس از پردازش خالی می‌شود", not ar._queued.get(PEER))

    # ── ۱۵.۲۱) سقف مختص هر چت (چت پرحرف بقیه را ساکت نمی‌کند) ──
    other = 6060
    await db.upsert_ai_profile(uid, other, target_name="مخاطب جدا", enabled=True)
    await db.set_ai_auto_mode(uid, other, True)
    await _save_ai_config(uid, {"rpm": 1, "quota_wait_max": 0})
    await ar._load_config()
    ar._last_activity.clear()
    ar._usage_chat.clear(); ar._usage_day_chat.clear()
    ar._usage.clear(); ar._usage_day.clear()

    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "چت اول", out=False, mid=9820)
    await asyncio.sleep(1.8)
    check("چت اول با سقف ۱ جواب گرفت",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)
    ok_first, why_first = ar._quota_ok(PEER)
    ok_second, _ = ar._quota_ok(other)
    check("سقف چت اول پر شده ولی سقف چت دوم دست‌نخورده است",
          (not ok_first) and ok_second, f"{why_first} | چت دوم={ok_second}")

    ar._last_activity.clear()
    n_before = len([x for x in SENT if x[0] == "send" and x[1] != "me"])
    await sim.fire(client, "چت دوم", out=False, sender_id=other, chat=other, mid=9821)
    await asyncio.sleep(1.8)
    check("و چت دوم با این‌حال جواب می‌گیرد (سقف‌ها مشترک نیستند)",
          len([x for x in SENT if x[0] == "send" and x[1] != "me"]) > n_before)
    check("وضعیت مصرف هر چت جداگانه گزارش می‌شود",
          "این چت" in ar._quota_state(PEER) and "کل اکانت" in ar._quota_state())

    # سقف کل اکانت جدا از سقف چت است
    await sim.fire(client, ".ai سقف 30 400 3000", out=True, mid=9822)
    await asyncio.sleep(0.7)
    await ar._load_config()
    check("«.ai سقف» سقف همین چت را می‌گذارد",
          (int(ar._cfg.get("rpm")), int(ar._cfg.get("rph"))) == (30, 400),
          f"{ar._cfg.get('rpm')}/{ar._cfg.get('rph')}")
    await sim.fire(client, ".ai سقف کل 90 2000 12000", out=True, mid=9823)
    await asyncio.sleep(0.7)
    await ar._load_config()
    check("«.ai سقف کل» سقف کل اکانت را می‌گذارد",
          int(ar._cfg.get("global_rpm")) == 90 and int(ar._cfg.get("global_rpd")) == 12000,
          f"{ar._cfg.get('global_rpm')}/{ar._cfg.get('global_rpd')}")
    await _save_ai_config(uid, {"global_rpm": 60, "global_rph": 1200,
                                "global_rpd": 9000, "quota_wait_max": 45})
    await ar._load_config()

    # ── ۱۵.۲۲) منطقه‌ی زمانی ──
    await sim.fire(client, ".ai منطقه Europe/Berlin", out=True, mid=9830)
    await asyncio.sleep(0.7)
    await ar._load_config()
    check("«.ai منطقه» منطقه‌ی زمانی را عوض می‌کند",
          ar._cfg.get("timezone") == "Europe/Berlin", str(ar._cfg.get("timezone")))
    pv_x = len(PV)
    await sim.fire(client, ".ai منطقه Asia/Berlin", out=True, mid=9831)
    await asyncio.sleep(0.7)
    await ar._load_config()
    check("منطقه‌ی نامعتبر رد می‌شود و تنظیم قبلی می‌ماند",
          ar._cfg.get("timezone") == "Europe/Berlin"
          and "شناخته نشد" in "\n".join(t for _, t in PV[pv_x:]))
    await sim.fire(client, ".ai منطقه Asia/Tehran", out=True, mid=9832)
    await asyncio.sleep(0.7)
    await ar._load_config()

    # ── ۱۵.۲۳) استدلال مدل هرگز به‌عنوان پیام فرستاده نمی‌شود ──
    _rdata = {"choices": [{"message": {"content": "",
                                       "reasoning_content": "دارم فکر می‌کنم"},
                           "finish_reason": "length"}]}
    check("متن استدلال به‌عنوان پاسخ برگردانده نمی‌شود",
          ai_providers._openai_text(_rdata) == "")
    check("ولی وجودش تشخیص داده می‌شود",
          ai_providers._openai_reasoning(_rdata).startswith("دارم فکر"))

    # ── ۱۵.۲۴) کلیدهای داخلی در دیتابیس ذخیره نمی‌شوند ──
    ar._cfg["_chat_labels"] = {"1": "تست"}
    await ar._save_config()
    _feats = {f["feature_name"]: f for f in await db.get_features(uid)}
    _saved = _feats["ai_reply"].get("config_json") or {}
    if isinstance(_saved, str):
        import json as _json
        _saved = _json.loads(_saved)
    check("کش‌های داخلی در config_json ذخیره نمی‌شوند",
          "_chat_labels" not in _saved,
          str([k for k in _saved if str(k).startswith("_")])[:60])

    # ── ۱۵.۲۵) «.روشن هوش مصنوعی» قابلیت را روشن و همین چت را فعال می‌کند ──
    # (قبلاً فقط قابلیت روشن می‌شد و کاربر فکر می‌کرد «اینجا جواب نمی‌دهد»)
    from core.plugin_manager import disable_plugin as _disable
    await db.set_feature(uid, "ai_reply", False)
    await _disable(uid, "ai_reply")
    await asyncio.sleep(0.3)
    check("برای تست، قابلیت خاموش شد", "ai_reply" not in pm.get_active_plugins(uid))
    await db.upsert_ai_profile(uid, PEER, enabled=False)
    await sim.fire(client, ".روشن هوش مصنوعی", out=True, mid=9840)
    await asyncio.sleep(1.2)
    check("«.روشن هوش مصنوعی» قابلیت را روشن می‌کند",
          "ai_reply" in pm.get_active_plugins(uid))
    _prof_after = await db.get_ai_profile(uid, PEER)
    check("و همین چت را هم فعال می‌کند", bool(_prof_after and _prof_after.get("enabled")),
          str((_prof_after or {}).get("enabled")))

    # ── ۱۶) موتور: پاک‌سازی خروجی و رفتار ──
    check("مقدمه‌چینی حذف می‌شود", E.clean_reply("```\n(لبخند) سلام! چطوری؟\n```") == "سلام! چطوری؟")
    check("شکستن پیام کار می‌کند", len(E.split_messages("یک\nدو\nسه")) == 3)
    check("تأخیر تایپ در بازه‌ی انسانی است",
          1.0 <= E.typing_seconds("سلام خوبی") <= 5.0)
    check("مود ناشناس رد می‌شود", E.norm_mode("چیزعجیب") is None)
    check("جایگاه ناشناس رد می‌شود", E.norm_relationship("بی‌ربط") is None)

    print("\n── نتیجه ──")
    ok = sum(1 for _, o in RESULTS if o)
    print(f"{ok}/{len(RESULTS)} تست موفق")
    if cap.errors():
        print("خطاهای ثبت‌شده:")
        for e in cap.errors()[:5]:
            print("  !", e[:200])
    fails = [n for n, o in RESULTS if not o]
    if fails:
        print("ناموفق‌ها:", fails)
    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
