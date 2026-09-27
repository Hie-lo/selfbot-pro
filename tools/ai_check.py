#!/usr/bin/env python3
"""
🤖 تست واقعی سرویس‌های AI — دقیقاً می‌گوید کدام سرویس چرا جواب نمی‌دهد.

اجرا (از پوشه‌ی پروژه، با venv فعال):
    python tools/ai_check.py                 # تست همه‌ی سرویس‌ها
    python tools/ai_check.py --models        # لیست مدل‌های موجود سرویس
    python tools/ai_check.py --prompt "سلام" # با متن دلخواه
    python tools/ai_check.py --model "X"     # تست با مدل خاص

چه چیزی را نشان می‌دهد:
  • سرویس‌هایی که از .env (یا دیتابیس) خوانده شده‌اند — با کلید ماسک‌شده
  • اعتبار آدرس (همان مشکل رایج کپی‌پیست: [https://…](https://…) )
  • نتیجه‌ی واقعی درخواست: کد HTTP، زمان پاسخ و متن پاسخ
  • ترجمه‌ی خطا به کارِ عملی: 401 کلید اشتباه، 402 اعتبار تمام،
    404 مدل ناموجود، 429 سقف مصرف، InvalidURL آدرس خراب، Timeout شبکه
"""

import asyncio
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# .env را مستقیم می‌خوانیم (بدون وابستگی به config، تا ابزار همیشه اجرا شود)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

GREEN, RED, YELLOW, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""

FIXES = {
    400: "درخواست نامعتبر — معمولاً نام مدل یا ساختار پیام مشکل دارد.",
    401: "کلید API اشتباه/باطل است. کلید را از پنل سرویس دوباره بگیر.",
    402: "اعتبار حساب تمام شده (OpenRouter برای مدل‌های پولی) — مدل :free انتخاب کن.",
    403: "دسترسی به این مدل/منطقه بسته است.",
    404: "این نام مدل در سرویس وجود ندارد. با --models لیست درست را ببین.",
    408: "سرویس دیر جواب داد؛ دوباره امتحان کن.",
    429: "سقف مصرف (Rate limit). مدل‌های رایگان OpenRouter: ۲۰ درخواست در دقیقه "
         "و ۵۰ در روز. چند دقیقه صبر کن یا سرویس دوم اضافه کن.",
    500: "خطای داخلی سرویس — چند دقیقه بعد دوباره تست کن.",
    502: "سرویس واسط خراب است (Bad gateway) — سرویس دیگر امتحان کن.",
    503: "سرویس موقتاً در دسترس نیست — سرویس دیگر امتحان کن.",
}


def line(t=""):
    print(t)


def header(t):
    line(f"\n{DIM}── {t} ──{RESET}")


