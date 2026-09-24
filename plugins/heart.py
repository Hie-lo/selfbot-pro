"""
پلاگین قلب متحرک
کامند: .قلب
اگه ریپلای باشه، روی همون پیام ریپلای میشه
ویرایش فقط بعد از سین زدنِ طرف مقابل شروع میشه (MessageRead)
"""
import asyncio
from telethon import events
from telethon.errors import MessageNotModifiedError, FloodWaitError
from plugins.base import BasePlugin

HEARTS = ["❤️", "🩷", "🧡", "💛", "💚", "🩵", "💙", "💜", "🖤", "🤍", "🩶", "💗"]


class HeartPlugin(BasePlugin):
    name = "heart_animation"
    description = "قلب متحرک"
    always_on = True

    async def start(self):

        async def heart_cmd(event):
            if not event.out:
                return

            reply_to = None
            if event.is_reply:
                reply_msg = await event.get_reply_message()
                reply_to = reply_msg.id

            await event.delete()

            # ارسال اولیه با ریپلای
            if reply_to:
                msg = await self.client.send_message(
                    event.chat_id, f"  {HEARTS[0]}  ", reply_to=reply_to
                )
            else:
                msg = await self.client.send_message(
                    event.chat_id, f"  {HEARTS[0]}  "
                )

            # --- منتظر سین زدنِ طرف مقابل (فقط برای پی‌وی) ---
            # توی گروه/کانال سین معنای دقیقی نداره، همونجا شروع می‌کنیم
            if event.is_private:
                try:
                    # منتظر MessageRead که is_outbox و max_id >= msg.id
                    read_future = asyncio.get_event_loop().create_future()

                    async def _on_read(read_event):
                        try:
                            # read_event.chat_id may be int, msg.chat_id is also int
                            chat_ok = False
                            try:
                                chat_ok = (read_event.chat_id == msg.chat_id)
                            except Exception:
                                # fallback: compare with event.chat_id
                                chat_ok = (getattr(read_event, 'chat_id', None) == event.chat_id)
                            is_outbox = getattr(read_event, 'is_outbox', True)
                            # بعضی نسخه‌ها outbox ندارن، فرض بر True
                            max_id = getattr(read_event, 'max_id', 0) or 0
                            if chat_ok and is_outbox and max_id >= msg.id:
                                if not read_future.done():
                                    read_future.set_result(True)
                            # برای حالت‌هایی که max_id نداره، هر read تو همون چت رو قبول کن
                            elif chat_ok and is_outbox and max_id == 0:
                                if not read_future.done():
                                    read_future.set_result(True)
                        except Exception:
                            pass

                    self.client.add_event_handler(_on_read, events.MessageRead)
                    try:
                        # 120 ثانیه صبر کن، اگه سین نزد خودش شروع می‌کنه
                        await asyncio.wait_for(read_future, timeout=120)
                    except asyncio.TimeoutError:
                        pass
                    finally:
                        try:
                            self.client.remove_event_handler(_on_read, events.MessageRead)
                        except Exception:
                            pass
                except Exception:
                    # هر خطایی خورد، بدون معطلی ادامه بده
                    pass

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

        self._add_handler(
            heart_cmd,
            events.NewMessage(pattern=r"^\.قلب$", outgoing=True),
        )

        self.logger.info("loaded")
