"""
پلاگین ذخیره از لینک پیام تلگرام
پشتیبانی کامل از کانال‌های قفل شده (Protected Chats) با روش دانلود و آپلود خودکار
کامند: .ذخیره https://t.me/channel/123
"""

import os
from telethon import events
from plugins.base import BasePlugin
from core.security import validate_telegram_link
from database import db
from config import DOWNLOADS_DIR


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
                # تبدیل آیدی کانال‌های خصوصی
                if channel_ref.isdigit():
                    real_id = int(f"-100{channel_ref}")
                    entity = await self.client.get_entity(real_id)
                else:
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

                # ارسال پیام اصلی
                if msg.media:
                    await self._send_media_safely(dest_id, msg, caption)
                else:
                    await self.client.send_message(dest_id, caption)

                # ارسال بخش‌های دیگر آلبوم (اگر آلبوم بود)
                if msg.grouped_id:
                    around = list(range(max(1, msg_id - 10), msg_id + 11))
                    messages = await self.client.get_messages(entity, ids=around)
                    album = [m for m in messages if m and m.grouped_id == msg.grouped_id and m.id != msg_id]
                    for m in album:
                        if m.media:
                            await self._send_media_safely(dest_id, m, None)

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

    async def _send_media_safely(self, dest_id, msg, caption=None):
        """
        ارسال فایل با دور زدن محدودیت کپی/فوروارد کانال‌های محافظت شده.
        اگر ارسال مستقیم خطا داد، فایل را دانلود و سپس آپلود می‌کند.
        """
        try:
            # تلاش اول: ارسال مستقیم (سریع‌ترین حالت)
            await self.client.send_file(dest_id, msg.media, caption=caption)
        except Exception as e:
            err_msg = str(e).lower()
            # اگر خطای مربوط به چت‌های قفل شده یا کپی محدود شده رخ داد
            if "protected" in err_msg or "forward" in err_msg or "restrict" in err_msg or "copy" in err_msg:
                self.logger.info(f"Protected chat media detected for msg_id={msg.id}. Downloading...")
                
                temp_dir = os.path.join(DOWNLOADS_DIR, "temp")
                os.makedirs(temp_dir, exist_ok=True)
                
                # دانلود فایل روی سرور
                file_path = await msg.download_media(file=temp_dir)
                if file_path:
                    try:
                        # آپلود به عنوان فایل جدید
                        await self.client.send_file(
                            dest_id, 
                            file_path, 
                            caption=caption,
                            force_document=False,
                            supports_streaming=True
                        )
                    finally:
                        # حذف فایل از روی سرور بلافاصله پس از آپلود
                        if os.path.exists(file_path):
                            os.remove(file_path)
                else:
                    raise ValueError("دانلود فایل محافظت‌شده ناموفق بود.")
            else:
                raise