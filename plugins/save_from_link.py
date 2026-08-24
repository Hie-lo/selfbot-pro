"""
پلاگین ذخیره از لینک پیام تلگرام
کامند: .ذخیره https://t.me/channel/123
مقصد: همان چتی که دستور در آن زده شده
"""

import os
from telethon import events
from plugins.base import BasePlugin
from core.security import validate_telegram_link
from config import DOWNLOADS_DIR


class SaveFromLinkPlugin(BasePlugin):
    name = "save_from_link"
    description = "ذخیره از لینک"
    always_on = True

    async def start(self):

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

            # مقصد = همان چتی که کاربر دستور را زده
            dest_id = event.chat_id

            await event.delete()

            try:
                if channel_ref.isdigit():
                    real_id = int(f"-100{channel_ref}")
                    entity = await self.client.get_entity(real_id)
                else:
                    entity = await self.client.get_entity(channel_ref)

                msg = await self.client.get_messages(entity, ids=msg_id)
                if not msg:
                    await self.client.send_message(dest_id, "❌ پیام پیدا نشد.")
                    return

                caption = f"🔗 ذخیره از: {url}"
                if msg.text:
                    caption += f"\n\n{msg.text}"

                if msg.media:
                    await self._send_media_safely(dest_id, msg, caption)
                else:
                    await self.client.send_message(dest_id, caption)

                # آلبوم
                if msg.grouped_id:
                    around = list(range(max(1, msg_id - 10), msg_id + 11))
                    messages = await self.client.get_messages(entity, ids=around)
                    album = [
                        m for m in messages
                        if m and m.grouped_id == msg.grouped_id and m.id != msg_id
                    ]
                    for m in album:
                        if m.media:
                            await self._send_media_safely(dest_id, m, None)

                self.logger.info(f"Saved from link: {url} -> chat {dest_id}")

            except Exception as e:
                self.logger.error(f"Save from link error: {e}")
                await self.client.send_message(dest_id, f"❌ خطا: {str(e)[:150]}")

        self._add_handler(
            save_cmd,
            events.NewMessage(pattern=r"^\.ذخیره\s+(https?://t\.me/.+)$", outgoing=True),
        )

        self.logger.info("loaded")

    async def _send_media_safely(self, dest_id, msg, caption=None):
        """ارسال با دور زدن محدودیت کانال‌های محافظت شده"""
        try:
            await self.client.send_file(dest_id, msg.media, caption=caption)
        except Exception as e:
            err_msg = str(e).lower()
            if any(kw in err_msg for kw in ("protected", "forward", "restrict", "copy")):
                self.logger.info(f"Protected chat, downloading msg_id={msg.id}")

                temp_dir = os.path.join(DOWNLOADS_DIR, "temp")
                os.makedirs(temp_dir, exist_ok=True)

                file_path = await msg.download_media(file=temp_dir)
                if file_path:
                    try:
                        await self.client.send_file(
                            dest_id, file_path,
                            caption=caption,
                            force_document=False,
                            supports_streaming=True,
                        )
                    finally:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                else:
                    raise ValueError("دانلود ناموفق")
            else:
                raise