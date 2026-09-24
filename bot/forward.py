"""
پنل فوروارد محتوا در ربات

مراحل:
  ۱. انتخاب چت مبدأ از لیست دیالوگ‌های اکانت (چت‌های بدون لینک هم هستند)
  ۲. انتخاب چت مقصد (از همان لیست یا Saved Messages یا ورود دستی)
  ۳. تنظیمات و شروع

هیچ پیامی به چت مبدأ ارسال نمی‌شود؛ گزارش فقط در چت ربات نمایش داده می‌شود.
"""

import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.texts import t
from bot.keyboards import (
    fwd_blocked_kb,
    fwd_dialogs_kb,
    fwd_join_kb,
    fwd_confirm_kb,
    fwd_running_kb,
    fwd_done_kb,
    fwd_delete_confirm_kb,
    back_kb,
)
from config import (
    FORWARD_CACHE_CAPTURE_MEDIA,
    FORWARD_CACHE_MODE,
    FORWARD_SPEED,
)
from core import cache_forward
from core import forwarder
from core.pacing import describe, label, legacy_warning, next_speed
from core.client_manager import get_client
from core.security import check_rate_limit
from database import db
from bot.subscription import check_subscription

logger = logging.getLogger("bot.forward")

PER_PAGE = 8
LIMIT_CYCLE = [0, 500, 5000, 20000, 100000]   # 0 = همه (پیش‌فرض)
MODE_CYCLE = ["attributed", "forward", "copy"]
MODE_TEXT = {
    "forward": "↪️ فوروارد با نام منبع",
    "attributed": "🏷 کپی با مشخصات فرستنده",
    "copy": "🔁 کپی بدون نام منبع",
}
SAMPLE_HEADER = (
    "📥 از: گروه خانواده (-1001234567890)\n"
    "👤 فرستنده: علی رضایی — @ali (123456789)\n"
    "🕒 2026-09-22 14:22 (UTC)\n"
    "🔗 https://t.me/c/1234567890/55\n"
    "──────────────"
)


# ═══════════════════════════════════
# State
# ═══════════════════════════════════


def _state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    st = context.user_data.get("fwd")
    if not st:
        st = {
            "src_page": 0,
            "dst_page": 0,
            "chats_only": True,      # پیش‌فرض: فقط گروه/کانال
            # اول روی سرور ذخیره، بعد ارسال (مصون از قطع دسترسی)
            "cache": FORWARD_CACHE_MODE,
            "cache_media": FORWARD_CACHE_CAPTURE_MEDIA,   # دانلود مدیا (فضای دیسک)
            "speed": FORWARD_SPEED,       # سرعت ارسال (تنظیم خودکار)
            "query": "",
            "limit": 0,          # پیش‌فرض: همه پیام‌ها
            # forward = فوروارد با نام منبع | attributed = کپی با مشخصات
            # فرستنده (پیش‌فرض) | copy = کپی بدون نام منبع
            "mode": "attributed",
            "media_only": False,
            "src": None,             # {"name":..., "entity":...}
            "dst": None,
            "chat_id": None,         # پیام لیست برای ویرایش‌های بعدی
            "msg_id": None,
        }
        context.user_data["fwd"] = st
    return st


async def _ready(user: dict) -> str | None:
    """
    آماده بودن کاربر برای فوروارد.
    خروجی: None = آماده | متن خطا
    """
    if not await check_subscription(user):
        return "❌ اشتراک فعال ندارید"
    if not await get_client(user["id"]):
        return "❌ اکانت متصل نیست"
    return None


# ═══════════════════════════════════
# رفع گرفتگی فوروارد
# ═══════════════════════════════════

async def _prepare_start(user) -> dict | None:
    """ردیف گیرکرده را (اگر مرده باشد) آزاد می‌کند؛ وگرنه برمی‌گرداند"""
    return await forwarder.prepare_job_start(user["id"])


async def _free_cache_files(rows) -> None:
    """پاک کردن فایل‌های کش job هایی که کاربر بست"""
    for row in rows or []:
        await forwarder.free_job_cache(row["id"])


