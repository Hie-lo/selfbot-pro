"""
سقف سراسری منابع سرور (Resource Governor)

کارهای سنگین (فوروارد، ffmpeg، دانلود، کش مدیا) قبلاً فقط سقف «هر کاربر»
داشتند؛ ده کاربر همزمان = CPU/دیسک/پهنای باند کل سرور پر می‌شد و همه‌ی
اکانت‌ها کند می‌شدند. اینجا برای هر نوع کار یک «استخر» با سقف ثابت داریم:

    forward  → کارهای فوروارد/دامپ (سنگین‌ترین)
    ffmpeg   → تبدیل استیکر (CPU)
    download → دانلود دستی (ذخیره از لینک، استیکر)
    capture  → دانلودهای خودکار کوچک (گاوصندوق ضدحذف، تایم‌دار)

ویژگی‌ها:
- صف منصفانه (FIFO) با نمایش جایگاه در صف
- لغو امن: کاری که در صف منتظر است با cancel_requested از صف خارج می‌شود
  بدون اینکه ظرفیت «گم» شود (مشکل شناخته‌شده‌ی wait_for + Semaphore)
- آزادسازی موقت ظرفیت هنگام FloodWait طولانی (کاری که ساعت‌ها منتظر
  تلگرام است نباید جای بقیه را بگیرد)

بدون وابستگی خارجی؛ همه‌چیز داخل همین پروسه.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from contextlib import asynccontextmanager

logger = logging.getLogger("governor")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class Pool:
    """سمافور FIFO با قابلیت لغو امن و نمایش جایگاه"""

    def __init__(self, name: str, limit: int):
        self.name = name
        self.limit = max(1, int(limit))
        self.active = 0
        self._waiters: deque[asyncio.Future] = deque()
        self.total_waits = 0          # چند بار کاری مجبور به انتظار شد

    # ── وضعیت ──

    @property
    def waiting(self) -> int:
        return sum(1 for f in self._waiters if not f.done())

    def position(self, fut: asyncio.Future) -> int:
        """جایگاه ۱-مبنا در صف (۰ = در صف نیست)"""
        pos = 0
        for f in self._waiters:
            if f.done():
                continue
            pos += 1
            if f is fut:
                return pos
        return 0

    def stats(self) -> str:
        return f"{self.active}/{self.limit}" + (
            f" (صف {self.waiting})" if self.waiting else ""
        )

    # ── گرفتن/آزاد کردن ──

    def try_acquire(self) -> bool:
        if self.active < self.limit and not self.waiting:
            self.active += 1
            return True
        return False

    async def acquire(self, cancel_check=None, on_wait=None, poll: float = 2.0) -> bool:
        """
        گرفتن ظرفیت. خروجی False فقط وقتی cancel_check() درست شود.
        on_wait(position) در شروع انتظار و با تغییر جایگاه صدا زده می‌شود.
        """
        if self.try_acquire():
            return True

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._waiters.append(fut)
        self.total_waits += 1
        last_pos = None
        try:
            while True:
                pos = self.position(fut)
                if on_wait and pos != last_pos and pos:
                    last_pos = pos
                    try:
                        await on_wait(pos)
                    except Exception as e:  # نمایش صف نباید کار را خراب کند
                        logger.debug(f"on_wait failed: {e}")
                if fut.done():
                    return True
                done, _ = await asyncio.wait({fut}, timeout=poll)
                if fut in done:
                    return True
                # بین timeout و این خط هیچ await نیست → fut نمی‌تواند وسط کار انجام شود
                if cancel_check and cancel_check():
                    self._remove(fut)
                    return False
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                # ظرفیت به ما رسیده بود ولی لغو شدیم → پس بده
                self.release()
            else:
                self._remove(fut)
            raise

    def _remove(self, fut: asyncio.Future):
        try:
            self._waiters.remove(fut)
        except ValueError:
            pass
        if not fut.done():
            fut.cancel()

    def release(self):
        # تحویل مستقیم به نفر بعدی (active تغییر نمی‌کند)
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_result(True)
                return
        self.active = max(0, self.active - 1)

    @asynccontextmanager
    async def slot(self):
        await self.acquire()
        try:
            yield
        finally:
            self.release()


# ═══════════════════════════════════
# استخرهای سراسری
# ═══════════════════════════════════

_CPU = os.cpu_count() or 1

POOLS: dict[str, Pool] = {
    "forward": Pool("forward", _env_int("GOV_FORWARD_JOBS", 3)),
    "ffmpeg": Pool("ffmpeg", _env_int("GOV_FFMPEG", max(1, _CPU))),
    "download": Pool("download", _env_int("GOV_DOWNLOADS", 4)),
    "capture": Pool("capture", _env_int("GOV_CAPTURE", 4)),
}

POOL_LABELS = {
    "forward": "فوروارد",
    "ffmpeg": "ffmpeg",
    "download": "دانلود",
    "capture": "کش مدیا",
}


def pool(kind: str) -> Pool:
    return POOLS[kind]


def slot(kind: str):
    """استفاده: async with governor.slot("ffmpeg"): ..."""
    return POOLS[kind].slot()


def busy(kind: str) -> bool:
    p = POOLS[kind]
    return p.active >= p.limit or p.waiting > 0


def stats_lines() -> list[str]:
    return [f"• {POOL_LABELS.get(k, k)}: {p.stats()}" for k, p in POOLS.items()]


# ═══════════════════════════════════
# کارهای طولانی (job) — فوروارد
# ═══════════════════════════════════


async def acquire_for_job(kind: str, job, on_progress=None) -> bool:
    """
    گرفتن ظرفیت برای یک job طولانی. در حین انتظار job.queued=True و
    job.queue_pos جایگاه صف است (برای نمایش در پیام پیشرفت).
    خروجی False → job در صف لغو شد (ظرفیتی گرفته نشده).
    """
    p = POOLS[kind]

    async def _on_wait(pos: int):
        job.queued = True
        job.queue_pos = pos
        if on_progress:
            await on_progress(job)

    ok = await p.acquire(
        cancel_check=lambda: bool(getattr(job, "cancel_requested", False)),
        on_wait=_on_wait,
    )
    job.queued = False
    job.queue_pos = 0
    job._gov_kind = kind if ok else None
    return ok


def release_job(job):
    kind = getattr(job, "_gov_kind", None)
    if kind:
        job._gov_kind = None
        POOLS[kind].release()


@asynccontextmanager
async def job_slot(kind: str, job, on_progress=None):
    """
    async with governor.job_slot("forward", job, on_progress):
        await run(...)
    اگر job در صف لغو شود، بدنه باز هم اجرا می‌شود (بدون ظرفیت) تا منطق
    ذخیره‌ی نهایی/گزارش خودِ job مثل قبل انجام شود — بدنه چون
    cancel_requested را می‌بیند سریع تمام می‌شود.
    """
    await acquire_for_job(kind, job, on_progress)
    try:
        yield
    finally:
        release_job(job)


# FloodWait طولانی‌تر از این → ظرفیت موقتاً آزاد می‌شود
YIELD_ON_FLOOD_SECONDS = int(os.getenv("GOV_YIELD_ON_FLOOD", "60"))


def yield_for_flood(job, seconds: int) -> bool:
    """قبل از خواب طولانی FloodWait: ظرفیت را به بقیه بده"""
    if seconds >= YIELD_ON_FLOOD_SECONDS and getattr(job, "_gov_kind", None):
        job._gov_yielded = job._gov_kind
        release_job(job)
        return True
    return False


async def reclaim_after_flood(job) -> None:
    """بعد از FloodWait: دوباره در صف قرار بگیر"""
    kind = getattr(job, "_gov_yielded", None)
    if not kind:
        return
    job._gov_yielded = None
    await acquire_for_job(kind, job, None)
