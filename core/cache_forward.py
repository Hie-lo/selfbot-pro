"""
فوروارد دو مرحله‌ای با کش روی سرور

مشکلی که حل می‌کند: در فوروارد زنده، اگر وسط کار دسترسی به کانال/گروه مبدأ
از دست برود (اخراج شدن، حذف کانال، خصوصی شدن)، همه‌چیز متوقف می‌شد.

راه‌حل:
  فاز ۱ — جمع‌آوری (capture):
      همه‌ پیام‌ها از مبدأ خوانده و روی سرور ذخیره می‌شوند
      (متن + مشخصات فرستنده + لینک پیام؛ مدیا اختیاری به‌صورت فایل)
  فاز ۲ — ارسال (send):
      پیام‌ها از روی کش سرور به مقصد فرستاده می‌شوند
      → حتی اگر دیگر به مبدأ دسترسی نداشته باشی، ارسال ادامه پیدا می‌کند

هر دو فاز FloodWait-tolerant و قابل ادامه پس از ری‌استارت هستند.
"""

import asyncio
import html
import logging
import os
import shutil
import time

from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    UserNotParticipantError,
)
from telethon.tl import types

from config import (
    FORWARD_CACHE_BATCH,
    FORWARD_CACHE_MAX_GB,
    FORWARD_CACHE_MEDIA_MAX_MB,
    FORWARD_CACHE_PAUSE,
    FORWARD_CONCURRENCY,
    FORWARD_SPEED,
)
from core import forwarder as F
from core.client_manager import get_client
from core.media import cleanup_recents, make_guard, unsave_by_reference
from core.pacing import Pacer
from database import db

DB_FLUSH_EVERY = 10       # هر چند پیام، پیشرفت در DB ثبت شود

logger = logging.getLogger("cache_forward")

CACHE_BATCH = 100
SAVE_INTERVAL = 2.0

# خطاهایی که یعنی «دسترسی به مبدأ از دست رفت»
ACCESS_LOST_ERRORS = (
    ChannelPrivateError,
    ChatAdminRequiredError,
    UserNotParticipantError,
)


def _is_access_lost(err: Exception) -> bool:
    if isinstance(err, ACCESS_LOST_ERRORS):
        return True
    text = str(err).lower()
    return any(
        k in text
        for k in (
            "not part of", "channel_private", "chatadminrequired",
            "you are not a participant", "cannot find any entity",
            "could not find the input entity", "banned from",
        )
    )


class CacheForwardJob:
    """job دو مرحله‌ای: جمع‌آوری روی سرور، سپس ارسال"""

    def __init__(
        self,
        job_id: int,
        user_db_id: int,
        source_name: str,
        dest_name: str,
        mode: str,
        attributed: bool,
        media_only: bool,
        limit: int,
        capture_media: bool,
        speed: str | None = None,
    ):
        self.id = job_id
        self.user_db_id = user_db_id
        self.source_name = source_name
        self.dest_name = dest_name
        self.mode = mode
        self.attributed = attributed
        self.media_only = media_only
        self.limit = limit
        self.capture_media = capture_media

        self.db_id: int = job_id
        self.chat_id = None
        self.message_id = None

        self.phase = "capture"       # capture | send | done
        self.state = "running"
        self.captured = 0
        self.sent = 0
        self.skipped = 0
        self.failed = 0
        self.media_skipped = 0
        self.bytes = 0
        self.cursor = 0              # آخرین پیام جمع‌آوری‌شده
        self.total = 0
        self.wait_seconds = 0
        self.capture_note = ""       # مثلاً «دسترسی قطع شد»

        # کش اطلاعات فرستنده‌ها (shared با forwarder._sender_info)
        self.senders: dict[int, tuple] = {}
        self.source_label = ""

        # سرعت (تنظیم خودکار با بازخورد تلگرام)
        self.speed = speed or FORWARD_SPEED
        self.pacer = Pacer(self.speed, FORWARD_CONCURRENCY or None)
        self.guard = None            # نگهبان پاکسازی Recents (پس‌زمینه)

        self.cancel_requested = False
        self.started_at = time.time()
        self.last_save = 0.0
        self.task: asyncio.Task | None = None

    # ── متن‌های کمکی ──

    @property
    def duration(self) -> int:
        return max(0, int(time.time() - self.started_at))

    def duration_text(self) -> str:
        secs = self.duration
        if secs < 60:
            return f"{secs} ثانیه"
        if secs < 3600:
            return f"{secs // 60} دقیقه و {secs % 60} ثانیه"
        return f"{secs // 3600} ساعت و {(secs % 3600) // 60} دقیقه"

    def cache_mb(self) -> float:
        return self.bytes / 1024 / 1024