async def _show_blocked(context, user, st, chat_id, msg_id, row) -> None:
    """پیام «فوروارد نیمه‌کاره جلوی کار را گرفته» + راه رفع"""
    sent = int(row.get("sent") or 0)
    total = int(row.get("total") or 0)
    progress = f"{sent:,} از {total:,}" if total else f"{sent:,}"
    status = {
        "running": "⏳ در حال اجرا (ولی هیچ فورواردی فعال نیست — گیر کرده)",
        "paused": "⏸ متوقف‌شده",
    }.get(row.get("status"), row.get("status") or "")

    text = (
        "⚠️ <b>یک فوروارد نیمه‌کاره جلوی شروع فوروارد جدید را گرفته است</b>\n\n"
        f"🆔 شناسه: <code>{row['id']}</code>\n"
        f"📤 {html.escape(str(row.get('src_name') or '—'))} → "
        f"📥 {html.escape(str(row.get('dst_name') or '—'))}\n"
        f"📨 پیشرفت: {progress}\n"
        f"وضعیت: {status}\n\n"
        "یکی را انتخاب کنید:\n"
        "▶️ <b>ادامه‌ی همان فوروارد</b> — از همان‌جا که مانده ادامه می‌دهد\n"
        "🔓 <b>بستن آن و شروع فوروارد جدید</b> — این ردیف آزاد می‌شود و "
        "فوروارد جدیدت همین حالا شروع می‌شود"
    )

    st["chat_id"], st["msg_id"] = chat_id, msg_id
    await context.bot.send_message(
        chat_id, text, reply_markup=fwd_blocked_kb(), parse_mode="HTML",
    )


# ═══════════════════════════════════
# رندر صفحه‌ها
# ═══════════════════════════════════


