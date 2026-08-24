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
        self._my_id = None

    async def start(self):
        me = await self.client.get_me()
        self._my_id = me.id

        self.logger.info(f"AntiDelete started for user {self.user_id}, my_id={self._my_id}")

        async def cache_message(event):
            if not event.is_private:
                return
            if not event.message:
                return

            chat_id = event.chat_id
            msg = event.message

            if chat_id not in self._cache:
                self._cache[chat_id] = {}

            if len(self._cache[chat_id]) >= self._max_cache:
                oldest = min(self._cache[chat_id].keys())
                del self._cache[chat_id][oldest]

            sender_id = msg.sender_id
            is_me = (sender_id == self._my_id)

            if is_me:
                sender_name = "شما"
            else:
                sender_name = "نامشخص"
                try:
                    sender = await self.client.get_entity(sender_id)
                    sender_name = getattr(sender, "first_name", "") or ""
                    if hasattr(sender, "last_name") and sender.last_name:
                        sender_name += " " + sender.last_name
                    sender_name = sender_name.strip() or str(sender_id)
                except Exception as e:
                    self.logger.debug(f"Get sender entity failed: {e}")
                    sender_name = str(sender_id) if sender_id else "نامشخص"

            chat_name = "نامشخص"
            try:
                chat_entity = await self.client.get_entity(chat_id)
                chat_name = getattr(chat_entity, "first_name", "") or ""
                if hasattr(chat_entity, "last_name") and chat_entity.last_name:
                    chat_name += " " + chat_entity.last_name
                chat_name = chat_name.strip() or str(chat_id)
            except Exception as e:
                self.logger.debug(f"Get chat entity failed: {e}")
                chat_name = str(chat_id)

            now = datetime.now(timezone.utc)
            time_str = now.strftime("%Y/%m/%d %H:%M:%S")

            self._cache[chat_id][msg.id] = {
                "text": msg.text or "",
                "media": msg.media,
                "sender_name": sender_name,
                "sender_id": sender_id,
                "is_me": is_me,
                "chat_name": chat_name,
                "chat_id": chat_id,
                "time_str": time_str,
                "date": msg.date,
            }

            self.logger.debug(f"Cached msg {msg.id} in chat {chat_id}")

        self._add_handler(cache_message, events.NewMessage)

        async def on_delete(event):
            self.logger.info(f"Delete event: chat={event.chat_id}, ids={event.deleted_ids}")

            if not event.is_private:
                self.logger.debug(f"Skipping non-private chat {event.chat_id}")
                return

            for msg_id in event.deleted_ids:
                chat_id = event.chat_id

                if chat_id not in self._cache:
                    self.logger.debug(f"Chat {chat_id} not in cache")
                    continue

                if msg_id not in self._cache[chat_id]:
                    self.logger.debug(f"Msg {msg_id} not in cache")
                    continue

                cached = self._cache[chat_id].pop(msg_id)
                self.logger.info(f"Found cached deleted msg {msg_id} from {cached.get('sender_name')}")

                if cached.get("date"):
                    now = datetime.now(timezone.utc)
                    msg_date = cached["date"]
                    if hasattr(msg_date, 'tzinfo') and msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)
                    diff = (now - msg_date).total_seconds()
                    if diff > 86400 * 7:
                        self.logger.info(f"Skipped old msg {msg_id} ({diff:.0f}s)")
                        continue

                await self._send_deleted(cached)

        self._add_handler(on_delete, events.MessageDeleted)
        self.logger.info("AntiDelete loaded")

    async def _get_dest_peer(self):
        """یافتن مقصد با نرمال‌سازی آیدی کانال"""
        target = await db.get_storage_target(self.user_id, "anti_delete")
        self.logger.debug(f"Storage target: {target}")

        dest_id = self._my_id
        if target and target.get("target_id"):
            dest_id = target["target_id"]

        self.logger.debug(f"Dest ID raw: {dest_id}")

        if dest_id == self._my_id:
            return self._my_id

        # نرمال‌سازی: اگر عدد مثبت بزرگ باشه، ممکنه کانال باشه
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

    async def _send_deleted(self, cached):
        self.logger.info("Sending deleted message to storage")

        dest_peer = await self._get_dest_peer()
        self.logger.debug(f"Dest peer: {dest_peer}")

        sender = cached.get("sender_name", "نامشخص")
        is_me = cached.get("is_me", False)
        chat_name = cached.get("chat_name", "نامشخص")
        text = cached.get("text", "")
        time_str = cached.get("time_str", "")

        header = (
            f"🗑 **پیام حذف شده**\n"
            f"💬 چت: {chat_name}\n"
            f"📌 {'طرف مقابل حذف کرد' if is_me else f'{sender} حذف کرد'}\n"
            f"👤 فرستنده: {sender}\n"
            f"📅 زمان: {time_str}\n"
        )

        if text:
            header += f"\n📝 متن:\n{text}"

        try:
            media = cached.get("media")
            if media:
                await self.client.send_file(dest_peer, media, caption=header)
            else:
                await self.client.send_message(dest_peer, header)
            self.logger.info(f"✅ Deleted msg saved | {sender} | {chat_name}")
        except Exception as e:
            self.logger.error(f"❌ Send failed: {type(e).__name__}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def stop(self):
        self._cache.clear()
        await super().stop()