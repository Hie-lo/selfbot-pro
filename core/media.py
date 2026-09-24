"""
ارسال مدیا بدون آلوده کردن لیست Recents

تلگرام وقتی اکانت یک **استیکر** یا **گیف (animation)** می‌فرستد، آن را به
لیست «استیکرهای اخیر» و «گیف‌های ذخیره‌شده» اضافه می‌کند. سه حالت:

  document → استیکر/گیف به‌صورت فایل ارسال می‌شود + حذف از Recents (پیش‌فرض)
  cleanup  → به‌صورت استیکر/گیف واقعی ارسال می‌شود + حذف از Recents
  off      → کاری انجام نمی‌شود

⚠️ نکته‌ی مهم (باگ واقعی که رفع شد):
  در Telethon، اگر مدیا «مرجع» باشد (MessageMediaDocument/Document/InputDocument
  که از خود تلگرام می‌آید)، پارامتر force_document **نادیده گرفته می‌شود**
  (utils.get_input_media) و سرور همان استیکر/گیف را می‌فرستد → به Recents
  اضافه می‌شود. پس در هر سه حالت (به‌جز off) بعد از ارسال، حتماً
  SaveRecentSticker/SaveGif با unsave=True صدا زده می‌شود تا لیست تمیز بماند.

نکته: فایلی که از دیسک آپلود می‌شود (webp/tgs/webm) استیکر نمی‌شود؛ آنجا
force_document واقعاً کار می‌کند.
"""

import asyncio
import logging

from telethon import utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl import types
from telethon.tl.functions.messages import (
    ClearRecentStickersRequest,
    SaveGifRequest,
    SaveRecentStickerRequest,
)

from config import CLEAN_RECENTS_MODE

logger = logging.getLogger("media")


# ═══════════════════════════════════
# تشخیص نوع مدیا
# ═══════════════════════════════════


def _document_of(item):
    """Document را از Message / MessageMediaDocument / Document بیرون می‌کشد"""
    if isinstance(item, types.Message):
        item = item.media
    if isinstance(item, types.MessageMediaDocument):
        item = item.document
    return item if isinstance(item, types.Document) else None


def _attr_names(item) -> list:
    doc = _document_of(item)
    return list(doc.attributes or []) if doc else []


def is_sticker(item) -> bool:
    return any(
        isinstance(a, types.DocumentAttributeSticker) for a in _attr_names(item)
    )


def is_animation(item) -> bool:
    """گیف (animation) — نه ویدیوی معمولی"""
    return any(
        isinstance(a, types.DocumentAttributeAnimated) for a in _attr_names(item)
    )


def needs_recents_protection(item) -> bool:
    return is_sticker(item) or is_animation(item)


def is_reference(item) -> bool:
    """
    آیا مدیا «مرجع» است (از خود تلگرام)؟ روی این نوع، force_document
    بی‌اثر است و استیکر/گیف واقعی ارسال می‌شود.
    """
    if isinstance(item, types.Message):
        item = item.media
    if isinstance(item, (types.MessageMediaDocument, types.InputDocument,
                         types.Document)):
        return True
    if isinstance(item, types.MessageMediaPhoto):
        return True
    return False


def media_kind(item) -> str:
    if is_sticker(item):
        return "استیکر"
    if is_animation(item):
        return "گیف"
    return ""


# ═══════════════════════════════════
# ارسال امن
# ═══════════════════════════════════


async def send_media_clean(
    client,
    dest,
    media,
    caption: str = None,
    reply_to=None,
    mode: str = None,
    guard: "RecentsGuard" = None,
    kind: str = None,
    **extra,
):
    """
    ارسال مدیا با محافظت از Recents.
    خروجی: پیام ارسال‌شده

    media می‌تواند Message / MessageMediaDocument / Document / مسیر فایل باشد.
    """
    mode = (mode or CLEAN_RECENTS_MODE or "document").lower()
    # kind صریح برای وقتی مدیا «ارجاع فشرده» است (InputDocument بدون attributes)
    explicit_kind = kind if kind in ("استیکر", "گیف") else None
    protect = mode != "off" and (explicit_kind is not None or needs_recents_protection(media))

    kwargs = dict(extra)
    if caption:
        kwargs["caption"] = caption
    if reply_to:
        kwargs["reply_to"] = reply_to

    if protect and mode == "document":
        # برای فایل‌های دیسکی واقعاً «فایل» می‌شود؛ برای مرجع‌ها بی‌اثر است
        # (به همین دلیل پایین‌تر حتماً از Recents هم پاک می‌کنیم)
        kwargs["force_document"] = True

    sent = await client.send_file(dest, media, **kwargs)

    if protect:
        # مرجع‌ها با force_document هم استیکر/گیف فرستاده می‌شوند → پاکسازی
        # sent ممکن است فایلِ generic باشد (force_document)، پس kind را از مدیای اصلی می‌گیریم
        sent_media = getattr(sent, "media", None)
        orig_kind = explicit_kind or media_kind(media)
        if guard is not None:
            # اگر sent قابل تشخیص نیست، با kindِ اصلی و با mediaِ اصلی fallback کن
            guard.submit(sent_media, kind=orig_kind, row=None)
            if not orig_kind:
                # fallback: اگر اصلی هم تشخیص نشد، خود sent را هم امتحان کن
                guard.submit(sent_media or media)
            elif sent_media is not None and media_kind(sent_media) != orig_kind:
                # sent generic شده → حتماً با مرجعِ اصلی هم تلاش کن
                guard.submit(media, kind=orig_kind)
        else:
            cleaned = await cleanup_recents(client, sent_media) if sent_media is not None else False
            if not cleaned and explicit_kind and isinstance(media, types.InputDocument):
                cleaned = await unsave_by_reference(
                    client, media.id, media.access_hash, media.file_reference, explicit_kind,
                )
            if not cleaned:
                await cleanup_recents(client, media)

    return sent


