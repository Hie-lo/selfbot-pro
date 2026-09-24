"""
پلاگین قلب متحرک — چند انیمیشن
کامندها: .قلب / .قلب 2 / .قلب2 / .قلب 3 ...
• .قلب   : انیمیشن ۱۲ قلب با حرکت (اصلی)
• .قلب2  : زنجیره‌ای تا ۶ قلب و بعد جابه‌جا (🖤 → 🖤💜 → ... ) — همه کوچیک (نیم‌فاصله بعد از هر قلب)
• .قلب3  : ضربان — تک‌قلب بزرگ با تغییر رنگ
• .قلب4  : نفس — قلب با فاصله تپنده
• .قلب5  : قلب‌های صورتیِ خاص دونه‌دونه عوض میشن (💕💞💓💗💘💝)
• (غیرفعال) .قلب بساز        : قلب بزرگ — ردیف‌به‌ردیف از بالا، دور قرمز، داخل حلقه‌های رنگی متقارن (موج سریع)
• .قلب بساز 2      : قلب یک‌رنگ؛ سریع ساخته میشه و کلش با هم همه‌ی رنگ‌ها رو می‌گیره
• .قلب بساز 3      : دور قرمز، داخل سفید که دونه‌دونه صورتی میشه
• .قلب بساز ‹رنگ›  : مثل 3 با رنگ دلخواه (قرمز، آبی، سبز، ...)
• .قلب6  : پنجره‌ی ۳تایی — 🩷 → 🩷❤️ → 🩷❤️🧡 → ❤️🧡💛 → ... (بدون فاصله)
اگه ریپلای باشه، روی همون پیام ریپلای میشه
ویرایش فقط بعد از سین زدنِ طرف مقابل شروع میشه (فقط پی‌وی، تا 120ث)
"""
import asyncio
import time
from collections import deque
from telethon import events
from telethon.errors import MessageNotModifiedError, FloodWaitError
from plugins.base import BasePlugin

HEARTS = ["❤️", "🩷", "🧡", "💛", "💚", "🩵", "💙", "💜", "🖤", "🤍", "🩶", "💗"]
# برای زنجیره‌ای — همون ترتیب مثال شما
HEARTS_GROW = ["🖤", "💜", "💙", "🩵", "💚", "💛", "🧡", "🩷", "❤️", "🤍", "🩶"]
GROW_MAX = 6   # حداکثر تعداد قلب در .قلب2
# نیم‌فاصله برای کوچیک کردن تک‌اموجی
ZWNJ = "\u200c"
# .قلب5 — فقط قلب‌های صورتیِ خاص، به همین ترتیب
HEARTS_PINK = ["💕", "💞", "💓", "💗", "💘", "💝"]
# .قلب6 — همه‌ی قلب‌های رنگی ساده (بدون قلب‌های صورتیِ خاص)
HEARTS_SLIDE = ["🩷", "❤️", "🧡", "💛", "💚", "🩵", "💙", "💜", "🖤", "🩶", "🤍"]


def small(hearts) -> str:
    """نیم‌فاصله بعد از هر قلب: تلگرام پیامِ فقط-اموجی (۱ تا ۳ تا) را بزرگ
    نشان می‌دهد؛ با نیم‌فاصله پیام دیگر «فقط اموجی» نیست و همه کوچیک می‌مونن"""
    return "".join(h + ZWNJ for h in hearts)


def slide_frames(hearts, width=3, rounds=3):
    """🩷 → 🩷❤️ → 🩷❤️🧡 → ❤️🧡💛 → ... (چرخشی، چند دور)"""
    n = len(hearts)
    frames = ["".join(hearts[:i]) for i in range(1, width)]
    for start in range(n * rounds):
        frames.append("".join(hearts[(start + k) % n] for k in range(width)))
    return frames


