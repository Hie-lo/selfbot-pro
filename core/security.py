"""
اعتبارسنجی ورودی‌ها، rate limiting، امنیت فایل
"""

import re
import os
import time
import hashlib
import logging
from collections import defaultdict

logger = logging.getLogger("security")

# ── Rate Limiter ──

_rate_limits: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(
    user_id: int,
    action: str,
    max_calls: int,
    window_seconds: int,
) -> bool:
    key = f"{user_id}:{action}"
    now = time.time()
    _rate_limits[key] = [
        t for t in _rate_limits[key] if now - t < window_seconds
    ]
    if len(_rate_limits[key]) >= max_calls:
        logger.warning(f"Rate limit hit: user={user_id} action={action}")
        return False
    _rate_limits[key].append(now)
    return True


# ── Validators ──


def validate_phone(phone: str) -> str | None:
    cleaned = re.sub(r"[\s\-\(\)]+", "", phone.strip())
    if re.match(r"^\+\d{10,15}$", cleaned):
        return cleaned
    return None


def validate_telegram_code(code: str) -> str | None:
    cleaned = code.strip()
    if re.match(r"^\d{5}$", cleaned):
        return cleaned
    return None


def validate_2fa_password(password: str) -> str | None:
    if password and 0 < len(password) <= 128:
        return password
    return None


def validate_url(url: str) -> str | None:
    cleaned = url.strip()
    if re.match(r"^https?://[^\s<>\"']+$", cleaned):
        return cleaned
    return None


def validate_telegram_link(url: str):
    cleaned = url.strip()
    # https://t.me/channel/123
    m = re.match(
        r"^https?://t\.me/([a-zA-Z]\w{3,})/(\d+)$", cleaned
    )
    if m:
        return m.group(1), int(m.group(2))
    # https://t.me/c/123456/789
    m = re.match(r"^https?://t\.me/c/(\d+)/(\d+)$", cleaned)
    if m:
        return m.group(1), int(m.group(2))
    return None


def validate_telegram_chat_link(url: str) -> str | None:
    """اعتبارسنجی لینک کانال/گروه برای resolve کردن entity تلگرام."""
    cleaned = url.strip().rstrip("/")

    # https://t.me/channel یا https://t.me/c/123456
    m = re.match(r"^https?://t\.me/([a-zA-Z]\w{3,})$", cleaned)
    if m:
        return m.group(1)

    m = re.match(r"^https?://t\.me/c/(\d+)$", cleaned)
    if m:
        return m.group(1)

    return None


def validate_interval(
    value: int, min_val: int = 10, max_val: int = 86400
) -> bool:
    return min_val <= value <= max_val


# ── Sanitizers ──


def sanitize_text(text: str, max_length: int = 4096) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return cleaned[:max_length]


def sanitize_filename(name: str) -> str:
    if not name:
        return "unnamed"
    cleaned = re.sub(r"[^\w\-.]", "_", name)
    cleaned = cleaned.replace("..", "_")
    return cleaned[:255]


# ── Hashing ──


def hash_phone(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


# ── File Validation ──

ALLOWED_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",
}

ALLOWED_VIDEO_SIGNATURES = [
    (b"\x00\x00\x00", b"ftyp"),
    (b"\x1a\x45\xdf\xa3", None),
]


def validate_file_type(file_path: str) -> str | None:
    try:
        with open(file_path, "rb") as f:
            header = f.read(32)

        for magic, mime in ALLOWED_MAGIC.items():
            if header.startswith(magic):
                return mime

        if b"ftyp" in header[:12]:
            return "video/mp4"

        if header.startswith(b"\x1a\x45\xdf\xa3"):
            return "video/webm"

        return None
    except Exception:
        return None


def validate_file_size(
    file_path: str, max_mb: int = 50
) -> bool:
    try:
        size = os.path.getsize(file_path)
        return size <= max_mb * 1024 * 1024
    except Exception:
        return False