async def _render_dialogs(context, user, target: str, force: bool = False):
    """لیست چت‌ها برای انتخاب مبدأ/مقصد"""
    st = _state(context)

    try:
        items = await forwarder.load_dialogs(user["id"], force=force)
    except ValueError as e:
        msg = (
            "❌ اکانت متصل نیست."
            if str(e) == "account_not_connected"
            else "❌ خواندن لیست چت‌ها ناموفق بود. دوباره تلاش کنید."
        )
        await context.bot.edit_message_text(
            chat_id=st["chat_id"], message_id=st["msg_id"],
            text=msg, reply_markup=back_kb("back_main"),
        )
        return

    if not items:
        await context.bot.edit_message_text(
            chat_id=st["chat_id"], message_id=st["msg_id"],
            text=t("fwd_no_dialogs"), reply_markup=back_kb("back_main"),
        )
        return

    filtered = forwarder.filter_dialogs(
        items, query=st["query"], chats_only=st["chats_only"]
    )

    pages = max(1, (len(filtered) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(st[f"{target}_page"], pages - 1))
    st[f"{target}_page"] = page

    key = "fwd_pick_source" if target == "src" else "fwd_pick_dest"
    kwargs = dict(
        total=len(items),
        found=len(filtered),
        page=page + 1,
        pages=pages,
        filter=t("fwd_filter_on" if st["chats_only"] else "fwd_filter_off"),
        query=html.escape(st["query"]) or "—",
    )
    if target == "dst":
        kwargs["src"] = html.escape(str((st.get("src") or {}).get("name", "—")))

    await context.bot.edit_message_text(
        chat_id=st["chat_id"],
        message_id=st["msg_id"],
        text=t(key, **kwargs),
        reply_markup=fwd_dialogs_kb(
            filtered[page * PER_PAGE: (page + 1) * PER_PAGE],
            page, pages, target,
            chats_only=st["chats_only"],
            has_query=bool(st["query"]),
        ),
        parse_mode="HTML",
    )


async def _render_confirm(context, user, *, src=None, dst=None):
    """صفحه تایید و تنظیمات"""
    st = _state(context)
    if src is not None:
        st["src"] = src
    if dst is not None:
        st["dst"] = dst

    if not st.get("src") or not st.get("dst"):
        return

    limit_txt = "همه پیام‌ها" if st["limit"] == 0 else f"آخرین {st['limit']} پیام"
    mode_txt = MODE_TEXT.get(st["mode"], st["mode"])

    if st.get("cache"):
        cache_txt = (
            "🗄 فعال — اول همه پیام‌ها روی سرور ذخیره می‌شوند، "
            "بعد ارسال شروع می‌شود. اگر وسط کار دسترسی به مبدأ قطع شود، "
            "ارسال ادامه پیدا می‌کند."
            + ("\n💾 کش مدیا: فعال (فضای دیسک مصرف می‌شود)"
               if st.get("cache_media")
               else "\n📄 کش مدیا: غیرفعال — مدیا با مرجع تلگرام فرستاده می‌شود")
        )
    else:
        cache_txt = "⚡ غیرفعال — فوروارد زنده (سریع‌تر، ولی اگر دسترسی قطع شود متوقف می‌شود)"

    speed_txt = f"{label(st.get('speed'))} — {describe(st.get('speed'))}"
    warn = legacy_warning()
    if warn:
        speed_txt += f"\n{warn}"

    await context.bot.edit_message_text(
        chat_id=st["chat_id"],
        message_id=st["msg_id"],
        text=t(
            "fwd_confirm",
            src=html.escape(st["src"]["name"]),
            dst=html.escape(st["dst"]["name"]),
            limit=limit_txt,
            mode=mode_txt,
            media="بله" if st["media_only"] else "خیر",
            sample=SAMPLE_HEADER if st["mode"] == "attributed" else "—",
            cache=cache_txt,
            speed=speed_txt,
        ),
        reply_markup=fwd_confirm_kb(
            st["limit"], st["mode"], st["media_only"],
            cache=st.get("cache", True),
            cache_media=st.get("cache_media", False),
            speed=st.get("speed") or FORWARD_SPEED,
        ),
        parse_mode="HTML",
    )


# ═══════════════════════════════════
# Handlerها
# ═══════════════════════════════════


async def cb_fwd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    err = await _ready(user)
    if err:
        await query.answer(err, show_alert=True)
        return
    await query.answer()

    st = _state(context)
    st.update({
        "src": None, "dst": None, "src_page": 0,
        "dst_page": 0, "query": "", "chat_id": None, "msg_id": None,
    })

    await query.edit_message_text(t("fwd_loading"))
    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id
    await _render_dialogs(context, user, "src")


async def cb_fwd_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رفتن به صفحه انتخاب مبدأ یا مقصد"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    target = "dst" if query.data.endswith("dst") else "src"

    # هر مسیر فقط یک‌بار answer می‌شود
    err = await _ready(user)
    if err:
        await query.answer(err, show_alert=True)
        return
    if target == "dst" and not st.get("src"):
        await query.answer("اول چت مبدأ را انتخاب کنید", show_alert=True)
        return
    await query.answer()

    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id
    await _render_dialogs(context, user, target)


async def cb_fwd_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    target = "dst" if query.data.startswith("fwd_dst_") else "src"
    st[f"{target}_page"] = int(query.data.rsplit("_p", 1)[1])
    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id

    await _render_dialogs(context, user, target)


async def cb_fwd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتخاب یک چت از لیست"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    target = "dst" if query.data.startswith("fwd_dst_") else "src"
    idx = int(query.data.rsplit("_i", 1)[1])

    item = forwarder.get_dialog(user["id"], idx)
    if not item:
        await query.answer(
            "لیست قدیمی شده؛ دوباره باز کنید", show_alert=True
        )
        return
    await query.answer()

    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id
    picked = {"name": item["name"], "entity": item["entity"]}

    if target == "src":
        # بعد از انتخاب مبدأ → لیست مقصد
        st["src"] = picked
        st["dst"] = None
        st["dst_page"] = 0
        await _render_dialogs(context, user, "dst")
    else:
        await _render_confirm(context, user, dst=picked)


async def cb_fwd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فیلتر: همه چت‌ها ↔ فقط کانال و گروه"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    st["chats_only"] = not st["chats_only"]
    st["src_page"] = 0
    st["dst_page"] = 0
    await query.answer(
        "🔍 فقط کانال و گروه" if st["chats_only"] else "🗂 همه چت‌ها"
    )

    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id
    target = "dst" if st.get("src") and not st.get("dst") else "src"
    await _render_dialogs(context, user, target)


async def cb_fwd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست نام چت برای جستجو"""
    query = update.callback_query
    await query.answer()

    st = _state(context)
    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id
    context.user_data["awaiting_fwd_search"] = True

    await query.edit_message_text(
        t("fwd_search_prompt"), reply_markup=back_kb("fwd_start")
    )


async def cb_fwd_clear_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    st["query"] = ""
    st["src_page"] = 0
    st["dst_page"] = 0
    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id

    target = "dst" if st.get("src") and not st.get("dst") else "src"
    await _render_dialogs(context, user, target)


async def cb_fwd_dst_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مقصد = Saved Messages"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    if not st.get("src"):
        await query.answer("اول چت مبدأ را انتخاب کنید", show_alert=True)
        return
    await query.answer()

    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id
    await _render_confirm(
        context, user,
        dst={"name": "💾 Saved Messages", "entity": "me"},
    )


async def cb_fwd_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ورود دستی لینک/یوزرنیم/آیدی/لینک دعوت
    (برای مبدأ و مقصد — callback: fwd_src_manual / fwd_dst_manual)
    """
    query = update.callback_query
    await query.answer()

    st = _state(context)
    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id

    target = "src" if "src_manual" in query.data else "dst"
    if target == "dst" and not st.get("src"):
        await query.answer("اول چت مبدأ را انتخاب کنید", show_alert=True)
        return

    context.user_data["awaiting_fwd_manual"] = target

    await query.edit_message_text(
        t("fwd_src_manual_note") if target == "src" else t("fwd_manual_prompt"),
        reply_markup=back_kb("fwd_start"),
        parse_mode="HTML" if target == "src" else None,
    )


async def cb_fwd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عضویت در گروه خصوصی با تایید صریح کاربر"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    invite_hash = query.data.replace("fwd_join_", "")
    client = await get_client(user["id"])
    if not client:
        await query.answer("❌ اکانت متصل نیست", show_alert=True)
        return

    await query.answer()
    pending = context.user_data.get("fwd_pending_join") or {}
    target = pending.get("target", "dst")
    st = _state(context)

    try:
        entity = await forwarder.join_invite(client, invite_hash)
    except Exception as e:
        logger.warning(f"Join invite failed: {type(e).__name__}: {e}")
        await query.edit_message_text(
            t("fwd_join_failed"), reply_markup=back_kb("fwd_start")
        )
        return

    if not entity:
        await query.edit_message_text(
            t("fwd_join_failed"), reply_markup=back_kb("fwd_start")
        )
        return

    name = getattr(entity, "title", None) or str(getattr(entity, "id", ""))
    await db.audit_log(
        user["id"], "invite_join", f"{invite_hash} -> {name}"
    )

    picked = {"name": name, "entity": entity}

    if target == "src":
        st["src"] = picked
        st["dst"] = None
        await query.edit_message_text(
            t("fwd_join_done", title=html.escape(str(name)))
            + "\n\n" + t("fwd_pick_dest", src=html.escape(str(name)),
                            total=0, found=0, page=1, pages=1,
                            filter=t("fwd_filter_on"), query="—"),
            parse_mode="HTML",
        )
        st["chat_id"] = query.message.chat_id
        st["msg_id"] = query.message.message_id
        await _render_dialogs(context, user, "dst", force=True)
    else:
        st["chat_id"] = query.message.chat_id
        st["msg_id"] = query.message.message_id
        await _render_confirm(context, user, dst=picked)


async def cb_fwd_cancel_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(t("fwd_join_cancelled"))
    context.user_data.pop("fwd_pending_join", None)
    if context.user_data.get("fwd"):
        context.user_data["fwd"]["chat_id"] = query.message.chat_id
        context.user_data["fwd"]["msg_id"] = query.message.message_id
    await query.edit_message_text(
        t("fwd_join_cancelled"), reply_markup=back_kb("fwd_start")
    )


async def cb_fwd_opt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظیمات: تعداد پیام / روش ارسال / فقط مدیا"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    st["chat_id"] = query.message.chat_id
    st["msg_id"] = query.message.message_id

    if query.data == "fwd_limit":
        try:
            i = LIMIT_CYCLE.index(st["limit"])
        except ValueError:
            i = -1
        st["limit"] = LIMIT_CYCLE[(i + 1) % len(LIMIT_CYCLE)]
        await query.answer(
            "همه پیام‌ها" if st["limit"] == 0 else f"آخرین {st['limit']} پیام"
        )
    elif query.data == "fwd_mode":
        try:
            i = MODE_CYCLE.index(st["mode"])
        except ValueError:
            i = -1
        st["mode"] = MODE_CYCLE[(i + 1) % len(MODE_CYCLE)]
        await query.answer(MODE_TEXT[st["mode"]])
    elif query.data == "fwd_media":
        st["media_only"] = not st["media_only"]
        await query.answer("فقط مدیا: بله" if st["media_only"] else "همه پیام‌ها")
    elif query.data == "fwd_cache":
        st["cache"] = not st.get("cache", True)
        await query.answer(
            "🗄 ذخیره روی سرور: فعال" if st["cache"] else "⚡ فوروارد زنده"
        )
    elif query.data == "fwd_speed":
        st["speed"] = next_speed(st.get("speed"))
        await query.answer(
            f"⚡ سرعت: {label(st['speed'])}\n{describe(st['speed'])}",
        )
    elif query.data == "fwd_cachemedia":
        st["cache_media"] = not st.get("cache_media", False)
        await query.answer(
            "🖼 کش مدیا: فعال (فضای دیسک)" if st["cache_media"]
            else "📄 کش مدیا: غیرفعال"
        )

    await _render_confirm(context, user)


async def cb_fwd_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فوروارد"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    src, dst = st.get("src"), st.get("dst")
    if not src or not dst:
        await query.answer("مبدأ و مقصد را انتخاب کنید", show_alert=True)
        return

    if not check_rate_limit(user["telegram_id"], "forward", 6, 600):
        await query.answer("🚫 درخواست زیاد. کمی بعد تلاش کنید.", show_alert=True)
        return

    if (forwarder.get_active_job(user["id"])
            or cache_forward.get_active_job(user["id"])):
        await query.answer("⏳ یک فوروارد در حال اجراست", show_alert=True)
        return

    await query.answer()

    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    # ردیف گیرکرده: یا خودکار آزاد می‌شود یا راه رفع نشان داده می‌شود
    blocking = await _prepare_start(user)
    if blocking:
        await _show_blocked(context, user, st, chat_id, msg_id, blocking)
        return

    await _launch_forward(context, user, st, chat_id, msg_id)


async def _launch_forward(context, user, st, chat_id, msg_id):
    """شروع واقعی فوروارد (موتور کشی یا زنده) — نقطه‌ی واحد شروع"""
    src, dst = st["src"], st["dst"]

    async def on_progress(job):
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=forwarder.job_summary(job),
                reply_markup=(
                    fwd_running_kb(job)
                    if job.state in ("running", "waiting")
                    else fwd_done_kb(paused=job.state != "done")
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            # «message is not modified» و قطعی‌های گذرا مهم نیستند
            logger.debug(f"progress edit skipped: {e}")

    # حالت کش موتور خودش ردیف و کش را می‌سازد (اینجا ردیف نساز → قفل الکی)
    if st.get("cache", True):
        await _start_cached_job(context, user, st, chat_id, msg_id)
        return

    # موتور زنده: ثبت job در دیتابیس → پس از ری‌استارت سرور هم ادامه می‌یابد
    src_ref = forwarder.peer_ref(src["entity"])
    dst_ref = forwarder.peer_ref(dst["entity"])
    src_ref["name"] = src["name"]
    dst_ref["name"] = dst["name"]

    try:
        row = await db.create_forward_job(
            user_id=user["id"],
            src=src_ref,
            dst=dst_ref,
            mode=st["mode"],
            attributed=(st["mode"] == "attributed"),
            media_only=st["media_only"],
            limit_count=st["limit"],
            chat_id=chat_id,
            message_id=msg_id,
            speed=st.get("speed") or FORWARD_SPEED,
        )
    except Exception as e:
        logger.error(f"create_forward_job failed: {e}")
        row = None

    if not row:
        blocking = await db.get_blocking_forward_job(user["id"])
        if blocking:
            await _show_blocked(context, user, st, chat_id, msg_id, blocking)
        else:
            await context.bot.send_message(
                chat_id, "⏳ یک فوروارد در جریان است. کمی بعد دوباره تلاش کنید."
            )
        return

    try:
        job = await forwarder.start_job(
            user_db_id=user["id"],
            source=src["entity"],
            dest=dst["entity"],
            source_name=src["name"],
            dest_name=dst["name"],
            mode=st["mode"],
            limit=st["limit"],
            media_only=st["media_only"],
            attributed=(st["mode"] == "attributed"),
            chat_id=chat_id,
            message_id=msg_id,
            on_progress=on_progress,
            db_id=row["id"],
            speed=st.get("speed") or FORWARD_SPEED,
        )
    except ValueError as e:
        # query قبلاً answer شده؛ دوباره answer نمی‌زنیم
        msg = {
            "job_running": "⏳ یک فوروارد دیگر همین حالا در حال اجراست.",
            "account_not_connected": "❌ اکانت متصل نیست.",
        }.get(str(e), "❌ شروع فوروارد ناموفق بود.")

        # ردیفِ بی‌صاحب نماند (وگرنه «شما فوروارد فعال دارید» گیر می‌دهد)
        try:
            await db.finish_forward_job(row["id"], "error", str(e)[:200])
        except Exception as err:
            logger.debug(f"mark failed job error skipped: {err}")

        await context.bot.send_message(chat_id, f"{msg}\nدوباره تلاش کنید.")
        return

    await db.audit_log(
        user["id"], "forward_start",
        f"job={job.id} {src['name']} -> {dst['name']} "
        f"mode={st['mode']} limit={st['limit']}",
    )

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id,
        text=forwarder.job_summary(job),
        reply_markup=fwd_running_kb(job),
        parse_mode="HTML",
    )


async def _start_cached_job(context, user, st, chat_id, msg_id):
    """شروع job دو مرحله‌ای: جمع‌آوری روی سرور، سپس ارسال"""
    src, dst = st["src"], st["dst"]

    src_ref = forwarder.peer_ref(src["entity"])
    dst_ref = forwarder.peer_ref(dst["entity"])
    src_ref["name"] = src["name"]
    dst_ref["name"] = dst["name"]

    row = None
    try:
        row = await db.create_forward_job(
            user_id=user["id"],
            src=src_ref,
            dst=dst_ref,
            mode=st["mode"],
            attributed=(st["mode"] == "attributed"),
            media_only=st["media_only"],
            limit_count=st["limit"],
            chat_id=chat_id,
            message_id=msg_id,
            cache_mode=True,
            capture_media=st.get("cache_media", False),
            speed=st.get("speed") or FORWARD_SPEED,
        )
    except Exception as e:
        logger.error(f"create_forward_job failed: {e}")

    if not row:
        blocking = await db.get_blocking_forward_job(user["id"])
        if blocking:
            await _show_blocked(context, user, st, chat_id, msg_id, blocking)
        else:
            await context.bot.send_message(
                chat_id, "⏳ یک فوروارد در جریان است. کمی بعد دوباره تلاش کنید."
            )
        return

    # متن‌های کشی همیشه «کپی» هستند (پیام از سرور ارسال می‌شود)
    if st["mode"] == "forward":
        st["mode"] = "copy"

    # ردیف تازه از دیتابیس (فاز معتبر)
    row = await db.get_forward_job(row["id"]) or row

    async def on_progress(job):
        try:
            stats = None
            if job.phase == "send":
                stats = await db.cache_stats(job.db_id)
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=cache_forward.cache_job_summary(job, stats),
                reply_markup=(
                    fwd_running_kb(job)
                    if job.state in ("running", "waiting")
                    else fwd_done_kb(paused=job.state != "done")
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug(f"cache progress edit skipped: {e}")

    try:
        job = await cache_forward.start_job(
            user_db_id=user["id"],
            source=src["entity"],
            dest=dst["entity"],
            source_name=src["name"],
            dest_name=dst["name"],
            mode="copy",
            attributed=(st["mode"] == "attributed"),
            media_only=st["media_only"],
            limit=st["limit"],
            capture_media=st.get("cache_media", False),
            speed=st.get("speed") or FORWARD_SPEED,
            chat_id=chat_id,
            message_id=msg_id,
            on_progress=on_progress,
            resume_row=row,
        )
    except ValueError as e:
        try:
            await db.finish_forward_job(row["id"], "error", str(e)[:200])
        except Exception as err:
            logger.debug(f"mark failed cache job error skipped: {err}")
        await context.bot.send_message(
            chat_id,
            {
                "job_running": "⏳ یک فوروارد دیگر در حال اجراست.",
                "account_not_connected": "❌ اکانت متصل نیست.",
            }.get(str(e), "❌ شروع فوروارد ناموفق بود."),
        )
        return

    await db.audit_log(
        user["id"], "forward_start",
        f"job={job.id} CACHE {src['name']} -> {dst['name']} "
        f"media={st.get('cache_media', False)}",
    )

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id,
        text=cache_forward.cache_job_summary(job),
        reply_markup=fwd_running_kb(job),
        parse_mode="HTML",
    )


async def cb_fwd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🔓 بستن فوروارد گیرکرده و شروع فوروارد جدید"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    st = _state(context)
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    try:
        closed = await db.release_unfinished_jobs(
            user["id"], note="کاربر فوروارد گیرکرده را بست (🔓)"
        )
    except Exception as e:
        logger.error(f"release_unfinished_jobs failed: {e}")
        closed = []

    await _free_cache_files(closed)

    await query.answer(
        f"🔓 {len(closed)} فوروارد گیرکرده آزاد شد" if closed else "چیزی برای بستن نبود"
    )

    await db.audit_log(user["id"], "forward_unlock",
                      f"closed={[r['id'] for r in closed]}")

    # اگر همان فورواردی که می‌خواست را داریم، همین حالا شروعش کن
    if st.get("src") and st.get("dst"):
        await _launch_forward(context, user, st, chat_id, msg_id)
        return

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id,
        text=(
            "🔓 <b>فوروارد گیرکرده آزاد شد</b>\n"
            "حالا می‌توانی فوروارد جدید بسازی."
        ),
        reply_markup=back_kb("fwd_start"),
        parse_mode="HTML",
    )