# ═══════════════════════════════════
# نمایش پیشرفت
# ═══════════════════════════════════


def cache_job_summary(job: CacheForwardJob, stats: dict = None) -> str:
    phase_text = {
        "capture": "🗄 مرحله ۱ از ۲ — جمع‌آوری روی سرور",
        "send": "📤 مرحله ۲ از ۲ — ارسال از روی سرور",
        "done": "✅ تمام شد",
    }.get(job.phase, job.phase)

    queued = F.queued_text(job)
    if queued:
        head = queued
    elif job.state == "waiting":
        head = (
            f"⏸ محدودیت تلگرام — ادامه خودکار پس از "
            f"{job.wait_seconds} ثانیه"
        )
    elif job.state == "done":
        head = "✅ فوروارد تمام شد"
    elif job.state == "error":
        head = "❌ با خطا متوقف شد (قابل ادامه)"
    elif job.state in ("cancelled", "paused"):
        head = "⏸ متوقف شد (قابل ادامه)"
    else:
        head = f"⏳ {phase_text}"

    lines = [
        f"<b>{head}</b>",
        "",
        f"📤 از: <b>{html.escape(str(job.source_name))}</b>",
        f"📥 به: <b>{html.escape(str(job.dest_name))}</b>",
        f"🏷 حالت: {F.mode_label(job)}",
        "",
    ]

    if job.phase == "capture":
        lines.append(f"🗄 جمع‌آوری‌شده: <b>{job.captured:,}</b>")
        if job.total:
            lines.append(f"📦 کل پیام‌های مبدأ: {job.total:,}")
        if job.capture_media:
            lines.append(f"💾 حجم کش‌شده: {job.cache_mb():.1f} مگابایت")
        lines.append("")
        lines.append(
            "🔒 در این مرحله چیزی به مقصد ارسال نمی‌شود؛\n"
            "پیام‌ها امن روی سرور ذخیره می‌شوند."
        )
    else:
        lines.append(f"✅ ارسال‌شده: <b>{job.sent:,}</b>")
        if stats:
            lines.append(f"⏳ باقی‌مانده: {stats.get('pending', 0):,}")
            remaining_mb = stats.get("pending_bytes", 0) / 1024 / 1024
            if remaining_mb:
                lines.append(f"💾 حجم باقی‌مانده: {remaining_mb:.1f} مگابایت")
        lines.append(f"❌ خطا: {job.failed}")
        guard = getattr(job, "guard", None)
        if guard is not None and guard.stats_text():
            lines.append(guard.stats_text())
        lines.append(
            f"⚡ سرعت: {job.pacer.stats_text()}"
            + (f" · محدودیت‌ها: {job.pacer.floods}" if job.pacer.floods else "")
        )

    lines.append(f"⏭ رد شده: {job.skipped}")

    done = job.captured if job.phase == "capture" else job.sent
    total = job.total or (job.captured if job.phase == "send" else 0)
    bar = F.progress_bar(done, total) if total else ""
    if bar and job.state in ("running", "waiting"):
        lines += ["", bar]

    if job.state in ("running", "waiting") and job.phase == "send":
        left = stats.get("pending", 0) if stats else max((total - done), 0)
        if left > 0:
            eta = job.pacer.eta_text(left)
            if eta:
                lines.append(f"⏳ تخمین باقی‌مانده: {eta}  (⚡ {job.pacer.rate():.1f} پیام/ثانیه)")
    elif job.state in ("running", "waiting"):
        left = (total - done) if total else 0
        if left > 0:
            lines.append(f"⏳ تخمین باقی‌مانده (این مرحله): {_eta(job, left)}")
    else:
        lines += ["", f"⏱ زمان کل: {job.duration_text()}"]

    if job.capture_note:
        lines += ["", f"⚠️ {html.escape(job.capture_note)}"]

    if job.state in ("cancelled", "paused", "error"):
        lines += ["", "▶️ با دکمه «ادامه فوروارد» از همین‌جا ادامه دهید."]

    return "\n".join(lines)