# ── .قلب بساز ──
# O = دور (قرمز) · i = داخل (رنگی) · . = پس‌زمینه
BIG_HEART_SHAPE = [
    "..OOO.OOO..",
    ".OiiiOiiiO.",
    "OiiiiiiiiiO",
    "OiiiiiiiiiO",
    "OiiiiiiiiiO",
    ".OiiiiiiiO.",
    "..OiiiiiO..",
    "...OiiiO...",
    "....OiO....",
    ".....O.....",
]
BIG_HEART_EDGE = "❤️"
# بیرون قلب خالی است؛ فقط جای خالیِ «سمت چپ» لازم است (سمت راست حذف می‌شود).
# فاصله‌ها را تلگرام ادغام می‌کند → «بریل خالی» (U+2800) که فاصله حساب
# نمی‌شود. عرض اموجی در تلگرام ≈ ۱.۱۷em و بریل کمی کمتر از نصف آن است:
# با ۲ و بعد ۲.۲۵ بریل برای هر خانه، ردیف‌های پایین هنوز کمی به چپ می‌رفتند
# (اسکرین‌شات تلگرام دسکتاپ: اموجی ≈۲۰px، نوک پایین ≈۵–۸px چپ) → ۲.۴۵
BLANK = "\u2800"
BLANK_PER_CELL = 2.45
# رنگ حلقه‌های داخلی از بیرون به مرکز (متقارن، چون بر اساس فاصله از دور است)
BIG_HEART_RINGS = ["🩷", "🧡", "💛", "💚"]
# همه‌ی رنگ‌ها (برای .قلب بساز 2 و .قلب بساز ‹رنگ›)
HEART_COLORS = {
    "قرمز": "❤️", "صورتی": "🩷", "نارنجی": "🧡", "زرد": "💛", "سبز": "💚",
    "آبی روشن": "🩵", "فیروزه ای": "🩵", "آبی": "💙", "بنفش": "💜",
    "قهوه ای": "🤎", "مشکی": "🖤", "سیاه": "🖤", "طوسی": "🩶", "خاکستری": "🩶",
    "سفید": "🤍",
}
COLOR_CYCLE = ["❤️", "🧡", "💛", "💚", "🩵", "💙", "💜", "🩷", "🤎", "🖤", "🩶", "🤍"]
# رنگ داخل قبل از پر شدن (برای سفید، خاکستری تا پر شدن دیده شود)
FILL_BASE = "🤍"
FILL_BASE_FOR_WHITE = "🩶"


def normalize_color(text: str) -> str:
    t = (text or "").strip().replace("ي", "ی").replace("ك", "ک")
    t = t.replace("\u200c", " ").replace("ابی", "آبی")
    return " ".join(t.split())


def _ring_depth(shape) -> dict:
    """فاصله‌ی هر خانه‌ی داخلی از دور قلب (۱ = کنار دور)"""
    h, w = len(shape), len(shape[0])
    depth, q = {}, deque()
    for r in range(h):
        for c in range(w):
            if shape[r][c] == "O":
                depth[(r, c)] = 0
                q.append((r, c))
    while q:
        r, c = q.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (r + dr, c + dc)
            if (0 <= n[0] < h and 0 <= n[1] < w and shape[n[0]][n[1]] == "i"
                    and n not in depth):
                depth[n] = depth[(r, c)] + 1
                q.append(n)
    return depth


