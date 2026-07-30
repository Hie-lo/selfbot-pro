"""
پلاگین ضد حذف پیام
پیام‌های حذف شده را در مقصد تنظیم شده ذخیره میکنه
کامند: .ضدحذف (اطلاعات)
"""

from telethon import events
from telethon.tl.types import PeerUser, PeerChat, PeerChannel
from plugins.base import BasePlugin
from database import db


class AntiDeletePlugin(BasePlugin):
    name = "anti_delete"
    description = "ضد حذف پیام"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        # کش پیام‌ها: {chat_id: {msg_id: {text, media, sender, ...}}}
        self._cache: dict[int, dict[int, dict]] = {}
        self._max_cache = 500  # حداکثر پیام در هر چت

    async def start(self):
        me = await self.client.get_me()
        my_id = me.id

        async def cache_message(event):
            """کش کردن پیام‌های جدید"""
            if not event.message:
                return

            chat_id = event.chat_id
            msg = event.message

            if chat_id not in self._cache:
                self._cache[chat_id] = {}

            # محدودیت حافظه
            if len(self._cache[chat_id]) >= self._max_cache:
                oldest = min(self._cache[chat_id].keys())
                del self._cache[chat_id][oldest]

            sender_name = "نامشخص"
            try:
                if msg.sender:
                    sender_name = getattr(msg.sender, "first_name", "") or ""
                    if hasattr(msg.sender, "last_name") and msg.sender.last_name:
                        sender_name += " " + msg.sender.last_name
                    sender_name = sender_name.strip() or str(msg.sender_id)
            except Exception:
                pass

            self._cache[chat_id][msg.id] = {
                "text": msg.text or "",
                "media": msg.media,
                "sender_name": sender_name,
                "sender_id": msg.sender_id,
                "date": msg.date,
            }

        self._add_handler(cache_message, events.NewMessage)

        async def on_delete(event):
            """وقتی پیامی حذف شد"""
            for msg_id in event.deleted_ids:
                # جستجو در کش
                for chat_id, msgs in self._cache.items():
                    if msg_id in msgs:
                        cached = msgs.pop(msg_id)
                        await self._send_deleted(chat_id, msg_id, cached, my_id)
                        break

        self._add_handler(on_delete, events.MessageDeleted)

        self.logger.info("loaded")

    async def _send_deleted(self, chat_id, msg_id, cached, my_id):
        """ارسال پیام حذف شده به مقصد"""
        target = await db.get_storage_target(self.user_id, "anti_delete")
        dest_id = my_id
        if target:
            dest_id = target["target_id"] or my_id

        # ساخت متن
        sender = cached.get("sender_name", "نامشخص")
        text = cached.get("text", "")
        date = cached.get("date", "")

        header = (
            f"🗑 **پیام حذف شده**\n"
            f"👤 فرستنده: {sender}\n"
            f"📅 زمان: {date}\n"
        )

        if text:
            header += f"📝 متن:\n{text}"

        try:
            media = cached.get("media")
            if media:
                await self.client.send_file(dest_id, media, caption=header)
            else:
                await self.client.send_message(dest_id, header)

            self.logger.info(f"Deleted msg {msg_id} saved")

        except Exception as e:
            self.logger.error(f"Send deleted failed: {e}")

    async def stop(self):
        self._cache.clear()
        await super().stop()