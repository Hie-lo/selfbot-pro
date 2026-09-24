"""
موتور فوروارد / دامپ محتوا

- انتخاب چت از لیست واقعی دیالوگ‌های اکانت (`iter_dialogs`)
  → چت‌های بدون لینک و یوزرنیم هم قابل انتخاب‌اند
- هیچ پیامی به چت مبدأ ارسال نمی‌شود (گزارش فقط در چت ربات)
- خواندن تاریخچه پیام‌ها را «خوانده‌شده» نمی‌کند (تیک آبی نمی‌خورد)
"""

import asyncio
import html
import logging
import time
from datetime import datetime

from telethon import utils
from telethon.errors import (
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UserAlreadyParticipantError,
)
from telethon.tl import types
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)

from config import (
    CLEAN_RECENTS_MODE,
    FORWARD_BATCH_SIZE,
    FORWARD_MAX_FLOOD_WAIT,
    FORWARD_SPEED,
)
from core.client_manager import get_client
from core.pacing import Pacer
from database import db
from core.media import (
    cleanup_recents,
    is_animation,
    is_sticker,
    make_guard,
    needs_recents_protection,
    send_media_clean,
)
from core.security import extract_invite_hash

logger = logging.getLogger("forwarder")

DIALOG_TTL = 600          # کش لیست چت‌ها (ثانیه)
BATCH_SIZE = 50           # سقف دسته در هر درخواست تلگرام
REPORT_INTERVAL = 3.0     # فاصله آپدیت پیام پیشرفت (ثانیه)
SAVE_INTERVAL = 2.0       # فاصله ذخیره پیشرفت روی سرور (ثانیه)

ICONS = {"user": "👤", "group": "👥", "channel": "📢"}

# محدودیت‌های تلگرام
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
HEADER_RULE = "──────────────\n"


def recents_guard_on() -> bool:
    """آیا محافظت Recents فعال است؟ (حالت document)"""
    return (CLEAN_RECENTS_MODE or "").lower() == "document"


def clip(text: str, limit: int) -> str:
    """کوتاه کردن متن با حفظ محدودیت تلگرام"""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 12].rstrip() + "\n…(کوتاه شد)"


def build_message_link(chat, msg_id: int) -> str | None:
    """لینک مستقیم به پیام مبدأ (برای مشخص بودن محل پیام)"""
    if chat is None or not msg_id:
        return None

    if isinstance(chat, types.User):
        # پیوی: لینک داخلی تلگرام
        return f"tg://openmessage?user_id={chat.id}&message_id={msg_id}"

    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"

    if isinstance(chat, types.Channel):
        peer = str(utils.get_peer_id(chat))
        internal = peer[4:] if peer.startswith("-100") else peer.lstrip("-")
        # حذف صفرهای ابتدایی تا فرمت لینک با تلگرام یکی باشد
        internal = internal.lstrip("0") or internal
        return f"https://t.me/c/{internal}/{msg_id}"

    # گروه معمولی (Chat) لینک پیام ندارد
    return None


def chat_ref_label(chat) -> str:
    """برچسب شناسه چت: یوزرنیم یا آیدی عددی"""
    username = getattr(chat, "username", None)
    if username:
        return f"@{username}"
    if chat is None:
        return ""
    try:
        return str(utils.get_peer_id(chat))
    except Exception:
        return str(getattr(chat, "id", "") or "")

_dialog_cache: dict[int, dict] = {}
_jobs: dict[int, "ForwardJob"] = {}
_active_jobs: dict[int, "ForwardJob"] = {}
_next_job_id = 1


# ═══════════════════════════════════
# لیست چت‌ها
# ═══════════════════════════════════


def dialog_kind(entity) -> str:
    if isinstance(entity, types.User):
        return "user"
    if isinstance(entity, types.Channel):
        return "group" if getattr(entity, "megagroup", False) else "channel"
    return "group"


def dialog_icon(kind: str) -> str:
    return ICONS.get(kind, "💬")


# ═══════════════════════════════════
# Peer ref — ذخیره و بازیابی چت‌ها برای ادامه job
# ═══════════════════════════════════


def peer_ref(entity) -> dict:
    """تبدیل entity به دیکشنری قابل ذخیره در دیتابیس"""
    if entity == "me" or entity is None:
        return {"kind": "self", "id": 0, "hash": 0, "name": "Saved Messages"}

    if isinstance(entity, types.User):
        parts = [getattr(entity, "first_name", "") or "",
                 getattr(entity, "last_name", "") or ""]
        return {
            "kind": "user",
            "id": entity.id,
            "hash": getattr(entity, "access_hash", 0) or 0,
            "name": " ".join(p for p in parts if p).strip()
                    or f"کاربر {entity.id}",
        }

    if isinstance(entity, types.Chat):
        return {
            "kind": "chat",
            "id": entity.id,
            "hash": 0,
            "name": getattr(entity, "title", "") or f"گروه {entity.id}",
        }

    if isinstance(entity, types.Channel):
        return {
            "kind": "channel",
            "id": entity.id,
            "hash": getattr(entity, "access_hash", 0) or 0,
            "name": getattr(entity, "title", "") or f"کانال {entity.id}",
        }

    return {"kind": "unknown", "id": 0, "hash": 0,
            "name": str(getattr(entity, "title", entity))}


async def resolve_peer_ref(client, ref: dict):
    """
    بازیابی entity از دیتابیس.
    اول با access_hash ذخیره‌شده (بدون نیاز به کش سشن)، بعد با آیدی.
    """
    kind = (ref or {}).get("kind")

    if kind == "self":
        return "me"

    peer_id = ref.get("id")
    access_hash = ref.get("hash")
    if not peer_id:
        raise ValueError("peer_ref_invalid")

    try:
        if kind == "user" and access_hash:
            return await client.get_entity(
                types.InputPeerUser(int(peer_id), int(access_hash))
            )
        if kind == "channel" and access_hash:
            return await client.get_entity(
                types.InputPeerChannel(int(peer_id), int(access_hash))
            )
        if kind == "chat":
            return await client.get_entity(types.InputPeerChat(int(peer_id)))
    except Exception as e:
        logger.debug(f"resolve_peer_ref with hash failed ({kind}): {e}")

    return await client.get_entity(int(peer_id))


# ═══════════════════════════════════
# resolve کردن چت (یوزرنیم / آیدی / لینک / لینک دعوت)
# ═══════════════════════════════════