async def cb_fwd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادامه یک فوروارد نیمه‌کاره از همان‌جا که متوقف شده"""
    query = update.callback_query

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    err = await _ready(user)
    if err:
        await query.answer(err, show_alert=True)
        return

    if (forwarder.get_active_job(user["id"])
            or cache_forward.get_active_job(user["id"])):
        await query.answer("⏳ همین حالا در حال اجراست", show_alert=True)
        return

    row = await db.get_user_forward_job(user["id"])
    if not row or row["status"] not in ("paused", "error", "running"):
        await query.answer("فوروارد نیمه‌کاره‌ای نیست", show_alert=True)
        return

    await query.answer()

    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    is_cache = bool(row.get("cache_mode"))

    async def on_progress(job):
        try:
            stats = None
            if is_cache and job.phase == "send":
                stats = await db.cache_stats(job.db_id)
            text = (
                cache_forward.cache_job_summary(job, stats)
                if is_cache else forwarder.job_summary(job)
            )
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=(
                    fwd_running_kb(job) if job.state in ("running", "waiting")
                    else fwd_done_kb(paused=job.state != "done")
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug(f"resume progress edit skipped: {e}")

    try:
        if is_cache:
            job = await cache_forward.resume_job(row, on_progress=on_progress,
                                                user_db_id=user["id"])
        else:
            job = await forwarder.resume_job(row, on_progress=on_progress,
                                            user_db_id=user["id"])
    except ValueError as e:
        msg = {
            "job_running": "⏳ یک فوروارد دیگر در حال اجراست.",
            "account_not_connected": "❌ اکانت متصل نیست.",
        }.get(str(e), "❌ ادامه ناموفق بود.")
        await context.bot.send_message(chat_id, msg)
        return

    await db.audit_log(user["id"], "forward_resume", f"job={row['id']}")
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id,
        text=forwarder.job_summary(job),
        reply_markup=fwd_running_kb(job),
        parse_mode="HTML",
    )


async def cb_fwd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    job_id = int(query.data.replace("fwd_stop_", ""))

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    job = forwarder.get_job(job_id) or cache_forward.get_job(job_id)
    if not job or job.user_db_id != user["id"]:
        await query.answer("درخواست نامعتبر", show_alert=True)
        return

    ok = await forwarder.stop_job(job_id) or await cache_forward.stop_job(job_id)
    await query.answer(
        "⏹ درخواست توقف ثبت شد..." if ok else "قبلاً تمام شده",
        show_alert=not ok,
    )


async def cb_fwd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🗑 درخواست حذف کامل — نمایش تایید"""
    query = update.callback_query
    await query.answer()
    from bot.keyboards import fwd_delete_confirm_kb
    # اگر جاب در حال اجراست هم می‌توان حذف کرد — اول متوقف می‌شود
    await query.edit_message_text(
        "🗑 <b>حذف کامل فوروارد</b>\n\n"
        "این فوروارد برای همیشه حذف می‌شود و دیگر قابل ادامه نیست.\n"
        "کشِ آن هم پاک می‌شود.\n\n"
        "مطمئنی؟",
        reply_markup=fwd_delete_confirm_kb(),
        parse_mode="HTML",
    )