def _eta(job: CacheForwardJob, left: int) -> str:
    elapsed = time.time() - job.started_at
    done = job.captured if job.phase == "capture" else job.sent
    if elapsed < 5 or done <= 0:
        return "—"
    seconds = int(left / (done / elapsed))
    if seconds < 60:
        return f"{seconds} ثانیه"
    if seconds < 3600:
        return f"{seconds // 60} دقیقه"
    return f"{seconds // 3600} ساعت و {(seconds % 3600) // 60} دقیقه"


def get_job(job_id: int):
    return _jobs.get(job_id)


def get_active_job(user_db_id: int):
    return _active_jobs.get(user_db_id)


async def stop_job(job_id: int) -> bool:
    job = _jobs.get(job_id)
    if not job or job.state not in ("running", "waiting"):
        return False
    job.cancel_requested = True
    return True


async def wait_job(job: CacheForwardJob) -> CacheForwardJob:
    if job.task:
        try:
            await job.task
        except Exception:
            pass
    return job


_jobs: dict[int, CacheForwardJob] = {}
_active_jobs: dict[int, CacheForwardJob] = {}
_next_job_id = 100000
_msg_links: dict[tuple, str] = {}


# ═══════════════════════════════════
# شروع
# ═══════════════════════════════════


async def start_job(
    *,
    user_db_id: int,
    source,
    dest,
    source_name: str,
    dest_name: str,
    mode: str = "copy",
    attributed: bool = True,
    media_only: bool = False,
    limit: int = 0,
    capture_media: bool = False,
    speed: str | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    on_progress=None,
    resume_row: dict | None = None,
) -> CacheForwardJob:
    if user_db_id in _active_jobs:
        raise ValueError("job_running")

    client = await get_client(user_db_id)
    if not client:
        raise ValueError("account_not_connected")

    if resume_row:
        job = CacheForwardJob(
            job_id=int(resume_row["id"]),
            user_db_id=user_db_id,
            source_name=resume_row.get("src_name") or source_name,
            dest_name=resume_row.get("dst_name") or dest_name,
            mode=resume_row.get("mode") or mode,
            attributed=bool(resume_row.get("attributed")),
            media_only=bool(resume_row.get("media_only")),
            limit=int(resume_row.get("limit_count") or 0),
            capture_media=bool(resume_row.get("capture_media")),
            speed=resume_row.get("speed") or speed,
        )
        job.phase = resume_row.get("phase") or "capture"
        job.cursor = int(resume_row.get("capture_cursor") or 0)
        job.captured = int(resume_row.get("captured") or 0)
        job.bytes = int(resume_row.get("cache_bytes") or 0)
        job.sent = int(resume_row.get("sent") or 0)
        job.skipped = int(resume_row.get("skipped") or 0)
        job.failed = int(resume_row.get("failed") or 0)
        job.total = int(resume_row.get("total") or 0)
        job.chat_id = resume_row.get("chat_id") or chat_id
        job.message_id = resume_row.get("message_id") or message_id
    else:
        job = CacheForwardJob(
            job_id=int(resume_row["id"]) if resume_row else 0,
            user_db_id=user_db_id,
            source_name=source_name,
            dest_name=dest_name,
            mode=mode,
            attributed=attributed,
            media_only=media_only,
            limit=limit,
            capture_media=capture_media,
            speed=speed,
        )
        job.chat_id, job.message_id = chat_id, message_id

    if not job.db_id:
        raise ValueError("job_db_missing")

    _jobs[job.id] = job
    _active_jobs[user_db_id] = job

    if not job.source_label and not isinstance(source, str):
        try:
            job.source_label = F.chat_ref_label(source)
        except Exception:
            job.source_label = ""

    job.task = asyncio.create_task(_run_governed(job, client, source, dest, on_progress))
    logger.info(
        f"Cache job {job.id} started: {source_name} -> {dest_name} "
        f"phase={job.phase} capture_media={job.capture_media}"
    )
    return job


# ═══════════════════════════════════
# حلقه اصلی
# ═══════════════════════════════════


