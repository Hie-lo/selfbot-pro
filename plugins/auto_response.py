"""
پلاگین پاسخ خودکار (دشمن)
کامندها:
  .دشمن @username یا reply     — اضافه کردن
  .دشمن حذف @username یا reply — حذف کردن
  .لیست دشمن                   — نمایش لیست
"""

import asyncio
import random
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

    async def start(self):
        me = await self.client.get_me()
        my_id = me.id

        # بارگذاری از DB
        await self._load_rules()

        async def add_enemy(event):
            if not event.out:
                return

            target_id = None
            target_name = "نامشخص"

            # اگه ریپلای کرده
            if event.is_reply:
                reply = await event.get_reply_message()
                if reply and reply.sender_id:
                    target_id = reply.sender_id
                    try:
                        entity = await self.client.get_entity(target_id)
                        target_name = getattr(entity, "first_name", str(target_id))
                    except Exception:
                        target_name = str(target_id)
            else:
                # از متن بعد از کامند
                text = event.pattern_match.group(1).strip()
                if text.startswith("@"):
                    try:
                        entity = await self.client.get_entity(text)
                        target_id = entity.id
                        target_name = getattr(entity, "first_name", text)
                    except Exception:
                        await event.delete()
                        await self.client.send_message(event.chat_id, "❌ کاربر پیدا نشد.")
                        return

            if not target_id:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ روی پیام ریپلای کنید یا @username بدید.")
                return

            if target_id == my_id:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ نمیتونید خودتون رو دشمن کنید!")
                return

            # ذخیره
            await db.save_auto_response_rule(
                self.user_id, target_id, DEFAULT_RESPONSES,
            )
            self._enemies[target_id] = {"name": target_name, "responses": DEFAULT_RESPONSES}

            await event.delete()
            await self.client.send_message(event.chat_id, f"😈 {target_name} به لیست دشمنان اضافه شد!")

        self._add_handler(
            add_enemy,
            events.NewMessage(pattern=r"^\.دشمن\s+(.+)$", outgoing=True),
        )

        async def add_enemy_reply(event):
            """اضافه کردن با ریپلای بدون آرگومان"""
            if not event.out:
                return
            if not event.is_reply:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ روی پیام ریپلای کنید یا @username بدید.")
                return

            reply = await event.get_reply_message()
            if not reply or not reply.sender_id:
                return

            target_id = reply.sender_id
            if target_id == my_id:
                await event.delete()
                return

            try:
                entity = await self.client.get_entity(target_id)
                target_name = getattr(entity, "first_name", str(target_id))
            except Exception:
                target_name = str(target_id)

            await db.save_auto_response_rule(
                self.user_id, target_id, DEFAULT_RESPONSES,
            )
            self._enemies[target_id] = {"name": target_name, "responses": DEFAULT_RESPONSES}

            await event.delete()
            await self.client.send_message(event.chat_id, f"😈 {target_name} به لیست دشمنان اضافه شد!")

        self._add_handler(
            add_enemy_reply,
            events.NewMessage(pattern=r"^\.دشمن$", outgoing=True),
        )

        async def remove_enemy(event):
            if not event.out:
                return

            target_id = None

            if event.is_reply:
                reply = await event.get_reply_message()
                if reply:
                    target_id = reply.sender_id
            else:
                text = event.pattern_match.group(1).strip()
                if text.startswith("@"):
                    try:
                        entity = await self.client.get_entity(text)
                        target_id = entity.id
                    except Exception:
                        pass

            if target_id and target_id in self._enemies:
                name = self._enemies.pop(target_id, {}).get("name", "")
                await db.delete_auto_response_rule(self.user_id, target_id)
                await event.delete()
                await self.client.send_message(event.chat_id, f"✅ {name} از لیست دشمنان حذف شد.")
            else:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ این کاربر در لیست نیست.")

        self._add_handler(
            remove_enemy,
            events.NewMessage(pattern=r"^\.دشمن حذف\s*(.*)$", outgoing=True),
        )

        async def list_enemies(event):
            if not event.out:
                return

            if not self._enemies:
                await event.delete()
                await self.client.send_message(event.chat_id, "📭 لیست دشمنان خالیه.")
                return

            text = "😈 **لیست دشمنان:**\n\n"
            for i, (uid, info) in enumerate(self._enemies.items(), 1):
                text += f"{i}. {info['name']} (`{uid}`)\n"

            await event.delete()
            await self.client.send_message(event.chat_id, text)

        self._add_handler(
            list_enemies,
            events.NewMessage(pattern=r"^\.لیست دشمن$", outgoing=True),
        )

        async def auto_reply(event):
            """پاسخ خودکار به دشمنان"""
            if event.out:
                return
            if not event.is_private:
                return

            sender_id = event.sender_id
            if sender_id not in self._enemies:
                return

            # cooldown
            import time
            now = time.time()
            last = self._cooldowns.get(sender_id, 0)
            if now - last < 5:
                return
            self._cooldowns[sender_id] = now

            responses = self._enemies[sender_id].get("responses", DEFAULT_RESPONSES)
            response = random.choice(responses)

            try:
                await event.reply(response)
                self.logger.info(f"Auto-replied to enemy {sender_id}")
            except Exception as e:
                self.logger.error(f"Auto-reply error: {e}")

        self._add_handler(auto_reply, events.NewMessage)

        self.logger.info("loaded")

    async def _load_rules(self):
        rules = await db.get_auto_response_rules(self.user_id)
        for r in rules:
            self._enemies[r["target_user_id"]] = {
                "name": str(r["target_user_id"]),
                "responses": r.get("response_list", DEFAULT_RESPONSES),
            }
        if rules:
            self.logger.info(f"loaded {len(rules)} enemy rules")

    async def stop(self):
        self._enemies.clear()
        self._cooldowns.clear()
        await super().stop()