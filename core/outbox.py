"""
صف ارسال گزارش‌ها (Outbox) — یکی برای هر کلاینت

وقتی کسی کل تاریخچه را پاک می‌کند، قبلاً ده‌ها/صدها پیام پشت سر هم و
بدون مکث ارسال می‌شد → FloodWait → و چون flood_sleep_threshold=0 است،
پیام‌ها با خطا دور ریخته می‌شدند.

اینجا:
- ارسال‌ها ترتیبی و با فاصله‌ی حداقل MIN_GAP ثانیه
- FloodWait → صبر واقعی (تا سقف MAX_FLOOD_SLEEP) و تلاش دوباره، نه دور ریختن
- سقف صف: اگر صف بیش از حد بزرگ شد، قدیمی‌ترین کارها رد می‌شوند و در لاگ
  ثبت می‌شود (حافظه‌ی نامحدود = خطر برای کل سرور)
- کارگر صف بعد از بیکاری خودش بسته می‌شود (بدون task همیشه‌زنده)
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from telethon.errors import FloodWaitError

from core import metrics

logger = logging.getLogger("outbox")

MIN_GAP = 0.4            # ثانیه بین دو ارسال
MAX_FLOOD_SLEEP = 900    # بیشتر از این → رها کردن همان یک کار
MAX_RETRIES = 3
MAX_QUEUE = 500
IDLE_EXIT = 60


class Outbox:
    def __init__(self, name: str = ""):
        self.name = name
        self._q: deque = deque()
        self._event = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self.dropped = 0

    def __len__(self):
        return len(self._q)

    def submit(self, factory, label: str = "") -> None:
        """factory: تابع بدون ورودی که coroutine ارسال را برمی‌گرداند"""
        if len(self._q) >= MAX_QUEUE:
            self._q.popleft()
            self.dropped += 1
            logger.warning(f"[{self.name}] outbox full — oldest item dropped")
        self._q.append((factory, label))
        self._event.set()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name=f"outbox-{self.name}")

    async def _run(self):
        loop = asyncio.get_running_loop()
        last = 0.0
        while True:
            if not self._q:
                self._event.clear()
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=IDLE_EXIT)
                except asyncio.TimeoutError:
                    if not self._q:
                        return
                continue

            factory, label = self._q.popleft()
            gap = MIN_GAP - (loop.time() - last)
            if gap > 0:
                await asyncio.sleep(gap)

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    await factory()
                    break
                except FloodWaitError as e:
                    metrics.inc("floodwait")
                    secs = int(getattr(e, "seconds", 5) or 5)
                    if secs > MAX_FLOOD_SLEEP or attempt == MAX_RETRIES:
                        logger.error(f"[{self.name}] {label}: flood {secs}s — gave up")
                        break
                    logger.warning(f"[{self.name}] {label}: flood {secs}s — waiting")
                    await asyncio.sleep(secs + 1)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[{self.name}] {label}: {type(e).__name__}: {e}")
                    break
            last = loop.time()

    async def drain(self, timeout: float = 30.0):
        """منتظر خالی شدن صف (برای تست/خاموشی)"""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while (self._q or (self._worker and not self._worker.done()
                           and self._event.is_set())) and loop.time() < end:
            await asyncio.sleep(0.05)

    def close(self):
        self._q.clear()
        if self._worker and not self._worker.done():
            self._worker.cancel()


def for_client(client) -> Outbox:
    ob = getattr(client, "_sb_outbox", None)
    if ob is None:
        ob = Outbox(str(getattr(client, "_sb_user", "")))
        client._sb_outbox = ob
    return ob