_BIG_DEPTH = _ring_depth(BIG_HEART_SHAPE)
_W = len(BIG_HEART_SHAPE[0])
# خانه‌های داخلی به ترتیب پر شدن: از بالا به پایین، هر بار یک جفتِ قرینه
_FILL_ORDER = []
for _r, _row in enumerate(BIG_HEART_SHAPE):
    for _c in range((_W + 1) // 2):
        if _row[_c] == "i":
            _FILL_ORDER.append(((_r, _c), (_r, _W - 1 - _c)))


def render_heart(cell, rows: int | None = None) -> str:
    """
    رسم قلب بزرگ؛ cell(r, c, kind) اموجی هر خانه را می‌دهد (kind: 'O' دور / 'i' داخل).
    عرض جای خالی از «ستون واقعی» حساب می‌شود (نه جمع گردشده‌ی تکه‌ها)
    تا خطای گرد کردن روی هم جمع نشود.
    """
    n = len(BIG_HEART_SHAPE) if rows is None else rows
    lines = []
    for r in range(n):
        row = BIG_HEART_SHAPE[r].rstrip(".")      # بیرونِ سمت راست لازم نیست
        out = [BLANK]                             # اول خط: تلگرام trim نکند
        x = 0.0                                   # مکان فعلی (به واحد عرض بریل)
        for c, ch in enumerate(row):
            if ch == ".":
                continue
            target = c * BLANK_PER_CELL           # جای درست این ستون
            need = int(target - x + 0.5)
            if need > 0:
                out.append(BLANK * need)
                x += need
            out.append(cell(r, c, ch))
            x += BLANK_PER_CELL                   # عرض یک اموجی
        lines.append("".join(out))
    return "\n".join(lines)


def _rings(shift: int = 0):
    rings = BIG_HEART_RINGS

    def cell(r, c, kind):
        if kind == "O":
            return BIG_HEART_EDGE
        d = min(_BIG_DEPTH[(r, c)], len(rings))
        return rings[(d - 1 + shift) % len(rings)]
    return cell


def big_heart(rows: int | None = None, shift: int = 0) -> str:
    """قلب اصلی: دور قرمز، داخل حلقه‌های رنگی (shift = چرخش رنگ‌ها)"""
    return render_heart(_rings(shift), rows)


def solid_heart(color: str, rows: int | None = None) -> str:
    return render_heart(lambda r, c, k: color, rows)


# سرعت‌ها (ثانیه)
# محدودیت ویرایش تلگرام (FloodWait) سمت سرور است و دور زدنی نیست؛ پس تعداد
# ویرایش‌ها کم نگه داشته می‌شود (هر انیمیشن ≈۱۳ تا ۱۷ ویرایش) و در صورت
# FloodWait سرعت همان اکانت موقتاً کم می‌شود (EditPacer).
BUILD_DELAY = 0.45       # ساخته شدن هر ردیف (حالت اصلی)
FAST_BUILD_DELAY = 0.35  # ساخته شدن سریع (۲ ردیف در هر ویرایش)
FAST_BUILD_ROWS = 2
WAVE_DELAY = 0.3         # موج رنگی داخل
WAVE_CYCLES = 2
COLOR_DELAY = 0.4        # عوض شدن رنگِ کلِ قلب
FILL_DELAY = 0.35        # پر شدن دونه‌دونه
FILL_PAIRS_PER_EDIT = 3  # چند جفتِ قرینه در هر ویرایش


def _fast_rows():
    n = len(BIG_HEART_SHAPE)
    ks = list(range(FAST_BUILD_ROWS, n + 1, FAST_BUILD_ROWS))
    if ks[-1] != n:
        ks.append(n)
    return ks


def big_heart_frames() -> list:
    """.قلب بساز — ردیف‌به‌ردیف از بالا → موج رنگی داخل → قلب نهایی"""
    frames = [(big_heart(rows=k), BUILD_DELAY) for k in range(1, len(BIG_HEART_SHAPE) + 1)]
    n = len(BIG_HEART_RINGS)
    for k in range(1, n * WAVE_CYCLES):
        frames.append((big_heart(shift=k % n), WAVE_DELAY))
    frames.append((big_heart(), 0))
    return frames


def solid_frames() -> list:
    """.قلب بساز 2 — قلب یک‌رنگ قرمز سریع ساخته میشه، بعد کلش با هم همه‌ی رنگ‌ها رو می‌گیره"""
    frames = [(solid_heart("❤️", rows=k), FAST_BUILD_DELAY) for k in _fast_rows()]
    for color in COLOR_CYCLE[1:] + ["❤️"]:
        frames.append((solid_heart(color), COLOR_DELAY))
    return frames


def fill_frames(color: str) -> list:
    """
    .قلب بساز 3 / .قلب بساز ‹رنگ› — دور قرمز، داخل سفید سریع ساخته میشه،
    بعد داخل دونه‌دونه (جفت‌های قرینه، از بالا) به رنگ انتخابی پر میشه
    """
    base = FILL_BASE_FOR_WHITE if color == FILL_BASE else FILL_BASE
    filled: set = set()

    def cell(r, c, kind):
        if kind == "O":
            return BIG_HEART_EDGE
        return color if (r, c) in filled else base

    frames = [(render_heart(cell, rows=k), FAST_BUILD_DELAY) for k in _fast_rows()]
    for i in range(0, len(_FILL_ORDER), FILL_PAIRS_PER_EDIT):
        for pair in _FILL_ORDER[i:i + FILL_PAIRS_PER_EDIT]:
            filled.update(pair)
        frames.append((render_heart(cell), FILL_DELAY))
    return frames


class EditPacer:
    """
    تنظیم خودکار سرعت با محدودیت واقعی تلگرام (که عددش منتشر نشده):
    بعد از هر FloodWait، مکث بین ویرایش‌های همین اکانت دو برابر می‌شود
    (حداکثر ×۴) و بعد از ۱۰ دقیقه بدون FloodWait به حالت عادی برمی‌گردد.
    """
    COOLDOWN = 600
    MAX_FACTOR = 4.0

    def __init__(self):
        self.factor = 1.0
        self.until = 0.0

    def delay(self, base: float) -> float:
        if self.factor > 1 and time.monotonic() > self.until:
            self.factor = 1.0
        return base * self.factor

    def flood(self):
        self.factor = min(self.MAX_FACTOR, self.factor * 2)
        self.until = time.monotonic() + self.COOLDOWN


async def play_frames(msg, frames, pacer: EditPacer) -> None:
    """
    پخش فریم‌ها. با FloodWait: صبر، بعد مستقیم «قلب نهایی» (نه گیر کردن
    وسط انیمیشن) و کند شدن انیمیشن‌های بعدی همین اکانت.
    """
    last = frames[-1][0]
    for fr, delay in frames[1:]:
        try:
            await msg.edit(fr)
        except MessageNotModifiedError:
            pass
        except FloodWaitError as e:
            pacer.flood()
            await asyncio.sleep(e.seconds + 1)
            if fr != last:
                try:
                    await msg.edit(last)
                except Exception:
                    pass
            return
        except Exception:
            return
        if delay:
            await asyncio.sleep(pacer.delay(delay))


def build_frames(arg: str) -> list | None:
    """انتخاب انیمیشن از روی آرگومان؛ None = رنگ ناشناخته"""
    arg = normalize_color(arg)
    if arg in ("", "1"):
        return big_heart_frames()
    if arg == "2":
        return solid_frames()
    if arg == "3":
        return fill_frames("🩷")
    color = HEART_COLORS.get(arg)
    return fill_frames(color) if color else None


def _split(frame: str):
    """جدا کردن قلب‌های یک فریم (❤️ دو کاراکتر است)"""
    out = []
    for ch in frame:
        if ch == "\ufe0f" and out:
            out[-1] += ch
        else:
            out.append(ch)
    return out


def first_frame(num) -> str:
    """اولین فریم هر انیمیشن (هم پیام عادی هم ریپلای)"""
    if num == "2":
        return small(HEARTS_GROW[:1])
    if num == "3":
        return "🤍"
    if num == "4":
        return "❤️"
    if num == "5":
        return HEARTS_PINK[0]
    if num == "6":
        return HEARTS_SLIDE[0]
    return ZWNJ + HEARTS[0]

class HeartPlugin(BasePlugin):
    name = "heart_animation"
    description = "قلب متحرک"
    always_on = True

    async def start(self):

        async def wait_for_seen(msg, event):
            # فقط پی‌وی منتظر سین بمون
            if not event.is_private:
                return
            try:
                read_future = asyncio.get_event_loop().create_future()
                async def _on_read(read_event):
                    try:
                        chat_ok = False
                        try:
                            chat_ok = (read_event.chat_id == msg.chat_id)
                        except Exception:
                            chat_ok = (getattr(read_event, 'chat_id', None) == event.chat_id)
                        is_outbox = getattr(read_event, 'is_outbox', True)
                        max_id = getattr(read_event, 'max_id', 0) or 0
                        if chat_ok and is_outbox and max_id >= msg.id:
                            if not read_future.done():
                                read_future.set_result(True)
                        elif chat_ok and is_outbox and max_id == 0:
                            if not read_future.done():
                                read_future.set_result(True)
                    except Exception:
                        pass
                self.client.add_event_handler(_on_read, events.MessageRead)
                try:
                    await asyncio.wait_for(read_future, timeout=120)
                except asyncio.TimeoutError:
                    pass
                finally:
                    try:
                        self.client.remove_event_handler(_on_read, events.MessageRead)
                    except Exception:
                        pass
            except Exception:
                pass

        async def anim_original(msg):
            # انیمیشن اصلی — 12 قلب با فاصله
            for round_num in range(3):
                for heart in HEARTS:
                    try:
                        spaces = " " * ((round_num + HEARTS.index(heart)) % 3 + 1)
                        await msg.edit(f"{spaces}{heart}{spaces}")
                        await asyncio.sleep(0.5)
                    except MessageNotModifiedError:
                        continue
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except Exception:
                        return
            try:
                await msg.edit("  ❤️  ")
            except Exception:
                pass

        async def anim_grow(msg):
            # .قلب2 — زنجیره‌ای تا حداکثر ۶ قلب، بعد به ترتیب جابه‌جا میشن:
            # 🖤 → 🖤💜 → ... → 🖤💜💙🩵💚💛 → 💜💙🩵💚💛🧡 → ...
            # نیم‌فاصله بعد از هر قلب → در همه‌ی مراحل کوچیک
            frames = slide_frames(HEARTS_GROW, width=GROW_MAX, rounds=2)
            for fr in frames[1:]:   # فریم اول همان پیام اولیه است
                try:
                    await msg.edit(small(list(_split(fr))))
                    await asyncio.sleep(0.6)
                except MessageNotModifiedError:
                    continue
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except Exception:
                    return
            # پایان: قلب قرمز کوچیک
            try:
                await msg.edit(small(["❤️"]))
            except Exception:
                pass

        async def anim_pulse(msg):
            # .قلب3 — ضربان تک‌قلب با تغییر رنگ (بزرگ، بدون فاصله)
            seq = ["🤍","🩶","🖤","💜","💙","🩵","💚","💛","🧡","🩷","❤️","💗","💓","💞","💕","💖"]
            for _ in range(2):
                for h in seq + seq[-2::-1]:   # رفت و برگشت (بدون تکرار قلب وسط)
                    try:
                        await msg.edit(h)
                        await asyncio.sleep(0.35)
                    except MessageNotModifiedError:
                        continue
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except Exception:
                        return
            try:
                await msg.edit("❤️")
            except Exception:
                pass

        async def anim_breathe(msg):
            # .قلب4 — نفس: قلب وسط با فاصله کم/زیاد
            hearts = ["❤️","💜","💙","💚"]
            for _ in range(3):
                for h in hearts:
                    for pad in [0,1,2,3,2,1]:
                        try:
                            spaces = " " * pad
                            # اول و آخر با ZWNJ اگه تک بود — ولی اینجا همیشه با فاصله، نیازی نیست
                            await msg.edit(f"{spaces}{h}{spaces}")
                            await asyncio.sleep(0.3)
                        except MessageNotModifiedError:
                            continue
                        except FloodWaitError as e:
                            await asyncio.sleep(e.seconds + 1)
                        except Exception:
                            return
            try:
                await msg.edit("  ❤️  ")
            except Exception:
                pass

        async def anim_pink(msg):
            # .قلب5 — فقط قلب‌های صورتیِ خاص، دونه‌دونه عوض میشن (بزرگ)
            for _ in range(4):
                for h in HEARTS_PINK:
                    try:
                        await msg.edit(h)   # بزرگ
                        await asyncio.sleep(0.5)
                    except MessageNotModifiedError:
                        continue
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except Exception:
                        return

        async def anim_slide(msg):
            # .قلب6 — حداکثر ۳ قلب، به ترتیب جابه‌جا میشن (بدون فاصله و نیم‌فاصله)
            frames = slide_frames(HEARTS_SLIDE)
            for fr in frames[1:]:   # فریم اول همان پیام اولیه است
                try:
                    await msg.edit(fr)
                    await asyncio.sleep(0.6)
                except MessageNotModifiedError:
                    continue
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except Exception:
                    return

        ANIM_MAP = {
            None: anim_original,
            "1": anim_original,
            "2": anim_grow,
            "3": anim_pulse,
            "4": anim_breathe,
            "5": anim_pink,
            "6": anim_slide,
        }

        async def heart_cmd(event):
            if not event.out:
                return
            # پشتیبانی از .قلب / .قلب2 / .قلب 2 / .قلب 3 ...
            text = (event.raw_text or "").strip()
            # text مثل ".قلب" یا ".قلب 2" یا ".قلب2"
            num = None
            # regex دستی بدون group capture پیچیده
            t = text.replace(" ", "")
            # t الان ".قلب" یا ".قلب2"
            if t.startswith(".قلب"):
                rest = t[len(".قلب"):]  # بعد از ".قلب
                if rest.isdigit():
                    num = rest
                else:
                    # حالت ".قلب 2" با فاصله قبلاً حذف شد، ولی اگه فاصله داشت rest شامل عدد میشه
                    # برای ".قلب 2" اصلی قبل از حذف فاصله، دوباره چک کن
                    parts = text.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        num = parts[1]
            # اگر عدد نامعتبر بود، همون اصلی
            anim = ANIM_MAP.get(num, anim_original)

            reply_to = None
            if event.is_reply:
                try:
                    reply_msg = await event.get_reply_message()
                    reply_to = reply_msg.id
                except Exception:
                    reply_to = None

            await event.delete()

            msg = await self.client.send_message(
                event.chat_id, first_frame(num), reply_to=reply_to
            )

            await wait_for_seen(msg, event)
            await anim(msg)

        pacer = EditPacer()   # یکی برای هر اکانت

        async def build_cmd(event):
            # .قلب بساز / .قلب بساز 2 / .قلب بساز 3 / .قلب بساز ‹رنگ›
            if not event.out:
                return
            text = (event.raw_text or "").strip()
            arg = text.split("بساز", 1)[1] if "بساز" in text else ""
            frames = build_frames(arg)
            if frames is None:
                names = "، ".join(dict.fromkeys(HEART_COLORS))
                try:
                    await event.edit(f"رنگ ناشناخته. رنگ‌ها: {names}")
                except Exception:
                    pass
                return
            reply_to = None
            if event.is_reply:
                try:
                    reply_to = (await event.get_reply_message()).id
                except Exception:
                    reply_to = None
            await event.delete()
            msg = await self.client.send_message(event.chat_id, frames[0][0], reply_to=reply_to)
            await wait_for_seen(msg, event)
            await play_frames(msg, frames, pacer)

        # ── .قلب بساز فعلاً غیرفعال است (به درخواست کاربر): شکل روی بعضی
        # دستگاه‌ها کمی کج دیده می‌شد و ویرایش‌های زیادش زود FloodWait می‌گرفت.
        # برای فعال کردن دوباره: کامنت چهار خط زیر را بردار و بخش «.قلب بساز»
        # را به bot/help_content.py (SECTIONS["fun"] و COMMANDS) برگردان.
        # self._add_handler(
        #     build_cmd,
        #     events.NewMessage(pattern=r"^\.قلب\s+بساز(?:\s*[1-3]|\s+[^\d\s][^\n]{0,20})?$", outgoing=True),
        # )

        self._add_handler(
            heart_cmd,
            events.NewMessage(pattern=r"^\.قلب(?:\s*\d+)?$", outgoing=True),
        )

        self.logger.info("loaded")