class JoinNeeded(Exception):
    """
    چت از طریق لینک دعوت شناسایی شد ولی اکانت عضو نیست.
    (resolve برای «هش» موفق شده اما برای دامپ باید عضو شد)
    """

    def __init__(self, invite_hash: str, title: str = ""):
        super().__init__("join_needed")
        self.hash = invite_hash
        self.title = title


def is_invite_ref(ref) -> bool:
    return isinstance(ref, str) and ref.startswith("+")


async def resolve_reference(client, ref):
    """
    تبدیل ورودی کاربر به entity واقعی تلگرام.

    پشتیبانی: @username · آیدی عددی · t.me/name · t.me/c/123 · لینک دعوت
    اگر چت با لینک دعوت پیدا شد ولی اکانت عضو نباشد → JoinNeeded
    """
    # لینک دعوت یا هش
    invite = extract_invite_hash(ref) if isinstance(ref, str) else None
    if invite:
        return await resolve_invite(client, invite)

    if isinstance(ref, str) and ref.startswith("@"):
        ref = ref[1:]

    if isinstance(ref, str) and ref.startswith("http"):
        from core.security import validate_telegram_chat_link
        ref = validate_telegram_chat_link(ref) or ref

    if isinstance(ref, str) and ref.lstrip("-").isdigit():
        value = int(ref)
        if value < 0:
            # آیدی منفی آماده (-100... یا -...)
            return await client.get_entity(value)
        # آیدی مثبت: کانال/سوپرگروه با فرم c/ (بیش از ۶ رقم) یا کاربر/گروه ساده
        if len(ref) > 6:
            try:
                return await client.get_entity(int(f"-100{ref}"))
            except Exception:
                return await client.get_entity(value)
        return await client.get_entity(value)

    return await client.get_entity(ref)


async def resolve_invite(client, invite_hash: str):
    """
    resolve لینک دعوت.

    اگر عضو باشیم → خود چت برگردانده می‌شود (بدون هیچ اثری برای اعضا)
    اگر نباشیم → JoinNeeded (عضویت برای اعضا قابل مشاهده است)
    """
    invite = await client(CheckChatInviteRequest(invite_hash))

    if isinstance(invite, types.ChatInviteAlready):
        return invite.chat

    title = getattr(invite, "title", "") or ""
    # ChatInvitePeek = پیام‌های محدود بدون عضویت کامل
    raise JoinNeeded(invite_hash, title)


async def join_invite(client, invite_hash: str):
    """
    عضویت در گروه/کانال خصوصی با لینک دعوت.
    توجه: عضویت برای اعضای گروه قابل مشاهده است.
    """
    try:
        result = await client(ImportChatInviteRequest(invite_hash))
        return result.chats[0] if result.chats else None
    except UserAlreadyParticipantError:
        return await resolve_invite(client, invite_hash)


def invite_notice(join: JoinNeeded) -> str:
    """پیام توضیحی وقتی اکانت عضو گروه نیست"""
    name = f"«{join.title}»" if join.title else "این گروه/کانال"
    return (
        f"🔒 {name} با لینک دعوت شناسایی شد، ولی اکانت شما عضو آن نیست.\n\n"
        "برای خواندن و دامپ کردن محتوا باید عضو شوید.\n"
        "⚠️ توجه: عضویت شما برای اعضای گروه قابل مشاهده است "
        "(پیام «X به گروه پیوست»)."
    )


def resolve_error_message(err: Exception) -> str:
    """تبدیل خطای resolve به پیام فارسی قابل‌فهم"""
    text = str(err)
    if isinstance(err, JoinNeeded):
        return invite_notice(err)
    if isinstance(err, InviteHashExpiredError):
        return "❌ لینک دعوت منقضی شده است."
    if isinstance(err, InviteHashInvalidError):
        return "❌ لینک دعوت نامعتبر است."
    if "Cannot get entity from a channel" in text:
        return (
            "🔒 اکانت شما عضو این گروه/کانال نیست.\n"
            "اول عضو شوید و بعد دوباره تلاش کنید."
        )
    if "Cannot find any entity" in text:
        return "❌ چتی با این آدرس پیدا نشد."
    return f"❌ پیدا نشد: {html.escape(text[:160])}"


async def load_dialogs(user_db_id: int, force: bool = False) -> list[dict]:
    """لیست چت‌های اکانت کاربر (با کش کوتاه‌مدت)"""
    now = time.time()
    cached = _dialog_cache.get(user_db_id)
    if cached and not force and (now - cached["at"]) < DIALOG_TTL:
        return cached["items"]

    client = await get_client(user_db_id)
    if not client:
        raise ValueError("account_not_connected")

    items: list[dict] = []
    try:
        async for d in client.iter_dialogs(limit=500):
            ent = d.entity
            # بات‌ها و اکانت‌های حذف‌شده به کار نمی‌آیند
            if isinstance(ent, types.User) and (ent.bot or ent.deleted):
                continue

            items.append({
                "peer_id": utils.get_peer_id(ent),
                "name": (d.name or "").strip() or str(d.id),
                "kind": dialog_kind(ent),
                "entity": ent,          # فقط در حافظه (برای فوروارد)
            })
    except Exception as e:
        logger.error(f"load_dialogs failed user={user_db_id}: {e}")
        raise ValueError(f"dialogs_failed:{e}")

    _dialog_cache[user_db_id] = {"at": now, "items": items}
    return items


def filter_dialogs(
    items: list[dict], query: str = "", chats_only: bool = False
) -> list[tuple[int, dict]]:
    """خروجی: لیست (ایندکس اصلی، آیتم) — ایندکس اصلی برای callback استفاده می‌شود"""
    q = (query or "").strip().lower()
    out = []
    for idx, item in enumerate(items):
        if chats_only and item["kind"] == "user":
            continue
        if q and q not in item["name"].lower():
            continue
        out.append((idx, item))
    return out


def is_owner(entity) -> bool:
    """آیا کاربر مالک این چت است؟ (فقط کانال/گروه — یوزر هرگز مالک نیست)"""
    # Channel شامل کانال و سوپرگروه است؛ creator=True یعنی مالک اصلی
    if isinstance(entity, types.Channel):
        return bool(getattr(entity, "creator", False))
    # Chat = گروه قدیمی
    if isinstance(entity, types.Chat):
        return bool(getattr(entity, "creator", False))
    return False


