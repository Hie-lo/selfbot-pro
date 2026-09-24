"""
کش مشترک پیام‌های پی‌وی — لایه‌ی داده‌ی ضدحذف و ضدویرایش

قبلاً (مشکل ۷):
- ضدحذف و ضدویرایش هرکدام یک کش جدا و یک هندلر جدا داشتند (داده‌ی تکراری)
- برای هر پیام تا ~۴ درخواست API (get_entity فرستنده/چت، get_me)
- کل شیء Telethon مدیا نگه داشته می‌شد (پرحجم)؛ سقف بر اساس «تعداد» بود
- مدیا فقط به‌صورت ارجاع بود → بعد از «حذف برای هر دو» اغلب برنمی‌گشت
- پیام‌های لینک‌دار (MessageMediaWebPage) «مدیا» حساب می‌شدند و ارسالشان
  شکست می‌خورد → متن حذف‌شده گزارش نمی‌شد
- پیام‌های Saved Messages هم کش می‌شدند → حذف یک گزارش در Saved Messages
  دوباره همان گزارش را می‌فرستاد

الان:
- یک هندلر و یک کش برای هر اکانت؛ هر پلاگین «مصرف‌کننده» است
- صفر درخواست API در مسیر دریافت پیام (نام‌ها از core.peers)
- رکورد فشرده با __slots__ و ارجاع فشرده‌ی مدیا (InputPhoto/InputDocument)
- سقف همزمان «حجم» (PV_CACHE_MAX_MB) و «سن» (PV_CACHE_MAX_AGE_HOURS)
- گاوصندوق مدیا (Vault): مدیای کوچکِ طرف مقابل همان لحظه دانلود و با
  AES-GCM (کلید جدا برای هر اکانت) روی دیسک نگه داشته می‌شود؛ با انقضا،
  حذف رکورد، خاموش شدن قابلیت، قطع/تعلیق اکانت و ری‌استارت پاک می‌شود
- هیچ پیامی در دیتابیس ذخیره نمی‌شود
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import sys
import time
import weakref
from collections import OrderedDict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl import types

from config import (
    DOWNLOADS_DIR,
    ENCRYPTION_KEY,
    PV_CACHE_MAX_AGE_HOURS,
    PV_CACHE_MAX_MB,
    PV_VAULT_ENABLED,
    PV_VAULT_MAX_MB,
    PV_VAULT_QUOTA_MB,
    PV_VAULT_TTL_HOURS,
    PV_VAULT_TYPES,
    PV_WARMUP_CHATS,
    PV_WARMUP_ENABLED,
    PV_WARMUP_HOURS,
    PV_WARMUP_MESSAGES,
    PV_WARMUP_VAULT_FILES,
)
from core import governor, metrics, peers

logger = logging.getLogger("pv_cache")

MB = 1024 * 1024
VAULT_ROOT = os.path.join(DOWNLOADS_DIR, "vault")
SWEEP_INTERVAL = 600
MAX_PENDING_CAPTURES = 20
WARMUP_DELAY = (3.0, 15.0)     # بعد از اتصال، تا کار روشن شدن تمام شود
WARMUP_GAP = 1.0               # فاصله بین خواندن دو چت (محدودیت تلگرام)
WARMUP_SCAN = 100              # چند دیالوگ آخر بررسی شود (= یک درخواست)
AUTO_DELETE_MARGIN = 15        # ثانیه — حذف نزدیک به زمان «حذف خودکار» ≠ حذف دستی
SERVICE_IDS = {777000}         # اعلان‌های رسمی تلگرام (کدها خودکار پاک می‌شوند)

_instances: "weakref.WeakSet[PVCache]" = weakref.WeakSet()


# ═══════════════════════════════════
# دسته‌بندی مدیا
# ═══════════════════════════════════

# برچسب‌هایی که core.media برای محافظت از Recents می‌شناسد
RECENTS_KIND = {"sticker": "استیکر", "gif": "گیف"}


def classify(media):
    """
    خروجی: (kind, ref, doc)
      kind: photo|voice|round|sticker|gif|video|audio|document|other|None
      ref : ارجاع فشرده برای ارسال دوباره (یا خود مدیا برای انواع نادر)
      doc : Document اصلی (برای attributes در گاوصندوق) یا None
    """
    if media is None or isinstance(
        media, (types.MessageMediaWebPage, types.MessageMediaEmpty)
    ):
        return None, None, None

    if isinstance(media, types.MessageMediaPhoto):
        p = media.photo
        if not isinstance(p, types.Photo):
            return None, None, None
        return "photo", types.InputPhoto(p.id, p.access_hash, p.file_reference), None

    if isinstance(media, types.MessageMediaDocument):
        d = media.document
        if not isinstance(d, types.Document):
            return None, None, None
        kind = "document"
        for a in d.attributes or []:
            if isinstance(a, types.DocumentAttributeSticker):
                kind = "sticker"
                break
            if isinstance(a, types.DocumentAttributeAnimated):
                kind = "gif"
                break
            if isinstance(a, types.DocumentAttributeAudio):
                kind = "voice" if a.voice else "audio"
            elif isinstance(a, types.DocumentAttributeVideo) and kind == "document":
                kind = "round" if a.round_message else "video"
        return kind, types.InputDocument(d.id, d.access_hash, d.file_reference), d

    # موقعیت، مخاطب، نظرسنجی، تاس و ... → همان رفتار قبلی (ارسال خود مدیا)
    return "other", media, None


def media_size(media) -> int:
    """حجم تقریبی فایل (برای تصمیم گاوصندوق، قبل از دانلود)"""
    if isinstance(media, types.MessageMediaDocument) and isinstance(media.document, types.Document):
        return int(media.document.size or 0)
    if isinstance(media, types.MessageMediaPhoto) and isinstance(media.photo, types.Photo):
        best = 0
        for s in media.photo.sizes or []:
            if isinstance(s, types.PhotoSizeProgressive):
                best = max(best, max(s.sizes or [0]))
            else:
                best = max(best, int(getattr(s, "size", 0) or 0))
        return best
    return 0


# ═══════════════════════════════════
# رکورد
# ═══════════════════════════════════


class Rec:
    __slots__ = (
        "msg_id", "chat_id", "sender_id", "is_me", "date", "ts",
        "text", "orig_text", "kind", "ref", "attrs", "mime",
        "vault", "size", "expires",
    )

    def __init__(self, msg_id, chat_id, sender_id, is_me, date, text,
                 kind=None, ref=None, attrs=None, mime=None):
        self.msg_id = msg_id
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.is_me = is_me
        self.date = date
        self.ts = date.timestamp() if date else time.time()
        self.text = text or ""
        self.orig_text = None          # متن قبل از اولین ویرایش (برای ضدحذف)
        self.kind = kind
        self.ref = ref
        self.attrs = attrs
        self.mime = mime
        self.vault = None              # مسیر فایل رمزنگاری‌شده
        self.expires = 0.0             # زمان «حذف خودکار» چت (ttl_period)؛ 0 = ندارد
        self.size = 0
        self.resize()

    def resize(self) -> int:
        size = 360 + sys.getsizeof(self.text)
        if self.orig_text is not None:
            size += sys.getsizeof(self.orig_text)
        if self.ref is not None:
            size += 240 + len(getattr(self.ref, "file_reference", b"") or b"")
            if self.kind == "other":
                size += 2048           # شیء کامل Telethon
        if self.attrs:
            size += 200 * len(self.attrs)
        self.size = size
        return size

    def auto_expired(self, now: float | None = None) -> bool:
        """
        آیا این پیام با تایمر «حذف خودکار» چت پاک شده؟ (کسی حذفش نکرده)
        تلگرام برای حذف خودکار هم همان آپدیت حذف را می‌فرستد.
        """
        if not self.expires:
            return False
        return (now or time.time()) >= self.expires - AUTO_DELETE_MARGIN

    @property
    def original_text(self) -> str:
        """متنی که ضدحذف گزارش می‌کند (مثل قبل: متن اولیه‌ی پیام)"""
        return self.orig_text if self.orig_text is not None else self.text


# ═══════════════════════════════════
# گاوصندوق مدیا
# ═══════════════════════════════════


def _derive_key(user_db_id: int) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"selfbot-pv-vault-v1",
        info=f"acct:{user_db_id}".encode(),
    ).derive(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)


class Vault:
    def __init__(self, user_db_id: int):
        self.user_db_id = user_db_id
        self.dir = os.path.join(VAULT_ROOT, str(int(user_db_id)))
        self._aead = AESGCM(_derive_key(user_db_id))
        self._files: OrderedDict[int, tuple[str, int, float]] = OrderedDict()
        self.bytes = 0
        self._pending: dict[int, asyncio.Task] = {}
        # فایل‌های جامانده از اجرای قبلی بی‌استفاده‌اند (کش در حافظه بود)
        shutil.rmtree(self.dir, ignore_errors=True)

    def __len__(self):
        return len(self._files)

    def eligible(self, rec: Rec, media) -> bool:
        if not PV_VAULT_ENABLED or rec.is_me or rec.kind not in PV_VAULT_TYPES:
            return False
        if getattr(media, "ttl_seconds", None):   # تایم‌دار → پلاگین timed_saver
            return False
        size = media_size(media)
        return 0 < size <= PV_VAULT_MAX_MB * MB and len(self._pending) < MAX_PENDING_CAPTURES

    def schedule(self, client, msg, rec: Rec):
        task = asyncio.create_task(self._capture(client, msg, rec))
        self._pending[rec.msg_id] = task
        task.add_done_callback(lambda t, mid=rec.msg_id: self._pending.pop(mid, None))

    async def _capture(self, client, msg, rec: Rec):
        try:
            async with governor.slot("capture"):
                data = await client.download_media(msg, file=bytes)
            if not data or len(data) > PV_VAULT_MAX_MB * MB:
                return
            nonce = os.urandom(12)
            blob = nonce + self._aead.encrypt(nonce, data, str(rec.msg_id).encode())
            self._make_room(len(blob))
            path = os.path.join(self.dir, f"{rec.msg_id}.bin")
            await asyncio.to_thread(_write_file, self.dir, path, blob)
            self._files[rec.msg_id] = (path, len(blob), time.time())
            self.bytes += len(blob)
            rec.vault = path
            metrics.inc("vault_saved")
        except FloodWaitError:
            metrics.inc("floodwait")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"vault capture {rec.msg_id} failed: {e}")

    def _make_room(self, need: int):
        quota = PV_VAULT_QUOTA_MB * MB
        while self._files and self.bytes + need > quota:
            mid = next(iter(self._files))
            self.remove(mid)

    async def wait_pending(self, msg_id: int, timeout: float = 15.0):
        task = self._pending.get(msg_id)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except Exception:
                pass

    async def load(self, rec: Rec) -> bytes | None:
        entry = self._files.get(rec.msg_id)
        if not entry:
            return None
        path = entry[0]
        try:
            blob = await asyncio.to_thread(_read_file, path)
            return self._aead.decrypt(blob[:12], blob[12:], str(rec.msg_id).encode())
        except Exception as e:
            logger.warning(f"vault load {rec.msg_id} failed: {e}")
            return None

    def remove(self, msg_id: int):
        task = self._pending.pop(msg_id, None)
        if task and not task.done():
            task.cancel()
        entry = self._files.pop(msg_id, None)
        if entry:
            self.bytes -= entry[1]
            try:
                os.remove(entry[0])
            except OSError:
                pass

    def sweep(self):
        cutoff = time.time() - PV_VAULT_TTL_HOURS * 3600
        while self._files:
            mid, (_, _, ts) = next(iter(self._files.items()))
            if ts >= cutoff:
                break
            self.remove(mid)

    def wipe(self):
        for task in list(self._pending.values()):
            task.cancel()
        self._pending.clear()
        self._files.clear()
        self.bytes = 0
        shutil.rmtree(self.dir, ignore_errors=True)


def _write_file(folder: str, path: str, blob: bytes):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def wipe_all_orphans():
    """هنگام روشن شدن: هر چیزی در vault از اجرای قبلی است و بی‌استفاده"""
    shutil.rmtree(VAULT_ROOT, ignore_errors=True)


# ═══════════════════════════════════
# کش اصلی
# ═══════════════════════════════════


class PVCache:
    def __init__(self, client, user_db_id: int):
        self.client = client
        self.user_db_id = user_db_id
        self.peers = peers.for_client(client)
        self.vault = Vault(user_db_id)
        self.my_id: int | None = None
        self._store: OrderedDict[int, Rec] = OrderedDict()
        self.bytes = 0
        self._consumers: dict[str, bool] = {}   # name → needs media
        self._handler = None
        self._sweeper: asyncio.Task | None = None
        self._warm_task: asyncio.Task | None = None
        self._warmed_media = False      # بازخوانی با مدیا انجام شده؟
        self.warm_stats = {"chats": 0, "messages": 0, "state": "—"}
        _instances.add(self)

    # ── مصرف‌کننده‌ها ──

    @property
    def need_media(self) -> bool:
        return any(self._consumers.values())

    async def acquire(self, name: str, media: bool) -> None:
        if self.my_id is None:
            me = await self.peers.me()
            if me is None:
                me_ent = await self.client.get_me()
                self.my_id = me_ent.id
            else:
                self.my_id = me.id
        self._consumers[name] = media
        if self._handler is None:
            self._handler = (self._on_new, events.NewMessage())
            self.client.add_event_handler(*self._handler)
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop())
        self._maybe_warm_up()

    async def release(self, name: str) -> None:
        self._consumers.pop(name, None)
        if not self.need_media:
            # دیگر کسی مدیا نمی‌خواهد → فایل‌ها و ارجاع‌ها همین حالا آزاد شوند
            self.vault.wipe()
            for mid in [m for m, r in self._store.items() if r.ref is not None]:
                rec = self._store[mid]
                if not rec.text:
                    self._store.pop(mid)
                    self.bytes -= rec.size
                    continue
                self.bytes -= rec.size
                rec.ref = rec.attrs = rec.mime = rec.kind = None
                self.bytes += rec.resize()
        if self._consumers:
            return
        if self._handler is not None:
            try:
                self.client.remove_event_handler(*self._handler)
            except Exception:
                pass
            self._handler = None
        if self._sweeper:
            self._sweeper.cancel()
            self._sweeper = None
        if self._warm_task and not self._warm_task.done():
            self._warm_task.cancel()
        self._warm_task = None
        self._warmed_media = False
        self._store.clear()
        self.bytes = 0
        self.vault.wipe()

    # ── دریافت پیام (مسیر داغ: بدون هیچ await/درخواست) ──

    async def _on_new(self, event):
        if not event.is_private:
            return
        msg = event.message
        chat_id = event.chat_id
        if msg is None or chat_id is None or chat_id == self.my_id:
            return   # Saved Messages کش نمی‌شود (گزارش‌ها همان‌جا می‌روند)
        self.add_message(msg, chat_id)

    def add_message(self, msg, chat_id: int, warm: bool = False) -> Rec | None:
        """
        افزودن پیام به کش. warm=True یعنی از بازخوانی تاریخچه آمده:
        رکوردی که زنده ثبت شده بازنویسی نمی‌شود (متن اصلی قبل از ویرایش حفظ
        شود) و دانلود گاوصندوق جداگانه و با سهمیه انجام می‌شود.
        """
        if getattr(msg, "action", None) is not None:
            return None     # پیام سرویس (عضویت، تماس و ...)
        media = getattr(msg, "media", None)
        # مدیای «یک‌بار دیدنی/زمان‌دار» بعد از دیده شدن خودبه‌خود پاک می‌شود؛
        # آن حذف کار کسی نیست → کش نمی‌شود (پلاگین تایم‌دار مسئول آن است)
        if media is not None and getattr(media, "ttl_seconds", None):
            return None

        existing = self._store.get(msg.id)
        if existing is not None and warm:
            if self.need_media and existing.ref is None and existing.kind is None:
                kind, ref, doc = classify(media)
                if ref is not None:
                    self.bytes -= existing.size
                    existing.kind, existing.ref = kind, ref
                    if doc is not None and kind in ("voice", "round"):
                        existing.attrs = list(doc.attributes)
                    existing.mime = getattr(doc, "mime_type", None) if doc is not None else None
                    self.bytes += existing.resize()
            return existing

        self.peers.learn_from_message(msg)
        sender_id = msg.sender_id
        is_me = bool(getattr(msg, "out", False)) or (sender_id == self.my_id)
        text = msg.text or ""

        kind = ref = doc = None
        if self.need_media:
            kind, ref, doc = classify(media)
        elif not text:
            return None     # فقط ضدویرایش فعال است و پیام متنی نیست

        rec = Rec(
            msg.id, chat_id, sender_id, is_me, msg.date, text, kind, ref,
            attrs=list(doc.attributes) if (doc is not None and kind in ("voice", "round")) else None,
            mime=getattr(doc, "mime_type", None) if doc is not None else None,
        )
        ttl_period = getattr(msg, "ttl_period", None)
        if ttl_period:
            rec.expires = rec.ts + int(ttl_period)
        self._put(rec)
        metrics.inc("pv_cached")
        if warm:
            return rec

        # نام طرف مقابل را در پس‌زمینه یاد بگیر (یک بار برای هر نفر)
        self.peers.warm(chat_id)
        if not is_me and sender_id and sender_id != chat_id:
            self.peers.warm(sender_id)

        if ref is not None and self.vault.eligible(rec, media):
            self.vault.schedule(self.client, msg, rec)
        return rec

    # ── بازخوانی تاریخچه (پیام‌های قبل از ری‌استارت / قبل از روشن کردن) ──

    def _maybe_warm_up(self):
        if not PV_WARMUP_ENABLED:
            return
        running = self._warm_task is not None and not self._warm_task.done()
        if running:
            return
        # اولین مصرف‌کننده، یا ضدحذف بعد از ضدویرایش روشن شد (ارجاع مدیا لازم است)
        if self._warm_task is None or (self.need_media and not self._warmed_media):
            self._warm_task = asyncio.create_task(self._warm_up(), name="pv-warmup")

    async def _warm_up(self, delay: tuple = None):
        self.warm_stats = {"chats": 0, "messages": 0, "state": "در انتظار"}
        try:
            lo, hi = delay or WARMUP_DELAY
            await asyncio.sleep(random.uniform(lo, hi))
            # بعد از مکث: همه‌ی پلاگین‌های این اکانت تا الان بارگذاری شده‌اند
            with_media = self.need_media
            self.warm_stats["state"] = "در حال خواندن"
            cutoff = time.time() - min(PV_WARMUP_HOURS, PV_CACHE_MAX_AGE_HOURS) * 3600

            try:
                dialogs = await self.client.get_dialogs(limit=WARMUP_SCAN)
            except FloodWaitError:
                metrics.inc("floodwait")
                self.warm_stats["state"] = "رد شد (محدودیت تلگرام)"
                return

            chats = []
            for d in dialogs:
                ent = getattr(d, "entity", None)
                if not isinstance(ent, types.User):
                    continue
                self.peers.learn(ent)               # نام‌ها رایگان
                if (ent.bot or ent.is_self or ent.deleted or ent.id == self.my_id
                        or ent.id in SERVICE_IDS):
                    continue
                d_date = getattr(d, "date", None)
                if not d_date or d_date.timestamp() < cutoff:
                    continue
                chats.append(ent)
                if len(chats) >= PV_WARMUP_CHATS:
                    break

            candidates = []
            for i, ent in enumerate(chats):
                if not self._consumers:
                    return
                if i:
                    await asyncio.sleep(WARMUP_GAP)
                try:
                    msgs = await self.client.get_messages(ent, limit=PV_WARMUP_MESSAGES)
                except FloodWaitError as e:
                    metrics.inc("floodwait")
                    if int(getattr(e, "seconds", 999) or 999) > 30:
                        self.warm_stats["state"] = "نیمه‌تمام (محدودیت تلگرام)"
                        break
                    await asyncio.sleep(int(e.seconds) + 1)
                    continue
                except Exception as e:
                    logger.debug(f"warm-up history {ent.id} failed: {e}")
                    continue
                for m in reversed(list(msgs or [])):          # قدیمی → جدید
                    date = getattr(m, "date", None)
                    if not date or date.timestamp() < cutoff:
                        continue
                    rec = self.add_message(m, ent.id, warm=True)
                    if rec is not None:
                        self.warm_stats["messages"] += 1
                        if rec.ref is not None and rec.vault is None:
                            candidates.append((m, rec))
                self.warm_stats["chats"] += 1

            self._resort()
            if with_media:
                self._warmed_media = True
                await self._warm_vault(candidates)
            if self.warm_stats["state"] == "در حال خواندن":
                self.warm_stats["state"] = "انجام شد"
            logger.info(
                f"warm-up user={self.user_db_id}: {self.warm_stats['chats']} chats, "
                f"{self.warm_stats['messages']} msgs"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.warm_stats["state"] = "خطا"
            logger.warning(f"warm-up user={self.user_db_id} failed: {type(e).__name__}: {e}")

    async def _warm_vault(self, candidates: list):
        """مدیای کوچکِ جدیدترین پیام‌ها — با سهمیه (نه هزاران دانلود در هر ری‌استارت)"""
        budget = PV_WARMUP_VAULT_FILES
        for m, rec in sorted(candidates, key=lambda x: -x[1].ts):
            if budget <= 0 or not self.need_media:
                break
            if self._store.get(rec.msg_id) is not rec:
                continue                      # در این فاصله حذف/بیرون رفت
            while len(self.vault._pending) >= MAX_PENDING_CAPTURES:
                await asyncio.sleep(0.5)
            if self.vault.eligible(rec, m.media):
                self.vault.schedule(self.client, m, rec)
                budget -= 1

    def _resort(self):
        """ترتیب کش بر اساس زمان پیام (بازخوانی، پیام‌های قدیمی‌تر را بعداً اضافه می‌کند)"""
        if len(self._store) > 1:
            self._store = OrderedDict(sorted(self._store.items(), key=lambda kv: kv[1].ts))
        self._evict()

    def _put(self, rec: Rec):
        old = self._store.pop(rec.msg_id, None)
        if old:
            self.bytes -= old.size
        self._store[rec.msg_id] = rec
        self.bytes += rec.size
        self._evict()

    def _evict(self):
        cap = PV_CACHE_MAX_MB * MB
        cutoff = time.time() - PV_CACHE_MAX_AGE_HOURS * 3600
        while self._store:
            first = next(iter(self._store.values()))
            if self.bytes <= cap and first.ts >= cutoff:
                break
            self._store.popitem(last=False)
            self.bytes -= first.size
            self.vault.remove(first.msg_id)

    def _evict_aged(self):
        """پاکسازی کامل پیام‌های قدیمی و پیام‌های «حذف خودکار» منقضی"""
        now = time.time()
        cutoff = now - PV_CACHE_MAX_AGE_HOURS * 3600
        for mid in [m for m, r in self._store.items()
                    if r.ts < cutoff or (r.expires and now > r.expires + 3600)]:
            rec = self._store.pop(mid)
            self.bytes -= rec.size
            self.vault.remove(mid)

    async def _sweep_loop(self):
        try:
            while True:
                await asyncio.sleep(SWEEP_INTERVAL)
                self._evict_aged()
                self._evict()
                self.vault.sweep()
        except asyncio.CancelledError:
            pass

    # ── دسترسی مصرف‌کننده‌ها ──

    def get(self, msg_id: int) -> Rec | None:
        return self._store.get(msg_id)

    def take(self, msg_id: int) -> Rec | None:
        """برداشتن رکورد (پیام حذف شد) — فایل گاوصندوق تا ارسال می‌ماند"""
        rec = self._store.pop(msg_id, None)
        if rec:
            self.bytes -= rec.size
        return rec

    def update_text(self, rec: Rec, new_text: str):
        if rec.orig_text is None:
            rec.orig_text = rec.text
        self.bytes -= rec.size
        rec.text = new_text or ""
        self.bytes += rec.resize()

    def __len__(self):
        return len(self._store)


def for_client(client, user_db_id: int) -> PVCache:
    c = getattr(client, "_sb_pvcache", None)
    if c is None:
        c = PVCache(client, user_db_id)
        client._sb_pvcache = c
    return c


def stats_line() -> str:
    caches = [c for c in list(_instances) if c._consumers]
    n_msgs = sum(len(c) for c in caches)
    mem = sum(c.bytes for c in caches)
    vf = sum(len(c.vault) for c in caches)
    vb = sum(c.vault.bytes for c in caches)
    warmed = sum(1 for c in caches if c.warm_stats.get("state") == "انجام شد")
    return (
        f"🗂 کش پی‌وی: {len(caches)} اکانت · {n_msgs:,} پیام · ≈{mem / MB:.1f} MB"
        f" · بازخوانی‌شده {warmed}/{len(caches)}"
        f"\n🔐 گاوصندوق مدیا: {vf:,} فایل · {vb / MB:.1f} MB"
    )