async def _run_governed(job: CacheForwardJob, client, source, dest, on_progress):
    """اجرا داخل سقف سراسری فوروارد (مشترک با موتور زنده)"""
    from core import governor
    async with governor.job_slot("forward", job, on_progress):
        await _run(job, client, source, dest, on_progress)


async def _run(job: CacheForwardJob, client, source, dest, on_progress):
    last_report = 0.0
    if F.recents_guard_on():
        job.guard = make_guard(client)

    async def report(force: bool = False):
        nonlocal last_report
        now = time.time()
        if on_progress and (force or now - last_report >= F.REPORT_INTERVAL):
            last_report = now
            await on_progress(job)

    try:
        # ── فاز ۱: جمع‌آوری روی سرور ──
        if job.phase == "capture" and source is not None:
            await report(force=True)
            if await _capture(job, client, source):
                job.phase = "send"

        # ── فاز ۲: ارسال از روی کش ──
        if not job.cancel_requested:
            job.phase = "send"
            await report(force=True)
            await _send(job, client, dest)

        if job.cancel_requested:
            job.state = "cancelled"
        else:
            job.state = "done"
            job.phase = "done"

    except asyncio.CancelledError:
        job.state = "paused"
        raise
    except Exception as e:
        logger.error(f"Cache job {job.id} failed: {type(e).__name__}: {e}",
                     exc_info=True)
        job.state = "error"
        job.capture_note = f"{type(e).__name__}: {e}"[:200]

    finally:
        _active_jobs.pop(job.user_db_id, None)

        # صف پاکسازی Recents را خالی کن (پاکسازی پس‌زمینه بود)
        if getattr(job, "guard", None) is not None:
            try:
                await job.guard.close()
            except Exception as e:
                logger.debug(f"recents guard close failed: {e}")

        if job.db_id:
            try:
                await db.update_forward_job(
                    job.db_id, sent=job.sent, skipped=job.skipped,
                    failed=job.failed, total=job.total,
                )
                await db.update_forward_job_phase(
                    job.db_id, phase=job.phase, captured=job.captured,
                    cache_bytes=job.bytes, capture_cursor=job.cursor,
                )
                await db.finish_forward_job(
                    job.db_id,
                    "done" if job.state == "done" else
                    ("paused" if job.state in ("cancelled", "paused") else "error"),
                    job.capture_note or None,
                )
            except Exception as e:
                logger.warning(f"Cache job {job.id} final save failed: {e}")

            # کش دیگر لازم نیست → آزادسازی فضا
            if job.state == "done":
                try:
                    files = await db.clear_job_cache(job.db_id)
                    _delete_files(files, job.db_id)
                    logger.info(f"Cache job {job.id} cache cleared")
                except Exception as e:
                    logger.warning(f"cache cleanup failed: {e}")

        await report(force=True)
        logger.info(
            f"Cache job {job.id} {job.state}: captured={job.captured} "
            f"sent={job.sent} failed={job.failed} bytes={job.bytes}"
        )


def _delete_files(paths, db_id=None):
    for path in paths or []:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logger.debug(f"remove {path}: {e}")

    # پوشه‌ی خالی job هم پاک شود تا دیسک تمیز بماند
    if db_id:
        folder = os.path.join(CACHE_DOWNLOADS, str(db_id))
        try:
            if os.path.isdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
        except Exception as e:
            logger.debug(f"remove cache dir {folder}: {e}")


# ═══════════════════════════════════
# فاز ۱ — جمع‌آوری
# ═══════════════════════════════════


