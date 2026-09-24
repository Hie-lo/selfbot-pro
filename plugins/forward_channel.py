"""
فوروارد تمام پست‌های یک کانال به کانال دیگر.

کامندها:
  .فوروارد <مبدأ> به <مقصد>
      فوروارد واقعی با نام منبع
  .فوروارد با آیدی <مبدأ> به <مقصد>
      کپی همراه با هدر «از کدام چت، چه کسی، چه زمانی» + لینک پیام

پشتیبانی از لینک دعوت گروه‌های خصوصی (t.me/+hash و t.me/joinchat/hash)
اگر اکانت عضو باشد؛ اگر عضو نباشد پیام راهنما ارسال می‌شود.

برای چت‌هایی که لینک/یوزرنیم ندارند (گروه‌های خصوصی معمولی) از دکمه
«📥 فوروارد محتوا» در ربات استفاده کنید — لیست چت‌های اکانت آنجا می‌آید.

هیچ پیامی در چت مبدأ یا مقصد ارسال نمی‌شود؛ گزارش کامل در Saved Messages
ذخیره می‌شود.
"""

import logging

from telethon import events

from plugins.base import BasePlugin
from config import (
    FORWARD_CACHE_CAPTURE_MEDIA,
    FORWARD_CACHE_MODE,
    FORWARD_SPEED,
)
from core import cache_forward, forwarder
from core.security import validate_telegram_chat_link
from database import db

logger = logging.getLogger("plugin.forward_channel")


