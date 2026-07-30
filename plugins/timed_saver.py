"""
پلاگین ذخیره پیام‌های تایم‌دار
خودکار کار میکنه، نیاز به کامند نداره
"""

import os
from datetime import datetime
from telethon import events
from plugins.base import BasePlugin
from database import db
from config import DOWNLOADS_DIR


class TimedSaverPlugin(BasePlugin):
    name = "timed_saver"
    description = "ذخیره پیام تایم‌دار"
    always_on = False

    async def start(self):
        me = await self.client.get_me()
        my_id = me.id

        async def on_message(event):
            if not event.is_private:
                return
            if not event.message or not event.message.media:
                return
            ttl = getattr(event.message.media, "ttl_seconds", None)
            if ttl is None:
                return

            chat_id = event.chat_id
            msg_id = event.message.id
            text = event.message.text or ""

            # نام چت
            try:
                entity = await self.client.get_entity(chat_id)
                chat_title = getattr(entity, "first_name", str(chat_id))
                if hasattr(entity, "last_name") and entity.last_name:
                    chat_title += " " + entity.last_name
            except Exception:
                chat_title = str(chat_id)

            # دانلود
            safe_title = "".join(c for c in chat_title if c.isalnum() or c in " -_")
            folder = os.path.join(DOWNLOADS_DIR, "timed", safe_title)
            os.makedirs(folder, exist_ok=True)

            media_type = type(event.message.media).__name__
            media_path = None

            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                fp = await event.message.download_media(file=folder)
                if fp:
                    ext = os.path.splitext(fp)[1]
                    new_path = os.path.join(folder, f"{ts}_{msg_id}{ext}")
                    os.rename(fp, new_path)
                    media_path = new_path
            except Exception as e:
                self.logger.error(f"Download failed: {e}")

            # ذخیره در DB
            await db.save_message_record(
                self.user_id, "timed", chat_id, chat_title,
                msg_id, text, media_type, media_path,
            )

            # فوروارد به مقصد
            target = await db.get_storage_target(self.user_id, "timed_saver")
            dest_id = my_id
            if target:
                dest_id = target["target_id"] or my_id

            caption = f"⏳ تایم‌دار از {chat_title}"
            if text:
                caption += f"\n{text}"

            try:
                if media_path and os.path.exists(media_path):
                    await self.client.send_file(dest_id, media_path, caption=caption)
                else:
                    await self.client.send_message(dest_id, caption)
            except Exception as e:
                self.logger.error(f"Forward failed: {e}")

            self.logger.info(f"Timed message saved from {chat_title}")

        self._add_handler(on_message, events.NewMessage)
        self.logger.info("loaded")