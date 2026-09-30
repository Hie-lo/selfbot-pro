"""
تست پلاگین قلب — قفل کردن انیمیشن‌های نسخه‌ی سالم.

نسخه‌ی مرجع: کامیت e9397bf (آخرین کامیت قلب) — همان طراحی‌ای که در
7c9cf24 تأیید شد: ‎.قلب3/.قلب5 بزرگ، ‎.قلب4 با فاصله، ‎.قلب2 حداکثر ۶ قلب.

هر انیمیشن باید دقیقاً همان فریم‌های نسخه‌ی مرجع را تولید کند؛ اگر کسی
(از جمله خودم) انیمیشن را عوض کند، «هش» زیر عوض می‌شود و تست می‌گیرد.

اجرا:
    PYTHONPATH=. python tests/test_heart.py
"""
import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plugins.heart as H          # noqa: E402
from core.digits import to_ascii   # noqa: E402

PASS, FAIL = [], []

# هشِ فریم‌های نسخه‌ی سالم (e9397bf) — از روی همان پیاده‌سازی گرفته شده
GOLD_HASH = {
    ".قلب": "cfc86c618668dc82",
    ".قلب2": "4869c9fee0a6fee7",
    ".قلب3": "d876f34a7fbd0a37",
    ".قلب4": "5bf00f41b8772b4e",
    ".قلب5": "8c4081e66ef13446",
    ".قلب6": "7ecfd69ea552e0eb",
}
ZWNJ = "\u200c"


def digest(frames) -> str:
    return hashlib.sha256("\x00".join(frames).encode()).hexdigest()[:16]


def check(name: str, ok: bool, extra: str = ""):
    (PASS if ok else FAIL).append(name)
    print(("✅ " if ok else "❌ ") + name + (f"   {extra}" if extra else ""))


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
        # _already_seen → GetPeerDialogs؛ در تست «خوانده نشده»
        self.calls += 1
        raise RuntimeError("no network in test")


async def run_cmd(plugin, client, cmd, **kw):
    client.sent.clear()
    await client.handlers["heart_cmd"](FakeEvent(cmd, **kw))
    return client.sent[-1].frames if client.sent else []


