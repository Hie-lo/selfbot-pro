"""
پلاگین ضد حذف پیام — فقط PV

لایه‌ی داده در core/pv_cache.py است (کش مشترک با ضدویرایش). این فایل فقط
«نمایش» است: جمع کردن حذف‌ها، ساخت گزارش و ارسال از طریق صف.

- قالب گزارش دقیقاً همان قالب قبلی است
- حذف‌های همزمان (مثلاً پاک شدن کل تاریخچه) ۱ ثانیه جمع می‌شوند؛ اگر
  بیشتر از PV_BURST_THRESHOLD پیام از یک چت بود → یک گزارش خلاصه + فایل
  متنی کامل، و مدیاها هرکدام با همان قالب همیشگی
- ارسال‌ها از صف با فاصله و صبر واقعی روی FloodWait (قبلاً دور ریخته می‌شد)
- مدیا: اول ارسال با ارجاع (بدون آپلود)، اگر نشد از گاوصندوق، اگر باز هم
  نشد متن + هشدار «مدیا قابل بازیابی نبود» (قبلاً کل گزارش گم می‌شد)
- حذف پیام‌های کانال/سوپرگروه نادیده گرفته می‌شود (قبلاً ممکن بود با یک
  پیام پی‌وی با همان شماره اشتباه گرفته شود)
"""

import asyncio
import io
import time
from datetime import datetime, timezone

from telethon import events
from telethon.errors import FloodWaitError

from config import PV_BURST_THRESHOLD, PV_CACHE_MAX_AGE_HOURS
from core import metrics, outbox, pv_cache
from core.media import send_media_clean
from database import db
from plugins.base import BasePlugin

DEBOUNCE = 1.0        # ثانیه سکوت بعد از آخرین حذف
MAX_BATCH_WAIT = 3.0  # حداکثر انتظار از اولین حذف
DEST_TTL = 60         # کش مقصد ذخیره (ثانیه)
MAX_AGE_SECONDS = min(86400 * 7, PV_CACHE_MAX_AGE_HOURS * 3600)

KIND_LABELS = {
    "photo": "عکس", "voice": "ویس", "round": "ویدیو گرد", "sticker": "استیکر",
    "gif": "گیف", "video": "ویدیو", "audio": "موزیک", "document": "فایل",
    "other": "مدیا",
}


