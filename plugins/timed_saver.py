"""
پلاگین ذخیره پیام‌های تایم‌دار
- فقط PV
- فقط پیام طرف مقابل (پیام‌های خودی ذخیره نمی‌شوند)
- جلوگیری قطعی از تکرار با DB (Unique Index + ON CONFLICT)
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

        async def on_message(event):
            # فقط PV
            if not event.is_private:
                return
            if not event.message or not event.message.media:
                return

            ttl = getattr(event.message.media, "ttl_seconds", None)
            if ttl is None:
                return

            # فقط پیام طرف مقابل
            if event.message.sender_id == my_id:
                return

            # جلوگیری از لوپ/فوروارد
            if event.message.forward:
                return

            chat_id = event.chat_id
            msg_id = event.message.id
            text = event.message.text or ""
            media_type = type(event.message.media).__name__

            # نام چت (طرف مقابل)
            try:
                chat_entity = await self.client.get_entity(chat_id)
                chat_title = getattr(chat_entity, "first_name", "") or ""
                if getattr(chat_entity, "last_name", None):
                    chat_title += " " + chat_entity.last_name
                chat_title = chat_title.strip() or str(chat_id)
            except Exception:
                chat_title = str(chat_id)

            # نام فرستنده
            sender_id = event.message.sender_id
            try:
                sender = await self.client.get_entity(sender_id)
                sender_name = getattr(sender, "first_name", "") or ""
                if getattr(sender, "last_name", None):
                    sender_name += " " + sender.last_name
                sender_name = sender_name.strip() or str(sender_id)
            except Exception:
                sender_name = str(sender_id) if sender_id else "نامشخص"

            safe_title = "".join(c for c in chat_title if c.isalnum() or c in " -_")
            folder = os.path.join(DOWNLOADS_DIR, "timed", safe_title)
            os.makedirs(folder, exist_ok=True)

            # دانلود مدیا
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
                self.logger.error(f"Timed download failed: {e}")

            # 1) اول INSERT در DB (اگر تکراری بود، ارسال نکن)
            inserted = await db.save_message_record(
                self.user_id,
                "timed",
                chat_id,
                chat_title,
                msg_id,
                text,
                media_type=media_type,
                media_path=media_path or "",
            )

            if not inserted:
                # اگر تکراری بود، فایل دانلودشده رو هم پاک کن
                if media_path and os.path.exists(media_path):
                    try:
                        os.remove(media_path)
                    except Exception:
                        pass
                self.logger.info(f"Duplicate timed msg skipped: chat={chat_id} msg={msg_id}")
                return

            # مقصد
            dest_peer = await self._get_dest_peer(my_id)

            caption = f"⏳ تایم‌دار از {sender_name}"
            if text:
                caption += f"\n{text}"

            try:
                if media_path and os.path.exists(media_path):
                    await self.client.send_file(dest_peer, media_path, caption=caption)
                else:
                    await self.client.send_message(dest_peer, caption)
            except Exception as e:
                self.logger.error(f"Timed forward failed: {e}")

        self._add_handler(on_message, events.NewMessage)
        self.logger.info("TimedSaver loaded")

    async def _get_dest_peer(self, my_id):
        target = await db.get_storage_target(self.user_id, "timed_saver")
        dest_id = my_id
        if target and target.get("target_id"):
            dest_id = target["target_id"]

        if dest_id == my_id:
            return my_id

        # اگر مثبت بود، کانال احتمالی: -100...
        if isinstance(dest_id, int) and dest_id > 0 and dest_id < 10000000000:
            dest_id = int(f"-100{dest_id}")

        try:
            return await self.client.get_entity(dest_id)
        except Exception:
            return dest_id