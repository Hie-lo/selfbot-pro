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
        self._max_cache = 500
        self._my_id = None

    async def start(self):
        me = await self.client.get_me()
        self._my_id = me.id

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

            if len(self._originals[chat_id]) >= self._max_cache:
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
                except Exception:
                    sender_name = str(sender_id) if sender_id else "نامشخص"

            self._originals[chat_id][msg_id] = {
                "text": event.message.text,
                "sender_name": sender_name,
                "sender_id": sender_id,
                "is_me": is_me,
            }

        self._add_handler(cache_original, events.NewMessage)

        async def on_edit(event):
            if not event.is_private:
                return
            if not event.message:
                return

            chat_id = event.chat_id
            msg_id = event.message.id
            new_text = event.message.text or ""

            original_data = None
            if chat_id in self._originals and msg_id in self._originals[chat_id]:
                original_data = self._originals[chat_id][msg_id]
                self._originals[chat_id][msg_id]["text"] = new_text

            original_text = original_data["text"] if original_data else "(کش نشده)"
            sender_name = original_data["sender_name"] if original_data else "نامشخص"
            sender_id = original_data["sender_id"] if original_data else ""
            is_me = original_data["is_me"] if original_data else False

            if original_text == new_text:
                return

            chat_name = "نامشخص"
            try:
                chat_entity = await self.client.get_entity(chat_id)
                chat_name = getattr(chat_entity, "first_name", "") or ""
                if hasattr(chat_entity, "last_name") and chat_entity.last_name:
                    chat_name += " " + chat_entity.last_name
                chat_name = chat_name.strip() or str(chat_id)
            except Exception:
                chat_name = str(chat_id)

            # فیکس مسیر ذخیره‌سازی
            target = await db.get_storage_target(self.user_id, "anti_edit")
            dest_id = self._my_id
            if target and target.get("target_id"):
                dest_id = target["target_id"]

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
                await self.client.send_message(dest_id, text)
                self.logger.info(f"Edited msg saved | {sender_name}")
            except Exception as e:
                self.logger.error(f"Send edit failed: {e}")

        self._add_handler(on_edit, events.MessageEdited)

        self.logger.info("loaded")

    async def stop(self):
        self._originals.clear()
        await super().stop()