"""
پلاگین قلب متحرک — چند انیمیشن
کامندها: .قلب / .قلب 2 / .قلب2 / .قلب 3 ...
• .قلب   : انیمیشن ۱۲ قلب با حرکت (اصلی)
• .قلب2  : زنجیره‌ای تا ۶ قلب و بعد جابه‌جا (🖤 → 🖤💜 → ... ) — همه کوچیک (نیم‌فاصله بعد از هر قلب)
• .قلب3  : ضربان — تک‌قلب بزرگ با تغییر رنگ
• .قلب4  : نفس — قلب با فاصله تپنده
• .قلب5  : قلب‌های صورتیِ خاص دونه‌دونه عوض میشن (💕💞💓💗💘💝)
• .قلب6  : پنجره‌ی ۳تایی — 🩷 → 🩷❤️ → 🩷❤️🧡 → ❤️🧡💛 → ... (بدون فاصله)
اگه ریپلای باشه، روی همون پیام ریپلای میشه
ویرایش فقط بعد از سین زدنِ طرف مقابل شروع میشه (فقط پی‌وی، تا 120ث)
"""
import asyncio
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

        self._add_handler(
            heart_cmd,
            events.NewMessage(pattern=r"^\.قلب(?:\s*\d+)?$", outgoing=True),
        )

        self.logger.info("loaded")
