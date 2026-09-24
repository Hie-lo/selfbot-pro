"""
خرید اشتراک — انتخاب پلن، پرداخت کارت به کارت،
ارسال عکس رسید و ارجاع درخواست به ادمین
"""

import html
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from config import (
    ADMIN_TELEGRAM_ID,
    PLANS,
    CARD_NUMBER,
    CARD_HOLDER,
    BANK_NAME,
    RECEIPT_MAX_MB,
)
from bot.texts import t
from bot.keyboards import plans_kb, plan_confirm_kb, back_kb, admin_review_kb
from core.security import check_rate_limit
from database import db

logger = logging.getLogger("bot.subscription")


# ═══════════════════════════════════
# Helpers
# ═══════════════════════════════════


def is_admin(tg_id: int) -> bool:
    return tg_id == ADMIN_TELEGRAM_ID


async def check_subscription(user: dict) -> bool:
    """آیا کاربر اشتراک فعال دارد؟"""
    if not user:
        return False
    if user.get("plan") == "free":
        return False
    expires = user.get("plan_expires_at")
    if not expires:
        return False
    now = datetime.now(timezone.utc)
    if hasattr(expires, "tzinfo") and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > now


def plans_summary() -> str:
    """لیست خوانا از پلن‌ها"""
    lines = []
    for plan in PLANS.values():
        lines.append(f"▪️ {plan['title']} — {plan['price']:,} تومان")
    return "\n".join(lines)


def request_status_text(status: str) -> str:
    return t(f"status_{status}") if status in (
        "pending", "approved", "rejected"
    ) else status


# ═══════════════════════════════════
# نمایش پلن‌ها
# ═══════════════════════════════════


async def cb_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش وضعیت اشتراک + لیست پلن‌های خرید"""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    user = await db.get_user(tg_user.id)
    if not user:
        from bot.handlers import get_or_create_user
        user = await get_or_create_user(update)

    has_sub = await check_subscription(user)

    if has_sub:
        text = t(
            "subscription_active",
            expires=str(user["plan_expires_at"])[:10],
        )
        days_left = (
            user["plan_expires_at"] - datetime.now(timezone.utc)
        ).days
        text += f"\n⏳ روزهای باقی‌مانده: {days_left}"
    else:
        text = t("no_subscription", price=f"{PLANS['1m']['price']:,}")

    text += "\n\n" + t("sub_choose_plan", plans=plans_summary())

    await query.edit_message_text(
        text,
        reply_markup=plans_kb(PLANS),
        parse_mode="HTML",
    )


async def cb_buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش جزئیات پلن + اطلاعات کارت"""
    query = update.callback_query
    await query.answer()

    plan_key = query.data.replace("buyplan_", "")
    plan = PLANS.get(plan_key)
    if not plan:
        await query.edit_message_text(
            t("error_general"), reply_markup=back_kb("subscription")
        )
        return

    # نکته: برای خرید اشتراک نیازی به اکانت متصل نیست
    # (کاربر جدید تا اشتراک نداشته باشد اجازه اتصال ندارد)
    bank = f"\n🏛 بانک: <b>{html.escape(BANK_NAME)}</b>" if BANK_NAME else ""

    await query.edit_message_text(
        t(
            "sub_plan_detail",
            title=plan["title"],
            price=f"{plan['price']:,}",
            days=plan["days"],
            card=html.escape(CARD_NUMBER),
            holder=html.escape(CARD_HOLDER),
            bank=bank,
        ),
        reply_markup=plan_confirm_kb(plan_key),
        parse_mode="HTML",
    )