def filter_owned_dialogs(items: list[dict]) -> list[tuple[int, dict]]:
    """فقط کانال‌ها/گروه‌هایی که کاربر مالک آنهاست (creator) — با دسترسی تضمین‌شده"""
    out = []
    for idx, item in enumerate(items):
        if item.get("kind") == "user":
            continue
        ent = item.get("entity")
        if ent is not None and is_owner(ent):
            out.append((idx, item))
    return out


def get_dialog(user_db_id: int, idx: int) -> dict | None:
    cached = _dialog_cache.get(user_db_id)
    if not cached:
        return None
    items = cached["items"]
    if 0 <= idx < len(items):
        return items[idx]
    return None


def clear_cache(user_db_id: int):
    _dialog_cache.pop(user_db_id, None)


# ═══════════════════════════════════
# Job
# ═══════════════════════════════════


class ForwardJob:
    def __init__(
        self,
        job_id: int,
        user_db_id: int,
        chat_id: int,
        message_id: int,
        source_name: str,
        dest_name: str,
        mode: str,
        limit: int,
        media_only: bool,
        attributed: bool = False,
        speed: str | None = None,
    ):
        self.id = job_id
        self.user_db_id = user_db_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.source_name = source_name
        self.dest_name = dest_name
        self.mode = mode                # forward | copy
        self.limit = limit              # 0 = همه
        self.media_only = media_only
        self.attributed = attributed    # درج مشخصات چت و فرستنده

        # کش اطلاعات فرستنده‌ها (برای درج در هدر)
        self.senders: dict[int, tuple] = {}
        self.chat_label = ""            # یوزرنیم/آیدی چت مبدأ

        # سرعت (تنظیم خودکار با بازخورد تلگرام) — موتور زنده ترتیبی است
        self.speed = speed or FORWARD_SPEED
        self.pacer = Pacer(self.speed, 1)
        self.guard = None        # نگهبان پاکسازی Recents (پس‌زمینه)

        # ادامه‌پذیری و مقاومت در برابر محدودیت‌ها
        self.db_id: int | None = None   # آیدی رکورد در دیتابیس
        self.cursor: int = 0            # آخرین پیام پردازش‌شده
        self.chunk_done: set = set()    # پیام‌های ارسال‌شده در دسته جاری
        self.last_save: float = 0.0
        self.wait_seconds: int = 0      # شمارش معکوس FloodWait
        self.wait_until: float = 0.0
        self.resumed: bool = False

        self.state = "running"          # running | done | cancelled | error
        self.sent = 0
        self.skipped = 0
        self.failed = 0
        self.copied = 0
        self.total = 0
        self.error = ""
        self.cancel_requested = False
        self.started_at = time.time()
        self.task: asyncio.Task | None = None

    @property
    def duration(self) -> int:
        return max(0, int(time.time() - self.started_at))

    def duration_text(self) -> str:
        secs = self.duration
        if secs < 60:
            return f"{secs} ثانیه"
        return f"{secs // 60} دقیقه و {secs % 60} ثانیه"


def eta_text(job: ForwardJob) -> str:
    """تخمین زمان باقی‌مانده بر اساس سرعت واقعی"""
    if not job.total or job.sent <= 0:
        return ""

    elapsed = time.time() - job.started_at
    if elapsed < 5:
        return ""

    rate = job.sent / elapsed                    # پیام در ثانیه
    if rate <= 0:
        return ""

    left = max(0, job.total - job.sent)
    seconds = int(left / rate)

    if seconds < 60:
        return f"{seconds} ثانیه"
    if seconds < 3600:
        return f"{seconds // 60} دقیقه"
    hours = seconds // 3600
    if hours < 24:
        return f"{hours} ساعت و {(seconds % 3600) // 60} دقیقه"
    return f"{hours // 24} روز و {hours % 24} ساعت"


def mode_label(job: ForwardJob) -> str:
    if job.attributed:
        return "کپی با مشخصات فرستنده"
    return (
        "فوروارد با نام منبع" if job.mode == "forward"
        else "کپی بدون نام منبع"
    )


def progress_bar(sent: int, total: int, width: int = 12) -> str:
    if not total or total <= 0:
        return ""
    ratio = min(sent / total, 1.0)
    filled = int(ratio * width)
    return "▓" * filled + "░" * (width - filled) + f"  {ratio * 100:.0f}%"


def queued_text(job) -> str | None:
    """متن «در صف» (سقف سراسری فوروارد سرور پر است)"""
    if not getattr(job, "queued", False):
        return None
    pos = getattr(job, "queue_pos", 0) or 0
    return "🕒 در صف اجرا — ظرفیت فوروارد سرور پر است" + (
        f" (نفر {pos})" if pos else ""
    ) + "؛ خودکار شروع می‌شود"


def job_summary(job: ForwardJob) -> str:
    state_text = queued_text(job) or {
        "running": "⏳ در حال فوروارد...",
        "waiting": f"⏸ محدودیت تلگرام — ادامه خودکار پس از {job.wait_seconds} ثانیه",
        "done": "✅ فوروارد تمام شد",
        "cancelled": "⏹ فوروارد متوقف شد (قابل ادامه)",
        "paused": "⏸ متوقف شد (قابل ادامه)",
        "error": "❌ فوروارد با خطا متوقف شد (قابل ادامه)",
    }.get(job.state, job.state)

    lines = [
        f"<b>{state_text}</b>",
        "",
        f"📤 از: <b>{html.escape(job.source_name)}</b>",
        f"📥 به: <b>{html.escape(job.dest_name)}</b>",
        f"🏷 حالت: {mode_label(job)}",
        "",
        f"✅ ارسال: <b>{job.sent}</b>"
        + (f" از {job.total}" if job.total else ""),
        f"⏭ رد شده: {job.skipped}",
        f"❌ خطا: {job.failed}",
        *([job.guard.stats_text()] if (job.guard is not None
                                       and job.guard.stats_text()) else []),
    ]

    if job.state in ("running", "waiting"):
        lines.append(
            f"⚡ سرعت: {job.pacer.stats_text()}"
            + (f" · محدودیت‌ها: {job.pacer.floods}" if job.pacer.floods else "")
        )

    if job.copied:
        lines.append(f"🔁 کپی بدون نام منبع: {job.copied}")

    bar = progress_bar(job.sent, job.total)
    if job.state in ("running", "waiting") and bar:
        lines += ["", bar]

    if job.state in ("running", "waiting"):
        eta = eta_text(job)
        if eta:
            lines.append(f"⏳ تخمین باقی‌مانده: {eta}")
    else:
        lines += ["", f"⏱ زمان: {job.duration_text()}"]

    if job.resumed:
        lines.append("↩️ از پیام " + f"{job.cursor:,}" + " ادامه یافت")

    if job.error:
        lines += ["", f"⚠️ {html.escape(job.error[:200])}"]

    return "\n".join(lines)