# ═══════════════════════════════════
# پاکسازی Recents
# ═══════════════════════════════════


async def _unsave(client, input_doc, sticker: bool, gif: bool) -> bool:
    """حذف یک داکیومنت از لیست‌های Recents"""
    done = False

    if isinstance(input_doc, types.InputDocumentEmpty):
        return False

    if gif:
        try:
            await client(SaveGifRequest(id=input_doc, unsave=True))
            done = True
        except FloodWaitError:
            raise                  # نگهبان صبر می‌کند و دوباره تلاش می‌کند
        except RPCError as e:
            logger.debug(f"SaveGif(unsave) skipped: {e}")

    if sticker:
        try:
            await client(SaveRecentStickerRequest(id=input_doc, unsave=True))
            done = True
        except FloodWaitError:
            raise
        except RPCError as e:
            logger.debug(f"SaveRecentSticker(unsave) skipped: {e}")

    return done


async def unsave_by_reference(client, doc_id, access_hash, file_reference,
                              kind: str) -> bool:
    """
    پاکسازی Recents با «مرجع ذخیره‌شده» — وقتی attributes مدیا در دست نیست.

    لازم است چون در ارسال مرجع (بدون دانلود) ممکن است پاسخ تلگرام فایل را
    بدون attributes برگرداند و cleanup_recents نتواند نوع را تشخیص دهد.
    """
    if kind not in ("استیکر", "گیف"):
        return False
    if not doc_id or not access_hash or not file_reference:
        return False
    try:
        ref = bytes(file_reference)
    except Exception:
        return False

    input_doc = types.InputDocument(
        id=int(doc_id), access_hash=int(access_hash), file_reference=ref,
    )
    return await _unsave(client, input_doc, sticker=(kind == "استیکر"),
                         gif=(kind == "گیف"))


async def cleanup_recents(client, *items) -> bool:
    """
    حذف استیکر/گیف از لیست‌های Recents (best-effort).

    items می‌تواند Message / Document / MessageMediaDocument باشد.
    """
    done = False

    for item in items:
        if not item:
            continue

        kind_sticker = is_sticker(item)
        kind_gif = is_animation(item)
        if not (kind_sticker or kind_gif):
            continue

        try:
            input_doc = utils.get_input_document(item)
        except (TypeError, ValueError, AttributeError):
            continue

        if await _unsave(client, input_doc, kind_sticker, kind_gif):
            done = True

    return done


