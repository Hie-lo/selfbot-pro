"""
پلاگین ذخیره پیام‌های تایم‌دار
"""

import os
from datetime import datetime
from telethon import events
from plugins.base import BasePlugin
from database import db
from config import DOWNLOADS_DIR


class TimedSaverPlugin(BasePlugin):
    name = "timed_saver"
    description = "ذخیره تایم‌دار"
    always_on = False

    async def start(self):
        me = await self.client.get_me()
        my_id = me.id

        self.logger.info(f"TimedSaver started for user {self.user_id}, my_id={my_id}")

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

            self.logger.info(f"Timed message detected: msg_id={msg_id}, chat_id={chat_id}")

            try:
                entity = await self.client.get_entity(chat_id)
                chat_title = getattr(entity, "first_name", str(chat_id))
                if hasattr(entity, "last_name") and entity.last_name:
                    chat_title += " " + entity.last_name
            except Exception as e:
                self.logger.debug(f"Get chat entity failed: {e}")
                chat_title = str(chat_id)

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
                    self.logger.debug(f"Downloaded timed media: {media_path}")
            except Exception as e:
                self.logger.error(f"Download failed: {e}")

            await db.save_message_record(
                self.user_id, "timed", chat_id, chat_title,
                msg_id, text, media_type, media_path,
            )

            dest_peer = await self._get_dest_peer(my_id)

            caption = f"⏳ تایم‌دار از {chat_title}"
            if text:
                caption += f"\n{text}"

            try:
                if media_path and os.path.exists(media_path):
                    await self.client.send_file(dest_peer, media_path, caption=caption)
                else:
                    await self.client.send_message(dest_peer, caption)
                self.logger.info(f"✅ Timed saved from {chat_title}")
            except Exception as e:
                self.logger.error(f"❌ Forward failed: {e}")

        self._add_handler(on_message, events.NewMessage)
        self.logger.info("TimedSaver loaded")

    async def _get_dest_peer(self, my_id):
        """یافتن مقصد با نرمال‌سازی آیدی کانال"""
        target = await db.get_storage_target(self.user_id, "timed_saver")
        self.logger.debug(f"Storage target: {target}")

        dest_id = my_id
        if target and target.get("target_id"):
            dest_id = target["target_id"]

        self.logger.debug(f"Dest ID raw: {dest_id}")

        if dest_id == my_id:
            return my_id

        # نرمال‌سازی: اگر عدد مثبت بزرگه، ممکنه کانال باشه
        if isinstance(dest_id, int) and dest_id > 0 and dest_id < 10000000000:
            normalized = int(f"-100{dest_id}")
            self.logger.debug(f"Normalized {dest_id} -> {normalized}")
            dest_id = normalized

        try:
            entity = await self.client.get_entity(dest_id)
            self.logger.debug(f"Resolved entity: {getattr(entity, 'title', dest_id)}")
            return entity
        except Exception as e:
            self.logger.warning(f"Failed to resolve dest {dest_id}: {e}")
            return dest_id

    async def stop(self):
        await super().stop()