async def cb_send_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست ارسال عکس رسید"""
    query = update.callback_query
    await query.answer()

    plan_key = query.data.replace("sendreceipt_", "")
    plan = PLANS.get(plan_key)
    if not plan:
        await query.edit_message_text(
            t("error_general"), reply_markup=back_kb("subscription")
        )
        return

    user = await db.get_user(update.effective_user.id)
    if not user:
        await query.edit_message_text(
            t("error_general"), reply_markup=back_kb("subscription")
        )
        return

    # اگه درخواست در انتظار داری، دوباره ثبت نکن
    last = await db.get_last_request(user["id"])
    if last and last["status"] == "pending":
        await query.edit_message_text(
            t("sub_already_pending"), reply_markup=back_kb("subscription")
        )
        return

    context.user_data["awaiting_receipt"] = plan_key
    context.user_data["receipt_plan_title"] = plan["title"]

    await query.edit_message_text(
        t("sub_ask_receipt", title=plan["title"], price=f"{plan['price']:,}"),
        reply_markup=back_kb("subscription"),
        parse_mode="HTML",
    )


# ═══════════════════════════════════
# دریافت عکس رسید
# ═══════════════════════════════════


async def handle_receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت عکس رسید و ارسال به ادمین"""
    from bot.handlers import get_or_create_user, main_kb

    user = await get_or_create_user(update)
    plan_key = context.user_data.get("awaiting_receipt")
    if not plan_key:
        return

    plan = PLANS.get(plan_key)
    if not plan:
        context.user_data.pop("awaiting_receipt", None)
        return

    msg = update.message
    if not msg:
        return

    # فقط عکس یا عکس ارسالی به صورت فایل
    is_photo = bool(msg.photo)
    is_image_doc = bool(
        msg.document
        and (msg.document.mime_type or "").startswith("image/")
    )
    if not is_photo and not is_image_doc:
        await msg.reply_text(t("sub_receipt_invalid"), parse_mode="HTML")
        return

    size_mb = 0
    if msg.document:
        size_mb = (msg.document.file_size or 0) / 1024 / 1024
    elif msg.photo:
        size_mb = (msg.photo[-1].file_size or 0) / 1024 / 1024

    if size_mb > RECEIPT_MAX_MB:
        await msg.reply_text(
            f"❌ حجم عکس زیاد است (حداکثر {RECEIPT_MAX_MB} مگابایت).",
            reply_markup=main_kb(update.effective_user.id, bool(await db.get_session(user["id"]))),
        )
        context.user_data.pop("awaiting_receipt", None)
        return

    if not check_rate_limit(user["telegram_id"], "receipt", 3, 3600):
        await msg.reply_text(
            "🚫 تعداد درخواست‌ها زیاد است. کمی بعد تلاش کنید.",
            reply_markup=main_kb(update.effective_user.id, True),
        )
        return

    context.user_data.pop("awaiting_receipt", None)

    file_id = msg.photo[-1].file_id if msg.photo else msg.document.file_id

    # ── ثبت درخواست در DB ──
    req = await db.create_subscription_request(
        user_id=user["id"],
        plan_key=plan_key,
        plan_title=plan["title"],
        price=plan["price"],
        days=plan["days"],
        receipt_chat_id=msg.chat_id,
        receipt_msg_id=msg.message_id,
        receipt_file_id=file_id,
    )

    if not req:
        await msg.reply_text(
            t("sub_already_pending"),
            reply_markup=main_kb(update.effective_user.id, True),
        )
        return

    await db.audit_log(
        user["id"], "subscription_request",
        f"#{req['id']} {plan_key} {plan['price']}",
    )

    await msg.reply_text(
        t(
            "sub_receipt_received",
            req_id=req["id"],
            title=plan["title"],
            price=f"{plan['price']:,}",
        ),
        parse_mode="HTML",
        reply_markup=main_kb(update.effective_user.id, True),
    )

    await notify_admin(context, req, msg, user)


async def notify_admin(context: ContextTypes.DEFAULT_TYPE, req: dict, msg, user: dict):
    """ارسال درخواست + عکس رسید به ادمین"""
    if not ADMIN_TELEGRAM_ID:
        return

    name = (user.get("first_name") or "کاربر").strip()
    username = f" (@{user['username']})" if user.get("username") else ""

    caption = t(
        "admin_req_new",
        req_id=req["id"],
        name=html.escape(name),
        username=html.escape(username),
        tg_id=user["telegram_id"],
        title=req["plan_title"],
        days=req["days"],
        price=f"{req['price']:,}",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )

    try:
        file_id = (
            req.get("receipt_file_id")
            or (msg.photo[-1].file_id if msg.photo else msg.document.file_id)
        )

        if msg.photo:
            sent = await context.bot.send_photo(
                chat_id=ADMIN_TELEGRAM_ID,
                photo=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=admin_review_kb(req["id"]),
            )
        elif msg.document:
            sent = await context.bot.send_document(
                chat_id=ADMIN_TELEGRAM_ID,
                document=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=admin_review_kb(req["id"]),
            )
        else:
            sent = await context.bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=caption,
                parse_mode="HTML",
                reply_markup=admin_review_kb(req["id"]),
            )

        await db.set_request_message_id(req["id"], sent.message_id)

    except Exception as e:
        logger.error(f"Admin notify failed for request #{req['id']}: {e}")


# ═══════════════════════════════════
# وضعیت درخواست‌های کاربر
# ═══════════════════════════════════


async def cb_my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = await db.get_user(update.effective_user.id)
    if not user:
        await query.edit_message_text(
            t("sub_no_request"), reply_markup=back_kb("subscription")
        )
        return

    last = await db.get_last_request(user["id"])
    if not last:
        await query.edit_message_text(
            t("sub_no_request"), reply_markup=back_kb("subscription")
        )
        return

    line = t(
        "sub_my_requests",
        req_id=last["id"],
        title=last["plan_title"],
        status=request_status_text(last["status"]),
        date=str(last["created_at"])[:16],
    )

    if last["status"] == "rejected" and last.get("reject_reason"):
        line += f"\n\n📝 علت رد: {html.escape(last['reject_reason'])}"

    if last["status"] == "approved":
        line += f"\n📅 انقضا: {str(user['plan_expires_at'])[:10]}"

    await query.edit_message_text(
        line,
        reply_markup=back_kb("subscription"),
        parse_mode="HTML",
    )
