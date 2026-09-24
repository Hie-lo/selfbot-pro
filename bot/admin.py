"""
پنل ادمین — بررسی درخواست‌های اشتراک، فعال‌سازی اشتراک و مدیریت مشتریان
"""

import html
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from config import PLANS
from bot.texts import t
from bot.keyboards import (
    admin_menu_kb,
    admin_review_kb,
    admin_users_kb,
    admin_user_kb,
    back_kb,
)
from bot.subscription import is_admin, check_subscription
from core.security import calc_plan_expiry
from database import db

logger = logging.getLogger("bot.admin")

PER_PAGE = 8


def _guard(update: Update) -> bool:
    """فقط ادمین اجازه دارد"""
    user = update.effective_user
    return bool(user and is_admin(user.id))


def _display_name(user: dict) -> str:
    name = (user.get("first_name") or "").strip() or "بدون نام"
    if user.get("username"):
        name += f" (@{user['username']})"
    return html.escape(name)


# ═══════════════════════════════════
# منوی ادمین
# ═══════════════════════════════════


async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    pending = await db.count_pending_requests()
    await query.edit_message_text(
        t("admin_panel", pending=pending),
        reply_markup=admin_menu_kb(pending),
        parse_mode="HTML",
    )


async def cb_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    stats = await db.count_users()
    await query.edit_message_text(
        t("admin_stats", **stats),
        reply_markup=back_kb("admin"),
        parse_mode="HTML",
    )


# ═══════════════════════════════════
# درخواست‌های اشتراک
# ═══════════════════════════════════


async def cb_admin_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    requests = await db.get_pending_requests()
    if not requests:
        await query.edit_message_text(
            t("admin_no_requests"), reply_markup=back_kb("admin")
        )
        return

    await query.edit_message_text(
        f"📥 <b>{len(requests)}</b> درخواست در انتظار بررسی:",
        reply_markup=back_kb("admin"),
        parse_mode="HTML",
    )

    # هر درخواست با عکس رسید + دکمه تایید/رد جداگانه ارسال می‌شود
    for req in requests:
        caption = t(
            "admin_req_new",
            req_id=req["id"],
            name=html.escape((req.get("first_name") or "کاربر").strip()),
            username=html.escape(
                f" (@{req['username']})" if req.get("username") else ""
            ),
            tg_id=req["telegram_id"],
            title=req["plan_title"],
            days=req["days"],
            price=f"{req['price']:,}",
            date=str(req["created_at"])[:16],
        )
        file_id = req.get("receipt_file_id")
        try:
            if file_id:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=admin_review_kb(req["id"]),
                )
            else:
                raise ValueError("no file_id")
        except Exception as e:
            logger.warning(f"Send request #{req['id']} photo failed: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=caption,
                parse_mode="HTML",
                reply_markup=admin_review_kb(req["id"]),
            )


async def _grant_subscription(user: dict, days: int) -> datetime:
    """تمدید/فعال‌سازی اشتراک یک کاربر"""
    expires = calc_plan_expiry(days, user.get("plan_expires_at"))
    await db.set_user_plan(user["telegram_id"], "premium", expires)
    return expires


