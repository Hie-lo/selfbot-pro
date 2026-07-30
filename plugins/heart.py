"""
پلاگین قلب متحرک
کامند: .قلب
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

            await event.delete()

            # شروع با یک متن که حاوی ایموجی باشه
            msg = await self.client.send_message(
                event.chat_id,
                f"  {HEARTS[0]}  "
            )

            for round_num in range(3):
                for heart in HEARTS:
                    try:
                        # فاصله‌ها متفاوت تا تلگرام ادیت رو قبول کنه
                        spaces = " " * ((round_num + HEARTS.index(heart)) % 3 + 1)
                        new_text = f"{spaces}{heart}{spaces}"

                        await msg.edit(new_text)
                        await asyncio.sleep(0.5)

                    except MessageNotModifiedError:
                        # اگه متن تغییر نکرده بود
                        continue

                    except FloodWaitError as e:
                        self.logger.warning(f"FloodWait {e.seconds}s")
                        await asyncio.sleep(e.seconds + 1)

                    except Exception as e:
                        self.logger.error(f"Edit error: {type(e).__name__}: {e}")
                        return

            # پایان
            try:
                await msg.edit("  ❤️  ")
            except Exception:
                pass

        self._add_handler(
            heart_cmd,
            events.NewMessage(pattern=r"^\.قلب$", outgoing=True),
        )

        self.logger.info("loaded")