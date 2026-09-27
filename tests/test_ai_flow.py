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

    # ── ۸) هیچ‌وقت بی‌جواب نمی‌ماند ──
    ai_providers.chat = fake_chat(fail=True)
    p = len(PV)
    await sim.fire(client, "کجایی؟", out=False)
    await asyncio.sleep(1.0)
    drafts = [t for _, t in PV[p:] if "پیشنهاد پاسخ" in t]
    check("با خطای همه‌ی سرویس‌ها، باز هم پیش‌نویس ساخته شد", bool(drafts))
    check("به من هشدار داده شد که سرویس خطا داد",
          any("هیچ سرویس AI جواب نداد" in t for _, t in PV[p:]))

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
