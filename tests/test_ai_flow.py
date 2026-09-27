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
from ai_sim import ME, PEER, PV, SENT                   # noqa: E402

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
    from bot.ai_panel import _ai_config
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
    from bot.ai_panel import contacts_kb, modes_kb, tones_kb
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