async def _capture(job: CacheForwardJob, client, source) -> bool:
    """
    خواندن مبدأ و ذخیره روی سرور.

    خروجی: True اگر جمع‌آوری تمام شده باشد (تاریخچه تمام شد، سقف رسید یا
    دسترسی قطع شد و دیگر امیدی نیست) و False اگر کاربر دستی متوقف کرده باشد
    — در آن صورت فاز «جمع‌آوری» می‌ماند تا با «▶️ ادامه» از همان cursor
    ادامه پیدا کند.
    """
    budget_bytes = int(FORWARD_CACHE_MAX_GB * 1024 ** 3)
    budget_hit = False

    try:
        total_obj = await F._fetch_total(client, source, job)
        job.total = getattr(total_obj, "total", 0) or 0
        if job.limit:
            job.total = min(job.total, job.limit) if job.total else job.limit
    except Exception:
        job.total = 0

    while not job.cancel_requested:
        if job.limit and job.captured >= job.limit:
            break

        size = max(20, FORWARD_CACHE_BATCH)
        try:
            chunk = await F._fetch_chunk(client, source, job.cursor, size, job)
        except Exception as e:
            if _is_access_lost(e):
                job.capture_note = (
                    "دسترسی به چت مبدأ قطع شد (اخراج/حذف/خصوصی شدن). "
                    "پیام‌های جمع‌آوری‌شده امن هستند و ارسال می‌شوند."
                )
                logger.warning(f"Job {job.id} capture stopped: {e}")
                break
            raise

        if not chunk:
            break

        rows = []
        last_id = None
        for m in chunk:
            if job.cancel_requested:
                break
            if getattr(m, "action", None) is not None:
                job.skipped += 1
                last_id = m.id
                continue
            if job.media_only and not m.media:
                job.skipped += 1
                last_id = m.id
                continue

            row = await _row_from_message(job, client, source, m)

            # محدودیت حجم کش مدیا
            if row.get("media_size"):
                will_use = row["media_size"] if row.get("media_path") else 0
                if will_use and (job.bytes + will_use) > budget_bytes:
                    budget_hit = True
                    row["media_path"] = None
                    job.media_skipped += 1

            if row.get("media_path"):
                job.bytes += row.get("media_size") or 0

            rows.append(row)
            last_id = m.id

        added = await db.add_cached_messages(job.db_id, job.user_db_id, rows)
        job.captured += added or len(rows)

        # cursor فقط روی پیام‌هایی جلو می‌رود که واقعاً پردازش/ذخیره شده‌اند
        # (اگر وسط دسته متوقف شویم، بقیه دسته دوباره خوانده می‌شود — نه اینکه
        #  از دست برود)
        if job.cancel_requested:
            if last_id is not None:
                job.cursor = last_id
        else:
            job.cursor = chunk[-1].id

        try:
            await db.update_forward_job_phase(
                job.db_id, captured=job.captured, cache_bytes=job.bytes,
                capture_cursor=job.cursor,
            )
        except Exception as e:
            logger.debug(f"capture progress save failed: {e}")

        if FORWARD_CACHE_PAUSE > 0 and not job.cancel_requested:
            await asyncio.sleep(FORWARD_CACHE_PAUSE)

    if budget_hit:
        job.capture_note = (
            f"سقف حجم کش ({FORWARD_CACHE_MAX_GB} گیگابایت) پر شد؛ "
            "بقیه مدیا فقط به‌صورت مرجع ذخیره شد."
        )

    # لغو دستی → همچنان در فاز جمع‌آوری می‌مانیم تا ادامه‌دادن معنا داشته باشد
    return not job.cancel_requested


async def _row_from_message(job: CacheForwardJob, client, source, msg) -> dict:
    """تبدیل پیام تلگرام به رکورد کش"""
    name, label = await F._sender_info(job, client, msg)

    key = (job.id, msg.id)
    if key not in _msg_links:
        _msg_links[key] = F.build_message_link(source, msg.id) or ""
    link = _msg_links[key] or None

    row = {
        "src_msg_id": msg.id,
        "msg_date": getattr(msg, "date", None),
        "text": msg.text or "",
        "media_kind": "",
        "media_path": None,
        "media_size": 0,
        "doc_id": None,
        "doc_hash": None,
        "file_ref": None,
        "sender_name": name,
        "sender_label": label,
        "msg_link": link,
    }

    if not msg.media:
        return row

    row["media_kind"] = F.media_kind_of(msg.media)
    doc = _document_of(msg.media)
    if doc is not None:
        row["doc_id"] = getattr(doc, "id", None)
        row["doc_hash"] = getattr(doc, "access_hash", None)
        row["file_ref"] = _file_ref_of(doc)
        row["media_size"] = getattr(doc, "size", 0) or 0

    if not job.capture_media:
        return row

    # دانلود فایل روی سرور
    size_mb = (row["media_size"] or 0) / 1024 / 1024
    if size_mb and size_mb > FORWARD_CACHE_MEDIA_MAX_MB:
        job.media_skipped += 1
        return row

    folder = os.path.join(CACHE_DOWNLOADS, str(job.db_id))
    os.makedirs(folder, exist_ok=True)
    try:
        path = await msg.download_media(file=folder)
        if path:
            row["media_path"] = path
            try:
                row["media_size"] = os.path.getsize(path)
            except Exception:
                pass
    except FloodWaitError:
        raise
    except Exception as e:
        logger.warning(f"cache download failed msg={msg.id}: {e}")

    return row