def get_job(job_id: int) -> ForwardJob | None:
    return _jobs.get(job_id)


def get_active_job(user_db_id: int) -> ForwardJob | None:
    return _active_jobs.get(user_db_id)


async def stop_job(job_id: int) -> bool:
    job = _jobs.get(job_id)
    if not job or job.state != "running":
        return False
    job.cancel_requested = True
    logger.info(f"Job {job_id} cancellation requested")
    return True


async def wait_job(job: ForwardJob) -> ForwardJob:
    if job.task:
        try:
            await job.task
        except Exception:
            pass
    return job


async def start_job(
    *,
    user_db_id: int,
    source,
    dest,
    source_name: str,
    dest_name: str,
    mode: str = "forward",
    limit: int = 0,
    media_only: bool = False,
    attributed: bool = False,
    chat_id: int | None = None,
    message_id: int | None = None,
    on_progress=None,
    db_id: int | None = None,
    cursor: int = 0,
    counters: dict | None = None,
    resumed: bool = False,
    speed: str | None = None,
) -> ForwardJob:
    """شروع (یا ادامه) فوروارد در پس‌زمینه — خروجی بلافاصله برمی‌گردد"""
    global _next_job_id

    if user_db_id in _active_jobs:
        raise ValueError("job_running")

    client = await get_client(user_db_id)
    if not client:
        raise ValueError("account_not_connected")

    job = ForwardJob(
        job_id=_next_job_id,
        user_db_id=user_db_id,
        chat_id=chat_id,
        message_id=message_id,
        source_name=source_name,
        dest_name=dest_name,
        mode=mode,
        limit=limit,
        media_only=media_only,
        attributed=attributed,
        speed=speed,
    )
    job.db_id = db_id
    job.cursor = int(cursor or 0)
    job.resumed = resumed
    if counters:
        job.sent = counters.get("sent", 0) or 0
        job.skipped = counters.get("skipped", 0) or 0
        job.failed = counters.get("failed", 0) or 0
        job.copied = counters.get("copied", 0) or 0
        job.total = counters.get("total", 0) or 0

    _next_job_id += 1
    _jobs[job.id] = job
    _active_jobs[user_db_id] = job

    job.task = asyncio.create_task(
        _run_governed(job, client, source, dest, on_progress)
    )
    logger.info(
        f"Job {job.id} started: user={user_db_id} "
        f"{source_name} -> {dest_name} mode={mode} "
        f"attributed={attributed} limit={limit}"
    )
    return job


# ═══════════════════════════════════
# اجرا
# ═══════════════════════════════════


async def _run_governed(job: ForwardJob, client, source, dest, on_progress):
    """
    اجرای job داخل سقف سراسری فوروارد (core.governor).
    اگر ظرفیت پر باشد job «در صف» می‌ماند و جایگاهش در پیام پیشرفت دیده می‌شود.
    """
    from core import governor
    async with governor.job_slot("forward", job, on_progress):
        await _run(job, client, source, dest, on_progress)


async def _flood_sleep(job: ForwardJob, seconds: int):
    """خواب FloodWait + عقب‌نشینی خودکار سرعت"""
    from core import governor, metrics
    metrics.inc("floodwait")
    # انتظار طولانی → ظرفیت فوروارد سرور را موقتاً به بقیه بده
    yielded = governor.yield_for_flood(job, int(seconds or 0))
    try:
        await _flood_sleep_inner(job, seconds)
    finally:
        if yielded and not job.cancel_requested:
            await governor.reclaim_after_flood(job)


async def _flood_sleep_inner(job: ForwardJob, seconds: int):
    try:
        job.pacer.flood(seconds)
    except Exception:
        pass
    """
    خواب FloodWait — قابل توقف و همراه با نمایش شمارش معکوس.
    هیچ‌وقت job را به‌خاطر محدودیت تلگرام رها نمی‌کنیم.
    """
    wait = max(1, min(int(seconds or 1), FORWARD_MAX_FLOOD_WAIT))
    job.state = "waiting"
    job.wait_until = time.time() + wait
    job.wait_seconds = wait

    logger.warning(
        f"Job {job.id}: flood wait {wait}s (sent={job.sent})"
    )

    remaining = wait
    while remaining > 0 and not job.cancel_requested:
        step = min(5, remaining)
        await asyncio.sleep(step)
        remaining -= step
        job.wait_seconds = remaining

    job.wait_seconds = 0
    job.wait_until = 0
    if not job.cancel_requested:
        job.state = "running"


async def _save_progress(job: ForwardJob, force: bool = False):
    """ذخیره پیشرفت روی سرور (برای ادامه پس از ری‌استارت)"""
    if not job.db_id:
        return
    now = time.time()
    if not force and (now - job.last_save) < SAVE_INTERVAL:
        return
    job.last_save = now
    try:
        await db.update_forward_job(
            job.db_id,
            last_msg_id=job.cursor,
            sent=job.sent,
            skipped=job.skipped,
            failed=job.failed,
            copied=job.copied,
            total=job.total,
            status="running" if job.state in ("running", "waiting") else job.state,
        )
    except Exception as e:
        logger.warning(f"Job {job.id} progress save failed: {e}")


async def _fetch_chunk(client, source, cursor: int, size: int, job: ForwardJob):
    """
    خواندن یک دسته پیام از مبدأ — مقاوم در برابر FloodWait.
    (iter_messages با تاریخچه بزرگ FloodWait می‌دهد و job را می‌کشت)
    """
    while True:
        try:
            return await client.get_messages(
                source, min_id=cursor, reverse=True, limit=size
            )
        except FloodWaitError as e:
            await _flood_sleep(job, e.seconds)
            if job.cancel_requested:
                return []


