"""
پلاگین ضد حذف پیام — فقط PV
"""

from datetime import datetime, timezone
from telethon import events
from plugins.base import BasePlugin
from core.media import send_media_clean
from database import db


class AntiDeletePlugin(BasePlugin):
    name = "anti_delete"
    description = "ضد حذف پیام"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._cache: dict[int, dict[int, dict]] = {}
        self._max_cache_per_chat = 500
        self._max_chats = 50
        self._my_id = None

    async def start(self):
        me = await self.client.get_me()
        self._my_id = me.id

        self.logger.info(f"AntiDelete started for user {self.user_id}, my_id={self._my_id}")

        async def cache_message(event):
            # فقط PV کش کن
            if not event.is_private:
                return
            if not event.message:
                return

            chat_id = event.chat_id
            msg = event.message

            if chat_id not in self._cache:
                # سقف تعداد چت‌های کش‌شده (جلوگیری از رشد بی‌نهایت حافظه)
                if len(self._cache) >= self._max_chats:
                    oldest_chat = next(iter(self._cache))
                    self._cache.pop(oldest_chat, None)
                self._cache[chat_id] = {}

            if len(self._cache[chat_id]) >= self._max_cache_per_chat:
                oldest = min(self._cache[chat_id].keys())
                del self._cache[chat_id][oldest]

            sender_id = msg.sender_id
            is_me = (sender_id == self._my_id)

            # — اطلاعات فرستنده با جزئیات مثل فوروارد —
            sender_name = "شما" if is_me else "نامشخص"
            sender_username = None
            sender_label = str(sender_id) if sender_id else ""
            if not is_me and sender_id:
                try:
                    sender = await self.client.get_entity(sender_id)
                    parts = [getattr(sender, "first_name", "") or "", getattr(sender, "last_name", "") or ""]
                    sender_name = " ".join(p for p in parts if p).strip() or str(sender_id)
                    sender_username = getattr(sender, "username", None)
                    if sender_username:
                        sender_label = f"@{sender_username} ({sender_id})"
                    else:
                        sender_label = str(sender_id)
                except Exception as e:
                    self.logger.debug(f"Get sender entity failed: {e}")
                    sender_name = str(sender_id) if sender_id else "نامشخص"
                    sender_label = str(sender_id) if sender_id else ""
            elif is_me:
                try:
                    me = await self.client.get_me()
                    sender_username = getattr(me, "username", None)
                    if sender_username:
                        sender_label = f"@{sender_username} ({self._my_id})"
                    else:
                        sender_label = str(self._my_id)
                except Exception:
                    sender_label = str(self._my_id)

            # — اطلاعات چت با جزئیات —
            chat_name = "نامشخص"
            chat_username = None
            chat_label = str(chat_id)
            try:
                chat_entity = await self.client.get_entity(chat_id)
                if hasattr(chat_entity, "title") and chat_entity.title:
                    chat_name = chat_entity.title.strip() or str(chat_id)
                else:
                    parts = [getattr(chat_entity, "first_name", "") or "", getattr(chat_entity, "last_name", "") or ""]
                    chat_name = " ".join(p for p in parts if p).strip() or str(chat_id)
                chat_username = getattr(chat_entity, "username", None)
                if chat_username:
                    chat_label = f"@{chat_username}"
                else:
                    # سعی کن peer_id را بگیری
                    try:
                        from telethon import utils as _utils
                        chat_label = str(_utils.get_peer_id(chat_entity))
                    except Exception:
                        chat_label = str(chat_id)
            except Exception as e:
                self.logger.debug(f"Get chat entity failed: {e}")
                chat_name = str(chat_id)

            now = datetime.now(timezone.utc)
            time_str = now.strftime("%Y/%m/%d %H:%M:%S")

            self._cache[chat_id][msg.id] = {
                "text": msg.text or "",
                "media": msg.media,
                "sender_name": sender_name,
                "sender_username": sender_username,
                "sender_label": sender_label,
                "sender_id": sender_id,
                "is_me": is_me,
                "chat_name": chat_name,
                "chat_username": chat_username,
                "chat_label": chat_label,
                "chat_id": chat_id,
                "time_str": time_str,
                "date": msg.date,
                "msg_id": msg.id,
            }

            self.logger.debug(f"Cached msg {msg.id} in chat {chat_id} ({chat_name})")

        self._add_handler(cache_message, events.NewMessage)

        async def on_delete(event):
            # لاگ اولیه
            self.logger.info(
                f"Delete event: chat={event.chat_id}, ids={event.deleted_ids}, "
                f"is_private={event.is_private}"
            )

            # Telethon گاهی chat_id رو None می‌فرسته برای PV
            # پس به جای تکیه بر event.chat_id، در تمام کش‌ها جستجو می‌کنیم

            # — جمع‌آوری همه پیام‌های حذف‌شده برای حفظ ترتیب —
            batch = []  # list of (msg_id, cached, found_chat_id)
            for msg_id in event.deleted_ids:
                cached = None
                found_chat_id = None
                for chat_id, msgs in self._cache.items():
                    if msg_id in msgs:
                        cached = msgs.pop(msg_id)
                        found_chat_id = chat_id
                        break
                if not cached:
                    self.logger.debug(f"Msg {msg_id} not found in any cache")
                    continue
                # فیلتر پیام‌های قدیمی
                if cached.get("date"):
                    now = datetime.now(timezone.utc)
                    msg_date = cached["date"]
                    if hasattr(msg_date, 'tzinfo') and msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)
                    diff = (now - msg_date).total_seconds()
                    if diff > 86400 * 7:
                        self.logger.info(f"Skipped old msg {msg_id} ({diff:.0f}s)")
                        continue
                batch.append((msg_id, cached, found_chat_id))

            if not batch:
                return

            # مرتب‌سازی بر اساس آیدی پیام (ترتیب زمانی اصلی) — مثل فوروارد
            batch.sort(key=lambda x: x[0])
            self.logger.info(f"Delete batch: {len(batch)} پیام مرتب شد (مرتب‌سازی بر اساس ID)")

            for _, cached, found_chat_id in batch:
                self.logger.info(
                    f"Found cached deleted msg {cached.get('msg_id')} from "
                    f"{cached.get('sender_name')} in chat {found_chat_id}"
                )
                await self._send_deleted(cached)

        self._add_handler(on_delete, events.MessageDeleted)
        self.logger.info("AntiDelete loaded")

    async def _get_dest_peer(self):
        """یافتن مقصد با نرمال‌سازی آیدی کانال"""
        target = await db.get_storage_target(self.user_id, "anti_delete")
        self.logger.debug(f"Storage target: {target}")

        dest_id = self._my_id
        if target and target.get("target_id"):
            dest_id = target["target_id"]

        self.logger.debug(f"Dest ID raw: {dest_id}")

        if dest_id == self._my_id:
            return self._my_id

        # نرمال‌سازی: اگر عدد مثبت بزرگه، ممکنه کانال باشه
        if isinstance(dest_id, int) and dest_id > 0 and dest_id < 10000000000:
            normalized = int(f"-100{dest_id}")
            self.logger.debug(f"Normalized {dest_id} -> {normalized}")
            dest_id = normalized

        try:
            entity = await self.client.get_entity(dest_id)
            self.logger.debug(f"Resolved entity: {getattr(entity, 'title', dest_id)}")
            return entity
        except Exception as e:
            self.logger.warning(f"Failed to resolve dest {dest_id}: {e}")
            return dest_id

    async def _send_deleted(self, cached):
        self.logger.info("Sending deleted message to storage")

        dest_peer = await self._get_dest_peer()
        self.logger.debug(f"Dest peer: {dest_peer}")

        sender = cached.get("sender_name", "نامشخص")
        sender_label = cached.get("sender_label") or str(cached.get("sender_id") or "")
        sender_id = cached.get("sender_id")
        is_me = cached.get("is_me", False)
        chat_name = cached.get("chat_name", "نامشخص")
        chat_label = cached.get("chat_label") or str(cached.get("chat_id") or "")
        chat_username = cached.get("chat_username")
        text = cached.get("text", "")
        time_str = cached.get("time_str", "")
        orig_date = cached.get("date")
        msg_id = cached.get("msg_id")

        # تاریخ اصلی پیام
        if orig_date:
            try:
                date_str = orig_date.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                date_str = str(orig_date)
        else:
            date_str = time_str

        # لینک پیام (برای PV)
        link_line = ""
        try:
            if msg_id and cached.get("chat_id"):
                # برای PV: tg://openmessage?user_id=...
                try:
                    from telethon import utils as _utils
                    # سعی کن لینک بسازی — برای گروه/کانال هم کار می‌کند
                    # برای سادگی: اگر یوزرنیم دارد از t.me استفاده کن
                    if chat_username:
                        link_line = f"🔗 https://t.me/{chat_username}/{msg_id}"
                    else:
                        # برای PV
                        link_line = f"tg://openmessage?user_id={cached.get('chat_id')}&message_id={msg_id}"
                except Exception:
                    link_line = f"🆔 پیام: {msg_id}"
            elif msg_id:
                link_line = f"🆔 پیام: {msg_id}"
        except Exception:
            pass
        if not link_line and msg_id:
            link_line = f"🆔 پیام: {msg_id}"

        # هدر با جزئیات مثل فوروارد
        header_parts = []
        header_parts.append("🗑 **پیام حذف شده (ضدحذف)**")
        # چت مبدأ
        if chat_username:
            header_parts.append(f"📥 از: {chat_name} (@{chat_username})")
        else:
            header_parts.append(f"📥 از: {chat_name} ({chat_label})")
        # فرستنده با آیدی/یوزرنیم
        if sender_label and sender_label != sender:
            header_parts.append(f"👤 فرستنده: {sender} — {sender_label}")
        elif sender_id:
            header_parts.append(f"👤 فرستنده: {sender} ({sender_id})")
        else:
            header_parts.append(f"👤 فرستنده: {sender}")
        header_parts.append(f"🕒 {date_str} (UTC)")
        if link_line:
            header_parts.append(link_line)
        # در پی‌وی هر دو طرف می‌توانند پیام را برای هر دو حذف کنند و تلگرام
        # نمی‌گوید چه کسی حذف کرده؛ پس فقط صاحب پیام را اعلام می‌کنیم
        header_parts.append(
            "📌 پیامِ خودت حذف شد" if is_me else "📌 پیامِ طرف مقابل حذف شد"
        )
        header_parts.append("──────────────")
        header = "\n".join(header_parts)

        if text:
            # clip برای جلوگیری از محدودیت تلگرام
            from core.forwarder import clip as _clip, TEXT_LIMIT, CAPTION_LIMIT
            # متن جدا از هدر است؛ برای مدیا کپشن جدا می‌شود
            pass

        try:
            media = cached.get("media")
            if media:
                from core.forwarder import clip as _clip, CAPTION_LIMIT
                # کپشن = هدر + متن (اگر جا شد)
                body = header
                if text:
                    # فاصله + متن
                    candidate = header + "\n\n📝 متن:\n" + text
                    # clip تا سقف کپشن
                    body = _clip(candidate, CAPTION_LIMIT)
                # ارسال امن: استیکر/گیف به Recents اضافه نشود
                await send_media_clean(
                    self.client, dest_peer, media, caption=body
                )
            else:
                from core.forwarder import clip as _clip, TEXT_LIMIT
                body = header
                if text:
                    body = header + "\n\n📝 متن:\n" + text
                    body = _clip(body, TEXT_LIMIT)
                await self.client.send_message(dest_peer, body)
            self.logger.info(f"✅ Deleted msg saved | {sender} | {chat_name} | id={msg_id}")
        except Exception as e:
            self.logger.error(f"❌ Send failed: {type(e).__name__}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def stop(self):
        self._cache.clear()
        await super().stop()