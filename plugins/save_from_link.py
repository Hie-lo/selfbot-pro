"""
پلاگین ذخیره از لینک پیام تلگرام
کامند: .ذخیره https://t.me/channel/123
"""

from telethon import events
from plugins.base import BasePlugin
from core.security import validate_telegram_link
from database import db


class SaveFromLinkPlugin(BasePlugin):
    name = "save_from_link"
    description = "ذخیره از لینک"
    always_on = True

    async def start(self):
        me = await self.client.get_me()
        my_id = me.id

        async def save_cmd(event):
            if not event.out:
                return

            url = event.pattern_match.group(1).strip()
            result = validate_telegram_link(url)

            if not result:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ لینک نامعتبر.")
                return

            channel_ref, msg_id = result
            await event.delete()

            try:
                # فیکس کانال خصوصی: t.me/c/1234567/890
                # آیدی باید -1001234567 بشه
                if channel_ref.isdigit():
                    # کانال خصوصی
                    real_id = int(f"-100{channel_ref}")
                    entity = await self.client.get_entity(real_id)
                else:
                    # کانال عمومی با username
                    entity = await self.client.get_entity(channel_ref)

                msg = await self.client.get_messages(entity, ids=msg_id)
                if not msg:
                    await self.client.send_message(event.chat_id, "❌ پیام پیدا نشد.")
                    return

                target = await db.get_storage_target(self.user_id, "save_from_link")
                dest_id = my_id
                if target and target.get("target_id"):
                    dest_id = target["target_id"]

                caption = f"🔗 ذخیره از: {url}"
                if msg.text:
                    caption += f"\n\n{msg.text}"

                if msg.media:
                    await self.client.send_file(dest_id, msg.media, caption=caption)
                else:
                    await self.client.send_message(dest_id, caption)

                # آلبوم
                if msg.grouped_id:
                    around = list(range(max(1, msg_id - 10), msg_id + 11))
                    messages = await self.client.get_messages(entity, ids=around)
                    album = [m for m in messages if m and m.grouped_id == msg.grouped_id and m.id != msg_id]
                    for m in album:
                        if m.media:
                            await self.client.send_file(dest_id, m.media)

                await self.client.send_message(event.chat_id, "✅ ذخیره شد.")
                self.logger.info(f"Saved from link: {url}")

            except Exception as e:
                self.logger.error(f"Save from link error: {e}")
                await self.client.send_message(event.chat_id, f"❌ خطا: {str(e)[:150]}")

        self._add_handler(
            save_cmd,
            events.NewMessage(pattern=r"^\.ذخیره\s+(https?://t\.me/.+)$", outgoing=True),
        )

        self.logger.info("loaded")