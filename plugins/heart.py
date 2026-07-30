"""
پلاگین قلب متحرک
کامند: .قلب
"""

import asyncio
from telethon import events
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
            msg = await self.client.send_message(event.chat_id, HEARTS[0])

            for _ in range(3):
                for heart in HEARTS:
                    try:
                        await msg.edit(heart)
                        await asyncio.sleep(0.4)
                    except Exception:
                        return

            try:
                await msg.edit("❤️")
            except Exception:
                pass

        self._add_handler(
            heart_cmd,
            events.NewMessage(pattern=r"^\.قلب$", outgoing=True),
        )

        self.logger.info("loaded")