async def _run(job: ForwardJob, client, source, dest, on_progress):
    """حلقه اصلی: دسته‌به‌دسته، مقاوم در برابر محدودیت، قابل ادامه"""
    last_report = 0.0
    job.chat_label = chat_ref_label(source) if not isinstance(source, str) else ""

    if recents_guard_on():
        job.guard = make_guard(client)

    async def report(force: bool = False):
        nonlocal last_report
        now = time.time()
        if on_progress and (force or now - last_report >= REPORT_INTERVAL):
            last_report = now
            await on_progress(job)

    try:
        # تعداد کل پیام‌های مبدأ (برای درصد و تخمین زمان)
        try:
            total_list = await _fetch_total(client, source, job)
            job.total = getattr(total_list, "total", 0) or 0
            if job.limit:
                job.total = min(job.total, job.limit) if job.total else job.limit
        except Exception:
            job.total = 0

        await report(force=True)

        while not job.cancel_requested:
            # سقف اختیاری (اگر کاربر تعداد مشخصی خواسته بود)
            remaining = None
            if job.limit:
                remaining = job.limit - job.sent
                if remaining <= 0:
                    break

            size = min(FORWARD_BATCH_SIZE, remaining) if remaining else FORWARD_BATCH_SIZE
            chunk = await _fetch_chunk(client, source, job.cursor, size, job)
            if not chunk:
                break

            job.cursor = chunk[-1].id      # حتی اگر همه رد شوند، جلو می‌رویم

            # فیلتر پیام‌ها
            pending = []
            for m in chunk:
                if getattr(m, "action", None) is not None:
                    job.skipped += 1
                    continue
                if job.media_only and not m.media:
                    job.skipped += 1
                    continue
                pending.append(m)

            if pending:
                await _send_chunk_safe(job, client, source, dest, pending)

            await _save_progress(job)
            await report()

            # مکث بین دسته‌ها (جلوگیری از برخورد با محدودیت تلگرام)
            if not job.cancel_requested and not job.attributed:
                # حالت فوروارد دسته‌ای: خودِ call دسته‌ای است، مکث کوتاه کافی است
                await asyncio.sleep(0.3)
            elif not job.cancel_requested:
                await job.pacer.wait()

        job.state = "cancelled" if job.cancel_requested else "done"

    except asyncio.CancelledError:
        job.state = "paused"
        raise
    except Exception as e:
        logger.error(f"Job {job.id} failed: {type(e).__name__}: {e}", exc_info=True)
        job.state = "error"
        job.error = f"{type(e).__name__}: {e}"

    finally:
        _active_jobs.pop(job.user_db_id, None)
        job.chunk_done.clear()

        # صف پاکسازی Recents را خالی کن
        if job.guard is not None:
            try:
                await job.guard.close()
            except Exception as e:
                logger.debug(f"recents guard close failed: {e}")

        # ذخیره نهایی وضعیت
        if job.db_id:
            try:
                await db.update_forward_job(
                    job.db_id,
                    last_msg_id=job.cursor, sent=job.sent, skipped=job.skipped,
                    failed=job.failed, copied=job.copied, total=job.total,
                )
                await db.finish_forward_job(
                    job.db_id,
                    "done" if job.state == "done" else
                    ("paused" if job.state in ("cancelled", "paused") else "error"),
                    job.error or None,
                )
            except Exception as e:
                logger.warning(f"Job {job.id} final save failed: {e}")

        await report(force=True)
        logger.info(
            f"Job {job.id} {job.state}: sent={job.sent} "
            f"skipped={job.skipped} failed={job.failed} cursor={job.cursor}"
        )


async def _fetch_total(client, source, job: ForwardJob):
    """تعداد کل پیام‌های مبدأ"""
    while True:
        try:
            return await client.get_messages(source, limit=0)
        except FloodWaitError as e:
            await _flood_sleep(job, e.seconds)
            if job.cancel_requested:
                raise


async def _send_chunk_safe(job: ForwardJob, client, source, dest, msgs):
    """
    ارسال یک دسته با مقاومت کامل در برابر FloodWait.
    پیام‌های ارسال‌شده ردیابی می‌شوند تا در تلاش مجدد تکراری نشوند.
    """
    done = set(job.chunk_done)

    while True:
        todo = [m for m in msgs if m.id not in done]
        if not todo or job.cancel_requested:
            break

        try:
            await _flush(job, client, source, dest, todo, done)
            break
        except FloodWaitError as e:
            await _flood_sleep(job, e.seconds)
            if job.cancel_requested:
                break

    # پایان دسته: پیام‌های تمام‌شده را پاک می‌کنیم
    for m in msgs:
        job.chunk_done.discard(m.id)


async def _guarded(fn, *args):
    """اجرای یک ارسال؛ خطا را به‌جای پرتاب، برمی‌گرداند (برای موج‌های همزمان)"""
    try:
        await fn(*args)
        return None
    except Exception as e:
        return e


async def _send_wave(job: ForwardJob, client, source, dest, wave, fn, done: set):
    """
    یک موج ارسال همزمان (به تعداد pacer.concurrency).

    ترتیب پیام‌ها حفظ می‌شود چون موج‌ها پشت سر هم و به ترتیب ساخته می‌شوند؛
    موفق‌ها بلافاصله در `done` ثبت می‌شوند تا در تلاش دوباره تکرار نشوند.
    """
    results = await asyncio.gather(*[_guarded(fn, job, client, source, dest, m)
                                     for m in wave])
    flood = None
    for m, res in zip(wave, results):
        if res is None:
            done.add(m.id)
            job.chunk_done.add(m.id)
        elif isinstance(res, FloodWaitError):
            flood = flood or res
        else:
            logger.warning(
                f"Job {job.id} msg {getattr(m, 'id', '?')} failed: "
                f"{type(res).__name__}: {res}"
            )
    if flood is not None:
        raise flood            # حلقه‌ی اصلی صبر می‌کند و بقیه ادامه می‌یابند


