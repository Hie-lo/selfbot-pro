"""
پلاگین مانیتور کانال (Routing حرفه‌ای)
کامندها:
  .مانیتور @source @dest    — ست کردن مسیر
  .مانیتور حذف @source       — حذف مسیر
  .لیست مانیتور             — نمایش لیست مسیرها
"""

from telethon import events
from plugins.base import BasePlugin
from database import db


class ChannelMonitorPlugin(BasePlugin):
    name = "channel_monitor"
    description = "مانیتور کانال"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        # {source_id: {dest_id, dest_type, ...}}
        self._routes: dict[int, dict] = {}

    async def start(self):
        # بارگذاری مسیرها از دیتابیس
        await self._load_routes()

        async def cmd_handler(event):
            if not event.out:
                return
            
            text = event.message.text.strip()
            
            if text == ".لیست مانیتور":
                await self._list_monitor(event)
            elif text.startswith(".مانیتور حذف"):
                await self._remove_monitor(event)
            elif text.startswith(".مانیتور"):
                await self._add_monitor(event)

        self._add_handler(
            cmd_handler,
            events.NewMessage(pattern=r"^\.(مانیتور|لیست مانیتور)", outgoing=True)
        )

        # هندلر اصلی رصد پیام‌ها
        async def monitor_listener(event):
            if event.is_private or event.is_group:
                return # فقط کانال‌ها
            
            source_id = event.chat_id
            if source_id not in self._routes:
                return

            route = self._routes[source_id]
            dest_id = route["dest_id"]
            
            try:
                # فوروارد یا ارسال کپی (ما اینجا کپی می‌فرستیم که تگ منبع نیفته، یا به انتخاب خودت)
                await self.client.send_message(
                    dest_id,
                    event.message
                )
                self.logger.info(f"Forwarded from {source_id} to {dest_id}")
            except Exception as e:
                self.logger.error(f"Monitor forward error: {e}")

        self._add_handler(monitor_listener, events.NewMessage)
        self.logger.info("loaded")

    async def _add_monitor(self, event):
        """ .مانیتور @source @dest """
        parts = event.message.text.split()
        if len(parts) < 3:
            await event.delete()
            await self.client.send_message(event.chat_id, "❌ فرمت درست: `.مانیتور @منبع @مقصد`")
            return

        src_ref = parts[1]
        dst_ref = parts[2]

        try:
            src_entity = await self.client.get_entity(src_ref)
            dst_entity = await self.client.get_entity(dst_ref)
            
            src_id = src_entity.id
            dst_id = dst_entity.id
            src_title = getattr(src_entity, 'title', src_ref)
            dst_title = getattr(dst_entity, 'title', dst_ref)

            await db.set_channel_route(
                self.user_id, src_id, src_title, "custom", dst_id, dst_title
            )
            
            self._routes[src_id] = {"dest_id": dst_id, "dest_title": dst_title}
            
            await event.delete()
            await self.client.send_message(
                event.chat_id, 
                f"✅ مانیتور تنظیم شد:\nمنبع: {src_title}\nمقصد: {dst_title}"
            )
        except Exception as e:
            await event.delete()
            await self.client.send_message(event.chat_id, f"❌ خطا در یافتن کانال: {e}")

    async def _remove_monitor(self, event):
        parts = event.message.text.split()
        if len(parts) < 3:
            return
        
        src_ref = parts[2]
        try:
            src_entity = await self.client.get_entity(src_ref)
            src_id = src_entity.id
            
            await db.delete_channel_route(self.user_id, src_id)
            self._routes.pop(src_id, None)
            
            await event.delete()
            await self.client.send_message(event.chat_id, f"✅ مانیتور کانال {src_ref} حذف شد.")
        except Exception as e:
            await event.delete()
            await self.client.send_message(event.chat_id, f"❌ خطا: {e}")

    async def _list_monitor(self, event):
        if not self._routes:
            await event.delete()
            await self.client.send_message(event.chat_id, "📭 لیستی وجود ندارد.")
            return
        
        text = "📡 **مسیرهای مانیتور:**\n\n"
        for src_id, data in self._routes.items():
            text += f"🔹 از: `{src_id}`\n🔸 به: {data['dest_title']}\n\n"
        
        await event.delete()
        await self.client.send_message(event.chat_id, text)

    async def _load_routes(self):
        routes = await db.get_channel_routes(self.user_id)
        for r in routes:
            self._routes[r["source_channel_id"]] = {
                "dest_id": r["destination_id"],
                "dest_title": r["destination_title"]
            }
        if routes:
            self.logger.info(f"loaded {len(routes)} monitor routes")

    async def stop(self):
        self._routes.clear()
        await super().stop()