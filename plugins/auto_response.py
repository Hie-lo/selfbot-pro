"""
پلاگین پاسخ خودکار (دشمن) + اسپم
کامندها:
  .دشمن                  — ریپلای: اضافه کردن
  .دشمن @username        — اضافه با یوزرنیم
  .دشمن حذف              — ریپلای: حذف
  .دشمن حذف @username    — حذف با یوزرنیم
  .لیست دشمن             — نمایش لیست
  .بکنش                  — ریپلای: شروع اسپم
  .بس                    — توقف اسپم
"""

import random
import time
import asyncio
from telethon import events
from plugins.base import BasePlugin
from database import db


DEFAULT_RESPONSES = [
    "🤡", "😂", "خفه", "برو بابا", "😑",
    "حوصلتو ندارم", "🖕", "بیکاری؟", "😒",
    "گمشو", "🤮", "چرت نگو", "😤",
]

SPAM_MESSAGES = [
    "🤡🤡🤡", "خفه شو", "گمشو بابا", "😂😂😂",
    "کصخل", "🖕🖕", "خر", "احمق",
    "بی‌ناموس", "کیرم تو کص ننت", "مادرجنده",
    "بیا اینو بخور 🍆", "ننت جنده",
    "کونی", "چاقال", "بی‌شرف", "حرومزاده",
    "🤮🤮🤮", "ببند دهنتو", "آشغال",
    "کص ننت", "برو گمشو", "🖕🖕🖕",
    "خایه‌مال", "بچه کونی", "سیکتیر",
]


class AutoResponsePlugin(BasePlugin):
    name = "auto_response"
    description = "پاسخ خودکار (دشمن) + اسپم"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._enemies: dict[int, dict] = {}
        self._cooldowns: dict[int, float] = {}
        self._spam_tasks: dict[int, asyncio.Task] = {}
        self._my_id = None

    async def start(self):
        me = await self.client.get_me()
        self._my_id = me.id

        await self._load_rules()

        # ── کامندهای دشمن ──

        async def cmd_handler(event):
            if not event.out:
                return

            text = event.message.text.strip()

            if text == ".لیست دشمن":
                await self._list_enemies(event)
                return

            if text.startswith(".دشمن حذف"):
                await self._remove_enemy(event)
                return

            if text.startswith(".دشمن"):
                await self._add_enemy(event)
                return

        self._add_handler(
            cmd_handler,
            events.NewMessage(pattern=r"^\.(دشمن|لیست دشمن)", outgoing=True),
        )

        # ── اسپم ──

        async def spam_cmd(event):
            if not event.out:
                return
            if not event.is_reply:
                await event.delete()
                await self.client.send_message(
                    event.chat_id, "❌ روی پیام طرف ریپلای کنید."
                )
                return

            reply = await event.get_reply_message()
            if not reply or not reply.sender_id:
                await event.delete()
                return

            target_id = reply.sender_id
            if target_id == self._my_id:
                await event.delete()
                return

            chat_id = event.chat_id
            await event.delete()

            # اگه قبلاً اسپم فعاله، اول متوقف کن
            task_key = chat_id
            if task_key in self._spam_tasks:
                self._spam_tasks[task_key].cancel()

            # شروع اسپم
            self._spam_tasks[task_key] = asyncio.create_task(
                self._spam_loop(chat_id, reply.id, target_id)
            )

            self.logger.info(f"Spam started on {target_id} in {chat_id}")

        self._add_handler(
            spam_cmd,
            events.NewMessage(pattern=r"^\.بکنش$", outgoing=True),
        )

        async def stop_spam(event):
            if not event.out:
                return

            chat_id = event.chat_id
            await event.delete()

            if chat_id in self._spam_tasks:
                self._spam_tasks[chat_id].cancel()
                del self._spam_tasks[chat_id]
                await self.client.send_message(chat_id, "⏹ اسپم متوقف شد.")
                self.logger.info(f"Spam stopped in {chat_id}")
            else:
                await self.client.send_message(chat_id, "❌ اسپمی فعال نیست.")

        self._add_handler(
            stop_spam,
            events.NewMessage(pattern=r"^\.بس$", outgoing=True),
        )

        # ── پاسخ خودکار به دشمنان ──

        async def auto_reply(event):
            if event.out:
                return

            sender_id = event.sender_id
            if sender_id not in self._enemies:
                return

            now = time.time()
            last = self._cooldowns.get(sender_id, 0)
            if now - last < 3:
                return
            self._cooldowns[sender_id] = now

            responses = self._enemies[sender_id].get(
                "responses", DEFAULT_RESPONSES
            )
            response = random.choice(responses)

            try:
                await event.reply(response)
                self.logger.info(f"Auto-replied to {sender_id}")
            except Exception as e:
                self.logger.error(f"Auto-reply error: {e}")

        self._add_handler(auto_reply, events.NewMessage)

        self.logger.info("loaded")

    # ── اسپم loop ──

    async def _spam_loop(self, chat_id, reply_msg_id, target_id):
        """ارسال پیام‌های اسپم با ریپلای"""
        try:
            count = 0
            while True:
                msg_text = random.choice(SPAM_MESSAGES)

                try:
                    await self.client.send_message(
                        chat_id,
                        msg_text,
                        reply_to=reply_msg_id,
                    )
                    count += 1
                except Exception as e:
                    err = str(e).lower()
                    if "flood" in err:
                        # FloodWait
                        wait = 30
                        try:
                            wait = int("".join(filter(str.isdigit, str(e)))) or 30
                        except Exception:
                            pass
                        self.logger.warning(f"Spam flood, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    elif "slow" in err:
                        await asyncio.sleep(10)
                        continue
                    else:
                        self.logger.error(f"Spam error: {e}")
                        break

                # تاخیر تصادفی بین 1 تا 3 ثانیه
                delay = random.uniform(1.0, 3.0)
                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            self.logger.info(f"Spam cancelled in {chat_id}")
        finally:
            self._spam_tasks.pop(chat_id, None)

    # ── مدیریت دشمن ──

    async def _add_enemy(self, event):
        text = event.message.text.strip()
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        # اگه "حذف" بود، اینجا نباید باشیم
        if arg.startswith("حذف"):
            return

        target_id = None
        target_name = "نامشخص"

        if event.is_reply:
            reply = await event.get_reply_message()
            if reply and reply.sender_id:
                target_id = reply.sender_id
                try:
                    entity = await self.client.get_entity(target_id)
                    target_name = getattr(entity, "first_name", str(target_id))
                except Exception:
                    target_name = str(target_id)

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
            event.chat_id, f"😈 {target_name} به لیست دشمنان اضافه شد!"
        )
        self.logger.info(f"Enemy added: {target_name} ({target_id})")

    async def _remove_enemy(self, event):
        text = event.message.text.strip()
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
            self.logger.info(f"Enemy removed: {name}")
        else:
            await event.delete()
            await self.client.send_message(event.chat_id, "❌ در لیست نیست.")

    async def _list_enemies(self, event):
        if not self._enemies:
            await event.delete()
            await self.client.send_message(event.chat_id, "📭 لیست خالیه.")
            return

        text = "😈 **لیست دشمنان:**\n\n"
        for i, (uid, info) in enumerate(self._enemies.items(), 1):
            text += f"{i}. {info['name']} (`{uid}`)\n"

        await event.delete()
        await self.client.send_message(event.chat_id, text)

    async def _load_rules(self):
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
        for task in self._spam_tasks.values():
            task.cancel()
        self._spam_tasks.clear()
        self._enemies.clear()
        self._cooldowns.clear()
        await super().stop()