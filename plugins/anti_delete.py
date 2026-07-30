"""
پلاگین ضد حذف پیام — فقط PV
"""

from datetime import datetime, timezone
from telethon import events
from plugins.base import BasePlugin
from database import db


class AntiDeletePlugin(BasePlugin):
    name = "anti_delete"
    description = "ضد حذف پیام"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._cache: dict[int, dict[int, dict]] = {}
        self._max_cache = 500

    async def start(self):
        me = await self.client.get_me()
        my_id = me.id

        async def cache_message(event):
            """کش پیام‌های جدید — فقط PV"""
            if not event.is_private:
                return
            if not event.message:
                return
            # پیام خودمون رو کش نکن
            if event.message.sender_id == my_id:
                return

            chat_id = event.chat_id
            msg = event.message

            if chat_id not in self._cache:
                self._cache[chat_id] = {}

            if len(self._cache[chat_id]) >= self._max_cache:
                oldest = min(self._cache[chat_id].keys())
                del self._cache[chat_id][oldest]

            # نام فرستنده
            sender_name = "نامشخص"
            sender_id = msg.sender_id
            try:
                sender = await self.client.get_entity(sender_id)
                sender_name = getattr(sender, "first_name", "") or ""
                if hasattr(sender, "last_name") and sender.last_name:
                    sender_name += " " + sender.last_name
                sender_name = sender_name.strip() or str(sender_id)
            except Exception:
                sender_name = str(sender_id) if sender_id else "نامشخص"

            # زمان فارسی‌تر
            now = datetime.now(timezone.utc)
            time_str = now.strftime("%Y/%m/%d %H:%M:%S")

            self._cache[chat_id][msg.id] = {
                "text": msg.text or "",
                "media": msg.media,
                "sender_name": sender_name,
                "sender_id": sender_id,
                "time_str": time_str,
                "chat_id": chat_id,
            }

        self._add_handler(cache_message, events.NewMessage)

        async def on_delete(event):
            """وقتی پیام حذف شد"""
            for msg_id in event.deleted_ids:
                for chat_id, msgs in self._cache.items():
                    if msg_id in msgs:
                        cached = msgs.pop(msg_id)
                        await self._send_deleted(cached, my_id)
                        break

        self._add_handler(on_delete, events.MessageDeleted)

        self.logger.info("loaded")

    async def _send_deleted(self, cached, my_id):
        """ارسال پیام حذف شده"""
        target = await db.get_storage_target(self.user_id, "anti_delete")
        dest_id = my_id
        if target:
            dest_id = target["target_id"] or my_id

        sender = cached.get("sender_name", "نامشخص")
        text = cached.get("text", "")
        time_str = cached.get("time_str", "")
        sender_id = cached.get("sender_id", "")

        header = (
            f"🗑 **پیام حذف شده**\n"
            f"👤 فرستنده: {sender}"
        )
        if sender_id:
            header += f" (`{sender_id}`)"
        header += f"\n📅 زمان: {time_str}\n"

        if text:
            header += f"\n📝 متن:\n{text}"

        try:
            media = cached.get("media")
            if media:
                await self.client.send_file(dest_id, media, caption=header)
            else:
                await self.client.send_message(dest_id, header)
            self.logger.info(f"Deleted msg saved from {sender}")
        except Exception as e:
            self.logger.error(f"Send deleted failed: {e}")

    async def stop(self):
        self._cache.clear()
        await super().stop()