"""
اندازه‌گیری + کنترل پذیرش (Admission Control)

- تأخیر حلقه‌ی asyncio (بهترین شاخص سلامت: اگر بالا برود یعنی همه‌ی
  اکانت‌ها کند شده‌اند)
- RAM پروسه و RAM آزاد — با در نظر گرفتن محدودیت cgroup (Docker/systemd)؛
  psutil داخل کانتینر RAM «میزبان» را گزارش می‌کند و بدون این، سقف حافظه
  بی‌اثر می‌شد و پروسه با OOM کشته می‌شد
- شمارنده‌های نرخ (در دقیقه)
- تصمیم «اکانت جدید پذیرفته شود؟» بر اساس منابع واقعی، نه فقط یک عدد ثابت

بدون وابستگی خارجی (psutil از قبل در requirements بود).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque

import psutil

from config import (
    MAX_CLIENTS,
    MIN_FREE_MEM_MB,
    MAX_RSS_MB,
    LAG_LIMIT_MS,
)

logger = logging.getLogger("metrics")

_proc = psutil.Process(os.getpid())
STARTED_AT = time.time()
_baseline_rss: int | None = None   # RSS قبل از اتصال اکانت‌ها


# ═══════════════════════════════════
# شمارنده‌ها
# ═══════════════════════════════════


class _Counter:
    """شمارنده‌ی کل + نرخ دقیقه‌ی گذشته (سطل‌های ۱۰ ثانیه‌ای، حافظه ثابت)"""

    __slots__ = ("total", "_buckets")

    def __init__(self):
        self.total = 0
        self._buckets: deque = deque(maxlen=7)   # (bucket_ts, count)

    def inc(self, n: int = 1):
        self.total += n
        b = int(time.time() // 10)
        if self._buckets and self._buckets[-1][0] == b:
            ts, c = self._buckets[-1]
            self._buckets[-1] = (ts, c + n)
        else:
            self._buckets.append((b, n))

    def per_minute(self) -> int:
        now_b = int(time.time() // 10)
        return sum(c for ts, c in self._buckets if now_b - ts < 6)


_counters: dict[str, _Counter] = {}


def inc(name: str, n: int = 1):
    c = _counters.get(name)
    if c is None:
        c = _counters[name] = _Counter()
    c.inc(n)


def total(name: str) -> int:
    c = _counters.get(name)
    return c.total if c else 0


def rate(name: str) -> int:
    c = _counters.get(name)
    return c.per_minute() if c else 0


# ═══════════════════════════════════
# تأخیر حلقه
# ═══════════════════════════════════

_LAG_INTERVAL = 0.5
_lag_ewma_ms = 0.0
_lag_samples: deque = deque(maxlen=120)    # ~۶۰ ثانیه
_lag_task: asyncio.Task | None = None


async def _lag_loop():
    global _lag_ewma_ms
    loop = asyncio.get_running_loop()
    while True:
        t0 = loop.time()
        await asyncio.sleep(_LAG_INTERVAL)
        lag = max(0.0, (loop.time() - t0 - _LAG_INTERVAL) * 1000)
        _lag_samples.append(lag)
        _lag_ewma_ms = lag if not _lag_ewma_ms else (_lag_ewma_ms * 0.9 + lag * 0.1)


def start():
    """شروع اندازه‌گیری (یک بار در post_init)"""
    global _lag_task, _baseline_rss
    if _baseline_rss is None:
        _baseline_rss = rss_bytes()
    if _lag_task is None or _lag_task.done():
        _lag_task = asyncio.create_task(_lag_loop(), name="loop-lag")


async def stop():
    global _lag_task
    if _lag_task:
        _lag_task.cancel()
        try:
            await _lag_task
        except (asyncio.CancelledError, Exception):
            pass
        _lag_task = None


def lag_ms() -> float:
    return _lag_ewma_ms


def lag_max_ms() -> float:
    return max(_lag_samples) if _lag_samples else 0.0


# ═══════════════════════════════════
# حافظه
# ═══════════════════════════════════


def rss_bytes() -> int:
    try:
        return _proc.memory_info().rss
    except Exception:
        return 0


def _read_int(path: str) -> int | None:
    try:
        with open(path) as f:
            raw = f.read().strip()
        if raw in ("max", ""):
            return None
        return int(raw)
    except Exception:
        return None


def _cgroup_available() -> int | None:
    """RAM آزاد داخل محدودیت cgroup (v2 یا v1)؛ None = محدودیتی نیست"""
    # cgroup v2
    limit = _read_int("/sys/fs/cgroup/memory.max")
    if limit:
        used = _read_int("/sys/fs/cgroup/memory.current") or 0
        return max(0, limit - used)
    # cgroup v1
    limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if limit and limit < (1 << 60):     # عدد خیلی بزرگ = بدون محدودیت
        used = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes") or 0
        return max(0, limit - used)
    return None


def available_bytes() -> int:
    host = psutil.virtual_memory().available
    cg = _cgroup_available()
    return min(host, cg) if cg is not None else host


def per_client_bytes(n_clients: int) -> int:
    if n_clients <= 0 or _baseline_rss is None:
        return 0
    return max(0, rss_bytes() - _baseline_rss) // n_clients


# ═══════════════════════════════════
# کنترل پذیرش
# ═══════════════════════════════════

R_CAP_COUNT = "count"
R_CAP_MEMORY = "memory"
R_CAP_LAG = "lag"

CAP_MESSAGES = {
    R_CAP_COUNT: "ظرفیت سرور تکمیل است ({max} اکانت). بعداً تلاش کنید.",
    R_CAP_MEMORY: "حافظه‌ی سرور در حال حاضر کافی نیست. بعداً تلاش کنید.",
    R_CAP_LAG: "سرور در حال حاضر زیر بار سنگین است. چند دقیقه دیگر تلاش کنید.",
}


def admission(n_clients: int, new_login: bool = False) -> tuple[bool, str | None]:
    """
    آیا یک اکانت دیگر پذیرفته شود؟
    همیشه:
      - سقف تعداد (MAX_CLIENTS_PER_SERVER)
      - سقف RSS پروسه (MAX_RSS_MB — فقط اگر ادمین صریحاً تنظیم کرده باشد)
    فقط برای «ورود جدید» (new_login=True):
      - حداقل RAM آزاد (MIN_FREE_MEM_MB)
      - تأخیر حلقه (LAG_LIMIT_MS)
    اکانت‌های موجود (ری‌استارت/ادامه) با حدس حافظه رد نمی‌شوند: قبلاً روی همین
    سرور کار می‌کردند و رد کردنشان یعنی قطع سرویس کاربر پولی. تأخیر حلقه هم
    هنگام ری‌استارت به‌خاطر اتصال همزمان موقتاً بالاست.
    """
    if n_clients >= MAX_CLIENTS:
        return False, R_CAP_COUNT
    if MAX_RSS_MB and rss_bytes() > MAX_RSS_MB * 1024 * 1024:
        return False, R_CAP_MEMORY
    if new_login:
        if MIN_FREE_MEM_MB and available_bytes() < MIN_FREE_MEM_MB * 1024 * 1024:
            return False, R_CAP_MEMORY
        if LAG_LIMIT_MS and lag_ms() > LAG_LIMIT_MS:
            return False, R_CAP_LAG
    return True, None


def admission_message(reason: str | None) -> str:
    return CAP_MESSAGES.get(reason or R_CAP_COUNT, CAP_MESSAGES[R_CAP_COUNT]).format(
        max=MAX_CLIENTS
    )


# ═══════════════════════════════════
# گزارش
# ═══════════════════════════════════


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):,.0f} MB"


def _uptime() -> str:
    s = int(time.time() - STARTED_AT)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d} روز و {h} ساعت"
    if h:
        return f"{h} ساعت و {m} دقیقه"
    return f"{m} دقیقه"


def crypto_backend() -> str:
    try:
        import cryptg  # noqa: F401
        return "cryptg"
    except Exception:
        pass
    try:
        from telethon.crypto import libssl
        if libssl.encrypt_ige:
            return "libssl"
    except Exception:
        pass
    return "pyaes (کند)"


def loop_backend() -> str:
    try:
        loop = asyncio.get_running_loop()
        return type(loop).__module__.split(".")[0]
    except RuntimeError:
        return "?"


def stats_text() -> str:
    """گزارش HTML برای /stats ادمین"""
    from core import governor
    from core.client_manager import active_clients

    n = len(active_clients)
    pcb = per_client_bytes(n)
    lines = [
        "📊 <b>وضعیت سرور</b>",
        "",
        f"👥 اکانت‌های متصل: <b>{n}</b> / {MAX_CLIENTS}",
        f"🧠 RAM پروسه: <b>{_mb(rss_bytes())}</b>"
        + (f" (هر اکانت ≈ {_mb(pcb)})" if pcb else ""),
        f"💾 RAM آزاد: {_mb(available_bytes())}"
        + (" (محدودیت کانتینر)" if _cgroup_available() is not None else ""),
        f"⏱ تأخیر حلقه: میانگین <b>{lag_ms():.0f}ms</b> · بیشینه‌ی ۶۰ث {lag_max_ms():.0f}ms",
        "",
        "📨 <b>ترافیک (دقیقه‌ی اخیر / کل)</b>",
        f"• پیام پی‌وی کش‌شده: {rate('pv_cached')} / {total('pv_cached'):,}",
        f"• درخواست نام به تلگرام: {rate('peer_rpc')} / {total('peer_rpc'):,}",
        f"• نام از حافظه (بدون درخواست): {rate('peer_hit')} / {total('peer_hit'):,}",
        f"• گزارش حذف/ویرایش: {total('deleted_reported'):,} / {total('edit_reported'):,}",
        f"• FloodWait: {rate('floodwait')} / {total('floodwait'):,}",
        "",
        "🧵 <b>ظرفیت کارهای سنگین</b> (فعال/سقف)",
        *governor.stats_lines(),
    ]
    try:
        from core import pv_cache
        lines += ["", pv_cache.stats_line()]
    except Exception:
        pass
    lines += [
        "",
        f"⚙️ رمزنگاری: {crypto_backend()} · حلقه: {loop_backend()}",
        f"⏳ آپ‌تایم: {_uptime()}",
    ]
    return "\n".join(lines)