async def main():
    print("=" * 66)
    print("تست قلب — انیمیشن‌ها باید عیناً نسخه‌ی سالم (e9397bf) باشند")
    print("=" * 66)

    # ── ۰) تبدیل ارقام ──
    check("ارقام فارسی تبدیل می‌شوند", to_ascii("۲۳۴۵۶") == "23456", to_ascii("۲۳۴۵۶"))
    check("ارقام عربی هم تبدیل می‌شوند", to_ascii("٣٤٥") == "345")

    H.SEEN_TIMEOUT = 0.4   # پیش‌فرض واقعی ۱۲ ساعت است؛ در تست کوتاه می‌کنیم
    client = FakeClient()
    plugin = H.HeartPlugin(client, user_id=1)
    await plugin.start()

    orig_sleep = asyncio.sleep

    async def fast_sleep(t=0):
        await orig_sleep(0)

    asyncio.sleep = fast_sleep
    try:
        # ── ۱) هش: انیمیشن‌ها قفل‌شده روی نسخه‌ی سالم ──
        seqs = {}
        for cmd, want in GOLD_HASH.items():
            frames = await run_cmd(plugin, client, cmd)
            seqs[cmd] = frames
            got = digest(frames)
            check(f"{cmd} عیناً مثل نسخه‌ی سالم است", got == want,
                  f"{len(frames)} فریم · {got}")

        # ── ۲) ارقام فارسی: همان انیمیشن، نه انیمیشن ۱ ──
        for fa, en in [(".قلب۲", ".قلب2"), (".قلب۳", ".قلب3"),
                       (".قلب۴", ".قلب4"), (".قلب۵", ".قلب5"),
                       (".قلب۶", ".قلب6"), (".قلب ۳", ".قلب3")]:
            check(f"«{fa}» = «{en}»",
                  await run_cmd(plugin, client, fa) == seqs[en])

        # ── ۳) طراحی هر انیمیشن (نسخه‌ی سالم) ──
        p1, p2, p3, p4, p5, p6 = (seqs[c] for c in
                                  [".قلب", ".قلب2", ".قلب3", ".قلب4", ".قلب5", ".قلب6"])

        check("«.قلب»: تک‌قلب با فاصله‌های ۱ تا ۳ (۱۲ رنگ × ۳ دور)",
              all(f.replace(ZWNJ, "").strip() in H.HEARTS for f in p1)
              and len(p1) == 38
              and {f.replace(ZWNJ, "").strip() for f in p1} == set(H.HEARTS),
              f"{len({f.replace(ZWNJ, '').strip() for f in p1})} رنگ از {len(H.HEARTS)}")
        check("«.قلب2»: همه‌ی مراحل کوچک (نیم‌فاصله) و حداکثر ۶ قلب",
              all(ZWNJ in f for f in p2)
              and max(f.count(ZWNJ) for f in p2) == H.GROW_MAX,
              f"حداکثر قلب در فریم: {max(f.count(ZWNJ) for f in p2)}")
        check("«.قلب3»: قلب بزرگ (بدون نیم‌فاصله) و رفت‌وبرگشت رنگ",
              all(ZWNJ not in f for f in p3) and p3[:1] == ["🤍"]
              and p3[len(H.HEARTS) + 14] in H.HEARTS,     # پیمایش معکوس
              f"نمونه: {p3[:3]}")
        check("«.قلب4»: حرکت با فاصله (۰ تا ۳ از دو طرف)",
              all(ZWNJ not in f and f.strip() in H.HEARTS for f in p4)
              and any(f.startswith("   ") for f in p4)
              and any(f.startswith(" ") and not f.startswith("  ") for f in p4)
              and p4[-1] == "  ❤️  ",
              f"نمونه: {[repr(f) for f in p4[:3]]}")
        check("«.قلب5»: فقط قلب‌های صورتی خاص",
              {f for f in p5} <= set(H.HEARTS_PINK) and len(p5) == 25,
              f"فریم‌ها: {sorted(set(p5))}")
        check("«.قلب6»: پنجره‌ی ۳تایی بدون فاصله/نیم‌فاصله",
              all(ZWNJ not in f for f in p6)
              and max(len(f.replace("\ufe0f", "").replace("", "")) for f in p6) >= 3,
              f"نمونه: {p6[:3]}")

        # ── ۴) هیچ دو انیمیشنی مثل هم نیست ──
        dup = [(a, b) for i, a in enumerate(seqs) for b in list(seqs)[i + 1:]
               if seqs[a] == seqs[b]]
        check("هیچ دو انیمیشنی یکسان نیستند", not dup, str(dup))

        # ── ۵) رفتار پیوی/گروه/ذخیره‌شده ──
        check("پی‌ویِ سین‌نشده انیمیشن نمی‌شود (فقط فریم اول)",
              len(await run_cmd(plugin, client, ".قلب", chat_id=999, private=True)) == 1)
        check("«پیام‌های ذخیره‌شده» فوری انیمیشن می‌شود (قبلاً هیچ‌وقت نمی‌شد)",
              len(await run_cmd(plugin, client, ".قلب", chat_id=1, private=True)) > 5)
        check("گروه فوری انیمیشن می‌شود",
              len(await run_cmd(plugin, client, ".قلب2", chat_id=-100123, private=False)) > 3)
        check("پیام غیرخروجی دست‌کاری نمی‌شود",
              await run_cmd(plugin, client, ".قلب", out=False) == [])
    finally:
        asyncio.sleep = orig_sleep

    print("\n" + "=" * 66)
    print(f"نتیجه: {len(PASS)}/{len(PASS) + len(FAIL)} چک موفق")
    for f in FAIL:
        print("  ❌", f)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
