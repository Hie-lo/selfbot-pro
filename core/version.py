"""
نسخه‌ی کدِ در حال اجرا.

برای این‌که در پنل و لاگ بشود فهمید کدِ روی سرور به‌روز است یا نه
(مثلاً «مود جدید در پنل نیست» معمولاً یعنی کد قدیمی اجرا می‌شود).
"""

import logging
import os
import subprocess

logger = logging.getLogger("version")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cache: str | None = None


def code_version() -> str:
    """مثل «65cea4f (2026-09-27 21:03)» — اگر git نبود، «unknown»"""
    global _cache
    if _cache is not None:
        return _cache
    try:
        out = subprocess.run(
            ["git", "-C", _ROOT, "log", "-1", "--format=%h (%cd)",
             "--date=format:%Y-%m-%d %H:%M"],
            capture_output=True, text=True, timeout=5,
        )
        _cache = (out.stdout or "").strip() or "unknown"
    except Exception as e:
        logger.debug(f"git version unavailable: {e}")
        _cache = "unknown"
    return _cache


def feature_summary() -> str:
    """خلاصه‌ی قابلیت‌های کدِ فعلی — برای مقایسه‌ی سریع با نسخه‌ی مورد انتظار"""
    bits = []
    try:
        from plugins import ai_engine as E
        bits.append(f"{len(E.MODES)} مود")
    except Exception:
        bits.append("? مود")
    try:
        from core import plugin_manager as pm
        bits.append(f"{len(pm.TOGGLEABLE_PLUGINS)} قابلیت قابل‌تنظیم")
    except Exception:
        pass
    return " · ".join(bits)
