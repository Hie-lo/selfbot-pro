"""
فوروارد تمام پست‌های یک کانال به کانال دیگر.
کامند: .فوروارد https://t.me/source به https://t.me/destination
"""

import asyncio

from telethon import events
from telethon.errors import FloodWaitError

from plugins.base import BasePlugin
from core.security import validate_telegram_chat_link


class ForwardChannelPlugin(BasePlugin):
    name = "forward_channel"
    description = "فوروارد کامل کانال"
    always_on = True

    async def start(self):

        async def forward_cmd(event):
            if not event.out:
                return

            source_url = event.pattern_match.group(1).strip()
            destination_url = event.pattern_match.group(2).strip()
            source_ref = validate_telegram_chat_link(source_url)
            destination_ref = validate_telegram_chat_link(destination_url)

            await event.delete()

            if not source_ref or not destination_ref:
                await self.client.send_message(
                    event.chat_id,
                    "❌ فرمت لینک نامعتبر است.\n"
                    "نمونه: .فوروارد https://t.me/source به https://t.me/destination",
                )
                return

            try:
                source = await self._resolve_chat(source_ref)
                destination = await self._resolve_chat(destination_ref)

                if getattr(source, "id", None) == getattr(destination, "id", None):
                    await self.client.send_message(
                        event.chat_id, "❌ مبدأ و مقصد نمی‌توانند یکسان باشند."
                    )
                    return

                total = await self._forward_messages(source, destination)
                await self.client.send_message(
                    event.chat_id,
                    f"✅ فوروارد انجام شد.\nتعداد پست‌ها: {total}",
                )
                self.logger.info(
                    "Forwarded %s messages: %s -> %s",
                    total,
                    source_url,
                    destination_url,
                )
            except FloodWaitError as error:
                await self.client.send_message(
                    event.chat_id,
                    f"⏳ محدودیت تلگرام فعال شد؛ {error.seconds} ثانیه بعد دوباره تلاش کنید.",
                )
            except Exception as error:
                self.logger.exception("Forward channel error")
                await self.client.send_message(
                    event.chat_id, f"❌ فوروارد انجام نشد: {str(error)[:180]}"
                )

        self._add_handler(
            forward_cmd,
            events.NewMessage(
                pattern=r"^\.فوروارد\s+(\S+)\s+به\s+(\S+)\s*$",
                outgoing=True,
            ),
        )
        self.logger.info("loaded")

    async def _resolve_chat(self, chat_ref: str):
        if chat_ref.isdigit():
            return await self.client.get_entity(int(f"-100{chat_ref}"))
        return await self.client.get_entity(chat_ref)

    async def _forward_messages(self, source, destination) -> int:
        total = 0
        batch = []

        async for message in self.client.iter_messages(source, reverse=True):
            if getattr(message, "action", None) is not None:
                continue

            batch.append(message)
            if len(batch) == 100:
                await self.client.forward_messages(
                    destination, batch, from_peer=source
                )
                total += len(batch)
                batch.clear()
                await asyncio.sleep(0.5)

        if batch:
            await self.client.forward_messages(
                destination, batch, from_peer=source
            )
            total += len(batch)

        return total