def _document_of(media):
    """Document/Photo را از هر شکلی بیرون می‌کشد (id/hash/file_reference لازم است)"""
    if isinstance(media, types.Document):
        return media
    if isinstance(media, (types.Photo, types.PhotoEmpty)):
        return media
    if isinstance(media, types.MessageMediaDocument):
        return media.document if isinstance(media.document, types.Document) else None
    if isinstance(media, types.MessageMediaPhoto):
        photo = media.photo
        if isinstance(photo, types.Photo):
            return photo          # Photo هم id/access_hash/file_reference دارد
    return None


def _file_ref_of(obj) -> bytes | None:
    ref = getattr(obj, "file_reference", None)
    if ref is None:
        return None
    return bytes(ref)


CACHE_DOWNLOADS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "downloads", "cache",
)


# ═══════════════════════════════════
# فاز ۲ — ارسال از روی کش
# ═══════════════════════════════════


async def _send(job: CacheForwardJob, client, dest):
    """
    ارسال پیام‌های کش‌شده — به مبدأ هیچ نیازی نیست.

    سرعت با Pacer تنظیم می‌شود: چند پیام همزمان (concurrency) در پرواز
    می‌روند ولی ثبت/شمارش به ترتیب انجام می‌شود تا ترتیب پیام‌ها حفظ شود.
    """
    pacer = job.pacer
    retry_pass = False
    last_db_flush = time.time()

    while not job.cancel_requested:
        rows = await db.get_pending_cache(job.db_id, CACHE_BATCH,
                                          include_failed=retry_pass)
        if not rows:
            if not retry_pass:
                # یک‌بار هم ردیف‌های خطاخورده (خطای گذرا) دوباره تلاش می‌شوند
                retry_pass = True
                continue
            break

        done_ids: list[int] = []
        idx = 0
        try:
            while idx < len(rows) and not job.cancel_requested:
                wave = rows[idx: idx + max(1, pacer.concurrency)]
                idx += len(wave)

                results = await asyncio.gather(
                    *[_try_send(job, client, dest, row) for row in wave],
                    return_exceptions=True,
                )

                for row, res in zip(wave, results):
                    if res is None:
                        done_ids.append(row["id"])
                        job.sent += 1
                        pacer.success()
                        continue

                    if isinstance(res, FloodWaitError):
                        # محدودیت خورد → عقب‌نشینی و تلاش دوباره برای همین پیام
                        seconds = int(getattr(res, "seconds", 60) or 60)
                        pacer.flood(seconds)
                        logger.info(
                            f"Cache job {job.id}: flood wait {seconds}s "
                            f"(sent={job.sent}, pause={pacer.pause:.2f}s)"
                        )
                        await F._flood_sleep(job, seconds)
                        if job.cancel_requested:
                            break
                        try:
                            await _send_row(job, client, dest, row)
                            done_ids.append(row["id"])
                            job.sent += 1
                            pacer.success()
                        except Exception as e:
                            await db.mark_cache_failed(row["id"], f"{type(e).__name__}: {e}")
                            job.failed += 1
                        continue

                    # خطای واقعی این پیام
                    logger.warning(
                        f"Cache job {job.id} msg {row['src_msg_id']} failed: "
                        f"{type(res).__name__}: {res}"
                    )
                    await db.mark_cache_failed(row["id"], f"{type(res).__name__}: {res}")
                    job.failed += 1

                # نوشتن در DB دسته‌ای (نه برای هر پیام) → سرعت بالاتر
                now = time.time()
                if done_ids and (len(done_ids) >= DB_FLUSH_EVERY
                                 or now - last_db_flush >= 1.0):
                    await db.mark_cache_sent(done_ids)
                    done_ids = []
                    last_db_flush = now
                    try:
                        await db.update_forward_job(job.db_id, sent=job.sent,
                                                    failed=job.failed)
                    except Exception as e:
                        logger.debug(f"send progress save failed: {e}")

                if pacer.pause >= 0.02:
                    await pacer.wait()

        finally:
            # باقی‌مانده‌ها هم ثبت شوند تا در ادامه‌دادن تکراری نشوند
            if done_ids:
                try:
                    await asyncio.shield(
                        db.mark_cache_sent(list(done_ids))
                    )
                except Exception:
                    pass
        # پایان این دسته‌ی کش → آزادسازی دسته‌ی بعدی
        if not retry_pass and FORWARD_CACHE_PAUSE > 0 and not job.cancel_requested:
            await asyncio.sleep(min(FORWARD_CACHE_PAUSE, pacer.pause if pacer.pause else 0))


