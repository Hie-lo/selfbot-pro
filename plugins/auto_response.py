"""
پلاگین پاسخ خودکار (دشمن)
کامندها:
  .دشمن                    — ریپلای روی پیام کسی
  .دشمن @username          — با یوزرنیم
  .دشمن حذف                — ریپلای برای حذف
  .دشمن حذف @username      — حذف با یوزرنیم
  .لیست دشمن               — نمایش لیست
"""

import random
import time
from telethon import events
from plugins.base import BasePlugin
from database import db


DEFAULT_RESPONSES = [
    "🤡", "😂", "خفه", "برو بابا", "😑",
    "حوصلتو ندارم", "🖕", "بیکاری؟", "😒",
]


class AutoResponsePlugin(BasePlugin):
    name = "auto_response"
    description = "پاسخ خودکار (دشمن)"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._enemies: dict[int, dict] = {}
        self._cooldowns: dict[int, float] = {}
        self._my_id = None

    async def start(self):
        me = await self.client.get_me()
        self._my_id = me.id

        await self._load_rules()

        async def cmd_handler(event):
            """هندلر واحد برای همه کامندهای دشمن"""
            if not event.out:
                return

            text = event.message.text.strip()

            # .لیست دشمن
            if text == ".لیست دشمن":
                await self._list_enemies(event)
                return

            # .دشمن حذف ...
            if text.startswith(".دشمن حذف"):
                await self._remove_enemy(event)
                return

            # .دشمن ...
            if text.startswith(".دشمن"):
                await self._add_enemy(event)
                return

        self._add_handler(
            cmd_handler,
            events.NewMessage(pattern=r"^\.(دشمن|لیست دشمن)", outgoing=True),
        )

        async def auto_reply(event):
            """پاسخ خودکار به دشمنان — فقط PV"""
            if event.out:
                return
            if not event.is_private:
                return

            sender_id = event.sender_id
            if sender_id not in self._enemies:
                return

            now = time.time()
            last = self._cooldowns.get(sender_id, 0)
            if now - last < 5:
                return
            self._cooldowns[sender_id] = now

            responses = self._enemies[sender_id].get("responses", DEFAULT_RESPONSES)
            response = random.choice(responses)

            try:
                await event.reply(response)
                self.logger.info(f"Auto-replied to {sender_id}")
            except Exception as e:
                self.logger.error(f"Auto-reply error: {e}")

        self._add_handler(auto_reply, events.NewMessage)

        self.logger.info("loaded")

    async def _add_enemy(self, event):
        """اضافه کردن دشمن"""
        text = event.message.text.strip()
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        target_id = None
        target_name = "نامشخص"

        # اول ریپلای چک کن
        if event.is_reply:
            reply = await event.get_reply_message()
            if reply and reply.sender_id:
                target_id = reply.sender_id
                try:
                    entity = await self.client.get_entity(target_id)
                    target_name = getattr(entity, "first_name", str(target_id))
                except Exception:
                    target_name = str(target_id)

        # اگه ریپلای نبود، از آرگومان
        if not target_id and arg and arg.startswith("@"):
            try:
                entity = await self.client.get_entity(arg)
                target_id = entity.id
                target_name = getattr(entity, "first_name", arg)
            except Exception:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ کاربر پیدا نشد.")
                return

        if not target_id:
            await event.delete()
            await self.client.send_message(
                event.chat_id,
                "❌ روی پیام ریپلای کنید یا @username بدید."
            )
            return

        if target_id == self._my_id:
            await event.delete()
            return

        await db.save_auto_response_rule(
            self.user_id, target_id, DEFAULT_RESPONSES,
        )
        self._enemies[target_id] = {
            "name": target_name,
            "responses": DEFAULT_RESPONSES,
        }

        await event.delete()
        await self.client.send_message(
            event.chat_id,
            f"😈 {target_name} به لیست دشمنان اضافه شد!"
        )
        self.logger.info(f"Enemy added: {target_name} ({target_id})")

    async def _remove_enemy(self, event):
        """حذف دشمن"""
        text = event.message.text.strip()
        # .دشمن حذف @username یا .دشمن حذف (ریپلای)
        parts = text.split(maxsplit=2)
        arg = parts[2].strip() if len(parts) > 2 else ""

        target_id = None

        if event.is_reply:
            reply = await event.get_reply_message()
            if reply:
                target_id = reply.sender_id

        if not target_id and arg and arg.startswith("@"):
            try:
                entity = await self.client.get_entity(arg)
                target_id = entity.id
            except Exception:
                pass

        if target_id and target_id in self._enemies:
            name = self._enemies.pop(target_id, {}).get("name", str(target_id))
            await db.delete_auto_response_rule(self.user_id, target_id)
            await event.delete()
            await self.client.send_message(
                event.chat_id, f"✅ {name} از لیست حذف شد."
            )
            self.logger.info(f"Enemy removed: {name} ({target_id})")
        else:
            await event.delete()
            await self.client.send_message(event.chat_id, "❌ این کاربر در لیست نیست.")

    async def _list_enemies(self, event):
        """نمایش لیست دشمنان"""
        if not self._enemies:
            await event.delete()
            await self.client.send_message(event.chat_id, "📭 لیست دشمنان خالیه.")
            return

        text = "😈 **لیست دشمنان:**\n\n"
        for i, (uid, info) in enumerate(self._enemies.items(), 1):
            text += f"{i}. {info['name']} (`{uid}`)\n"

        await event.delete()
        await self.client.send_message(event.chat_id, text)

    async def _load_rules(self):
        """بارگذاری از DB"""
        rules = await db.get_auto_response_rules(self.user_id)
        for r in rules:
            tid = r["target_user_id"]
            responses = r.get("response_list", DEFAULT_RESPONSES)
            if isinstance(responses, str):
                import json
                responses = json.loads(responses)
            self._enemies[tid] = {
                "name": str(tid),
                "responses": responses,
            }
        if rules:
            self.logger.info(f"loaded {len(rules)} enemies")

    async def stop(self):
        self._enemies.clear()
        self._cooldowns.clear()
        await super().stop()