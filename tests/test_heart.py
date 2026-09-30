"""
تست پلاگین قلب — باگ «همه‌ی انیمیشن‌ها یکسان» و پوشش ارقام فارسی.

اجرا:
    PYTHONPATH=. python tests/test_heart.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plugins.heart as H          # noqa: E402
from core.digits import to_ascii   # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, extra: str = ""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


# ── ماکت‌های ساده (بدون تلگرام واقعی) ──
class FakeMsg:
    def __init__(self, first):
        self.id = 1
        self.chat_id = 111
        self.frames = [first]

    async def edit(self, text, **kw):
        self.frames.append(text)


class FakeEvent:
    def __init__(self, text, chat_id=111, private=False, out=True):
        self.raw_text = text
        self.out = out
        self.is_private = private
        self.is_reply = False
        self.chat_id = chat_id
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeClient:
    def __init__(self):
        self.sent = []
        self.handlers = {}
        self.calls = 0

    def add_event_handler(self, cb, ev):
        self.handlers[cb.__name__] = cb

    def remove_event_handler(self, cb, ev):
        pass

    async def send_message(self, chat_id, text, reply_to=None):
        m = FakeMsg(text)
        self.sent.append(m)
        return m

    async def __call__(self, request):
        # _already_seen → GetPeerDialogs؛ این‌جا همیشه «خوانده نشده»
        self.calls += 1
        raise RuntimeError("no network in test")


async def run_cmd(plugin, client, cmd, **kw):
    ev = FakeEvent(cmd, **kw)
    client.sent.clear()
    await client.handlers["heart_cmd"](ev)
    return client.sent[-1].frames if client.sent else []


async def main():
    print("=" * 60)
    print("تست قلب: ارقام فارسی + تفاوت انیمیشن‌ها")
    print("=" * 60)

    # ── ۱) خودِ تبدیل ارقام ──
    check("ارقام فارسی تبدیل می‌شوند", to_ascii("۲۳۴۵۶") == "23456", to_ascii("۲۳۴۵۶"))
    check("ارقام عربی هم تبدیل می‌شوند", to_ascii("٣٤٥") == "345", to_ascii("٣٤٥"))
    check("متن غیرعددی دست‌نخورده می‌ماند", to_ascii(".قلب") == ".قلب")

    H.SEEN_TIMEOUT = 0.4   # پیش‌فرض واقعی ۱۲ ساعت است؛ در تست کوتاه می‌کنیم

    client = FakeClient()
    plugin = H.HeartPlugin(client, user_id=1)
    await plugin.start()

    # سرعت انیمیشن‌ها صفر تا تست طول نکشد
    orig_sleep = asyncio.sleep

    async def fast_sleep(t=0):
        await orig_sleep(0)

    asyncio.sleep = fast_sleep
    try:
        # ── ۲) باگ اصلی: .قلب۳ باید همان .قلب3 باشد (نه انیمیشن ۱) ──
        base = await run_cmd(plugin, client, ".قلب")
        three_fa = await run_cmd(plugin, client, ".قلب۳")
        three_en = await run_cmd(plugin, client, ".قلب3")
        check("«.قلب۳» و «.قلب3» یکی هستند", three_fa == three_en,
              f"{len(three_fa)} ویرایش")
        check("«.قلب۳» دیگر روی انیمیشن ۱ نمی‌افتد", three_fa != base,
              f"۳: {len(three_fa)} ویرایش · ۱: {len(base)} ویرایش")

        # فاصله هم مشکلی نباشد
        check("«.قلب ۲» (با فاصله، رقم فارسی) هم کار می‌کند",
              await run_cmd(plugin, client, ".قلب ۲") == await run_cmd(plugin, client, ".قلب2"))

        # ── ۳) هر ۶ انیمیشن باید واقعاً فرق داشته باشند ──
        seqs = {}
        for cmd in [".قلب", ".قلب2", ".قلب3", ".قلب4", ".قلب5", ".قلب6"]:
            seqs[cmd] = await run_cmd(plugin, client, cmd)
        for cmd, fr in seqs.items():
            check(f"{cmd}: انیمیشن واقعاً اجرا می‌شود",
                  len(fr) >= 5 and len(set(fr)) >= 4,
                  f"{len(fr)} ویرایش / {len(set(fr))} یکتا")

        names = list(seqs)
        dup = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
               if seqs[a] == seqs[b]]
        check("هیچ دو انیمیشنی عین هم نیستند", not dup, str(dup))

        # فریم اول هم باید متفاوت باشد تا کاربر اشتباه نکند
        firsts = {c: seqs[c][0] for c in names if seqs[c]}
        check("فریم اول انیمیشن‌ها یکسان نیست", len(set(firsts.values())) == len(firsts),
              str({k: v for k, v in list(firsts.items())[:3]}))

        # ── ۴) نشانه‌های ظاهری هر انیمیشن ──
        zwnj, blank = "\u200c", "\u2800"
        p3 = seqs[".قلب3"]
        check("«.قلب3» ضربان دارد (بزرگ ↔ کوچک)", any(zwnj in f for f in p3),
              "نمونه: " + repr([f for f in p3 if zwnj in f][:2]))
        p4 = seqs[".قلب4"]
        check("«.قلب4» حرکت دارد (فاصله‌ی بریل، نه فاصله‌ی معمولی)",
              any(f.startswith(blank) for f in p4) and not any(f.startswith(" ") for f in p4),
              "نمونه: " + repr([f for f in p4 if f.startswith(blank)][:2]))

        # ── ۵) شماره‌ی نامعتبر → انیمیشن اصلی (بدون خطا) ──
        check("«.قلب۹» با انیمیشن اصلی اجرا می‌شود (بدون خطا)",
              await run_cmd(plugin, client, ".قلب۹") == base)

        # ── ۶) پی‌وی با سین‌نشدن → انیمیشن شروع نشود (رفتار موردتأیید) ──
        frames_priv = await run_cmd(plugin, client, ".قلب", chat_id=999, private=True)
        check("پی‌ویِ سین‌نشده انیمیشن نمی‌شود (فقط فریم اول)", len(frames_priv) == 1,
              f"{len(frames_priv)} فریم")

        # ── ۷) پیام‌های ذخیره‌شده → فوری (قبلاً هیچ‌وقت انیمیشن نمی‌شد) ──
        frames_saved = await run_cmd(plugin, client, ".قلب", chat_id=1, private=True)
        check("«پیام‌های ذخیره‌شده» فوری انیمیشن می‌شود", len(frames_saved) > 5,
              f"{len(frames_saved)} فریم")

        # ── ۸) گروه → فوری ──
        frames_group = await run_cmd(plugin, client, ".قلب2", chat_id=-100123, private=False)
        check("گروه فوری انیمیشن می‌شود", len(frames_group) > 3, f"{len(frames_group)} فریم")

        # ── ۹) پیام غیرخروجی (text خودم توسط خودم؟) نادیده گرفته شود ──
        frames_in = await run_cmd(plugin, client, ".قلب", out=False)
        check("پیام غیرخروجی دست‌کاری نمی‌شود", frames_in == [])
    finally:
        asyncio.sleep = orig_sleep

    print("\n" + "=" * 60)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    if FAIL:
        print("ناموفق‌ها:")
        for f in FAIL:
            print("  ❌", f)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
