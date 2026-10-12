"""
پلاگین ضد حذف پیام — فقط PV
"""

import time
from datetime import datetime, timezone
from telethon import events
from plugins.base import BasePlugin
from core import runtime
from core.media import send_media_clean
from database import db


CARD_WINDOW = 180.0        # ثانیه؛ حذف‌های یک نفر در این بازه روی یک کارت جمع می‌شوند
CARD_MAX_AGE = 48 * 3600   # حذف قدیمی‌تر از این، کارت دکمه‌دار نمی‌گیرد


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
        self._card_state: dict[int, dict] = {}   # uid → {msg_id, ts, count}
        self._chat_meta: dict[int, dict] = {}    # chat_id → اطلاعات نام/یوزرنیم

    async def _build_chat_meta(self, chat_id: int, sender_id, is_me: bool) -> dict:
        """
        اطلاعات نام/یوزرنیمِ یک چت را یک‌بار می‌گیرد و کش می‌کند.
        در پیوی، «چت» و «فرستنده» یک نفرند، پس یک درخواست کافی است.
        """
        meta = {"sender_name": "شما" if is_me else "نامشخص",
                "sender_username": None,
                "sender_label": str(sender_id or ""),
                "chat_name": str(chat_id), "chat_username": None,
                "chat_label": str(chat_id)}
        if is_me:
            try:
                me = await self.client.get_me()
                uname = getattr(me, "username", None)
                meta["sender_username"] = uname
                meta["sender_label"] = f"@{uname} ({self._my_id})" if uname \
                    else str(self._my_id)
            except Exception:
                pass
            return meta

        try:
            ent = await self.client.get_entity(chat_id)
        except Exception as e:                       # noqa: BLE001
            self.logger.debug(f"Get chat entity failed: {e}")
            return meta

        first = getattr(ent, "first_name", "") or ""
        last = getattr(ent, "last_name", "") or ""
        title = getattr(ent, "title", "") or ""
        name = (title.strip() or " ".join(x for x in (first, last) if x).strip()
                or str(chat_id))
        uname = getattr(ent, "username", None)
        label = f"@{uname}" if uname else str(chat_id)
        try:
            from telethon import utils as _utils
            label = label if uname else str(_utils.get_peer_id(ent))
        except Exception:
            pass
        meta.update(sender_name=name, sender_username=uname,
                    sender_label=(f"@{uname} ({sender_id})" if uname else str(sender_id)),
                    chat_name=name, chat_username=uname, chat_label=label)
        return meta

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

            # — اطلاعات فرستنده/چت —
            # در پیوی، طرفِ چت همان فرستنده است؛ پس **یک** meta برای هر چت
            # کش می‌شود و تا ۱۰ دقیقه دوباره از تلگرام خوانده نمی‌شود.
            # (قبلاً برای هر پیام دو بار get_entity می‌زد → پرحجم و کند)
            meta = self._chat_meta.get(chat_id)
            now_mono = time.monotonic()
            if meta is None or (now_mono - meta.get("ts", 0.0)) > 600:
                meta = await self._build_chat_meta(chat_id, sender_id, is_me)
                meta["ts"] = now_mono
                self._chat_meta[chat_id] = meta
                if len(self._chat_meta) > 200:
                    for old_cid in sorted(self._chat_meta,
                                          key=lambda k: self._chat_meta[k].get("ts", 0)
                                          )[:100]:
                        self._chat_meta.pop(old_cid, None)

            sender_name = meta["sender_name"]
            sender_username = meta["sender_username"]
            sender_label = meta["sender_label"]
            chat_name = meta["chat_name"]
            chat_username = meta["chat_username"]
            chat_label = meta["chat_label"]

            now = datetime.now(timezone.utc)
            time_str = now.strftime("%Y/%m/%d %H:%M:%S")

            # لینک مستقیم به همین پیام (برای «باز کردن گفتگو» با یک ضربه)
            if chat_username:
                msg_link = f"https://t.me/{chat_username}/{msg.id}"
            else:
                uid_for_link = abs(sender_id) if sender_id else abs(chat_id)
                msg_link = (f"tg://openmessage?user_id={uid_for_link}"
                            f"&message_id={msg.id}")

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
                "msg_link": msg_link,
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

        # کارت دکمه‌دار جدا: «پیام بده» / «آماده‌کردن چت (بی‌صدا)»
        # گزارش بالا از کلاینت سلف‌بات می‌رود و دکمه‌ی شیشه‌ای نمی‌پذیرد؛
        # دکمه فقط از طرف ربات کنترلی ممکن است.
        await self._quick_actions(cached)

    async def _quick_actions(self, cached: dict) -> None:
        """
        کارت دکمه‌دار در پیوی ربات کنترلی — برای این‌که کاربری که چتش را
        پاک کرده، با یک ضربه دوباره قابل پیام‌دادن/باز کردن باشد.

        بهینه (خواسته‌ی کاربر: «اگه هزار تا پیام پاک کنه، هزارتا ریکوئست؟»):
          • پنجره‌ی تجمیع: تا ۳ دقیقه، همه‌ی حذف‌های یک نفر روی **همان
            کارت** به‌روز می‌شوند (edit) — نه پیام جدید. پس ۱۰۰۰ حذف =
            ۱ پیام + چند ویرایش.
          • حذف‌های قدیمی‌تر از ۴۸ ساعت کارت نمی‌گیرند (گزارش در چت
            ذخیره‌سازی هست، ولی مزاحم نمی‌شود).
          • فیلدفیلد فقط پیوی.
          • طرف در همان لحظه با «نام/یوزرنیم» در سابقه ثبت می‌شود تا
            بعداً حتی با «@یوزرنیم» هم پیدایش کنی.
        """
        try:
            if runtime.bot is None:
                return
            chat_id = int(cached.get("chat_id") or 0)
            # در تلگرام آیدی پی‌وی مثبت است و گروه/کانال منفی
            # (-N برای گروه معمولی و ‎-100… برای سوپرگروه/کانال)
            if chat_id < 0:
                return                             # گروه/کانال → دکمه لازم نیست
            uid = abs(chat_id)
            if not uid or uid == self._my_id:
                return

            # حذف‌های قدیمی ارزش ساختن کارت ندارند (سابقه‌شان هست)
            d = cached.get("date")
            if d is not None:
                try:
                    if d.tzinfo is None:
                        d = d.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - d).total_seconds()
                    if age > CARD_MAX_AGE:
                        return
                except Exception:
                    pass

            # روشن/خاموش (پیش‌فرض: روشن) — «.کاربر دکمه خاموش»
            try:
                rows = {f["feature_name"]: f for f in await db.get_features(self.user_id)}
                cfg = dict((rows.get("anti_delete") or {}).get("config_json") or {})
                if cfg.get("quick_buttons", True) is False:
                    return
            except Exception:
                pass

            # طرف را در سابقه ثبت کن (با یوزرنیم!) — «فقط پیوی»
            # همین است که «دسترسی به کاربری که چت را پاک کرده» را آسان می‌کند.
            try:
                await db.remember_user(
                    self.user_id, uid,
                    first_name=(cached.get("sender_name")
                                if not str(cached.get("sender_name") or "").isdigit()
                                else None),
                    username=cached.get("sender_username"),
                    chat_id=chat_id, source="ضدحذف", kind="pv",
                    last_msg_id=cached.get("msg_id"),
                )
            except Exception as e:                 # noqa: BLE001
                self.logger.debug(f"remember on delete skipped: {e}")

            owner = await db.get_user_by_db_id(self.user_id)
            owner_tg = (owner or {}).get("telegram_id")
            if not owner_tg:
                return

            import html as _h

            def _esc(value):
                return _h.escape(str(value or ""))

            name = cached.get("chat_name") or ""
            if not name or name.isdigit():
                name = cached.get("sender_name") or ""
            if not name or name.isdigit():
                try:                               # نام واقعی از سابقه
                    from core.plugin_manager import get_active_plugins
                    plugin = get_active_plugins(self.user_id).get("user_info")
                    if plugin is not None:
                        name = await plugin.display_name(uid)
                except Exception:
                    pass
            if not name:
                name = str(uid)

            # ── تجمیع: کارت قبلی همین کاربر را ویرایش کن ──
            now = time.monotonic()
            state = self._card_state.get(uid)
            fresh = state and (now - state.get("ts", 0) < CARD_WINDOW)
            count = (state.get("count", 0) + 1) if fresh else 1
            head = ("🗑 <b>یک پیام حذف شد</b>" if count == 1
                    else f"🗑 <b>{count} پیام حذف شد</b>")
            link = cached.get("msg_link") or ""
            text = (
                f"{head}\n"
                f"👤 {_esc(name)} · <code>{uid}</code>\n"
                + (f"🔗 <a href=\"{_esc(link)}\">باز کردن همان پیام</a>\n" if link else "")
                + "\nمی‌خواهی دوباره پیام بدهی یا چت را بی‌صدا باز کنی؟\n"
                "<i>«آماده‌کردن چت» فقط پیش‌نویس می‌سازد — طرف هیچ اطلاعی "
                "نمی‌گیرد.</i>"
            )

            from bot.user_panel import anti_delete_kb
            kb = anti_delete_kb(uid, link=cached.get("msg_link"))

            if fresh and state.get("msg_id"):
                ok = await runtime.edit_card(owner_tg, state["msg_id"], text,
                                             reply_markup=kb,
                                             disable_web_page_preview=True)
                if ok:
                    state.update(ts=now, count=count)
                    self._card_state[uid] = state
                    return

            sent = await runtime.send_card(owner_tg, text, reply_markup=kb,
                                           disable_web_page_preview=True)
            mid = getattr(sent, "message_id", None)
            self._card_state[uid] = {"msg_id": mid, "ts": now, "count": count}
            # سقف حافظه: قدیمی‌ترین‌ها بیرون
            if len(self._card_state) > 200:
                for old_uid in sorted(self._card_state,
                                      key=lambda k: self._card_state[k].get("ts", 0)
                                      )[:100]:
                    self._card_state.pop(old_uid, None)
        except Exception as e:                     # noqa: BLE001
            self.logger.debug(f"quick_actions skipped: {type(e).__name__}: {e}")

    async def stop(self):
        self._cache.clear()
        self._card_state.clear()
        await super().stop()