async def _try_send(job, client, dest, row):
    """ارسال یک پیام؛ خطا را برمی‌گرداند تا gather آن را بگیرد"""
    try:
        await _send_row(job, client, dest, row)
        return None
    except Exception as e:          # FloodWaitError هم داخلش هست
        return e


def _flood_seconds() -> int:
    """ثانیه‌های FloodWait از exception جاری (پیش‌فرض محافظه‌کارانه)"""
    import sys
    exc = sys.exc_info()[1]
    return int(getattr(exc, "seconds", 60) or 60)


async def _send_row(job: CacheForwardJob, client, dest, row: dict):
    """ارسال یک پیام کش‌شده"""
    text = row.get("text") or ""
    media_kind = row.get("media_kind") or ""

    header = _cached_header(job, row) if job.attributed else ""

    if not media_kind:
        body = F.clip(header + text, F.TEXT_LIMIT)
        if body:
            await client.send_message(dest, body)
        return

    # ── مدیا ──
    caption = F.clip((header + text) if text else header, F.CAPTION_LIMIT) \
        if (header or text) else None

    protected = media_kind in ("استیکر", "گیف")
    force_doc = protected and F.recents_guard_on()

    # ۱) فایل دانلودشده روی سرور
    path = row.get("media_path")
    if path and os.path.exists(path):
        kwargs = {}
        if caption:
            kwargs["caption"] = caption
        if force_doc:
            kwargs["force_document"] = True
        elif media_kind in ("ویدیو", "گیف"):
            kwargs["supports_streaming"] = True

        sent = await client.send_file(dest, path, **kwargs)
        await _maybe_clean_recents(job, client, media_kind, sent, row=row)
        return

    # ۲) ارسال با مرجع داکیومنت/عکس (بدون دانلود)
    ref_sent = await _send_by_reference(job, client, dest, row, caption,
                                        force_doc, media_kind)
    if ref_sent:
        return

    # ۳) راهی نماند → متن جانشین
    placeholder = F.clip(
        (header + text) if text else
        (header + "📎 مدیا — قابل بازیابی نبود (دسترسی به مبدأ قطع شد)"),
        F.TEXT_LIMIT,
    )
    await client.send_message(dest, placeholder)
    job.media_skipped += 1


async def _send_by_reference(job, client, dest, row, caption, force_doc, media_kind):
    """ارسال مدیا با id/hash/file_reference ذخیره‌شده"""
    doc_id, doc_hash = row.get("doc_id"), row.get("doc_hash")
    file_ref = row.get("file_ref")
    if not (doc_id and doc_hash):
        return False

    if isinstance(file_ref, memoryview):
        file_ref = bytes(file_ref)
    if not file_ref:
        return False

    try:
        if media_kind == "عکس":
            media = types.InputMediaPhoto(
                types.InputPhoto(id=int(doc_id), access_hash=int(doc_hash),
                                 file_reference=file_ref)
            )
        else:
            media = types.InputDocument(
                id=int(doc_id), access_hash=int(doc_hash),
                file_reference=file_ref,
            )

        kwargs = {}
        if caption:
            kwargs["caption"] = caption
        if force_doc:
            kwargs["force_document"] = True
        elif media_kind in ("ویدیو", "گیف"):
            kwargs["supports_streaming"] = True

        sent = await client.send_file(dest, media, **kwargs)
        await _maybe_clean_recents(job, client, media_kind, sent, fallback=media,
                                   row=row)
        return True

    except FloodWaitError:
        raise
    except Exception as e:
        logger.debug(f"send by reference failed msg={row['src_msg_id']}: {e}")
        return False


