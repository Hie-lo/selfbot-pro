"""
پلاگین مانیتور کانال
کامندها:
  .مانیتور @source @dest
  .مانیتور حذف @source
  .لیست مانیتور
"""

from telethon import events
from telethon import utils
from plugins.base import BasePlugin
from database import db


class ChannelMonitorPlugin(BasePlugin):
    name = "channel_monitor"
    description = "مانیتور کانال"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._routes: dict[int, dict] = {}

    async def start(self):
        await self._load_routes()

        self.logger.info(f"ChannelMonitor loaded with {len(self._routes)} routes")

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
            events.NewMessage(pattern=r"^\.(مانیتور|لیست مانیتور)", outgoing=True),
        )

        async def monitor_listener(event):
            if event.out:
                return

            # دریافت آیدی نرمال‌شده کانال
            source_id = event.chat_id
            
            # اگر event.chat_id نداریم، از peer_id استفاده کن
            if source_id is None and hasattr(event, 'message') and event.message:
                peer = event.message.peer_id
                if hasattr(peer, 'channel_id'):
                    source_id = peer.channel_id

            if source_id is None:
                return

            # نرمال‌سازی آیدی
            normalized_id = self._normalize_channel_id(source_id)

            self.logger.debug(f"Monitor: raw_id={source_id}, normalized={normalized_id}, routes={list(self._routes.keys())}")

            if normalized_id not in self._routes:
                return

            route = self._routes[normalized_id]
            dest_id = route["dest_id"]

            self.logger.info(f"Monitor hit: {normalized_id} -> {dest_id}")

            try:
                if event.message.media:
                    await self.client.send_file(
                        dest_id,
                        event.message.media,
                        caption=event.message.text or "",
                    )
                else:
                    await self.client.send_message(
                        dest_id,
                        event.message.text or "",
                    )
                self.logger.info(f"✅ Forwarded OK")
            except Exception as e:
                self.logger.error(f"❌ Forward error: {type(e).__name__}: {e}")

        self._add_handler(monitor_listener, events.NewMessage)

    def _normalize_channel_id(self, chat_id):
        """تبدیل آیدی کانال به فرمت استاندارد -100..."""
        if chat_id is None:
            return None
        
        # اگر از قبل -100 داره
        if isinstance(chat_id, int) and chat_id < 0:
            return chat_id
        
        # اگر مثبته، تبدیل به کانال
        if isinstance(chat_id, int) and chat_id > 0:
            return int(f"-100{chat_id}")
        
        return chat_id

    async def reload_routes(self):
        self._routes.clear()
        await self._load_routes()
        
        # نرمال‌سازی همه آیدی‌ها
        normalized_routes = {}
        for src_id, data in self._routes.items():
            norm_id = self._normalize_channel_id(src_id)
            if norm_id:
                normalized_routes[norm_id] = data
        
        self._routes = normalized_routes
        self.logger.info(f"Routes reloaded: {len(self._routes)} active")

    async def _add_monitor(self, event):
        parts = event.message.text.split()
        if len(parts) < 3:
            await event.delete()
            await self.client.send_message(
                event.chat_id, "❌ فرمت: `.مانیتور @منبع @مقصد`"
            )
            return

        try:
            src_entity = await self.client.get_entity(parts[1])
            dst_entity = await self.client.get_entity(parts[2])

            src_id = utils.get_peer_id(src_entity)
            dst_id = utils.get_peer_id(dst_entity)
            src_title = getattr(src_entity, "title", parts[1])
            dst_title = getattr(dst_entity, "title", parts[2])

            await db.set_channel_route(
                self.user_id, src_id, src_title, "custom", dst_id, dst_title
            )
            
            # نرمال‌سازی برای کش داخلی
            norm_src_id = self._normalize_channel_id(src_id)
            self._routes[norm_src_id] = {
                "dest_id": dst_id,
                "dest_title": dst_title,
            }

            await event.delete()
            await self.client.send_message(
                event.chat_id,
                f"✅ مانیتور:\n📥 {src_title} (`{src_id}`)\n📤 {dst_title} (`{dst_id}`)"
            )
            self.logger.info(f"Route added: {src_id} -> {dst_id}")

        except Exception as e:
            await event.delete()
            await self.client.send_message(event.chat_id, f"❌ خطا: {e}")

    async def _remove_monitor(self, event):
        parts = event.message.text.split()
        if len(parts) < 3:
            await event.delete()
            await self.client.send_message(
                event.chat_id, "❌ فرمت: `.مانیتور حذف @منبع`"
            )
            return

        try:
            src_entity = await self.client.get_entity(parts[2])
            src_id = utils.get_peer_id(src_entity)
            norm_src_id = self._normalize_channel_id(src_id)
            
            await db.delete_channel_route(self.user_id, src_id)
            self._routes.pop(norm_src_id, None)
            
            await event.delete()
            await self.client.send_message(event.chat_id, "✅ حذف شد.")
            self.logger.info(f"Route removed: {src_id}")
        except Exception as e:
            await event.delete()
            await self.client.send_message(event.chat_id, f"❌ خطا: {e}")

    async def _list_monitor(self, event):
        if not self._routes:
            await event.delete()
            await self.client.send_message(event.chat_id, "📭 خالی.")
            return

        text = "📡 **مسیرها:**\n\n"
        for i, (src_id, data) in enumerate(self._routes.items(), 1):
            text += f"{i}. `{src_id}` → {data['dest_title']}\n"

        await event.delete()
        await self.client.send_message(event.chat_id, text)

    async def _load_routes(self):
        routes = await db.get_channel_routes(self.user_id)
        for r in routes:
            src_id = self._normalize_channel_id(r["source_channel_id"])
            if src_id:
                self._routes[src_id] = {
                    "dest_id": r["destination_id"],
                    "dest_title": r["destination_title"],
                }
        if routes:
            self.logger.info(f"loaded {len(routes)} routes")

    async def stop(self):
        self._routes.clear()
        await super().stop()