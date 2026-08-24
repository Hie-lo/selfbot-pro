"""
پلاگین ضد ویرایش — فقط PV — نسخه اصلاح‌شده و ایمن
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
            
            # دریافت مطمئن پیام برای کش کردن
            msg = event.message
            if not msg:
                msg = await event.get_message()
            if not msg or not msg.text:
                return

            chat_id = event.chat_id
            msg_id = msg.id
            sender_id = msg.sender_id
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
                "text": msg.text,
                "sender_name": sender_name,
                "sender_id": sender_id,
                "is_me": is_me,
            }
            self.logger.debug(f"Cached original msg {msg_id} in chat {chat_id}")

        self._add_handler(cache_original, events.NewMessage)

        async def on_edit(event):
            # دریافت پیام ویرایش شده به صورت کاملاً امن از سرور تلگرام
            msg = event.message
            if not msg:
                msg = await event.get_message()
            
            if not msg:
                self.logger.debug("Failed to fetch edited message object")
                return

            self.logger.info(f"Edit event: chat={event.chat_id}, msg_id={msg.id}")

            if not event.is_private:
                return

            chat_id = event.chat_id
            msg_id = msg.id
            new_text = msg.text or ""

            # جستجوی سراسری در کش برای هندل کردن آیدی‌های None چت
            original_data = None
            found_chat_id = None

            for cid, msgs in self._originals.items():
                if msg_id in msgs:
                    original_data = msgs[msg_id]
                    found_chat_id = cid
                    self._originals[cid][msg_id]["text"] = new_text
                    break

            if not original_data:
                self.logger.debug(f"Original text for msg {msg_id} not found in cache (not pre-cached)")
                return

            original_text = original_data.get("text", "")
            sender_name = original_data.get("sender_name", "نامشخص")
            sender_id = original_data.get("sender_id", "")
            is_me = original_data.get("is_me", False)

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
        target = await db.get_storage_target(self.user_id, "anti_edit")
        dest_id = self._my_id
        if target and target.get("target_id"):
            dest_id = target["target_id"]

        if dest_id == self._my_id:
            return self._my_id

        if isinstance(dest_id, int) and dest_id > 0 and dest_id < 10000000000:
            dest_id = int(f"-100{dest_id}")

        try:
            return await self.client.get_entity(dest_id)
        except Exception:
            return dest_id

    async def stop(self):
        self._originals.clear()
        await super().stop()