async def _flush(job: ForwardJob, client, source, dest, batch, done: set):
    """
    ارسال یک دسته پیام.
    FloodWaitError به بالا پرتاب می‌شود تا حلقه اصلی مدیریت کند.
    پیام‌های موفق در `done` ثبت می‌شوند (ضد ارسال تکراری).
    """
    if job.attributed:
        idx = 0
        while idx < len(batch) and not job.cancel_requested:
            wave = batch[idx: idx + max(1, job.pacer.concurrency)]
            idx += len(wave)
            await _send_wave(job, client, source, dest, wave,
                             _one_attributed, done)
            await _msg_pause(job, len(wave))
        return

    if job.mode == "forward":
        # استیکر/گیف نباید فوروارد واقعی شود (Recents)
        if recents_guard_on() and any(_protected(m) for m in batch):
            run = []
            for m in batch:
                if job.cancel_requested:
                    return
                if _protected(m):
                    if run:
                        await _forward_run(job, client, source, dest, run, done)
                        run = []
                    await _one(job, client, source, dest, m)
                    done.add(m.id)
                    job.chunk_done.add(m.id)
                    await _msg_pause(job)
                else:
                    run.append(m)
            if run:
                await _forward_run(job, client, source, dest, run, done)
            return

        if await _forward_run(job, client, source, dest, batch, done):
            return

        # شکست دسته‌ای (کانال محافظت‌شده) → تک تک
        for m in batch:
            if job.cancel_requested:
                return
            await _one(job, client, source, dest, m)
            done.add(m.id)
            job.chunk_done.add(m.id)
            await _msg_pause(job)
        return

    # حالت کپی بدون نام منبع
    idx = 0
    while idx < len(batch) and not job.cancel_requested:
        wave = batch[idx: idx + max(1, job.pacer.concurrency)]
        idx += len(wave)
        await _send_wave(
            job, client, source, dest, wave,
            lambda j, c, s_, d, m: _one(j, c, s_, d, m, force_copy=True),
            done,
        )
        await _msg_pause(job, len(wave))


async def _msg_pause(job: ForwardJob, n: int = 1):
    """مکث بعد از یک موج ارسال — با تنظیم خودکار سرعت (Pacer)"""
    job.pacer.success(n)
    await job.pacer.wait()
    return


MEDIA_KIND_TEXT = {
    "sticker": "استیکر",
    "gif": "گیف",
    "video": "ویدیو",
    "photo": "عکس",
    "document": "فایل",
    "other": "مدیا",
}


def media_kind_of(media) -> str:
    """نوع مدیا به فارسی (برای کش و نمایش)"""
    if media is None:
        return ""
    if is_sticker(media):
        return "استیکر"
    if is_animation(media):
        return "گیف"

    from telethon.tl import types as _t
    if isinstance(media, _t.MessageMediaPhoto):
        return "عکس"
    if isinstance(media, _t.MessageMediaDocument):
        doc = media.document
        if isinstance(doc, _t.Document):
            for a in (doc.attributes or []):
                if isinstance(a, _t.DocumentAttributeVideo):
                    return "ویدیو"
                if isinstance(a, _t.DocumentAttributeAudio):
                    return "ویس" if a.voice else "صدا"
            mime = (doc.mime_type or "")
            if mime.startswith("image/"):
                return "عکس"
            return "فایل"
        return "مدیا"
    if isinstance(media, _t.MessageMediaWebPage):
        return "لینک"
    return "مدیا"


def _protected(msg) -> bool:
    """پیامی که فوروارد واقعی‌اش Recents را آلوده می‌کند (استیکر/گیف)"""
    return bool(getattr(msg, "media", None)) and needs_recents_protection(msg.media)


async def _forward_run(job: ForwardJob, client, source, dest, batch, done: set = None) -> bool:
    """فوروارد واقعی یک دسته — True اگر موفق باشد. FloodWait بالا می‌رود."""
    try:
        await client.forward_messages(dest, batch, from_peer=source)
    except FloodWaitError:
        raise                      # حلقه اصلی مدیریت می‌کند
    except Exception as e:
        logger.info(f"Job {job.id} batch forward failed ({e}), per-message mode")
        return False

    job.sent += len(batch)
    if done is not None:
        for m in batch:
            done.add(m.id)
            job.chunk_done.add(m.id)
    await _clean_batch(job, client, batch)
    return True


# ═══════════════════════════════════
# کپی با مشخصات فرستنده
# ═══════════════════════════════════


async def _sender_info(job: ForwardJob, client, msg) -> tuple:
    """
    (نام فرستنده، برچسب شناسه) — با کش به‌ازای هر job
    اولویت شناسه: یوزرنیم → آیدی عددی → نام
    """
    sender_id = getattr(msg, "sender_id", None)

    cached = job.senders.get(sender_id)
    if cached:
        return cached

    name = None
    username = None

    try:
        sender = await msg.get_sender()
    except Exception:
        sender = None

    if sender is not None:
        if isinstance(sender, types.User):
            parts = [getattr(sender, "first_name", "") or "",
                     getattr(sender, "last_name", "") or ""]
            name = " ".join(p for p in parts if p).strip() or None
            username = getattr(sender, "username", None)
        else:
            name = getattr(sender, "title", None)
            username = getattr(sender, "username", None)

    # برچسب شناسه: یوزرنیم اگر بود، وگرنه آیدی عددی
    if username:
        id_label = f"@{username}"
        if sender_id:
            id_label += f" ({sender_id})"
    elif sender_id:
        id_label = str(sender_id)
    else:
        id_label = ""

    result = (name or "نامشخص", id_label)

    if sender_id:
        job.senders[sender_id] = result
    return result


async def attribution_header(job: ForwardJob, client, msg) -> str:
    """
    هدر مشخصات پیام: از کدام چت، چه کسی، چه زمانی، لینک پیام
    """
    name, id_label = await _sender_info(job, client, msg)

    chat_line = f"📥 از: {job.source_name}"
    if job.chat_label:
        chat_line += f" ({job.chat_label})"

    sender_line = f"👤 فرستنده: {name}"
    if id_label:
        sender_line += f" — {id_label}"

    date = getattr(msg, "date", None)
    date_line = (
        f"🕒 {date.strftime('%Y-%m-%d %H:%M')} (UTC)" if date else ""
    )

    link = msg_link_cache.get((job.id, msg.id))
    link_line = f"🔗 {link}" if link else f"🆔 پیام: {msg.id}"

    lines = [chat_line, sender_line]
    if date_line:
        lines.append(date_line)
    lines.append(link_line)
    lines.append(HEADER_RULE.rstrip())
    return "\n".join(lines) + "\n"


# لینک پیام‌ها (یک‌بار برای هر پیام محاسبه می‌شود)
msg_link_cache: dict[tuple, str] = {}