class ForwardChannelPlugin(BasePlugin):
    name = "forward_channel"
    description = "فوروارد کامل کانال"
    always_on = True

    async def start(self):

        async def forward_cmd(event):
            if not event.out:
                return

            attributed = bool(event.pattern_match.group(1))
            source_url = event.pattern_match.group(2).strip()
            destination_url = event.pattern_match.group(3).strip()
            source_ref = validate_telegram_chat_link(source_url)
            destination_ref = validate_telegram_chat_link(destination_url)

            # حذف کامند از هر چتی که زده شده (چت مبدأ هم اگر بود)
            await event.delete()

            if not source_ref or not destination_ref:
                await self._report(
                    "❌ فرمت لینک نامعتبر است.\n"
                    "نمونه: .فوروارد https://t.me/source به https://t.me/destination"
                )
                return

            if (forwarder.get_active_job(self.user_id)
                    or cache_forward.get_active_job(self.user_id)):
                await self._report("⏳ یک فوروارد در حال اجراست. صبر کنید.")
                return

            try:
                source = await self._resolve_chat(source_ref)
                destination = await self._resolve_chat(destination_ref)
            except Exception as error:
                logger.warning(f"Resolve failed: {error}")
                msg = forwarder.resolve_error_message(error)
                if isinstance(error, forwarder.JoinNeeded):
                    msg += (
                        "\n\n💡 برای عضویت: در ربات → 📥 فوروارد محتوا → "
                        "✍️ ورود دستی لینک"
                    )
                await self._report(msg)
                return

            if getattr(source, "id", None) == getattr(destination, "id", None):
                await self._report("❌ مبدأ و مقصد نمی‌توانند یکسان باشند.")
                return

            # ثبت job در دیتابیس → پس از ری‌استارت سرور ادامه می‌یابد
            src_ref = forwarder.peer_ref(source)
            dst_ref = forwarder.peer_ref(destination)

            # ردیفِ گیرکرده‌ی مرده جلوی کار را نگیرد (خودترمیمی)
            blocking = await forwarder.prepare_job_start(self.user_id)
            if blocking:
                await self._report(
                    "⚠️ یک فوروارد نیمه‌کاره جلوی شروع را گرفته است:\n"
                    f"🆔 {blocking['id']} | {blocking.get('src_name')} → "
                    f"{blocking.get('dst_name')} | "
                    f"{int(blocking.get('sent') or 0):,} ارسال‌شده\n"
                    "برای رفع: «.فوروارد بستن» یا در ربات: "
                    "📥 فوروارد محتوا → 🔓 بستن و شروع فوروارد جدید"
                )
                return

            try:
                row = await db.create_forward_job(
                    user_id=self.user_id,
                    src=src_ref,
                    dst=dst_ref,
                    mode="copy" if attributed else "forward",
                    attributed=attributed,
                    media_only=False,
                    limit_count=0,
                    cache_mode=FORWARD_CACHE_MODE,
                    capture_media=FORWARD_CACHE_CAPTURE_MEDIA,
                    speed=self._speed(),
                )
            except Exception as e:
                self.logger.error(f"create_forward_job failed: {e}")
                row = None

            if not row:
                await self._report(
                    "⏳ یک فوروارد در جریان یا نیمه‌کاره دارید.\n"
                    "▶️ برای ادامه: ربات → 📥 فوروارد محتوا → ▶️ ادامه فوروارد\n"
                    "🔓 برای بستن و شروع تازه: «.فوروارد بستن» یا دکمه‌ی "
                    "«🔓 بستن آن و شروع فوروارد جدید» در ربات"
                )
                return

            # گزارش‌ها فقط در Saved Messages — بی‌صدا برای بقیه
            report = await self.client.send_message(
                "me", "⏳ در حال آماده‌سازی فوروارد..."
            )

            cached = bool(row.get("cache_mode"))

            async def on_progress(job):
                try:
                    if cached:
                        stats = None
                        if job.phase == "send":
                            stats = await db.cache_stats(job.db_id)
                        text = cache_forward.cache_job_summary(job, stats)
                    else:
                        text = forwarder.job_summary(job)
                    await self.client.edit_message(report, text, parse_mode="html")
                except Exception as e:
                    logger.debug(f"report edit skipped: {e}")

            if cached:
                try:
                    job = await cache_forward.start_job(
                        user_db_id=self.user_id,
                        source=source,
                        dest=destination,
                        source_name=getattr(source, "title", None) or source_url,
                        dest_name=getattr(destination, "title", None) or destination_url,
                        mode="copy",
                        attributed=attributed,
                        limit=0,
                        media_only=False,
                        capture_media=bool(row.get("capture_media")),
                        chat_id=report.chat_id,
                        message_id=report.id,
                        speed=self._speed(),
                        on_progress=on_progress,
                        resume_row=row,
                    )
                except ValueError as e:
                    if str(e) == "job_running":
                        await self._report("⏳ یک فوروارد در حال اجراست. صبر کنید.")
                    else:
                        await self._report(f"❌ شروع ناموفق: {e}")
                    return

                await cache_forward.wait_job(job)
                await self._finish_report(job, report, cached=True)
                return

            try:
                job = await forwarder.start_job(
                    user_db_id=self.user_id,
                    source=source,
                    dest=destination,
                    source_name=getattr(source, "title", None) or source_url,
                    dest_name=getattr(destination, "title", None) or destination_url,
                    mode="copy" if attributed else "forward",
                    limit=0,
                    media_only=False,
                    attributed=attributed,
                    chat_id=report.chat_id,
                    message_id=report.id,
                    on_progress=on_progress,
                    db_id=row["id"],
                    speed=self._speed(),
                )
            except ValueError as e:
                if str(e) == "job_running":
                    await self._report("⏳ یک فوروارد در حال اجراست. صبر کنید.")
                else:
                    await self._report(f"❌ شروع ناموفق: {e}")
                return

            await forwarder.wait_job(job)
            await self._finish_report(job, report)

            self.logger.info(
                "Forward job %s (attributed=%s): %s -> %s sent=%s failed=%s",
                job.id, attributed, source_url, destination_url,
                job.sent, job.failed,
            )

        async def close_stuck_cmd(event):
            """🔓 بستن فوروارد گیرکرده (رفع پیام «شما فوروارد فعال دارید»)"""
            try:
                closed = await db.release_unfinished_jobs(
                    self.user_id, note="کاربر با «.فوروارد بستن» آزاد کرد"
                )
            except Exception as e:
                self.logger.error(f"release unfinished jobs failed: {e}")
                await self._report(f"❌ بستن ناموفق: {e}")
                return

            for row in closed:
                await forwarder.free_job_cache(row["id"])

            if closed:
                lines = "\n".join(
                    f"• 🆔 {r['id']} | {r.get('src_name')} → {r.get('dst_name')} | "
                    f"{int(r.get('sent') or 0):,} ارسال‌شده"
                    for r in closed
                )
                await self._report(
                    f"🔓 {len(closed)} فوروارد گیرکرده بسته شد:\n{lines}\n\n"
                    "حالا می‌توانی فوروارد جدید بسازی."
                )
            else:
                await self._report("✅ فوروارد گیرکرده‌ای نبود — راه باز است.")

        self._add_handler(
            forward_cmd,
            events.NewMessage(
                pattern=(
                    r"^\.فوروارد\s+(با آیدی\s+)?(\S+)\s+به\s+(\S+)\s*$"
                ),
                outgoing=True,
            ),
        )
        self._add_handler(
            close_stuck_cmd,
            events.NewMessage(
                pattern=r"^\.فوروارد\s+(بستن|آزاد|آزادسازی)\s*$",
                outgoing=True,
            ),
        )

        async def delete_cmd(event):
            """🗑 حذف کامل فوروارد — برای همیشه"""
            # اول هر جابِ فعال را متوقف کن
            try:
                from core import forwarder as fw, cache_forward as cf
                active = fw.get_active_job(self.user_id) or cf.get_active_job(self.user_id)
                if active:
                    try:
                        await fw.stop_job(active.id)
                    except Exception:
                        pass
                    try:
                        await cf.stop_job(active.id)
                    except Exception:
                        pass
                    import asyncio as _asyncio
                    await _asyncio.sleep(0.5)
            except Exception:
                pass
            try:
                closed = await db.release_unfinished_jobs(
                    self.user_id, note="کاربر با «.فوروارد حذف» برای همیشه حذف کرد", status="cancelled"
                )
            except Exception as e:
                self.logger.error(f"delete failed: {e}")
                await self._report(f"❌ حذف ناموفق: {e}")
                return
            for row in closed:
                await forwarder.free_job_cache(row["id"])
            # errorها را هم ببند
            try:
                pool = db.get_pool()
                if pool:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """UPDATE forward_jobs SET status='cancelled', error='حذف کامل توسط کاربر (.فوروارد حذف)', updated_at=NOW()
                               WHERE user_id=$1 AND status IN ('error','paused','running')""", self.user_id)
            except Exception as e:
                self.logger.debug(f"extra cancel failed: {e}")
            if closed:
                lines = "\n".join(f"• 🆔 {r['id']} | {r.get('src_name')} → {r.get('dst_name')}" for r in closed)
                await self._report(f"🗑 {len(closed)} فوروارد برای همیشه حذف شد:\n{lines}\n\nکش هم پاک شد.")
            else:
                # حتی اگر running نبود ولی paused/error بود، حالا پاک شد
                await self._report("🗑 فوروارد برای همیشه حذف شد — دیگر قابل ادامه نیست. کش پاک شد.")

        self._add_handler(
            delete_cmd,
            events.NewMessage(
                pattern=r"^\.فوروارد\s+حذف\s*$",
                outgoing=True,
            ),
        )
        self.logger.info("loaded")

    async def _report(self, text: str):
        """ارسال گزارش در Saved Messages (هیچ پیامی در چت‌ها ارسال نمی‌شود)"""
        try:
            await self.client.send_message("me", text)
        except Exception as e:
            self.logger.error(f"Report failed: {e}")

    def _speed(self) -> str:
        """سرعت انتخابی کاربر (پیش‌فرض از .env)"""
        return getattr(self, "speed", None) or FORWARD_SPEED

    async def _finish_report(self, job, report, cached: bool = False):
        """گزارش پایانی و پیام ادامه در Saved Messages"""
        try:
            if cached:
                stats = await db.cache_stats(job.db_id)
                text = cache_forward.cache_job_summary(job, stats)
            else:
                text = forwarder.job_summary(job)
            await self.client.edit_message(report, text, parse_mode="html")
        except Exception as e:
            logger.debug(f"final report edit skipped: {e}")

        if job.state in ("cancelled", "paused", "error"):
            if cached:
                extra = (
                    "⏸ فوروارد نیمه‌کاره ماند ولی پیام‌های جمع‌آوری‌شده "
                    "روی سرور امن هستند.\n"
                    "برای ادامه: ربات → 📥 فوروارد محتوا → ▶️ ادامه"
                )
            else:
                extra = (
                    "⏸ فوروارد نیمه‌کاره ماند.\n"
                    "برای ادامه: ربات → 📥 فوروارد محتوا → ▶️ ادامه"
                )
            await self._report(extra)
        elif cached and job.capture_note:
            await self._report(f"⚠️ {job.capture_note}")

    async def _resolve_chat(self, chat_ref: str):
        """یوزرنیم، آیدی، لینک کانال یا لینک دعوت"""
        return await forwarder.resolve_reference(self.client, chat_ref)
