"""
پلاگین انیمیشن تایپ
کامندها:
  .بنویس ‹متن›   — متن را با انیمیشن تایپ می‌فرستد (مثل اینکه همین حالا
                    دارد تایپ می‌شود)

⚠️ هر دستور/قابلیت جدید یا تغییرکرده → bot/help_content.py را هم به‌روز کنید
(tests/test_help_coverage.py دستورات جاافتاده را پیدا می‌کند).
"""

import asyncio
import math
import unicodedata

from telethon import events

from plugins.base import BasePlugin
from plugins.heart import EditPacer, play_frames

CURSOR = "▌"                  # نشانگر تایپ
MAX_FRAMES = 14               # سقف ویرایش‌ها (تلگرام روی ویرایش سریع محدودیت می‌گذارد)
MIN_DELAY = 0.55              # کف فاصله‌ی فریم‌ها — جلوگیری از FloodWait
TOTAL_SECONDS = 6.0           # مدت کل انیمیشن
MAX_ANIM_CHARS = 3000         # بیشتر از این، انیمیشن بی‌معنی است


def _units(text: str) -> list[str]:
    """
    تقسیم متن به واحدهای نمایشی؛ ایموجی، ZWNJ و علامت‌های ترکیبی
    تکه‌تکه نمی‌شوند (وگرنه وسط انیمیشن مربع خالی دیده می‌شود).
    """
    out: list[str] = []
    prev = ""
    for ch in text:
        join = (
            unicodedata.combining(ch) != 0
            or ch in ("\ufe0e", "\ufe0f", "\u200d", "\u20e3")
            or prev in ("\u200d",)
            or "\U0001f3fb" <= ch <= "\U0001f3ff"          # رنگ پوست
            or ("\U0001f1e6" <= prev <= "\U0001f1ff"       # پرچم (دو کاراکتر)
                and "\U0001f1e6" <= ch <= "\U0001f1ff")
        )
        if join and out:
            out[-1] += ch
        else:
            out.append(ch)
        prev = ch
    return out


def build_frames(text: str) -> list[tuple[str, float]]:
    """
    لیست (متن فریم، تأخیر بعد از آن). فریم اول فقط نشانگر تایپ است و
    فریم آخر متن کامل بدون نشانگر.
    """
    units = _units(text)
    if not units:
        return [(CURSOR, 0.0)]

    n = len(units)
    frames_count = min(MAX_FRAMES, max(4, n // 3 + 1))
    step = max(1, math.ceil(n / frames_count))

    texts = [CURSOR]
    for i in range(step, n, step):
        texts.append("".join(units[:i]) + CURSOR)
    if texts[-1] != text + CURSOR:
        texts.append(text + CURSOR)
    texts.append(text)                       # فریم نهایی: متن کامل

    delay = max(MIN_DELAY, TOTAL_SECONDS / max(1, len(texts) - 1))
    return [(t, delay) for t in texts[:-1]] + [(texts[-1], 0.0)]


class TypingPlugin(BasePlugin):
    name = "typing_animation"
    description = "انیمیشن تایپ (.بنویس)"
    always_on = True

    async def start(self):
        # pacer مشترک این اکانت — بعد از FloodWait انیمیشن‌های بعدی کندتر می‌شوند
        pacer = EditPacer()

        async def type_cmd(event):
            if not event.out:
                return

            text = (event.pattern_match.group(1) or "").strip()
            if not text:
                await event.edit("✍️ بعد از دستور متن را بنویس — مثال:\n"
                                 "<code>.بنویس سلام، خوبی؟</code>",
                                 parse_mode="html")
                return

            reply_to = None
            if event.is_reply:
                try:
                    reply = await event.get_reply_message()
                    reply_to = reply.id if reply else None
                except Exception:
                    reply_to = None

            frames = build_frames(text[:MAX_ANIM_CHARS])

            await event.delete()
            msg = await self.client.send_message(
                event.chat_id, frames[0][0], reply_to=reply_to,
            )
            # play_frames خودش FloodWait را مدیریت می‌کند: با محدودیت، متن
            # کامل را یک‌جا می‌گذارد تا انیمیشن نصفه نماند
            await play_frames(msg, frames, pacer)
            self.logger.info(f"Typed {len(text)} chars in {event.chat_id}")

        # .بنویس / .بنویس متن (متن می‌تواند چندخطی باشد)
        self._add_handler(
            type_cmd,
            events.NewMessage(pattern=r"^\.بنویس(?:\s+([\s\S]+))?$", outgoing=True),
        )

        self.logger.info("loaded")
