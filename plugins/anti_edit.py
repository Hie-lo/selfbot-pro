"""
پلاگین ضد ویرایش
نسخه اصلی پیام قبل از ویرایش ذخیره میشه
کامند: .ضدویرایش (اطلاعات)
"""

from telethon import events
from plugins.base import BasePlugin
from database import db


class AntiEditPlugin(BasePlugin):
    name = "anti_edit"
    description = "ضد ویرایش"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        # کش متن اصلی: {chat_id: {msg_id: original_text}}
        self._originals: dict[int, dict[int, str]] = {}
        self._max_cache = 500

    async def start(self):
        me = await self.client.get_me()
        my_id = me.id

        async def cache_original(event):
            """کش متن اصلی پیام‌های جدید"""
            if not event.message or not event.message.text:
                return

            chat_id = event.chat_id
            if chat_id not in self._originals:
                self._originals[chat_id] = {}

            if len(self._originals[chat_id]) >= self._max_cache:
                oldest = min(self._originals[chat_id].keys())
                del self._originals[chat_id][oldest]

            self._originals[chat_id][event.message.id] = event.message.text

        self._add_handler(cache_original, events.NewMessage)

        async def on_edit(event):
            """وقتی پیامی ویرایش شد"""
            if not event.message:
                return

            chat_id = event.chat_id
            msg_id = event.message.id
            new_text = event.message.text or ""

            # پیدا کردن متن اصلی
            original = ""
            if chat_id in self._originals and msg_id in self._originals[chat_id]:
                original = self._originals[chat_id][msg_id]
                # آپدیت کش با متن جدید
                self._originals[chat_id][msg_id] = new_text

            # اگه متن تغییر نکرده بود
            if original == new_text:
                return

            # فرستنده
            sender_name = "نامشخص"
            try:
                if event.message.sender:
                    sender_name = getattr(event.message.sender, "first_name", "") or str(event.message.sender_id)
            except Exception:
                pass

            # ارسال به مقصد
            target = await db.get_storage_target(self.user_id, "anti_edit")
            dest_id = my_id
            if target:
                dest_id = target["target_id"] or my_id

            text = (
                f"✏️ **پیام ویرایش شده**\n"
                f"👤 فرستنده: {sender_name}\n\n"
                f"📝 **متن قبلی:**\n{original}\n\n"
                f"📝 **متن جدید:**\n{new_text}"
            )

            try:
                await self.client.send_message(dest_id, text)
                self.logger.info(f"Edited msg {msg_id} saved")
            except Exception as e:
                self.logger.error(f"Send edit failed: {e}")

        self._add_handler(on_edit, events.MessageEdited)

        self.logger.info("loaded")

    async def stop(self):
        self._originals.clear()
        await super().stop()