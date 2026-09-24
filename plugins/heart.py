"""
پلاگین قلب متحرک — چند انیمیشن
کامندها: .قلب / .قلب 2 / .قلب2 / .قلب 3 ...
• .قلب   : انیمیشن ۱۲ قلب با حرکت (اصلی)
• .قلب2  : زنجیره‌ای — هی یکی اضافه میشه (‌🖤 → 🖤💜 → 🖤💜💙 ... با نیم‌فاصله برای کوچیک موندن تک‌اموجی)
• .قلب3  : ضربان — تک‌قلب با تغییر رنگ
• .قلب4  : نفس — قلب با فاصله تپنده
• .قلب5  : دنباله جرقه‌ای
اگه ریپلای باشه، روی همون پیام ریپلای میشه
ویرایش فقط بعد از سین زدنِ طرف مقابل شروع میشه (فقط پی‌وی، تا 120ث)
"""
import asyncio
from telethon import events
from telethon.errors import MessageNotModifiedError, FloodWaitError
from plugins.base import BasePlugin

HEARTS = ["❤️", "🩷", "🧡", "💛", "💚", "🩵", "💙", "💜", "🖤", "🤍", "🩶", "💗"]
# برای زنجیره‌ای — همون ترتیب مثال شما
HEARTS_GROW = ["🖤", "💜", "💙", "🩵", "💚", "💛", "🧡", "🩷", "❤️", "💗", "🤍", "🩶"]
# نیم‌فاصله برای کوچیک کردن تک‌اموجی
ZWNJ = "\u200c"

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
            # .قلب2 — زنجیره‌ای: ‌🖤 → 🖤💜 → 🖤💜💙 → ... → کامل → دوباره
            # نکته نیم‌فاصله: تک‌اموجی با ZWNJ کوچیک می‌مونه
            for _ in range(2):  # 2 دور کامل
                for i in range(1, len(HEARTS_GROW) + 1):
                    try:
                        chain = "".join(HEARTS_GROW[:i])
                        if i == 1:
                            chain = ZWNJ + chain  # کوچیک
                        await msg.edit(chain)
                        await asyncio.sleep(0.6)
                    except MessageNotModifiedError:
                        continue
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except Exception:
                        return
            # پایان: قلب قرمز کوچیک
            try:
                await msg.edit(ZWNJ + "❤️")
            except Exception:
                pass

        async def anim_pulse(msg):
            # .قلب3 — ضربان تک‌قلب با تغییر رنگ (همه با ZWNJ کوچیک)
            seq = ["🤍","🩶","🖤","💜","💙","🩵","💚","💛","🧡","🩷","❤️","💗","💓","💞","💕","💖"]
            for _ in range(2):
                for h in seq:
                    try:
                        await msg.edit(ZWNJ + h)
                        await asyncio.sleep(0.35)
                    except MessageNotModifiedError:
                        continue
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except Exception:
                        return
                for h in reversed(seq):
                    try:
                        await msg.edit(ZWNJ + h)
                        await asyncio.sleep(0.35)
                    except:
                        return
            try:
                await msg.edit(ZWNJ + "❤️")
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

        async def anim_sparkle(msg):
            # .قلب5 — دنباله جرقه‌ای
            frames = [
                ZWNJ+"💖",
                "💖✨",
                "✨💖✨",
                "💖✨💖",
                "✨💖✨💖✨",
                "💖✨💖✨💖",
                "✨💖",
                ZWNJ+"✨",
                ZWNJ+"💖",
            ]
            for _ in range(3):
                for fr in frames:
                    try:
                        await msg.edit(fr)
                        await asyncio.sleep(0.4)
                    except MessageNotModifiedError:
                        continue
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 1)
                    except Exception:
                        return
            try:
                await msg.edit(ZWNJ+"💖")
            except Exception:
                pass

        ANIM_MAP = {
            None: anim_original,
            "1": anim_original,
            "2": anim_grow,
            "3": anim_pulse,
            "4": anim_breathe,
            "5": anim_sparkle,
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

            if reply_to:
                msg = await self.client.send_message(
                    event.chat_id, f"{ZWNJ}{HEARTS[0]}" if num in (None,"1","3","5") else f"{ZWNJ}{HEARTS_GROW[0]}", reply_to=reply_to
                )
            else:
                # برای .قلب2 اول باید ‌🖤 باشه، برای بقیه هم ZWNJ+اولین
                first = ZWNJ + (HEARTS_GROW[0] if num=="2" else HEARTS[0])
                if num=="3":
                    first = ZWNJ+"🤍"
                elif num=="5":
                    first = ZWNJ+"💖"
                msg = await self.client.send_message(
                    event.chat_id, first
                )

            await wait_for_seen(msg, event)
            await anim(msg)

        self._add_handler(
            heart_cmd,
            events.NewMessage(pattern=r"^\.قلب(?:\s*\d+)?$", outgoing=True),
        )

        self.logger.info("loaded")