class AntiDeletePlugin(BasePlugin):
    name = "anti_delete"
    description = "ضد حذف پیام"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._cache: pv_cache.PVCache | None = None
        self._outbox = None
        self._my_id = None
        self._batch: list = []
        self._batch_first = 0.0
        self._batch_last = 0.0
        self._flush_task: asyncio.Task | None = None
        self._dest = None
        self._dest_ts = 0.0

    async def start(self):
        self._cache = pv_cache.for_client(self.client, self.user_id)
        await self._cache.acquire(self.name, media=True)
        self._my_id = self._cache.my_id
        self._outbox = outbox.for_client(self.client)
        self._add_handler(self._on_delete, events.MessageDeleted())
        self.logger.info("AntiDelete loaded")

    # ── دریافت حذف ──

    async def _on_delete(self, event):
        # حذف در کانال/سوپرگروه: شماره‌ها از «جعبه»ی دیگری‌اند → ربطی به پی‌وی ندارند
        if event.chat_id is not None:
            return
        now = time.monotonic()
        got = False
        for mid in event.deleted_ids or []:
            rec = self._cache.take(mid)
            if rec is not None:
                self._batch.append(rec)
                got = True
        if not got:
            return
        if not self._batch_first:
            self._batch_first = now
        self._batch_last = now
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_later())

    async def _flush_later(self):
        while True:
            now = time.monotonic()
            quiet = now - self._batch_last
            waited = now - self._batch_first
            if quiet >= DEBOUNCE or waited >= MAX_BATCH_WAIT:
                break
            await asyncio.sleep(min(DEBOUNCE - quiet, MAX_BATCH_WAIT - waited) + 0.01)
        batch, self._batch = self._batch, []
        self._batch_first = self._batch_last = 0.0
        try:
            self._dispatch(batch)
        except Exception as e:
            self.logger.error(f"dispatch failed: {type(e).__name__}: {e}")

    def _dispatch(self, batch: list):
        now = datetime.now(timezone.utc).timestamp()
        fresh = []
        for rec in batch:
            if now - rec.ts > MAX_AGE_SECONDS:
                self._cache.vault.remove(rec.msg_id)
                continue
            fresh.append(rec)
        if not fresh:
            return

        # ترتیب زمانی اصلی (مثل قبل)
        fresh.sort(key=lambda r: r.msg_id)

        by_chat: dict[int, list] = {}
        for rec in fresh:
            by_chat.setdefault(rec.chat_id, []).append(rec)

        for chat_id, recs in by_chat.items():
            if len(recs) > PV_BURST_THRESHOLD:
                self._outbox.submit(
                    lambda r=recs: self._send_burst(r), label=f"burst x{len(recs)}"
                )
                # مدیاها جدا با قالب همیشگی (متن‌ها داخل فایل خلاصه‌اند)
                for rec in recs:
                    if rec.ref is not None:
                        self._outbox.submit(
                            lambda r=rec: self._send_deleted(r), label=f"del {rec.msg_id}"
                        )
            else:
                for rec in recs:
                    self._outbox.submit(
                        lambda r=rec: self._send_deleted(r), label=f"del {rec.msg_id}"
                    )

    # ── مقصد ──

    async def _get_dest_peer(self):
        """یافتن مقصد با نرمال‌سازی آیدی کانال (۶۰ ثانیه کش)"""
        now = time.monotonic()
        if self._dest is not None and now - self._dest_ts < DEST_TTL:
            return self._dest

        target = await db.get_storage_target(self.user_id, "anti_delete")
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
                except Exception as e:
                    self.logger.warning(f"Failed to resolve dest {dest_id}: {e}")
                    peer = dest_id
        self._dest, self._dest_ts = peer, now
        return peer

    # ── ساخت هدر (همان قالب قبلی) ──

    async def _describe(self, rec):
        peers = self._cache.peers
        sender_id = rec.sender_id
        if rec.is_me:
            sender_name = "شما"
            me = await peers.me()
            my_id = self._my_id
            if me and me.username:
                sender_label = f"@{me.username} ({my_id})"
            else:
                sender_label = str(my_id)
        else:
            info = await peers.get(sender_id) if sender_id else None
            if info is not None:
                sender_name = info.full_name or str(sender_id)
                sender_label = (
                    f"@{info.username} ({sender_id})" if info.username else str(sender_id)
                )
            else:
                sender_name = str(sender_id) if sender_id else "نامشخص"
                sender_label = str(sender_id) if sender_id else ""

        chat_id = rec.chat_id
        cinfo = await peers.get(chat_id)
        if cinfo is not None:
            chat_name = cinfo.display
            chat_username = cinfo.username
            chat_label = f"@{chat_username}" if chat_username else str(cinfo.id)
        else:
            chat_name, chat_username, chat_label = str(chat_id), None, str(chat_id)
        return sender_name, sender_label, chat_name, chat_username, chat_label

    async def _build_header(self, rec) -> str:
        sender, sender_label, chat_name, chat_username, chat_label = await self._describe(rec)
        sender_id = rec.sender_id
        msg_id = rec.msg_id

        try:
            date_str = rec.date.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            date_str = str(rec.date)

        if chat_username:
            link_line = f"🔗 https://t.me/{chat_username}/{msg_id}"
        else:
            link_line = f"tg://openmessage?user_id={rec.chat_id}&message_id={msg_id}"

        header_parts = ["🗑 **پیام حذف شده (ضدحذف)**"]
        if chat_username:
            header_parts.append(f"📥 از: {chat_name} (@{chat_username})")
        else:
            header_parts.append(f"📥 از: {chat_name} ({chat_label})")
        if sender_label and sender_label != sender:
            header_parts.append(f"👤 فرستنده: {sender} — {sender_label}")
        elif sender_id:
            header_parts.append(f"👤 فرستنده: {sender} ({sender_id})")
        else:
            header_parts.append(f"👤 فرستنده: {sender}")
        header_parts.append(f"🕒 {date_str} (UTC)")
        header_parts.append(link_line)
        # در پی‌وی هر دو طرف می‌توانند پیام را برای هر دو حذف کنند و تلگرام
        # نمی‌گوید چه کسی حذف کرده؛ پس فقط صاحب پیام را اعلام می‌کنیم
        header_parts.append(
            "📌 پیامِ خودت حذف شد" if rec.is_me else "📌 پیامِ طرف مقابل حذف شد"
        )
        header_parts.append("──────────────")
        return "\n".join(header_parts)

    # ── ارسال ──

    async def _send_deleted(self, rec):
        from core.forwarder import clip, CAPTION_LIMIT, TEXT_LIMIT

        dest = await self._get_dest_peer()
        header = await self._build_header(rec)
        text = rec.original_text

        try:
            if rec.ref is None:
                body = header
                if text:
                    body = clip(header + "\n\n📝 متن:\n" + text, TEXT_LIMIT)
                await self.client.send_message(dest, body)
            else:
                caption = header
                if text:
                    caption = clip(header + "\n\n📝 متن:\n" + text, CAPTION_LIMIT)
                if not await self._send_media(dest, rec, caption):
                    label = KIND_LABELS.get(rec.kind, "مدیا")
                    body = header + f"\n⚠️ {label} قابل بازیابی نبود"
                    if text:
                        body += "\n\n📝 متن:\n" + text
                    await self.client.send_message(dest, clip(body, TEXT_LIMIT))
            metrics.inc("deleted_reported")
            self.logger.info(f"✅ Deleted msg saved | id={rec.msg_id}")
        finally:
            self._cache.vault.remove(rec.msg_id)

    async def _send_media(self, dest, rec, caption: str) -> bool:
        """ارجاع → گاوصندوق. خروجی False = هیچ‌کدام نشد"""
        kind_label = pv_cache.RECENTS_KIND.get(rec.kind)

        # ۱) ارجاع (بدون آپلود، کیفیت اصلی)
        try:
            await send_media_clean(self.client, dest, rec.ref, caption=caption, kind=kind_label)
            return True
        except FloodWaitError:
            raise
        except Exception as e:
            self.logger.info(f"reference resend failed ({type(e).__name__}) — trying vault")

        # ۲) گاوصندوق
        await self._cache.vault.wait_pending(rec.msg_id)
        data = await self._cache.vault.load(rec)
        if not data:
            return False
        bio = io.BytesIO(data)
        bio.name = {"photo": "photo.jpg", "voice": "voice.ogg", "round": "video.mp4"}.get(
            rec.kind, "file.bin"
        )
        kwargs = {"caption": caption}
        if rec.kind == "voice":
            kwargs["voice_note"] = True
        elif rec.kind == "round":
            kwargs["video_note"] = True
        if rec.attrs:
            kwargs["attributes"] = rec.attrs
        if rec.mime and rec.kind != "photo":
            kwargs["mime_type"] = rec.mime
        try:
            await self.client.send_file(dest, bio, **kwargs)
            metrics.inc("vault_restored")
            return True
        except FloodWaitError:
            raise
        except Exception as e:
            self.logger.warning(f"vault resend failed: {type(e).__name__}: {e}")
            return False

    async def _send_burst(self, recs: list):
        """گزارش خلاصه برای حذف دسته‌ای + فایل متنی کامل"""
        from core.forwarder import clip, CAPTION_LIMIT

        dest = await self._get_dest_peer()
        first = recs[0]
        _, _, chat_name, chat_username, chat_label = await self._describe(first)
        mine = sum(1 for r in recs if r.is_me)
        theirs = len(recs) - mine

        def _d(r):
            try:
                return r.date.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return str(r.date)

        where = f"{chat_name} (@{chat_username})" if chat_username else f"{chat_name} ({chat_label})"
        summary = "\n".join([
            f"🗑 **{len(recs)} پیام حذف شد (ضدحذف)**",
            f"📥 از: {where}",
            f"🕒 {_d(recs[0])} … {_d(recs[-1])} (UTC)",
            f"📌 {theirs} پیام از طرف مقابل · {mine} پیام از خودت",
            "📎 متن کامل همه‌ی پیام‌ها در فایل پیوست است؛ مدیاها جداگانه می‌آیند.",
            "──────────────",
        ])

        lines = [f"Deleted messages — {where}", ""]
        for r in recs:
            if r.is_me:
                who = "شما"
            else:
                info = self._cache.peers.peek(r.sender_id) if r.sender_id else None
                who = (info.full_name if info and info.full_name else str(r.sender_id))
            media = f" [{KIND_LABELS.get(r.kind, 'مدیا')}]" if r.ref is not None else ""
            body = r.original_text or ""
            lines.append(f"[{_d(r)}] #{r.msg_id} {who}{media}:")
            lines.append(body if body else "—")
            lines.append("")
        bio = io.BytesIO("\n".join(lines).encode("utf-8"))
        bio.name = f"deleted_{first.chat_id}_{first.msg_id}.txt"

        await self.client.send_file(dest, bio, caption=clip(summary, CAPTION_LIMIT),
                                    force_document=True)
        metrics.inc("deleted_reported", len(recs))
        # رکوردهای بدون مدیا اینجا تمام شدند
        for r in recs:
            if r.ref is None:
                self._cache.vault.remove(r.msg_id)

    async def stop(self):
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._batch.clear()
        if self._cache is not None:
            await self._cache.release(self.name)
        await super().stop()
