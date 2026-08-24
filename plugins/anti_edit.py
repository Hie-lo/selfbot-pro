"""
پلاگین ضد ویرایش — فقط PV
"""

from datetime import datetime, timezone
from telethon import events
from plugins.base import BasePlugin
from database import db


class AntiEditPlugin(BasePlugin):
    name = "anti_edit"
    description = "ضد ویرایش"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._originals: dict[int, dict[int, dict]] = {}
        self._max_cache_per_chat = 500
        self._my_id = None

    async def start(self):
        me = await self.client.get_me()
        self._my_id = me.id

        self.logger.info(f"AntiEdit started for user {self.user_id}, my_id={self._my_id}")

        async def cache_original(event):
            if not event.is_private:
                return
            if not event.message or not event.message.text:
                return

            chat_id = event.chat_id
            msg_id = event.message.id
            sender_id = event.message.sender_id
            is_me = (sender_id == self._my_id)

            if chat_id not in self._originals:
                self._originals[chat_id] = {}

            if len(self._originals[chat_id]) >= self._max_cache_per_chat:
                oldest = min(self._originals[chat_id].keys())
                del self._originals[chat_id][oldest]

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

            self._originals[chat_id][msg_id] = {
                "text": event.message.text,
                "sender_name": sender_name,
                "sender_id": sender_id,
                "is_me": is_me,
            }

            self.logger.debug(f"Cached original msg {msg_id} in chat {chat_id}")

        self._add_handler(cache_original, events.NewMessage)

        async def on_edit(event):
            self.logger.info(
                f"Edit event: chat={event.chat_id}, "
                f"is_private={event.is_private}, "
                f"msg_id={event.message.id if event.message else None}"
            )

            if not event.is_private:
                self.logger.debug(f"Skipping non-private edit in chat {event.chat_id}")
                return
            if not event.message:
                return

            chat_id = event.chat_id
            msg_id = event.message.id
            new_text = event.message.text or ""

            # جستجو در تمام کش‌ها (برای handling chat_id=None)
            original_data = None
            found_chat_id = None

            for cid, msgs in self._originals.items():
                if msg_id in msgs:
                    original_data = msgs[msg_id]
                    found_chat_id = cid
                    # آپدیت کش با متن جدید
                    self._originals[cid][msg_id]["text"] = new_text
                    break

            if not original_data:
                self.logger.debug(f"Msg {msg_id} not found in any cache")
                return

            original_text = original_data.get("text", "")
            sender_name = original_data.get("sender_name", "نامشخص")
            sender_id = original_data.get("sender_id", "")
            is_me = original_data.get("is_me", False)

            # اگر متن تغییر نکرده بود
            if original_text == new_text:
                return

            # نام چت
            chat_name = "نامشخص"
            try:
                chat_entity = await self.client.get_entity(found_chat_id)
                chat_name = getattr(chat_entity, "first_name", "") or ""
                if hasattr(chat_entity, "last_name") and chat_entity.last_name:
                    chat_name += " " + chat_entity.last_name
                chat_name = chat_name.strip() or str(found_chat_id)
            except Exception as e:
                self.logger.debug(f"Get chat entity failed: {e}")
                chat_name = str(found_chat_id)

            dest_peer = await self._get_dest_peer()

            now = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")

            text = (
                f"✏️ **پیام ویرایش شده**\n"
                f"💬 چت: {chat_name}\n"
                f"👤 ویرایش‌کننده: {sender_name}"
            )
            if sender_id and not is_me:
                text += f" (`{sender_id}`)"
            text += (
                f"\n📅 زمان: {now}\n\n"
                f"📝 **متن قبلی:**\n{original_text}\n\n"
                f"📝 **متن جدید:**\n{new_text}"
            )

            try:
                await self.client.send_message(dest_peer, text)
                self.logger.info(f"✅ Edited msg saved | {sender_name} | {chat_name}")
            except Exception as e:
                self.logger.error(f"❌ Send edit failed: {type(e).__name__}: {e}")

        self._add_handler(on_edit, events.MessageEdited)
        self.logger.info("AntiEdit loaded")

    async def _get_dest_peer(self):
        """یافتن مقصد با نرمال‌سازی آیدی کانال"""
        target = await db.get_storage_target(self.user_id, "anti_edit")
        self.logger.debug(f"Storage target: {target}")

        dest_id = self._my_id
        if target and target.get("target_id"):
            dest_id = target["target_id"]

        self.logger.debug(f"Dest ID raw: {dest_id}")

        if dest_id == self._my_id:
            return self._my_id

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
        self._originals.clear()
        await super().stop()