async def _one_attributed(job: ForwardJob, client, source, dest, msg):
    """کپی پیام همراه با هدر مشخصات فرستنده (بدون نام منبع تلگرام)"""
    try:
        # لینک پیام را یک‌بار محاسبه و کش کن
        key = (job.id, msg.id)
        if key not in msg_link_cache:
            link = build_message_link(source, msg.id)
            msg_link_cache[key] = link or ""
        else:
            link = msg_link_cache[key]

        header = await attribution_header(job, client, msg)
        text = msg.text or ""
        media = getattr(msg, "media", None)

        if media is None:
            await client.send_message(dest, clip(header + text, TEXT_LIMIT))
            job.sent += 1
            job.copied += 1
            return

        if isinstance(media, (types.MessageMediaPhoto,
                              types.MessageMediaDocument)):
            body = f"{header}\n{text}" if text else header
            await send_media_clean(
                client, dest, media, caption=clip(body, CAPTION_LIMIT),
                guard=getattr(job, "guard", None),
            )
            job.sent += 1
            job.copied += 1
            return

        if isinstance(media, types.MessageMediaWebPage):
            body = header + (f"\n{text}" if text else "")
            await client.send_message(dest, clip(body, TEXT_LIMIT))
            job.sent += 1
            job.copied += 1
            return

        # نظرسنجی / لوکیشن / کانتکت / تاس / بازی → هدر جدا + کپی محتوا
        await client.send_message(dest, header)
        try:
            await client.forward_messages(dest, [msg], from_peer=source)
        except Exception:
            pass
        job.sent += 1
        job.copied += 1

    except FloodWaitError:
        raise                      # حلقه اصلی مدیریت می‌کند
    except Exception as e:
        logger.warning(
            f"Job {job.id} attributed msg {getattr(msg, 'id', '?')} "
            f"failed: {type(e).__name__}: {e}"
        )
        job.failed += 1


async def _clean_batch(job, client, batch):
    """
    پاکسازی Recents پس از فوروارد/ارسال استیکر و گیف.

    در همه‌ی حالت‌ها (جز off) لازم است: فوروارد واقعیِ استیکر و ارسالِ
    مرجع، هر دو سرور را وادار می‌کنند استیکر/گیف را به Recents اضافه کند
    (پارامتر force_document روی مدیای مرجع بی‌اثر است).
    """
    if (CLEAN_RECENTS_MODE or "document").lower() == "off":
        return

    guard = getattr(job, "guard", None)
    for msg in batch:
        try:
            if msg and needs_recents_protection(msg.media):
                if guard is not None:
                    guard.submit(msg.media)
                else:
                    await cleanup_recents(client, msg.media)
        except Exception as e:
            logger.debug(f"recents cleanup skipped: {e}")


async def _one(job: ForwardJob, client, source, dest, msg, force_copy: bool = False):
    if not force_copy and not (recents_guard_on() and _protected(msg)):
        try:
            await client.forward_messages(dest, [msg], from_peer=source)
            job.sent += 1
            await _clean_batch(job, client, [msg])
            return
        except FloodWaitError:
            raise                  # حلقه اصلی مدیریت می‌کند
        except Exception:
            pass

    # کپی بدون نام منبع — روی کانال‌های محافظت‌شده هم کار می‌کند
    try:
        if msg.media:
            try:
                await send_media_clean(
                    client, dest, msg.media, caption=msg.text or "",
                    guard=getattr(job, "guard", None),
                )
                job.sent += 1
                job.copied += 1
                return
            except Exception:
                pass

        if msg.text:
            await client.send_message(dest, msg.text)
            job.sent += 1
            job.copied += 1
            return

        job.skipped += 1

    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        job.failed += 1
    except Exception as e:
        logger.warning(
            f"Job {job.id} msg {getattr(msg, 'id', '?')} failed: "
            f"{type(e).__name__}: {e}"
        )
        job.failed += 1


# ═══════════════════════════════════
# ادامه job (پس از توقف یا ری‌استارت سرور)
# ═══════════════════════════════════


async def resume_job(
    job_row: dict,
    on_progress=None,
    *,
    user_db_id: int | None = None,
) -> ForwardJob:
    """
    ادامه یک job ذخیره‌شده از همان پیامی که متوقف شده بود.
    روی‌هم‌رفتن پیام‌ها تکرار نمی‌شود (cursor).
    """
    uid = int(user_db_id or job_row["user_id"])

    if uid in _active_jobs:
        raise ValueError("job_running")

    client = await get_client(uid)
    if not client:
        raise ValueError("account_not_connected")

    src_ref = {
        "kind": job_row.get("src_kind"),
        "id": job_row.get("src_id"),
        "hash": job_row.get("src_hash"),
        "name": job_row.get("src_name") or "",
    }
    dst_ref = {
        "kind": job_row.get("dst_kind"),
        "id": job_row.get("dst_id"),
        "hash": job_row.get("dst_hash"),
        "name": job_row.get("dst_name") or "",
    }

    source = await resolve_peer_ref(client, src_ref)
    dest = await resolve_peer_ref(client, dst_ref)

    job = await start_job(
        user_db_id=uid,
        source=source,
        dest=dest,
        source_name=src_ref["name"],
        dest_name=dst_ref["name"],
        mode=job_row.get("mode") or "forward",
        limit=int(job_row.get("limit_count") or 0),
        media_only=bool(job_row.get("media_only")),
        attributed=bool(job_row.get("attributed")),
        chat_id=job_row.get("chat_id"),
        message_id=job_row.get("message_id"),
        on_progress=on_progress,
        db_id=job_row["id"],
        cursor=int(job_row.get("last_msg_id") or 0),
        counters={
            "sent": job_row.get("sent"),
            "skipped": job_row.get("skipped"),
            "failed": job_row.get("failed"),
            "copied": job_row.get("copied"),
            "total": job_row.get("total"),
        },
        resumed=True,
        speed=job_row.get("speed"),
    )

    logger.info(
        f"Job {job.id} resumed from cursor {job.cursor} "
        f"(sent so far: {job.sent})"
    )
    return job


GHOST_AGE_SECONDS = 120        # بعد از این مدت، job بی‌صاحب قطعاً مرده است


def job_is_alive(job_id: int) -> bool:
    """آیا همین حالا موتوری این job را اجرا می‌کند؟"""
    from core import cache_forward
    return bool(get_job(job_id) or cache_forward.get_job(job_id))