async def cb_fwd_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید حذف کامل"""
    query = update.callback_query
    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)
    # اگر جاب فعال دارد، اول متوقفش کن
    active = forwarder.get_active_job(user["id"]) or cache_forward.get_active_job(user["id"])
    if active:
        try:
            await forwarder.stop_job(active.id)
        except Exception:
            pass
        try:
            await cache_forward.stop_job(active.id)
        except Exception:
            pass
        # کمی صبر تا state به paused برود
        import asyncio
        await asyncio.sleep(0.5)
    try:
        closed = await db.release_unfinished_jobs(
            user["id"], note="کاربر فوروارد را برای همیشه حذف کرد (🗑)", status="cancelled"
        )
    except Exception as e:
        logger.error(f"release_unfinished_jobs failed: {e}")
        closed = []
    await _free_cache_files(closed)
    # همچنین هر جابِ paused/error باقی‌مانده را هم ببند
    # (release_unfinished_jobs فقط running/paused را می‌بندد؛ errorها می‌مانند)
    # برای حذف کامل، errorها را هم به cancelled تبدیل کن
    try:
        # مستقیم تمام ردیف‌های نیمه‌کاره را پاک کن
        pool = db.get_pool()
        if pool:
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE forward_jobs SET status='cancelled', error='حذف کامل توسط کاربر', updated_at=NOW()
                       WHERE user_id=$1 AND status IN ('error','paused','running')""",
                    user["id"],
                )
    except Exception as e:
        logger.debug(f"extra cancel error jobs failed: {e}")
    await db.audit_log(user["id"], "forward_delete", f"closed={[r['id'] for r in closed]}")
    await query.answer("🗑 فوروارد برای همیشه حذف شد")
    await query.edit_message_text(
        "🗑 <b>فوروارد برای همیشه حذف شد</b>\n"
        "دیگر قابل ادامه نیست و کش آن پاک شد.\n"
        "برای شروعِ فوروارد جدید از دکمه‌ی زیر استفاده کن.",
        reply_markup=back_kb("fwd_start"),
        parse_mode="HTML",
    )