async def main():
    import httpx
    from core import ai_providers as ap

    prompt = "سلام، خوبی؟ چیکار می‌کنی؟"
    if "--prompt" in sys.argv:
        prompt = sys.argv[sys.argv.index("--prompt") + 1]
    only_model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    list_models = "--models" in sys.argv

    line(f"{DIM}🤖 تست سرویس‌های AI — پوشه: {ROOT}{RESET}")

    header("۱) خواندن تنظیمات")
    providers = ap.load_providers()
    if not providers:
        line(f"{RED}❌ هیچ سرویسی تنظیم نشده.{RESET}")
        line("   در .env این سه خط را پر کن (نمونه در .env.example):")
        line("   AI_BASE_URL=https://openrouter.ai/api/v1")
        line("   AI_API_KEY=sk-or-...")
        line("   AI_MODEL=google/gemma-4-26b-a4b-it:free")
        return 1

    src = {"config": "تنظیمات ذخیره‌شده در دیتابیس", "env-json": "AI_PROVIDERS در .env",
           "env": "متغیرهای ساده‌ی .env", "none": "—"}.get(ap.PROVIDER_SOURCE, ap.PROVIDER_SOURCE)
    line(f"  منبع تنظیمات: {src}")

    bad_urls = 0
    for p in providers:
        good = ap.looks_like_url(p.base_url)
        if not good:
            bad_urls += 1
        mark = f"{GREEN}✅{RESET}" if good else f"{RED}❌{RESET}"
        line(f"  {mark} {p.name}  ({p.kind})")
        line(f"     آدرس : {p.base_url}")
        line(f"     مدل  : {p.model or '—'}")
        line(f"     کلید : {ap.mask_key(p.keys[0]) if p.keys else '❌ ندارد'}"
             f"  ({len(p.keys)} کلید)")
        if not good:
            line(f"     {RED}↳ آدرس نامعتبر است! باید با https:// شروع شود و "
                 f"براکت/لینک مارک‌داون نداشته باشد.{RESET}")
            line(f"     ↳ متن پاک‌شده: {ap.clean_value(p.base_url)!r}")

    if only_model:
        for p in providers:
            p.model = ap.clean_value(only_model)

    if bad_urls:
        header("نتیجه")
        line(f"{RED}آدرس سرویس خراب است — همه‌ی درخواست‌ها شکست می‌خورند.{RESET}")
        line("داخل .env مقدار AI_BASE_URL را طوری بنویس که فقط خود آدرس باشد:")
        line("  ✅ AI_BASE_URL=https://openrouter.ai/api/v1")
        line("  ❌ AI_BASE_URL=[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)")
        return 1

    if list_models:
        header("۲) لیست مدل‌های سرویس")
        for p in providers:
            url = (f"{p.base_url}/models" if p.kind != "gemini"
                   else f"{p.base_url}/models?key={p.keys[0] if p.keys else ''}")
            headers = {}
            if p.kind != "gemini" and p.keys:
                headers["Authorization"] = f"Bearer {p.keys[0]}"
            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.get(url, headers=headers)
                if r.status_code != 200:
                    line(f"  {RED}❌ {p.name}: HTTP {r.status_code}{RESET} "
                         f"{(FIXES.get(r.status_code) or r.text[:120])}")
                    continue
                data = r.json().get("data", [])
                ids = [m.get("id") for m in data if m.get("id")]
                free = [i for i in ids if str(i).endswith(":free")]
                line(f"  ✅ {p.name}: {len(ids)} مدل، {len(free)} رایگان")
                for i in (free or ids)[:15]:
                    line(f"     • {i}")
            except Exception as e:
                line(f"  {RED}❌ {p.name}: {type(e).__name__}: {e}{RESET}")
        line("\n💡 یکی از همین شناسه‌ها را در AI_MODEL بگذار.")
        return 0

    header("۲) تست واقعی درخواست")
    msgs = [
        {"role": "system", "content":
            "تو یک آدم واقعی در چت تلگرامی هستی. کوتاه و محاوره‌ای جواب بده."},
        {"role": "user", "content": prompt},
    ]
    any_ok = False
    for p in providers:
        line(f"\n  ▶ {p.name} — {p.model}")
        t0 = time.time()
        try:
            text = await ap._call_provider(p, p.keys[0] if p.keys else "", msgs, 120, 0.8)
        except Exception as e:
            text, err = None, f"{type(e).__name__}: {e}"
        else:
            err = getattr(p, "last_error", "") or ""
        dt = time.time() - t0
        if text:
            any_ok = True
            line(f"    {GREEN}✅ پاسخ در {dt:.1f} ثانیه:{RESET}")
            line(f"    «{text.strip()[:300]}»")
            continue

        line(f"    {RED}❌ ناموفق در {dt:.1f} ثانیه{RESET}")
        line(f"    خطای ثبت‌شده: {err or '(بدون خطا — پاسخ خالی)'}")

        code = None
        for c in FIXES:
            if err.startswith(f"HTTP {c}") or f"HTTP {c}:" in err:
                code = c
                break
        if code and code in FIXES:
            line(f"    {YELLOW}💡 {FIXES[code]}{RESET}")
        elif "InvalidURL" in err or "نامعتبر" in err:
            line(f"    {YELLOW}💡 آدرس سرویس خراب است — دوباره در .env بنویس "
                 f"(بدون براکت و لینک مارک‌داون).{RESET}")
        elif "ConnectError" in err or "TLS" in err or "Timeout" in err:
            line(f"    {YELLOW}💡 مشکل شبکه/فیلترینگ. اگر سرور خارج است، "
                 f"آدرس و پورت را چک کن؛ در ایران پروکسی لازم است.{RESET}")
        elif "خالی" in err:
            line(f"    {YELLOW}💡 مدل جواب خالی داد — مدل دیگری انتخاب کن "
                 f"(مدل‌های reasoning گاهی متن خالی می‌دهند).{RESET}")

    header("نتیجه")
    if any_ok:
        line(f"{GREEN}✅ حداقل یک سرویس سالم است — قابلیت AI آماده‌ی استفاده است.{RESET}")
        return 0
    line(f"{RED}❌ هیچ سرویسی جواب نداد.{RESET}")
    line("  ۱) آدرس/کلید/مدل را از .env چک کن (بالا نشان داده شد)")
    line("  ۲) با --models یک مدل معتبر رایگان (:free) ببین")
    line("  ۳) اگر 429 است، سقف روزانه‌ی مدل رایگان پر شده — سرویس دوم اضافه کن")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nمتوقف شد.")