def is_ghost_job(row) -> bool:
    """
    ردیفِ «در حال اجرا» که هیچ موتوری اجرایش نمی‌کند.
    همان حالتی که کاربر می‌گوید «فوروارد فعالی ندارم ولی می‌گوید دارم».
    """
    try:
        return (row.get("status") == "running") and not job_is_alive(int(row["id"]))
    except Exception:
        return False


def job_is_stale(row, seconds: int = GHOST_AGE_SECONDS) -> bool:
    """آیا از آخرین به‌روزرسانی این ردیف زمان زیادی گذشته؟ (فرصت ادامه‌ی خودکار تمام شده)"""
    updated = row.get("updated_at")
    if not updated:
        return True
    now = datetime.now(updated.tzinfo) if updated.tzinfo else datetime.now()
    return (now - updated).total_seconds() > seconds


async def free_job_cache(job_id: int) -> None:
    """آزادسازی ردیف‌ها و فایل‌های کش یک job (وقتی دیگر لازم نیست)"""
    try:
        files = await db.clear_job_cache(job_id)
    except Exception as e:
        logger.debug(f"clear_job_cache({job_id}) skipped: {e}")
        return
    try:
        from core import cache_forward
        cache_forward._delete_files(files, job_id)
    except Exception as e:
        logger.debug(f"delete cache files of job {job_id} skipped: {e}")


async def prepare_job_start(user_id: int,
                            note: str = "آزادسازی خودکار job گیرکرده (اجرای آن مرده بود)") -> dict | None:
    """
    قبل از شروع job جدید:
      • ردیفِ گیرکرده‌ی مرده را خودکار آزاد می‌کند (خودترمیمی)
      • خروجی: ردیفی که واقعاً جلوی کار را گرفته (کاربر باید تصمیم بگیرد)
        یا None اگر راه باز است
    """
    row = await db.get_blocking_forward_job(user_id)
    if not row:
        return None

    if is_ghost_job(row) and job_is_stale(row):
        # وضعیت «خطا» → قفل باز می‌شود ولی کاربر می‌تواند بعداً ادامه‌اش دهد
        await db.release_unfinished_jobs(user_id, note=note, status="error")
        logger.info(f"Ghost forward job {row.get('id')} auto-released (user={user_id})")
        return None

    return row


async def close_orphan_jobs() -> int:
    """
    بستن ردیف‌های «در حال اجرا» که هیچ موتوری اجرا نمی‌کند
    (اجرا وسط راه مرده / ری‌استارت شده و ادامه پیدا نکرده).
    بدون این کار، کاربرها پیام «شما فوروارد فعال دارید» می‌گیرند
    در حالی که هیچ فورواردی فعال نیست.
    """
    try:
        rows = await db.get_resumable_jobs()
    except Exception as e:
        logger.debug(f"close_orphan_jobs: DB not ready ({e})")
        return 0

    from core import cache_forward

    closed = 0
    for row in rows:
        jid = int(row["id"])
        if get_job(jid) or cache_forward.get_job(jid):
            continue                     # واقعاً در حال اجراست
        if row["status"] != "running":
            continue                     # متوقف‌شده‌ی کاربر را دست نمی‌زنیم
        try:
            client = await get_client(row["user_id"])
        except Exception:
            client = None
        if not client:
            continue                     # شاید فقط اکانت وصل نشده

        await db.finish_forward_job(
            jid, "error",
            "اجرای فوروارد قطع شد (ری‌استارت سرور) — از پنل ادامه دهید یا ببندید",
        )
        closed += 1
        logger.info(f"Orphan forward job {jid} released (was running, no engine)")

    if closed:
        logger.info(f"Released {closed} orphan forward job(s)")
    return closed


async def resume_pending_jobs(on_progress_factory=None, delay: float = 5.0):
    """
    ادامه خودکار job های نیمه‌کاره — هنگام بالا آمدن سرور.
    با فاصله اجرا می‌شوند تا همه کلاینت‌ها همزمان فشار نیاورند.
    """
    try:
        rows = await db.get_resumable_jobs()
    except Exception as e:
        logger.warning(f"resume_pending_jobs: DB not ready ({e})")
        return 0

    resumed = 0
    for row in rows:
        uid = row["user_id"]
        try:
            client = await get_client(uid)
            if not client:
                continue

            on_progress = None
            if on_progress_factory:
                on_progress = on_progress_factory(uid, row)

            # job دو مرحله‌ای (کش روی سرور) موتور جداگانه دارد
            if row.get("cache_mode"):
                from core import cache_forward
                await cache_forward.resume_job(row, on_progress=on_progress,
                                               user_db_id=uid)
            else:
                await resume_job(row, on_progress=on_progress, user_db_id=uid)

            resumed += 1
            await asyncio.sleep(delay)
        except ValueError as e:
            if str(e) != "job_running":
                logger.info(f"Job {row['id']} not resumed: {e}")
                # ادامه ممکن نشد → قفلِ شروع‌های بعدی نماند
                try:
                    await db.finish_forward_job(row["id"], "error", str(e)[:200])
                except Exception as err:
                    logger.debug(f"mark not-resumed job error skipped: {err}")
        except Exception as e:
            logger.warning(f"Job {row['id']} resume failed: {e}")
            try:
                await db.finish_forward_job(row["id"], "error", str(e)[:200])
            except Exception as err:
                logger.debug(f"mark failed-resume job error skipped: {err}")

    if resumed:
        logger.info(f"Resumed {resumed} forward job(s)")

    # هر ردیفی که «در حال اجرا» مانده ولی موتوری ندارد → آزاد شود
    try:
        await close_orphan_jobs()
    except Exception as e:
        logger.debug(f"orphan sweep skipped: {e}")

    return resumed


def pending_job_summary(row: dict) -> str:
    """خلاصه یک job ذخیره‌شده برای نمایش در ربات"""
    done = int(row.get("sent") or 0)
    total = int(row.get("total") or 0)
    status = {
        "running": "⏳ در حال اجرا",
        "paused": "⏸ متوقف",
        "error": "❌ خطا",
        "done": "✅ تمام‌شده",
    }.get(row.get("status"), row.get("status") or "")

    progress = f"{done:,} از {total:,}" if total else f"{done:,}"
    return (
        f"🆔 <code>{row['id']}</code> | {status}\n"
        f"📤 {html.escape(str(row.get('src_name') or ''))} → "
        f"📥 {html.escape(str(row.get('dst_name') or ''))}\n"
        f"📨 ارسال‌شده: {progress}"
    )
