"""
پلاگین مانیتور کانال
کامندها:
  .مانیتور @source @dest
  .مانیتور حذف @source
  .لیست مانیتور
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
        self._routes: dict[int, dict] = {}

    async def start(self):
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
            events.NewMessage(pattern=r"^\.(مانیتور|لیست مانیتور)", outgoing=True),
        )

        async def monitor_listener(event):
            # پیام‌های خودمون رو رد کن
            if event.out:
                return

            source_id = event.chat_id

            # چک کن آیا این chat_id در مسیرهای ما هست
            if source_id not in self._routes:
                return

            route = self._routes[source_id]
            dest_id = route["dest_id"]

            self.logger.info(
                f"Monitor hit: source={source_id} dest={dest_id} "
                f"msg_id={event.message.id}"
            )

            try:
                # ارسال کپی پیام
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

                self.logger.info(f"Forwarded {source_id} -> {dest_id}")

            except Exception as e:
                self.logger.error(f"Monitor forward error: {type(e).__name__}: {e}")

        self._add_handler(monitor_listener, events.NewMessage)
        self.logger.info(f"loaded with {len(self._routes)} routes")

    async def reload_routes(self):
        self._routes.clear()
        await self._load_routes()
        self.logger.info(f"routes reloaded: {len(self._routes)} active")

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

            src_id = src_entity.id
            dst_id = dst_entity.id
            src_title = getattr(src_entity, "title", parts[1])
            dst_title = getattr(dst_entity, "title", parts[2])

            await db.set_channel_route(
                self.user_id, src_id, src_title, "custom", dst_id, dst_title
            )
            self._routes[src_id] = {
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
            src_id = src_entity.id
            await db.delete_channel_route(self.user_id, src_id)
            self._routes.pop(src_id, None)
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
            self._routes[r["source_channel_id"]] = {
                "dest_id": r["destination_id"],
                "dest_title": r["destination_title"],
            }
        if routes:
            self.logger.info(f"loaded {len(routes)} routes")

    async def stop(self):
        self._routes.clear()
        await super().stop()