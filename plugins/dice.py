"""
پلاگین تاس تقلبی
کامند: .تاس عدد
کامند: .تاس ایموجی عدد
"""

import asyncio
from telethon import events
from telethon.tl.types import InputMediaDice
from plugins.base import BasePlugin


def persian_to_english(text: str) -> str:
    mapping = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    return text.translate(mapping)


class DicePlugin(BasePlugin):
    name = "dice"
    description = "تاس تقلبی"
    always_on = True

    async def start(self):

        async def dice_normal(event):
            if not event.out:
                return

            num_str = persian_to_english(event.pattern_match.group(1).strip())
            try:
                target = int(num_str)
            except ValueError:
                await event.delete()
                return

            if target < 1 or target > 6:
                await event.delete()
                return

            reply_to = None
            if event.is_reply:
                reply_msg = await event.get_reply_message()
                reply_to = reply_msg.id

            await event.delete()

            for _ in range(100):
                if reply_to:
                    msg = await self.client.send_message(
                        event.chat_id,
                        file=InputMediaDice("🎲"),
                        reply_to=reply_to,
                    )
                else:
                    msg = await self.client.send_message(
                        event.chat_id,
                        file=InputMediaDice("🎲"),
                    )

                if hasattr(msg.media, "value") and msg.media.value == target:
                    break
                else:
                    await msg.delete()
                    await asyncio.sleep(0.05)

        self._add_handler(
            dice_normal,
            events.NewMessage(pattern=r"^\.تاس\s+([^🎲🎯🎳🏀⚽🎰].*)$", outgoing=True),
        )

        async def dice_emoji(event):
            if not event.out:
                return

            emoji = event.pattern_match.group(1)
            num_str = persian_to_english(event.pattern_match.group(2).strip())
            try:
                target = int(num_str)
            except ValueError:
                await event.delete()
                return

            ranges = {
                "🎲": (1, 6), "🎯": (1, 6), "🎳": (1, 6),
                "🏀": (1, 5), "⚽": (1, 5), "🎰": (1, 64),
            }
            mn, mx = ranges.get(emoji, (1, 6))
            if target < mn or target > mx:
                await event.delete()
                return

            reply_to = None
            if event.is_reply:
                reply_msg = await event.get_reply_message()
                reply_to = reply_msg.id

            await event.delete()

            max_att = 200 if emoji == "🎰" else 100
            for _ in range(max_att):
                if reply_to:
                    msg = await self.client.send_message(
                        event.chat_id,
                        file=InputMediaDice(emoji),
                        reply_to=reply_to,
                    )
                else:
                    msg = await self.client.send_message(
                        event.chat_id,
                        file=InputMediaDice(emoji),
                    )

                if hasattr(msg.media, "value") and msg.media.value == target:
                    break
                else:
                    await msg.delete()
                    await asyncio.sleep(0.03)

        self._add_handler(
            dice_emoji,
            events.NewMessage(pattern=r"^\.تاس\s+(🎲|🎯|🎳|🏀|⚽|🎰)\s+(.+)$", outgoing=True),
        )

        self.logger.info("loaded")