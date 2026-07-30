"""
پلاگین قلب متحرک
کامند: .قلب
اگه ریپلای باشه، روی همون پیام ریپلای میشه
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