async def _maybe_clean_recents(job, client, media_kind, sent, fallback=None,
                                row: dict = None):
    """
    استیکر/گیف را از Recents پاک کن.

    در همه‌ی حالت‌ها (جز off) لازم است: برای مدیای «مرجع»، پارامتر
    force_document در Telethon نادیده گرفته می‌شود و سرور همان استیکر/گیف
    را می‌فرستد → به Recents اضافه می‌شود.
    """
    from config import CLEAN_RECENTS_MODE
    if (CLEAN_RECENTS_MODE or "document").lower() == "off":
        return
    if media_kind not in ("استیکر", "گیف"):
        return
    cleaned = False
    try:
        media = getattr(sent, "media", None) or fallback
        if media is not None:
            cleaned = await cleanup_recents(client, media)
    except Exception as e:
        logger.debug(f"recents cleanup skipped: {e}")

    # اگر پاسخ تلگرام نوع مدیا را مشخص نکرد، با مرجع ذخیره‌شده پاک کن
    if not cleaned and row:
        try:
            await unsave_by_reference(
                client, row.get("doc_id"), row.get("doc_hash"),
                row.get("file_ref"), media_kind,
            )
        except Exception as e:
            logger.debug(f"recents cleanup by reference skipped: {e}")


def _cached_header(job: CacheForwardJob, row: dict) -> str:
    """هدر مشخصات از داده‌های کش‌شده (بدون نیاز به دسترسی به مبدأ)"""
    chat_line = f"📥 از: {job.source_name}"
    if job.source_label:
        chat_line += f" ({job.source_label})"

    name = row.get("sender_name") or "نامشخص"
    label = row.get("sender_label") or ""
    sender_line = f"👤 فرستنده: {name}"
    if label:
        sender_line += f" — {label}"

    lines = [chat_line, sender_line]

    date = row.get("msg_date")
    if date:
        lines.append(f"🕒 {date.strftime('%Y-%m-%d %H:%M')} (UTC)")

    link = row.get("msg_link")
    lines.append(f"🔗 {link}" if link else f"🆔 پیام: {row.get('src_msg_id')}")

    return "\n".join(lines) + "\n" + F.HEADER_RULE


# ═══════════════════════════════════
# ادامه پس از ری‌استارت
# ═══════════════════════════════════


async def resume_job(row: dict, on_progress=None, user_db_id: int = None):
    """ادامه یک job دو مرحله‌ای از همان‌جایی که مانده"""
    uid = int(user_db_id or row["user_id"])

    if uid in _active_jobs:
        raise ValueError("job_running")

    client = await get_client(uid)
    if not client:
        raise ValueError("account_not_connected")

    src_ref = {"kind": row.get("src_kind"), "id": row.get("src_id"),
               "hash": row.get("src_hash"), "name": row.get("src_name") or ""}
    dst_ref = {"kind": row.get("dst_kind"), "id": row.get("dst_id"),
               "hash": row.get("dst_hash"), "name": row.get("dst_name") or ""}

    source = dest = None
    phase = row.get("phase") or "capture"

    # مبدأ فقط برای فاز جمع‌آوری لازم است (اگر در دسترس نباشد، ماهیتاً
    # جمع‌آوری تمام‌شده است و سراغ ارسال می‌رویم)
    if phase == "capture":
        try:
            source = await F.resolve_peer_ref(client, src_ref)
        except Exception as e:
            logger.warning(f"Job {row['id']}: source unavailable ({e}) → send phase")
            source = None
            phase = "send"

    try:
        dest = await F.resolve_peer_ref(client, dst_ref)
    except Exception as e:
        raise ValueError(f"destination_unavailable:{e}")

    if phase == "capture" and source is None:
        phase = "send"

    if phase != "capture":
        row = dict(row)
        row["phase"] = "send"

    job = await start_job(
        user_db_id=uid,
        source=source,
        dest=dest,
        source_name=src_ref["name"],
        dest_name=dst_ref["name"],
        on_progress=on_progress,
        resume_row=row,
    )
    logger.info(f"Cache job {job.id} resumed: phase={job.phase} "
                f"cursor={job.cursor} sent={job.sent}")
    return job