class RecentsGuard:
    """
    پاکسازی استیکر/گیف از Recents در پس‌زمینه — بدون کند کردن ارسال.

    چرا لازم است؟
      ۱) پاکسازی درون‌خطی اگر FloodWait بخورد بی‌صدا رد می‌شد و استیکر
         در لیست می‌ماند (همان ایرادی که کاربر دید).
      ۲) اگر بلافاصله بعد از ارسال پاک کنیم، ممکن است سرور هنوز استیکر را
         ثبت نکرده باشد و unsave بی‌اثر شود → کمی صبر می‌کنیم.

    هر مورد چند بار تلاش می‌شود؛ FloodWait → صبر و تلاش دوباره.
    """

    FIRST_DELAY = 0.8         # مهلت ثبت سمت سرور
    GROUP = 20                # در هر دور چند مورد پردازش شود
    RETRY_WAITS = (1.5, 4.0)  # فاصله‌ی تلاش‌های دوباره
    MAX_WAIT = 60.0           # سقف صبر روی FloodWait پاکسازی
    FLOOD_BUDGET = 120.0      # مجموع صبر مجاز برای پاکسازی یک مورد
    CLOSE_TIMEOUT = 30.0      # سقف انتظار برای تمام شدن کارگر

    def __init__(self, client):
        self.client = client
        self.queued = 0
        self.cleaned = 0
        self.failed = 0
        self._q: asyncio.Queue = None
        self._task = None
        self._closed = False

    def start(self) -> None:
        if self._q is None:
            self._q = asyncio.Queue()
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._worker())

    def submit(self, media=None, kind: str = "", row: dict = None) -> None:
        """یک مورد برای پاکسازی؛ media یا row (مرجع ذخیره‌شده) کافی است"""
        if not kind:
            kind = media_kind(media)
        if kind not in ("استیکر", "گیف"):
            return
        if media is None and not (row and row.get("doc_id")):
            return

        self.start()
        self.queued += 1
        self._q.put_nowait((media, kind, row))

    # ── کارگر ──
    async def _worker(self) -> None:
        while True:
            item = await self._q.get()
            if item is None:
                self._q.task_done()
                return
            group = [item]
            while len(group) < self.GROUP:
                try:
                    nxt = self._q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    self._q.task_done()
                    self._closed = True
                    break
                group.append(nxt)

            # مهلت ثبت سمت سرور (یک‌بار برای کل گروه)
            await asyncio.sleep(self.FIRST_DELAY)

            await asyncio.gather(*[self._clean(*it) for it in group],
                                 return_exceptions=True)
            for _ in group:
                self._q.task_done()

    def _pending(self) -> int:
        if self._q is None:
            return 0
        # qsize=0 ولی کارگر ممکن است در خواب FIRST_DELAY باشد؛ unfinished_tasks را هم چک کن
        try:
            return max(self._q.qsize(), self._q.unfinished_tasks)
        except AttributeError:
            return self._q.qsize()

    async def _clean(self, media, kind: str, row: dict) -> None:
        """
        پاکسازی یک مورد.

        سیاست تلاش دوباره:
          • FloodWait → صبر و تلاش دوباره (تا سقف بودجه)
          • نتیجه‌ی نامشخص (unsave بی‌اثر) → یک تلاش دوباره‌ی کوتاه
          • خطای واقعی → همان لحظه ثبت می‌شود (تلاش دوباره بی‌فایده است)
        """
        spent = 0.0
        attempts = 1 + len(self.RETRY_WAITS)

        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(self.RETRY_WAITS[min(attempt, len(self.RETRY_WAITS)) - 1])
            try:
                ok = False
                if media is not None:
                    ok = await cleanup_recents(self.client, media)
                if not ok and row:
                    ok = await unsave_by_reference(
                        self.client, row.get("doc_id"), row.get("doc_hash"),
                        row.get("file_ref"), kind,
                    )
                if ok:
                    self.cleaned += 1
                    return
                if attempt >= 1:
                    break          # نتیجه‌ی نامشخص → بیشتر از یک‌بار تلاش نکن
            except FloodWaitError as e:
                wait = min(float(getattr(e, "seconds", 5) or 5), self.MAX_WAIT)
                spent += wait
                if spent > self.FLOOD_BUDGET:
                    break
                logger.debug(f"recents cleanup flood wait {wait:.0f}s")
                await asyncio.sleep(wait)
            except Exception as e:
                logger.debug(f"recents cleanup error: {type(e).__name__}: {e}")
                self.failed += 1
                logger.warning(
                    f"recents cleanup failed ({type(e).__name__}) — "
                    "استیکر/گیف ممکن است در Recents بماند"
                )
                return

        self.failed += 1
        logger.warning("recents cleanup gave up (مدیا در Recents ماند)")

    # ── پایان کار ──
    async def flush(self, timeout: float = 90.0) -> bool:
        """صبر تا خالی شدن صف پاکسازی (با سقف زمانی)"""
        if self._q is None:
            return True
        deadline = asyncio.get_event_loop().time() + timeout
        while self._pending() > 0 or (self.cleaned + self.failed) < self.queued:
            if asyncio.get_event_loop().time() > deadline:
                logger.warning(
                    f"recents guard: {self._pending()} مورد در صف، {self.queued - self.cleaned - self.failed} در پردازش (مهلت تمام شد)"
                )
                return False
            await asyncio.sleep(0.2)
        return True

    async def close(self) -> None:
        if self._q is None:
            return
        await self.flush()
        self._q.put_nowait(None)
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self.CLOSE_TIMEOUT)
            except Exception:
                self._task.cancel()

    def stats_text(self) -> str:
        if not self.queued:
            return ""
        out = f"🧹 پاکسازی Recents: {self.cleaned}"
        if self.failed:
            out += f" (ناموفق: {self.failed})"
        return out


def make_guard(client) -> RecentsGuard:
    return RecentsGuard(client)


async def clear_recent_stickers(client) -> bool:
    """پاک کردن کل لیست استیکرهای اخیر"""
    try:
        await client(ClearRecentStickersRequest())
        return True
    except RPCError as e:
        logger.warning(f"ClearRecentStickers failed: {e}")
        return False
