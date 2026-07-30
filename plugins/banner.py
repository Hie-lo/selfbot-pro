"""
پلاگین بنر تبلیغاتی
کامندها:
  .تبچی روشن/خاموش
  .تنظیم بنر 300    (ریپلای روی پیام)
  .لیست بنر
  .پاکسازی بنر
"""

import asyncio
from telethon import events
from plugins.base import BasePlugin
from database import db


class BannerPlugin(BasePlugin):
    name = "banner"
    description = "بنر تبلیغاتی"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self):

        async def set_banner(event):
            if not event.out:
                return
            interval = int(event.pattern_match.group(1))
            if interval < 10:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ حداقل ۱۰ ثانیه.")
                return
            if not event.is_reply:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ روی پیام ریپلای کنید.")
                return

            reply = await event.get_reply_message()
            chat_id = event.chat_id
            msg_id = reply.id

            await db.save_banner(self.user_id, chat_id, msg_id, interval)

            task_key = f"{chat_id}_{msg_id}"
            if task_key in self._tasks:
                self._tasks[task_key].cancel()
            self._tasks[task_key] = asyncio.create_task(
                self._loop(chat_id, msg_id, interval)
            )

            await event.delete()
            await self.client.send_message(chat_id, f"✅ بنر ثبت شد! (هر {interval} ثانیه)")

        self._add_handler(
            set_banner,
            events.NewMessage(pattern=r"^\.تنظیم بنر\s+(\d+)$", outgoing=True),
        )

        async def list_banners(event):
            if not event.out:
                return
            banners = await db.get_banners_for_chat(self.user_id, event.chat_id)
            if not banners:
                await event.delete()
                await self.client.send_message(event.chat_id, "📭 بنری نیست.")
                return
            text = "📋 **بنرها:**\n\n"
            for i, b in enumerate(banners, 1):
                text += f"{i}. پیام: {b['source_msg_id']} | هر {b['interval_seconds']}ث\n"
            await event.delete()
            await self.client.send_message(event.chat_id, text)

        self._add_handler(
            list_banners,
            events.NewMessage(pattern=r"^\.لیست بنر$", outgoing=True),
        )

        async def clear_banners(event):
            if not event.out:
                return
            chat_id = event.chat_id
            # توقف تسک‌ها
            to_cancel = [k for k in self._tasks if k.startswith(f"{chat_id}_")]
            for k in to_cancel:
                self._tasks[k].cancel()
                del self._tasks[k]
            await db.clear_banners(self.user_id, chat_id)
            await event.delete()
            await self.client.send_message(chat_id, f"✅ {len(to_cancel)} بنر حذف شد.")

        self._add_handler(
            clear_banners,
            events.NewMessage(pattern=r"^\.پاکسازی بنر$", outgoing=True),
        )

        # بارگذاری بنرهای ذخیره شده
        await self._load_saved()
        self.logger.info("loaded")

    async def _load_saved(self):
        banners = await db.get_all_banners(self.user_id)
        for b in banners:
            task_key = f"{b['chat_id']}_{b['source_msg_id']}"
            self._tasks[task_key] = asyncio.create_task(
                self._loop(b["chat_id"], b["source_msg_id"], b["interval_seconds"])
            )
        if banners:
            self.logger.info(f"loaded {len(banners)} saved banners")

    async def _loop(self, chat_id, msg_id, interval):
        task_key = f"{chat_id}_{msg_id}"
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    msg = await self.client.get_messages(chat_id, ids=msg_id)
                    if not msg:
                        self.logger.warning(f"Banner msg {msg_id} deleted")
                        break
                    if msg.media:
                        await self.client.send_file(chat_id, msg.media, caption=msg.text or "")
                    else:
                        await self.client.send_message(chat_id, msg.text or "")
                except Exception as e:
                    self.logger.error(f"Banner send error: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            self._tasks.pop(task_key, None)

    async def stop(self):
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        await super().stop()