async def cb_fwd_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from bot.keyboards import fwd_done_kb
    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)
    row = await db.get_user_forward_job(user["id"])
    paused = bool(row and row["status"] in ("paused", "error", "running"))
    await query.edit_message_text(
        "❌ انصراف داده شد.",
        reply_markup=fwd_done_kb(paused=paused),
    )


# ═══════════════════════════════════
# ورودی متنی
# ═══════════════════════════════════


async def handle_fwd_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """جستجوی چت با نام"""
    context.user_data.pop("awaiting_fwd_search", None)

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    text = (update.message.text or "").strip()[:40]

    try:
        await update.message.delete()
    except Exception:
        pass

    st = _state(context)
    st["query"] = text
    st["src_page"] = 0
    st["dst_page"] = 0

    if not st.get("chat_id"):
        await context.bot.send_message(
            update.effective_chat.id, t("fwd_search_prompt")
        )
        return

    target = "dst" if st.get("src") and not st.get("dst") else "src"

    try:
        items = await forwarder.load_dialogs(user["id"])
    except ValueError:
        return

    if not forwarder.filter_dialogs(
        items, query=text, chats_only=st["chats_only"]
    ):
        await context.bot.edit_message_text(
            chat_id=st["chat_id"],
            message_id=st["msg_id"],
            text=t("fwd_search_empty", query=html.escape(text)),
            reply_markup=back_kb("fwd_start"),
            parse_mode="HTML",
        )
        return

    await _render_dialogs(context, user, target)


