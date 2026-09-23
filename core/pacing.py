"""
تنظیم سرعت فوروارد — سریع، ولی بدون خوردن محدودیت تلگرام

هر «پریست سرعت» تعیین می‌کند:
  pause       → فاصله‌ی شروع ارسال هر پیام (ثانیه)
  concurrency → چند پیام همزمان «در پرواز» باشد
  floor / cap → کف و سقف تنظیم خودکار

Pacer با بازخورد خود تلگرام تنظیم می‌شود:
  • FloodWait خورد → pause را چند برابر می‌کند (عقب‌نشینی) و منتظر می‌ماند
  • چند پیام پشت سر هم بدون مشکل رفت → کم‌کم تندتر می‌شود (تا کف پریست)

نتیجه: هیچ‌وقت بی‌دلیل کند نمی‌مانیم و هیچ‌وقت هم بی‌گدار تند نمی‌رویم.
"""

import asyncio
import logging
import time

from config import FORWARD_MSG_PAUSE_ENV, FORWARD_SPEED_ENV, _SLOW_ENV_WARNING

logger = logging.getLogger("pacing")

# ═══════════════════════════════════
# پریست‌ها
# ═══════════════════════════════════

PRESETS = {
    # نام        pause  conc  conc_max  floor  cap    توضیح
    "safe": {
        "pause": 0.8, "concurrency": 1, "conc_max": 2,
        "floor": 0.5, "cap": 60.0,
        "label": "🐢 محتاط",
        "hint": "کندترین ولی بی‌خطرترین — برای اکانت‌های تازه یا حساس",
    },
    "balanced": {
        "pause": 0.2, "concurrency": 1, "conc_max": 3,
        "floor": 0.08, "cap": 120.0,
        "label": "⚖️ متعادل",
        "hint": "پیشنهادی — از یک پیام همزمان شروع و در صورت روان بودن تندتر می‌شود",
    },
    "fast": {
        "pause": 0.08, "concurrency": 2, "conc_max": 5,
        "floor": 0.04, "cap": 180.0,
        "label": "🚀 سریع",
        "hint": "دو پیام همزمان — تا پنج بالا می‌رود اگر تلگرام گیر ندهد",
    },
    "max": {
        "pause": 0.0, "concurrency": 4, "conc_max": 8,
        "floor": 0.0, "cap": 300.0,
        "label": "🔥 حداکثری",
        "hint": "چهار پیام همزمان (تا هشت) — ممکن است محدودیت زودتر بخورد",
    },
}

PRESET_CYCLE = ["safe", "balanced", "fast", "max"]
DEFAULT_SPEED = "balanced"


def normalize(name: str) -> str:
    """نام پریست معتبر (ناشناخته → پیش‌فرض)"""
    name = (name or "").strip().lower()
    return name if name in PRESETS else DEFAULT_SPEED


def label(name: str) -> str:
    return PRESETS[normalize(name)]["label"]


def hint(name: str) -> str:
    return PRESETS[normalize(name)]["hint"]


def next_speed(name: str) -> str:
    name = normalize(name)
    i = PRESET_CYCLE.index(name)
    return PRESET_CYCLE[(i + 1) % len(PRESET_CYCLE)]


def legacy_warning() -> str:
    """اگر .env قدیمی سرعت را کم می‌کند، هشدار بده"""
    if not _SLOW_ENV_WARNING:
        return ""
    return (
        f"⚠️ در .env مقدار FORWARD_MSG_PAUSE={FORWARD_MSG_PAUSE_ENV} ست شده "
        "که سرعت را کم می‌کند. آن خط را حذف کن (یا به‌جایش "
        "FORWARD_SPEED=balanced|fast بگذار) و پنل را دوباره باز کن."
    )


def describe(name: str, per_message: float = 0.35) -> str:
    """تخمین سرعت برای نمایش به کاربر (پیام بر دقیقه)"""
    p = PRESETS[normalize(name)]
    gap = max(p["pause"], 0.0) + per_message / max(p["concurrency"], 1)
    rate = 60.0 / gap if gap > 0 else 600.0
    return f"≈ {rate:,.0f} پیام در دقیقه"


# ═══════════════════════════════════
# Pacer
# ═══════════════════════════════════