async def cb_admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    req_id = int(query.data.replace("adm_ok_", ""))

    # جلوگیری از تایید دوباره با دو کلیک پشت سر هم
    resolved = await db.resolve_subscription_request(
        req_id, "approved", update.effective_user.id
    )
    if not resolved:
        await query.answer(t("admin_reversed"), show_alert=True)
        return

    req = await db.get_subscription_request(req_id)
    if not req:
        await query.answer("❌ درخواست پیدا نشد", show_alert=True)
        return

    user = await db.get_user(req["telegram_id"])
    expires = await _grant_subscription(user, req["days"])

    await db.audit_log(
        user["id"], "subscription_approved",
        f"#{req_id} {req['plan_key']} by {update.effective_user.id}",
    )

    await query.answer(t("admin_req_approved", req_id=req_id), show_alert=True)

    days_left = (expires - datetime.now(timezone.utc)).days
    await query.edit_message_caption(
        caption=(
            (query.message.caption or "")
            + f"\n\n✅ <b>تایید شد</b> — انقضا: {expires.strftime('%Y-%m-%d')}"
        ),
        parse_mode="HTML",
    )

    # اطلاع به مشتری
    try:
        await context.bot.send_message(
            chat_id=user["telegram_id"],
            text=t(
                "sub_approved",
                title=req["plan_title"],
                expires=expires.strftime("%Y-%m-%d"),
                days=days_left,
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Notify approved user failed: {e}")


async def cb_admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    req_id = int(query.data.replace("adm_no_", ""))
    req = await db.get_subscription_request(req_id)
    if not req or req["status"] != "pending":
        await query.answer(t("admin_reversed"), show_alert=True)
        return

    context.user_data["awaiting_reject_req"] = req_id
    await query.answer()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📝 {t('admin_req_ask_reject')}\n\n🆔 درخواست: <code>{req_id}</code>",
        parse_mode="HTML",
    )


async def handle_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت علت رد درخواست"""
    req_id = context.user_data.pop("awaiting_reject_req", None)
    if not req_id:
        return

    reason = (update.message.text or "").strip()[:300]

    resolved = await db.resolve_subscription_request(
        req_id, "rejected", update.effective_user.id, reason
    )
    if not resolved:
        await update.message.reply_text(t("admin_reversed"))
        return

    req = await db.get_subscription_request(req_id)
    await db.audit_log(
        req["user_id"], "subscription_rejected",
        f"#{req_id} reason={reason[:100]}",
    )

    await update.message.reply_text(
        t("admin_req_rejected", req_id=req_id)
    )

    try:
        await context.bot.send_message(
            chat_id=req["telegram_id"],
            text=t("sub_rejected", reason=f"📝 علت: {html.escape(reason)}"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Notify rejected user failed: {e}")


# ═══════════════════════════════════
# مدیریت مشتریان
# ═══════════════════════════════════


async def cb_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    page = int(query.data.replace("admin_users_", ""))
    users = await db.get_all_users()
    total = len(users)

    for u in users:
        u["_has_sub"] = await check_subscription(u)

    start = page * PER_PAGE
    chunk = users[start:start + PER_PAGE]

    if not chunk and page > 0:
        page = 0
        chunk = users[:PER_PAGE]

    await query.edit_message_text(
        t(
            "admin_users_title",
            page=page + 1,
            total=total,
        ),
        reply_markup=admin_users_kb(chunk, page, PER_PAGE, total),
        parse_mode="HTML",
    )


async def cb_admin_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    db_user_id = int(query.data.replace("admin_user_", ""))
    await render_admin_user(query, db_user_id)


async def render_admin_user(query, db_user_id: int):
    """نمایش کارت یک مشتری (بدون answer مجدد)"""
    user = await db.get_user_by_db_id(db_user_id)
    if not user:
        await query.edit_message_text(
            "❌ کاربر پیدا نشد", reply_markup=back_kb("admin_users_0")
        )
        return

    has_sub = await check_subscription(user)
    expires = (
        str(user["plan_expires_at"])[:16] if user.get("plan_expires_at") else "—"
    )
    features = await db.get_features(db_user_id)
    enabled = sum(1 for f in features if f["is_enabled"])
    session = await db.get_session(db_user_id)

    status = "🚫 مسدود" if user.get("is_banned") else (
        "🟢 متصل" if session else "🔴 قطع"
    )

    await query.edit_message_text(
        t(
            "admin_user_title",
            name=html.escape((user.get("first_name") or "بدون نام")),
            username=html.escape(
                f" (@{user['username']})" if user.get("username") else ""
            ),
            tg_id=user["telegram_id"],
            plan=user.get("plan") or "free",
            expires=expires if has_sub else "—",
            status=status,
            features=enabled,
        ),
        reply_markup=admin_user_kb(
            user["telegram_id"], bool(user.get("is_banned")), PLANS
        ),
        parse_mode="HTML",
    )


async def cb_admin_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فعال‌سازی دستی اشتراک توسط ادمین"""
    query = update.callback_query

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    payload = query.data.replace("admsub_", "")
    tg_id_str, _, plan_key = payload.partition("_")
    plan = PLANS.get(plan_key)
    user = await db.get_user(int(tg_id_str))

    if not user or not plan:
        await query.answer("❌ نامعتبر", show_alert=True)
        return

    expires = await _grant_subscription(user, plan["days"])
    await db.audit_log(
        user["id"], "admin_grant_subscription",
        f"{plan_key} {plan['days']}d by {update.effective_user.id}",
    )

    await query.answer(
        t("admin_user_granted", plan=plan["title"], tg_id=tg_id_str,
          expires=expires.strftime("%Y-%m-%d")),
        show_alert=True,
    )
    logger.info(
        f"Admin {update.effective_user.id} granted {plan_key} to {tg_id_str}"
    )

    # اطلاع به مشتری
    try:
        await context.bot.send_message(
            chat_id=user["telegram_id"],
            text=t(
                "sub_approved",
                title=plan["title"],
                expires=expires.strftime("%Y-%m-%d"),
                days=(expires - datetime.now(timezone.utc)).days,
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Notify granted user failed: {e}")

    await render_admin_user(query, user["id"])


async def cb_admin_cancel_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    tg_id = int(query.data.replace("admcancel_", ""))
    user = await db.get_user(tg_id)
    if not user:
        await query.answer("❌ کاربر پیدا نشد", show_alert=True)
        return

    await db.set_user_plan(tg_id, "free", None)
    await db.audit_log(
        user["id"], "admin_cancel_subscription",
        f"by {update.effective_user.id}",
    )
    await query.answer(t("admin_user_cancelled"), show_alert=True)
    await render_admin_user(query, user["id"])


async def cb_admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not _guard(update):
        await query.answer(t("admin_only"), show_alert=True)
        return

    tg_id = int(query.data.replace("admban_", ""))
    user = await db.get_user(tg_id)
    if not user:
        await query.answer("❌ کاربر پیدا نشد", show_alert=True)
        return

    new_state = not bool(user.get("is_banned"))
    await db.set_user_banned(tg_id, new_state)
    await db.audit_log(
        user["id"], "admin_ban" if new_state else "admin_unban",
        f"by {update.effective_user.id}",
    )

    await query.answer(
        "🚫 مسدود شد" if new_state else "✅ رفع مسدودی شد",
        show_alert=True,
    )
    await render_admin_user(query, user["id"])
