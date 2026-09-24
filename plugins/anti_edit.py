"""
پلاگین ضد ویرایش — فقط PV

لایه‌ی داده در core/pv_cache.py است (کش مشترک با ضدحذف):
- دیگر برای هر پیام get_entity/get_me صدا زده نمی‌شود (نام‌ها از core.peers)
- گزارش‌ها از صف با فاصله و صبر واقعی روی FloodWait ارسال می‌شوند
- قالب گزارش دقیقاً همان قالب قبلی است
"""

from datetime import datetime, timezone

from telethon import events

from core import metrics, outbox, pv_cache, self_actions
from database import db
from plugins.base import BasePlugin

DEST_TTL = 60


class AntiEditPlugin(BasePlugin):
    name = "anti_edit"
    description = "ضد ویرایش"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._cache: pv_cache.PVCache | None = None
        self._outbox = None
        self._my_id = None
        self._dest = None
        self._dest_ts = 0.0

    async def start(self):
        self._cache = pv_cache.for_client(self.client, self.user_id)
        await self._cache.acquire(self.name, media=False)
        self._my_id = self._cache.my_id
        self._outbox = outbox.for_client(self.client)
        self._add_handler(self._on_edit, events.MessageEdited())
        self.logger.info("AntiEdit loaded")

    async def _on_edit(self, event):
        if not event.is_private:
            return
        msg = event.message
        if msg is None:
            return
        rec = self._cache.get(msg.id)
        if rec is None:
            return

        original_text = rec.text
        new_text = msg.text or ""
        if original_text == new_text:
            return          # ری‌اکشن/پیش‌نمایش لینک/تغییر غیرمتنی
        self._cache.update_text(rec, new_text)
        if not original_text:
            return          # مثل قبل: فقط پیام‌هایی که متن داشتند
        if self_actions.edited_by_self(self.client, msg.id):
            return          # ویرایش خود سلف‌بات (انیمیشن .قلب، .پنل، ...)

        edited_at = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
        self._outbox.submit(
            lambda: self._send_edit(rec, original_text, new_text, edited_at),
            label=f"edit {rec.msg_id}",
        )

    async def _send_edit(self, rec, original_text: str, new_text: str, now: str):
        peers = self._cache.peers
        sender_id = rec.sender_id
        is_me = rec.is_me

        if is_me:
            sender_name = "شما"
        else:
            info = await peers.get(sender_id) if sender_id else None
            if info is not None:
                sender_name = info.full_name or str(sender_id)
            else:
                sender_name = str(sender_id) if sender_id else "نامشخص"

        cinfo = await peers.get(rec.chat_id)
        chat_name = (cinfo.full_name if cinfo else "") or str(rec.chat_id)

        dest_peer = await self._get_dest_peer()

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

        await self.client.send_message(dest_peer, text)
        metrics.inc("edit_reported")
        self.logger.info(f"✅ Edited msg saved | id={rec.msg_id}")

    async def _get_dest_peer(self):
        import time
        now = time.monotonic()
        if self._dest is not None and now - self._dest_ts < DEST_TTL:
            return self._dest

        target = await db.get_storage_target(self.user_id, "anti_edit")
        dest_id = self._my_id
        if target and target.get("target_id"):
            dest_id = target["target_id"]

        if dest_id == self._my_id:
            peer = self._my_id
        else:
            if isinstance(dest_id, int) and 0 < dest_id < 10000000000:
                dest_id = int(f"-100{dest_id}")
            try:
                peer = await self.client.get_input_entity(dest_id)
            except Exception:
                try:
                    peer = await self.client.get_entity(dest_id)
                except Exception:
                    peer = dest_id
        self._dest, self._dest_ts = peer, now
        return peer

    async def stop(self):
        if self._cache is not None:
            await self._cache.release(self.name)
        await super().stop()