class Pacer:
    """
    کنترل‌کننده‌ی سرعت یک job.

    usage:
        pacer = Pacer("balanced")
        while rows:
            wave = rows[:pacer.concurrency]
            results = await asyncio.gather(*[send(r) for r in wave], ...)
            ...
            pacer.success(len(wave))
            await pacer.wait()

        # وقتی FloodWait خورد:
        pacer.flood(seconds)
    """

    def __init__(self, speed: str = None, concurrency: int = None):
        name = normalize(speed or DEFAULT_SPEED)
        preset = PRESETS[name]

        self.speed = name
        self.pause = float(preset["pause"])
        self.floor = float(preset["floor"])
        self.cap = float(preset["cap"])
        self.concurrency = int(concurrency or preset["concurrency"])
        self.conc_base = int(preset["concurrency"])
        self.conc_max = max(self.conc_base, int(preset.get("conc_max", self.conc_base)))

        # اگر کاربر خودش FORWARD_MSG_PAUSE را در .env ست کرده باشد،
        # همان مقدار مبنا می‌شود (کف هم همان می‌ماند تا پایین‌تر نرویم)
        if FORWARD_MSG_PAUSE_ENV is not None and FORWARD_SPEED_ENV is None:
            try:
                custom = max(0.0, float(FORWARD_MSG_PAUSE_ENV))
                self.pause = custom
                self.floor = custom
            except ValueError:
                pass

        # آمار
        self.floods = 0
        self.done = 0
        self.started = time.time()
        self._since_flood = 0
        self._ramp_block = 0.0     # تا این زمان همزمانی بالا نمی‌رود

    # ── تنظیم خودکار ──
    def success(self, n: int = 1) -> None:
        """
        چند ارسال موفق → کم‌کم تندتر.

        اول فاصله‌ی پیام‌ها را به کف پریست می‌رساند، بعد (اگر تلگرام
        اذیت نکرد) همزمانی را یکی‌یکی بالا می‌برد. بالاترین سرعت فقط وقتی
        به دست می‌آید که واقعاً هیچ محدودیتی نخورده باشیم.
        """
        self.done += n
        self._since_flood += n

        if self._since_flood >= 15 and self.pause > self.floor:
            self.pause = max(self.floor, self.pause * 0.85)
            self._since_flood = 0
            return

        at_floor = self.pause <= self.floor + 1e-9
        if (at_floor and self.concurrency < self.conc_max
                and time.time() >= self._ramp_block
                and self._since_flood >= 20):
            self.concurrency += 1
            self._since_flood = 0
            logger.info(
                f"pacer: همزمانی به {self.concurrency} رسید "
                f"(speed={self.speed}, pause={self.pause:.3f}s)"
            )

    def flood(self, seconds: float | None = None) -> None:
        """محدودیت خورد → عقب‌نشینی (هم فاصله، هم همزمانی)"""
        self.floods += 1
        self._since_flood = 0
        base = max(self.pause, 0.05)
        self.pause = min(self.cap, max(base * 2, 0.5))
        if seconds and seconds > 5:
            # محدودیت طولانی → محتاط‌تر بمان
            self.pause = min(self.cap, self.pause * 1.5)
        # همزمانی برگردد به پایه و مدتی بالا نرود
        if self.concurrency != self.conc_base:
            logger.info(
                f"pacer: همزمانی به {self.conc_base} برگشت (flood "
                f"{float(seconds or 0):.0f}s)"
            )
        self.concurrency = self.conc_base
        self._ramp_block = time.time() + (60.0 if not seconds or seconds <= 30
                                          else 180.0)

    async def wait(self) -> None:
        """فاصله‌ی بین پیام‌ها"""
        if self.pause > 0:
            await asyncio.sleep(self.pause)

    # ── آمار ──
    def rate(self) -> float:
        """پیام بر ثانیه (مشاهده‌شده)"""
        elapsed = max(time.time() - self.started, 0.001)
        if self.done >= 5:
            return self.done / elapsed
        # تخمین اولیه از پریست
        gap = self.pause + 0.35 / max(self.concurrency, 1)
        return 1.0 / gap if gap > 0 else 5.0

    def eta_seconds(self, remaining: int) -> int:
        rate = self.rate()
        if rate <= 0:
            return 0
        return int(remaining / rate)

    def eta_text(self, remaining: int) -> str:
        if remaining <= 0:
            return ""
        secs = self.eta_seconds(remaining)
        if secs <= 0:
            return ""
        if secs < 60:
            return f"≈ {secs} ثانیه"
        if secs < 3600:
            return f"≈ {secs // 60} دقیقه"
        hours, mins = divmod(secs // 60, 60)
        return f"≈ {hours} ساعت و {mins} دقیقه"

    def stats_text(self) -> str:
        return f"{label(self.speed)} · {self.rate():.1f} پیام/ثانیه"

    def state(self) -> dict:
        """برای ذخیره/بازیابی"""
        return {
            "speed": self.speed,
            "pause": self.pause,
            "concurrency": self.concurrency,
            "floods": self.floods,
        }