async def handle_fwd_manual_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ورود دستی مبدأ/مقصد: لینک، یوزرنیم، آیدی یا لینک دعوت"""
    target = context.user_data.pop("awaiting_fwd_manual", "dst")

    from bot.handlers import get_or_create_user
    user = await get_or_create_user(update)

    text = (update.message.text or "").strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    st = _state(context)
    if target == "dst" and not st.get("src"):
        await context.bot.send_message(
            update.effective_chat.id, "❌ اول چت مبدأ را انتخاب کنید."
        )
        return

    client = await get_client(user["id"])
    if not client:
        await context.bot.send_message(
            update.effective_chat.id, "❌ اکانت متصل نیست."
        )
        return

    try:
        entity = await forwarder.resolve_reference(client, text)
    except forwarder.JoinNeeded as join:
        # لینک دعوت: عضویت باید با تایید کاربر انجام شود
        context.user_data["fwd_pending_join"] = {"target": target}
        st["chat_id"] = update.effective_chat.id
        st["msg_id"] = None

        await context.bot.send_message(
            update.effective_chat.id,
            t("fwd_join_prompt",
              title=html.escape(join.title) or "گروه خصوصی"),
            parse_mode="HTML",
            reply_markup=fwd_join_kb(join.hash),
        )
        return
    except Exception as e:
        logger.warning(f"Manual {target} resolve failed ({text}): {e}")
        await context.bot.send_message(
            update.effective_chat.id, forwarder.resolve_error_message(e)
        )
        return

    name = (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or str(getattr(entity, "id", text))
    )

    picked = {"name": str(name), "entity": entity}

    if target == "src":
        st["src"] = picked
        st["dst"] = None
        st["chat_id"] = update.effective_chat.id
        st["msg_id"] = None
        await context.bot.send_message(
            update.effective_chat.id,
            f"✅ مبدأ: <b>{html.escape(str(name))}</b>",
            parse_mode="HTML",
        )
        # لیست مقصد (بدون ویرایش پیام قبلی)
        await context.bot.send_message(
            update.effective_chat.id,
            t("fwd_pick_dest", src=html.escape(str(name)), total=0, found=0,
              page=1, pages=1, filter=t("fwd_filter_on"), query="—"),
            parse_mode="HTML",
        )
        return

    if st.get("chat_id") and st.get("msg_id"):
        await _render_confirm(context, user, dst=picked)
    else:
        await context.bot.send_message(
            update.effective_chat.id,
            f"✅ مقصد: <b>{html.escape(str(name))}</b>",
            parse_mode="HTML",
            reply_markup=back_kb("fwd_start"),
        )
