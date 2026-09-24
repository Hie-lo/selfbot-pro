"""
هندلرهای ربات تلگرام — نسخه کیبورد مجازی
"""
import html
import asyncio
import logging
from bot.keyboards import monitor_menu_kb, mon_confirm_delete_kb
from telegram import Update
from telegram.error import NetworkError, TimedOut, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    TypeHandler,
    InlineQueryHandler,
    ApplicationHandlerStop,
    filters,
)

from config import (
    ADMIN_TELEGRAM_ID,
    MONTHLY_PRICE_TOMAN,
    LOGIN_TIMEOUT,
    MAX_LOGIN_ATTEMPTS,
)
from bot.texts import t
from bot.keyboards import (
    features_kb,
    storage_menu_kb,
    storage_target_kb,
    storage_owned_kb,
    confirm_kb,
    back_kb,
    numpad_kb,
    code_entry_text,
    ALL_FEATURES,
)
from database import db
from bot.subscription import (
    check_subscription,
    cb_subscription,
    cb_buy_plan,
    cb_send_receipt,
    cb_my_requests,
    handle_receipt_photo,
    is_admin as is_admin_id,
)
from bot.forward import (
    cb_fwd_start,
    cb_fwd_nav,
    cb_fwd_page,
    cb_fwd_pick,
    cb_fwd_filter,
    cb_fwd_search,
    cb_fwd_clear_search,
    cb_fwd_dst_saved,
    cb_fwd_manual,
    cb_fwd_join,
    cb_fwd_cancel_join,
    cb_fwd_opt,
    cb_fwd_go,
    cb_fwd_stop,
    cb_fwd_resume,
    cb_fwd_unlock,
    cb_fwd_delete,
    cb_fwd_delete_confirm,
    cb_fwd_delete_cancel,
    handle_fwd_search_input,
    handle_fwd_manual_input,
)
from bot.admin import (
    cb_admin,
    cb_admin_stats,
    cb_admin_requests,
    cb_admin_approve,
    cb_admin_reject,
    cb_admin_users,
    cb_admin_user,
    cb_admin_grant,
    cb_admin_cancel_sub,
    cb_admin_ban,
    handle_reject_reason,
)
from core.security import (
    validate_phone,
    validate_2fa_password,
    check_rate_limit,
    hash_phone,
)
from bot.help_panel import cb_help, cmd_help, cb_help_nav, inline_help
from core.crypto import encrypt, decrypt
from core import client_manager, access

logger = logging.getLogger("bot.handlers")

# ── Conversation States ──
(
    STATE_PHONE,
    STATE_2FA,
    STATE_STORAGE_TARGET,
) = range(3)


# ═══════════════════════════════════
# Helpers
# ═══════════════════════════════════


def main_kb(tg_id: int, has_account: bool):
    """منوی اصلی — دکمه پنل ادمین فقط برای ادمین"""
    from bot.keyboards import main_menu_kb
    return main_menu_kb(has_account, is_admin=is_admin_id(tg_id))


async def get_or_create_user(update: Update) -> dict:
    tg_user = update.effective_user
    user = await db.get_user(tg_user.id)
    if not user:
        user = await db.create_user(
            telegram_id=tg_user.id,
            first_name=tg_user.first_name or "",
            username=tg_user.username or "",
        )
        await db.audit_log(user["id"], "register", "New user")
    return user


async def safe_send(chat, text: str, retries: int = 3, **kwargs):
    for attempt in range(retries):
        try:
            return await chat.send_message(text, **kwargs)
        except RetryAfter as e:
            await asyncio.sleep(int(getattr(e, "retry_after", 1)) + 1)
        except (TimedOut, NetworkError):
            if attempt < retries - 1:
                await asyncio.sleep(1 + attempt)
            else:
                raise


def extract_storage_feature(callback_data: str, suffix: str) -> str:
    prefix = "starget_"
    if not callback_data.startswith(prefix) or not callback_data.endswith(suffix):
        raise ValueError("Invalid callback data")
    return callback_data[len(prefix):-len(suffix)]


# ═══════════════════════════════════
# /start
# ═══════════════════════════════════


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update)
    name = update.effective_user.first_name or "کاربر"
    session = await db.get_session(user["id"])
    await update.message.reply_text(
        t("welcome", name=name),
        reply_markup=main_kb(update.effective_user.id, session is not None),
    )


# ═══════════════════════════════════
# Callbacks: منو
# ═══════════════════════════════════


async def cb_back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    session = await db.get_session(user["id"])
    name = update.effective_user.first_name or "کاربر"
    await query.edit_message_text(
        t("welcome", name=name),
        reply_markup=main_kb(update.effective_user.id, session is not None),
    )


async def cb_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    session = await db.get_session(user["id"])
    status = access.status_label(session)
    has_sub = await check_subscription(user)
    plan_text = "✅ فعال" if has_sub else "❌ ندارید"
    await query.edit_message_text(
        t("panel_title", status=status, plan=plan_text),
        reply_markup=back_kb(),
    )


async def cb_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    session = await db.get_session(user["id"])
    if not session:
        await query.edit_message_text(t("error_no_account"), reply_markup=back_kb())
        return
    status = access.status_label(session)
    has_sub = await check_subscription(user)
    plan_text = "✅ فعال" if has_sub else "❌ ندارید"
    await query.edit_message_text(
        t("panel_title", status=status, plan=plan_text),
        reply_markup=main_kb(update.effective_user.id, True),
    )


# ═══════════════════════════════════
# قابلیت‌ها
# ═══════════════════════════════════


async def cb_features(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    has_sub = await check_subscription(user)
    if not has_sub:
        await query.edit_message_text(
            t("no_subscription", price=f"{MONTHLY_PRICE_TOMAN:,}"),
            reply_markup=back_kb(),
        )
        return
    session = await db.get_session(user["id"])
    if not session:
        await query.edit_message_text(t("error_no_account"), reply_markup=back_kb())
        return
    features = await db.get_features(user["id"])
    enabled_map = {f["feature_name"]: f["is_enabled"] for f in features}
    await query.edit_message_text(
        "🧩 **قابلیت‌ها**\n\nروی هر قابلیت بزنید:",
        reply_markup=features_kb(enabled_map),
        parse_mode="Markdown",
    )


async def cb_toggle_feature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    feature_name = query.data.replace("toggle_", "")

    # noop برای دکمه‌های غیرفعال
    if feature_name == "noop" or query.data == "noop":
        await query.answer()
        return

    user = await get_or_create_user(update)
    has_sub = await check_subscription(user)
    if not has_sub:
        await query.answer("❌ اشتراک ندارید", show_alert=True)
        return

    from core.client_manager import get_client
    from core.plugin_manager import (
        TOGGLEABLE_PLUGINS,
        enable_plugin,
        disable_plugin,
    )

    # قابلیتی که پلاگین ندارد، نباید اصلاً قابل روشن کردن باشد
    if feature_name not in TOGGLEABLE_PLUGINS:
        await query.answer("❌ این قابلیت پشتیبانی نمی‌شود", show_alert=True)
        return

    client = await get_client(user["id"])
    if not client:
        await query.answer("❌ اکانت متصل نیست", show_alert=True)
        return

    is_on = await db.is_feature_enabled(user["id"], feature_name)
    new_state = not is_on

    # اول پلاگین، بعد ذخیره در DB (تا وضعیت نمایش‌داده‌شده واقعی باشد)
    if new_state:
        loaded = await enable_plugin(user["id"], feature_name, client)
        if not loaded:
            await query.answer("❌ فعال‌سازی ناموفق بود", show_alert=True)
            return
    else:
        await disable_plugin(user["id"], feature_name)

    await db.set_feature(user["id"], feature_name, new_state)
    await db.audit_log(
        user["id"], "feature_toggle",
        f"{feature_name} -> {'ON' if new_state else 'OFF'}",
    )

    from bot.keyboards import ALL_FEATURES
    fname = ALL_FEATURES.get(feature_name, feature_name)
    await query.answer(f"{'✅' if new_state else '❌'} {fname}", show_alert=False)

    features = await db.get_features(user["id"])
    enabled_map = {f["feature_name"]: f["is_enabled"] for f in features}
    await query.edit_message_text(
        "🧩 **قابلیت‌ها**\n\nروی هر قابلیت بزنید:",
        reply_markup=features_kb(enabled_map),
        parse_mode="Markdown",
    )


# ═══════════════════════════════════
# ذخیره‌سازی
# ═══════════════════════════════════


async def cb_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    session = await db.get_session(user["id"])
    if not session:
        await query.edit_message_text(t("error_no_account"), reply_markup=back_kb())
        return
    await query.edit_message_text(
        "📂 **مسیر ذخیره‌سازی**\n\nبرای هر قابلیت مقصد را مشخص کنید:",
        reply_markup=storage_menu_kb(),
        parse_mode="Markdown",
    )


async def cb_storage_feature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    feature_name = query.data.replace("storage_", "")
    user = await get_or_create_user(update)
    target = await db.get_storage_target(user["id"], feature_name)
    current = "تنظیم نشده"
    if target:
        current = "💾 Saved Messages" if target["target_type"] == "saved" else f"📢 {target.get('target_title', target['target_id'])}"
    fname = ALL_FEATURES.get(feature_name, feature_name)
    await query.edit_message_text(
        f"📂 **{fname}**\n\nمسیر فعلی: {current}\n\nمقصد جدید:",
        reply_markup=storage_target_kb(feature_name),
        parse_mode="Markdown",
    )


async def cb_recents_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید پاکسازی لیست استیکرهای Recent"""
    query = update.callback_query
    await query.answer()

    from bot.keyboards import recents_clear_kb
    await query.edit_message_text(
        t("recents_ask"),
        reply_markup=recents_clear_kb(),
        parse_mode="HTML",
    )


async def cb_recents_clear_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاک کردن لیست استیکرهای اخیر اکانت"""
    query = update.callback_query

    user = await get_or_create_user(update)
    client = await client_manager.get_client(user["id"])
    if not client:
        await query.answer(t("error_no_account"), show_alert=True)
        return

    from core.media import clear_recent_stickers
    ok = await clear_recent_stickers(client)
    await db.audit_log(user["id"], "recents_clear", "ok" if ok else "failed")

    await query.answer(
        t("recents_done") if ok else t("recents_failed"), show_alert=True
    )
    await query.edit_message_text(
        t("recents_done") if ok else t("recents_failed"),
        reply_markup=back_kb("storage"),
    )


async def cb_storage_target_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        feature_name = extract_storage_feature(query.data, "_saved")
    except ValueError:
        await query.edit_message_text(t("error_general"), reply_markup=back_kb("storage"))
        return
    user = await get_or_create_user(update)
    me_id = update.effective_user.id
    await db.set_storage_target(user["id"], feature_name, "saved", me_id, "Saved Messages")
    await db.audit_log(user["id"], "storage_set", f"{feature_name} -> saved")
    await query.edit_message_text(
        t("storage_set", feature=feature_name, target="Saved Messages"),
        reply_markup=back_kb("storage"),
    )


# ============================================================
# کلیک روی دکمه ثبت کانال سفارشی برای ذخیره‌سازی
# ============================================================
async def cb_storage_target_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # پاسخ فوری به تلگرام برای جلوگیری از لودینگ دکمه

    try:
        feature_name = extract_storage_feature(query.data, "_custom")
    except ValueError:
        await query.edit_message_text(
            t("error_general"),
            reply_markup=back_kb("storage"),
        )
        return

    context.user_data["awaiting_storage_target"] = feature_name
    safe_feature = html.escape(feature_name)

    # استفاده از HTML امن به جای Markdown
    text = (
        f"📢 <b>تنظیم مقصد برای «{safe_feature}»</b>\n\n"
        f"آیدی عددی یا یوزرنیم مقصد را بفرستید:\n\n"
        f"مثال:\n"
        f"<code>-1001234567890</code>\n"
        f"<code>@my_channel</code>\n\n"
        f"برای انصراف /cancel بزنید."
    )
    
    await query.edit_message_text(
        text,
        reply_markup=back_kb("storage"),
        parse_mode="HTML"
    )


# ============================================================
# انتخاب مسیر از کانال/گروه‌های مالک (فقط creator)
# ============================================================
STORAGE_OWN_PER_PAGE = 8

async def cb_storage_target_owned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # starget_{feature}_own
    feature_name = data[len("starget_"):-len("_own")]
    if feature_name not in [k for k,_ in __import__("bot.keyboards", fromlist=["STORAGE_FEATURES"]).STORAGE_FEATURES]:
        await query.edit_message_text(t("error_general"), reply_markup=back_kb("storage"))
        return
    user = await get_or_create_user(update)
    from core import forwarder as fw
    try:
        items = await fw.load_dialogs(user["id"])
    except ValueError as e:
        msg = "❌ اکانت متصل نیست." if str(e) == "account_not_connected" else "❌ خواندن لیست چت‌ها ناموفق بود."
        await query.edit_message_text(msg, reply_markup=back_kb("storage"))
        return
    owned = fw.filter_owned_dialogs(items)
    pages = max(1, (len(owned) + STORAGE_OWN_PER_PAGE - 1) // STORAGE_OWN_PER_PAGE)
    chunk = owned[0:STORAGE_OWN_PER_PAGE]
    fname = ALL_FEATURES.get(feature_name, feature_name)
    text = (
        f"👑 <b>کانال‌ها و گروه‌های شما (فقط مالک)</b>\n\n"
        f"برای «{html.escape(str(fname))}» یک مقصد انتخاب کنید:\n"
        f"تعداد مالک: {len(owned)} — صفحه 1 از {pages}\n\n"
        f"فقط چت‌هایی که شما مالک (creator) آن هستید نمایش داده می‌شود؛ با دسترسی تضمین‌شده."

    )
    if not owned:
        text += "\n📭 هیچ کانال/گروهی که مالکش باشید پیدا نشد."
    await query.edit_message_text(text, reply_markup=storage_owned_kb(chunk, 0, pages, feature_name), parse_mode="HTML")


async def cb_storage_owned_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    import re
    m = re.match(r"^starget_(.+)_own_p(\d+)$", query.data)
    if not m:
        await query.edit_message_text(t("error_general"), reply_markup=back_kb("storage"))
        return
    feature_name, page_str = m.group(1), m.group(2)
    page = int(page_str)
    user = await get_or_create_user(update)
    from core import forwarder as fw
    try:
        items = await fw.load_dialogs(user["id"])
    except ValueError as e:
        msg = "❌ اکانت متصل نیست." if str(e) == "account_not_connected" else "❌ خواندن لیست چت‌ها ناموفق بود."
        await query.edit_message_text(msg, reply_markup=back_kb("storage"))
        return
    owned = fw.filter_owned_dialogs(items)
    pages = max(1, (len(owned) + STORAGE_OWN_PER_PAGE - 1) // STORAGE_OWN_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = owned[page * STORAGE_OWN_PER_PAGE:(page + 1) * STORAGE_OWN_PER_PAGE]
    fname = ALL_FEATURES.get(feature_name, feature_name)
    text = (
        f"👑 <b>کانال‌ها و گروه‌های شما (فقط مالک)</b>\n\n"
        f"برای «{html.escape(str(fname))}» یک مقصد انتخاب کنید:\n"
        f"تعداد مالک: {len(owned)} — صفحه {page+1} از {pages}\n\n"
        f"فقط چت‌هایی که شما مالک (creator) آن هستید نمایش داده می‌شود؛ با دسترسی تضمین‌شده."

    )
    await query.edit_message_text(text, reply_markup=storage_owned_kb(chunk, page, pages, feature_name), parse_mode="HTML")


async def cb_storage_owned_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    import re
    m = re.match(r"^starget_(.+)_own_i(\d+)$", query.data)
    if not m:
        await query.edit_message_text(t("error_general"), reply_markup=back_kb("storage"))
        return
    feature_name, idx_str = m.group(1), m.group(2)
    idx = int(idx_str)
    user = await get_or_create_user(update)
    from core import forwarder as fw
    from telethon import utils
    item = fw.get_dialog(user["id"], idx)
    if not item:
        await query.answer("لیست قدیمی شده؛ دوباره باز کنید", show_alert=True)
        return
    # اطمینان: فقط مالک اجازه دارد
    ent = item.get("entity")
    if not fw.is_owner(ent):
        await query.answer("❌ فقط کانال/گروهی که مالکش هستید قابل انتخاب است", show_alert=True)
        return
    try:
        target_id = utils.get_peer_id(ent)
    except Exception:
        await query.edit_message_text("❌ آیدی مقصد نامعتبر است.", reply_markup=back_kb("storage"))
        return
    target_title = item.get("name") or str(target_id)
    safe_title = html.escape(str(target_title))
    safe_feature = html.escape(str(ALL_FEATURES.get(feature_name, feature_name)))
    try:
        await db.set_storage_target(user["id"], feature_name, "custom", target_id, target_title)
        await db.audit_log(user["id"], "storage_set", f"{feature_name} -> {target_title} ({target_id}) [owned]")
    except Exception as e:
        logger.error(f"DB storage save failed (owned): {e}")
        await query.edit_message_text("❌ خطا در ذخیره اطلاعات در دیتابیس.", reply_markup=back_kb("storage"))
        return
    await query.edit_message_text(
        f"✅ مسیر ذخیره‌سازی «<b>{safe_feature}</b>» با موفقیت تنظیم شد:\n\n"
        f"📂 نام مقصد: <b>{safe_title}</b>\n"
        f"🆔 آیدی عددی: <code>{target_id}</code>\n"
        f"👑 مالک: شما",
        reply_markup=back_kb("storage"),
        parse_mode="HTML",
    )


# ============================================================
# دریافت و پردازش آیدی/یوزرنیم کانال ارسالی کاربر
# ============================================================
async def handle_storage_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update)
    feature_name = context.user_data.get("awaiting_storage_target")

    if not feature_name:
        return

    text = update.message.text.strip()
    safe_text = html.escape(text)

    # اعتبارسنجی سختگیرانه
    is_username = text.startswith("@") and len(text) > 1
    is_numeric = text.lstrip("-").isdigit()

    if not (is_username or is_numeric):
        await update.message.reply_text(
            "❌ <b>فرمت نامعتبر است.</b>\n\n"
            "لطفاً یکی از این فرمت‌ها را بفرستید:\n"
            "• یوزرنیم با @ (مثل <code>@channel</code>)\n"
            "• آیدی عددی (مثل <code>-1001234567890</code>)\n\n"
            "برای انصراف /cancel بزنید.",
            parse_mode="HTML",
        )
        return

    from core.client_manager import get_client
    client = await get_client(user["id"])

    if not client:
        await update.message.reply_text(
            "❌ اکانت متصل نیست. ابتدا اکانت خود را وصل کنید.",
            reply_markup=main_kb(update.effective_user.id, False),
        )
        context.user_data.pop("awaiting_storage_target", None)
        return

    target_id = 0
    target_title = text

    try:
        from telethon import utils
        entity = await client.get_entity(text)
        target_id = utils.get_peer_id(entity)

        if hasattr(entity, "title") and entity.title:
            target_title = entity.title
        elif hasattr(entity, "first_name") and entity.first_name:
            target_title = entity.first_name

        logger.info(f"Resolved storage: {text} -> {target_id} ({target_title})")

    except Exception as e:
        logger.warning(f"Resolve failed for {text}: {e}")
        await update.message.reply_text(
            f"❌ کانال یا گروه «<b>{safe_text}</b>» پیدا نشد.\n\n"
            f"📌 <b>دلایل احتمالی:</b>\n"
            f"۱. اکانت سلف‌بات شما هنوز عضو این کانال نشده است.\n"
            f"۲. یوزرنیم یا آیدی وارد شده اشتباه است.\n"
            f"۳. کانال حذف شده یا خصوصی است.\n\n"
            f"لطفاً مجدداً ارسال کنید یا برای انصراف /cancel بزنید.",
            parse_mode="HTML",
        )
        return

    if not target_id:
        await update.message.reply_text("❌ آیدی مقصد نامعتبر است. مجدداً تلاش کنید.")
        return

    safe_title = html.escape(str(target_title))
    safe_feature = html.escape(feature_name)

    try:
        await db.set_storage_target(
            user["id"], feature_name, "custom",
            target_id, target_title,
        )
        await db.audit_log(
            user["id"], "storage_set",
            f"{feature_name} -> {target_title} ({target_id})",
        )
    except Exception as e:
        logger.error(f"DB storage save failed: {e}")
        await update.message.reply_text("❌ خطا در ذخیره اطلاعات در دیتابیس.")
        context.user_data.pop("awaiting_storage_target", None)
        return

    context.user_data.pop("awaiting_storage_target", None)

    await update.message.reply_text(
        f"✅ مسیر ذخیره‌سازی «<b>{safe_feature}</b>» با موفقیت تنظیم شد:\n\n"
        f"📂 نام مقصد: <b>{safe_title}</b>\n"
        f"🆔 آیدی عددی: <code>{target_id}</code>",
        reply_markup=main_kb(update.effective_user.id, True),
        parse_mode="HTML",
    )


# ═══════════════════════════════════
# لاگین — کیبورد مجازی
# ═══════════════════════════════════


async def cb_connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)

    has_sub = await check_subscription(user)
    if not has_sub:
        await query.edit_message_text(
            t("no_subscription", price=f"{MONTHLY_PRICE_TOMAN:,}"),
            reply_markup=back_kb(),
        )
        return

    session = await db.get_session(user["id"])
    if session and session.get("status") in access.DEAD_STATUSES:
        # session باطل/منقضی‌شده نباید جلوی اتصال دوباره را بگیرد
        # (قبلاً کاربر برای همیشه «قبلاً متصل شده» می‌دید)
        await db.delete_session(user["id"])
        session = None
    if session:
        await query.edit_message_text(
            t("login_already") + f"\n📌 {access.status_label(session)}",
            reply_markup=back_kb(),
        )
        return

    if not check_rate_limit(
        user["telegram_id"], "login", MAX_LOGIN_ATTEMPTS, 300
    ):
        await query.edit_message_text(t("login_too_many"), reply_markup=back_kb())
        return

    # ست کردن state
    context.user_data["awaiting_phone"] = True
    await query.edit_message_text(t("login_start"))


async def handle_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت شماره تلفن"""
    user = await get_or_create_user(update)
    raw_phone = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    phone = validate_phone(raw_phone)
    if not phone:
        await safe_send(update.effective_chat, t("login_invalid_phone"))
        return

    context.user_data["login_phone"] = phone
    context.user_data["login_phone_hash"] = hash_phone(phone)
    # حذف awaiting_phone چون به مرحله کد میریم
    context.user_data.pop("awaiting_phone", None)

    try:
        phone_code_hash = await client_manager.request_login_code(
            user_db_id=user["id"],
            phone=phone,
        )
        context.user_data["phone_code_hash"] = phone_code_hash
    except ValueError as e:
        await safe_send(
            update.effective_chat,
            t("login_failed", error=str(e)),
            reply_markup=main_kb(update.effective_user.id, False),
        )
        context.user_data.clear()
        return
    except Exception as e:
        logger.error(f"Login code error user {user['id']}: {type(e).__name__}: {e}")
        await client_manager.cleanup_pending(user["id"])
        context.user_data.clear()
        await safe_send(
            update.effective_chat,
            t("login_failed", error="خطا در ارسال کد"),
            reply_markup=main_kb(update.effective_user.id, False),
        )
        return

    await db.audit_log(user["id"], "login_code_sent", "")
    context.user_data["entered_code"] = ""

    try:
        await safe_send(
            update.effective_chat,
            code_entry_text(""),
            reply_markup=numpad_kb(""),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Numpad display failed user {user['id']}: {e}")


async def cb_code_digit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کاربر یک رقم از کیبورد مجازی زد"""
    query = update.callback_query

    digit = query.data.replace("code_", "")
    entered = context.user_data.get("entered_code", "")

    if len(entered) >= 5:
        await query.answer("کد کامل وارد شده")
        return

    entered += digit
    context.user_data["entered_code"] = entered

    await query.answer(f"رقم {digit}")

    # آپدیت نمایش
    await query.edit_message_text(
        code_entry_text(entered),
        reply_markup=numpad_kb(entered),
        parse_mode="Markdown",
    )

    # اگر ۵ رقم کامل شد → لاگین
    if len(entered) == 5:
        await _process_code(update, context, entered)


async def cb_code_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمه پاک کردن (Backspace)"""
    query = update.callback_query

    entered = context.user_data.get("entered_code", "")

    if entered:
        entered = entered[:-1]
        context.user_data["entered_code"] = entered

    await query.answer()
    await query.edit_message_text(
        code_entry_text(entered),
        reply_markup=numpad_kb(entered),
        parse_mode="Markdown",
    )


async def cb_code_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمه لغو از کیبورد مجازی"""
    query = update.callback_query
    await query.answer()

    user = await get_or_create_user(update)
    await client_manager.cleanup_pending(user["id"])
    context.user_data.clear()

    await query.edit_message_text(
        "❌ ورود لغو شد.",
        reply_markup=main_kb(update.effective_user.id, False),
    )


async def _process_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    """پردازش کد ۵ رقمی بعد از تکمیل شدن"""
    query = update.callback_query
    user = await get_or_create_user(update)

    # نمایش loading
    await query.edit_message_text(
        "⏳ **در حال بررسی کد...**",
        parse_mode="Markdown",
    )

    try:
        result = await client_manager.complete_login(
            user_db_id=user["id"],
            code=code,
        )
    except ValueError as e:
        context.user_data.clear()
        await query.edit_message_text(
            t("login_failed", error=str(e)),
            reply_markup=main_kb(update.effective_user.id, False),
        )
        return
    except Exception as e:
        logger.error(f"Login verify failed user {user['id']}: {type(e).__name__}: {e}")
        await client_manager.cleanup_pending(user["id"])
        context.user_data.clear()
        await query.edit_message_text(
            t("login_failed", error="کد اشتباه یا منقضی شده"),
            reply_markup=main_kb(update.effective_user.id, False),
        )
        return

    if result == "2fa_required":
        await query.edit_message_text(
            t("login_2fa", timeout=LOGIN_TIMEOUT),
        )
        # 2FA از طریق متن عادی وارد می‌شود
        context.user_data["awaiting_2fa"] = True
        return

    # لاگین موفق
    try:
        await _finalize_and_save(user, context)
        await query.edit_message_text(
            t("login_success"),
            reply_markup=main_kb(update.effective_user.id, True),
        )
    except Exception as e:
        logger.error(f"Finalize failed user {user['id']}: {type(e).__name__}: {e}")
        await query.edit_message_text(
            t("login_failed", error="خطا در ذخیره سشن"),
            reply_markup=main_kb(update.effective_user.id, False),
        )


async def handle_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Router واحد برای همه پیام‌های متنی PV
    بر اساس state در user_data تصمیم می‌گیره
    """
    # اولویت ۰: ادمین در حال نوشتن علت رد درخواست
    if context.user_data.get("awaiting_reject_req"):
        if is_admin_id(update.effective_user.id):
            await handle_reject_reason(update, context)
        else:
            context.user_data.pop("awaiting_reject_req", None)
        return

    # اولویت ۱: جستجوی چت برای فوروارد
    if context.user_data.get("awaiting_fwd_search"):
        await handle_fwd_search_input(update, context)
        return

    # اولویت ۲: ورود دستی مبدأ/مقصد فوروارد
    if context.user_data.get("awaiting_fwd_manual"):
        await handle_fwd_manual_input(update, context)
        return

    # اولویت ۳: storage target
    if context.user_data.get("awaiting_storage_target"):
        await handle_storage_target_input(update, context)
        return

    # اولویت ۲: monitor source
    if context.user_data.get("awaiting_mon_source"):
        await handle_mon_source_input(update, context)
        return

    # اولویت ۳: monitor dest
    if context.user_data.get("awaiting_mon_dest"):
        await handle_mon_dest_input(update, context)
        return

    # اولویت ۴: login phone
    if context.user_data.get("awaiting_phone"):
        await handle_phone_input(update, context)
        return

    # اولویت ۵: 2FA
    if context.user_data.get("awaiting_2fa"):
        await handle_2fa_input(update, context)
        return

    # هیچ state ای نیست → نادیده بگیر
    return


async def handle_2fa_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش 2FA"""
    user = await get_or_create_user(update)
    raw_password = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    password = validate_2fa_password(raw_password)
    if not password:
        await safe_send(update.effective_chat, "❌ رمز نامعتبر. دوباره وارد کنید:")
        return

    try:
        await client_manager.complete_2fa(
            user_db_id=user["id"],
            password=password,
        )
    except Exception as e:
        logger.error(f"2FA failed user {user['id']}: {type(e).__name__}: {e}")
        await client_manager.cleanup_pending(user["id"])
        context.user_data.clear()
        await safe_send(
            update.effective_chat,
            t("login_failed", error="رمز دوعاملی اشتباه"),
            reply_markup=main_kb(update.effective_user.id, False),
        )
        return

    try:
        await _finalize_and_save(user, context)
        await safe_send(
            update.effective_chat,
            t("login_success"),
            reply_markup=main_kb(update.effective_user.id, True),
        )
    except Exception as e:
        logger.error(f"Finalize 2FA failed user {user['id']}: {type(e).__name__}: {e}")
        await safe_send(
            update.effective_chat,
            t("login_failed", error="خطا در ذخیره سشن"),
            reply_markup=main_kb(update.effective_user.id, False),
        )


async def _finalize_and_save(user: dict, context: ContextTypes.DEFAULT_TYPE):
    session_string = await client_manager.finalize_login(user["id"])

    try:
        await db.save_session(
            user_id=user["id"],
            phone_hash=context.user_data.get("login_phone_hash", ""),
            session_data_enc=encrypt(session_string),
            # api_id/api_hash سراسری‌اند (config)؛ نگه‌داشتن نسخه‌ی تکراری برای
            # هر session فقط سطح نشت را بزرگ می‌کرد
            api_id_enc="",
            api_hash_enc="",
        )
        await db.update_session_status(user["id"], "connected")
        await db.audit_log(user["id"], "login_success", "")
    except Exception:
        from core.plugin_manager import unload_all_for_user
        try:
            await unload_all_for_user(user["id"])
        except Exception:
            pass
        try:
            await client_manager.disconnect_client(user["id"])
        except Exception:
            pass
        try:
            await db.delete_session(user["id"])
        except Exception:
            pass
        context.user_data.clear()
        raise

    context.user_data.clear()


# ═══════════════════════════════════
# قطع اکانت
# ═══════════════════════════════════


async def cb_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚠️ آیا مطمئنید؟\nتمام قابلیت‌ها غیرفعال می‌شوند.",
        reply_markup=confirm_kb("disconnect"),
    )


async def cb_confirm_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)

    session = await db.get_session(user["id"])
    session_string = None
    if session and session.get("session_data_enc"):
        try:
            session_string = decrypt(session["session_data_enc"])
        except Exception:
            session_string = None

    # توقف فوروارد/پلاگین‌ها، سپس خروج واقعی از تلگرام تا session
    # روی سرور تلگرام هم باطل شود (نه فقط قطع اتصال محلی)
    try:
        await access.stop_forward_jobs(user["id"])
        from core.plugin_manager import unload_all_for_user
        await unload_all_for_user(user["id"])
        await client_manager.logout_session(user["id"], session_string)
    except Exception as e:
        logger.error(f"Disconnect error: {e}")
    await client_manager.disconnect_client(user["id"])

    await db.delete_session(user["id"])
    await db.audit_log(user["id"], "disconnect", "logout")
    await query.edit_message_text(
        "✅ اکانت قطع شد و session سلف‌بات از تلگرام هم خارج شد.",
        reply_markup=main_kb(update.effective_user.id, False),
    )


# ═══════════════════════════════════
# راهنما
# ═══════════════════════════════════


# پیاده‌سازی در bot/help_panel.py (محتوا: bot/help_content.py)


# ═══════════════════════════════════
# Cancel
# ═══════════════════════════════════


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو هر عملیاتی + پاکسازی کامل state"""
    user = await get_or_create_user(update)

    # پاکسازی pending login
    try:
        await client_manager.cleanup_pending(user["id"])
    except Exception:
        pass

    # پاکسازی همه stateها
    context.user_data.clear()

    session = await db.get_session(user["id"])
    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_kb(update.effective_user.id, session is not None),
    )

# ═══════════════════════════════════
# ادمین
# ═══════════════════════════════════


async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /activate <telegram_id> <days>")
        return
    try:
        target_id = int(args[0])
        days = int(args[1])
        if not 1 <= days <= 3650:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Invalid args")
        return
    from core.security import calc_plan_expiry
    user = await db.get_user(target_id)
    if not user:
        await update.message.reply_text("User not found")
        return
    # اگر اشتراک فعلی هنوز فعال است، روزها به آن اضافه می‌شود
    expires = calc_plan_expiry(days, user.get("plan_expires_at"))
    await db.update_user(target_id, plan="premium", plan_expires_at=expires)
    await db.audit_log(user["id"], "subscription_activated", f"days={days}")
    state = await access.sync_user(user["id"])
    await update.message.reply_text(
        f"✅ اشتراک فعال شد\nکاربر: {target_id}\nروز: {days}\n"
        f"انقضا: {expires.strftime('%Y-%m-%d')}\nسلف‌بات: {state}"
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return
    users = await db.get_all_users()
    if not users:
        await update.message.reply_text("No users")
        return

    lines = []
    for i, u in enumerate(users[:100], 1):
        has_sub = await check_subscription(u)
        sub = "💎" if has_sub else "⚪"
        name = html.escape((u.get("first_name") or "")[:24])
        uname = html.escape(u.get("username") or "-")
        lines.append(f"{i}. {sub} <code>{u['telegram_id']}</code> {name} @{uname}")

    text = "👥 <b>کاربران</b> ({}):\n\n".format(len(users)) + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML")

# ═══════════════════════════════════
# مانیتور کانال — پنل
# ═══════════════════════════════════

STATE_MON_SOURCE = 10
STATE_MON_DEST = 11


async def cb_monitor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست مسیرهای مانیتور"""
    query = update.callback_query
    await query.answer()

    user = await get_or_create_user(update)
    routes = await db.get_all_channel_routes(user["id"])

    await query.edit_message_text(
        "📡 **مانیتور کانال**\n\n"
        "هر کانال را به یک مقصد وصل کنید.\n"
        "با دکمه ✅/❌ فعال/غیرفعال کنید.\n"
        "با 🗑 حذف کنید.",
        reply_markup=monitor_menu_kb(routes),
        parse_mode="Markdown",
    )


async def cb_mon_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فعال/غیرفعال کردن یک مسیر"""
    query = update.callback_query

    src_id = int(query.data.replace("mon_toggle_", ""))
    user = await get_or_create_user(update)

    new_state = await db.toggle_channel_route(user["id"], src_id)
    status = "فعال ✅" if new_state else "غیرفعال ❌"
    await query.answer(f"مسیر {status} شد", show_alert=False)

    # ── اطلاع به پلاگین ──
    from core.client_manager import get_client
    client = await get_client(user["id"])
    if client:
        from core.plugin_manager import get_active_plugins
        plugins = get_active_plugins(user["id"])
        monitor = plugins.get("channel_monitor")
        if monitor:
            await monitor.reload_routes()

    # رفرش منو
    routes = await db.get_all_channel_routes(user["id"])
    await query.edit_message_text(
        "📡 **مانیتور کانال**\n\n"
        "هر کانال را به یک مقصد وصل کنید.\n"
        "با دکمه ✅/❌ فعال/غیرفعال کنید.\n"
        "با 🗑 حذف کنید.",
        reply_markup=monitor_menu_kb(routes),
        parse_mode="Markdown",
    )


async def cb_mon_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید حذف مسیر"""
    query = update.callback_query
    await query.answer()

    src_id = int(query.data.replace("mon_delete_", ""))
    await query.edit_message_text(
        f"⚠️ آیا مسیر مانیتور `{src_id}` حذف شود?",
        reply_markup=mon_confirm_delete_kb(src_id),
        parse_mode="Markdown",
    )


async def cb_mon_confirm_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف قطعی مسیر"""
    query = update.callback_query

    src_id = int(query.data.replace("mon_confirm_del_", ""))
    user = await get_or_create_user(update)

    await db.delete_channel_route(user["id"], src_id)

    # اطلاع به پلاگین
    from core.client_manager import get_client
    client = await get_client(user["id"])
    if client:
        from core.plugin_manager import get_active_plugins
        plugins = get_active_plugins(user["id"])
        monitor = plugins.get("channel_monitor")
        if monitor:
            await monitor.reload_routes()

    await query.answer("✅ حذف شد", show_alert=True)

    routes = await db.get_all_channel_routes(user["id"])
    await query.edit_message_text(
        "📡 **مانیتور کانال**\n\n"
        "هر کانال را به یک مقصد وصل کنید.",
        reply_markup=monitor_menu_kb(routes),
        parse_mode="Markdown",
    )


async def cb_mon_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع اضافه کردن مسیر جدید"""
    query = update.callback_query
    user = await get_or_create_user(update)
    if not await check_subscription(user):
        await query.answer("❌ اشتراک فعال ندارید", show_alert=True)
        return
    await query.answer()

    context.user_data["awaiting_mon_source"] = True

    await query.edit_message_text(
        "📡 <b>اضافه کردن مسیر جدید</b>\n\n"
        "آیدی یا یوزرنیم <b>کانال منبع</b> را بفرستید:\n\n"
        "مثال: <code>@channel_name</code> یا <code>-1001234567890</code>\n\n"
        "/cancel برای انصراف",
        parse_mode="HTML",
    )


async def handle_mon_source_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت کانال منبع"""
    text = update.message.text.strip()
    safe_text = html.escape(text)

    # اعتبارسنجی فرمت
    if not (text.startswith("@") or text.lstrip("-").isdigit()):
        await update.message.reply_text(
            "❌ <b>فرمت نامعتبر است.</b>\n\n"
            "لطفاً یکی از این فرمت‌ها را بفرستید:\n"
            "• یوزرنیم با @ (مثل <code>@channel</code>)\n"
            "• آیدی عددی (مثل <code>-1001234567890</code>)\n\n"
            "/cancel برای انصراف",
            parse_mode="HTML",
        )
        return

    context.user_data["mon_source_ref"] = text
    context.user_data.pop("awaiting_mon_source", None)
    context.user_data["awaiting_mon_dest"] = True

    await update.message.reply_text(
        f"✅ منبع دریافت شد: <code>{safe_text}</code>\n\n"
        f"حالا آیدی یا یوزرنیم <b>مقصد</b> را بفرستید:\n\n"
        f"مثال: <code>@dest_channel</code> یا <code>-1001234567890</code>\n\n"
        f"/cancel برای انصراف",
        parse_mode="HTML",
    )


async def handle_mon_dest_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت مقصد و ثبت مسیر"""
    user = await get_or_create_user(update)
    src_ref = context.user_data.get("mon_source_ref", "")
    dst_ref = update.message.text.strip()

    # اعتبارسنجی
    if not src_ref:
        await update.message.reply_text(
            "❌ منبع مشخص نیست. از اول شروع کنید.",
            reply_markup=main_kb(update.effective_user.id, True),
        )
        context.user_data.clear()
        return

    if not (dst_ref.startswith("@") or dst_ref.lstrip("-").isdigit()):
        await update.message.reply_text(
            "❌ <b>فرمت مقصد نامعتبر است.</b>\n\n"
            "لطفاً یوزرنیم با @ یا آیدی عددی بفرستید.\n\n"
            "/cancel برای انصراف",
            parse_mode="HTML",
        )
        return

    from core.client_manager import get_client
    client = await get_client(user["id"])

    if not client:
        await update.message.reply_text(
            "❌ اکانت متصل نیست.",
            reply_markup=main_kb(update.effective_user.id, False),
        )
        context.user_data.clear()
        return

    try:
        from telethon import utils
        src_entity = await client.get_entity(src_ref)
        dst_entity = await client.get_entity(dst_ref)

        src_id = utils.get_peer_id(src_entity)
        dst_id = utils.get_peer_id(dst_entity)
        src_title = getattr(src_entity, "title", src_ref)
        dst_title = getattr(dst_entity, "title", dst_ref)

        await db.set_channel_route(
            user["id"], src_id, src_title, "custom", dst_id, dst_title
        )

        from core.plugin_manager import get_active_plugins
        plugins = get_active_plugins(user["id"])
        monitor = plugins.get("channel_monitor")
        if monitor:
            await monitor.reload_routes()

        safe_src_title = html.escape(str(src_title))
        safe_dst_title = html.escape(str(dst_title))

        await update.message.reply_text(
            f"✅ مسیر ثبت شد:\n"
            f"📥 منبع: <b>{safe_src_title}</b>\n"
            f"📤 مقصد: <b>{safe_dst_title}</b>",
            reply_markup=main_kb(update.effective_user.id, True),
            parse_mode="HTML",
        )
        logger.info(f"Monitor route added: {src_id} -> {dst_id}")

    except Exception as e:
        logger.error(f"Monitor add failed: {e}")
        await update.message.reply_text(
            f"❌ خطا در ثبت مسیر:\n<code>{html.escape(str(e)[:200])}</code>\n\n"
            f"مطمئن شو اکانتت عضو کانال‌هاست.",
            parse_mode="HTML",
        )

    context.user_data.clear()



# ═══════════════════════════════════
# ثبت هندلرها
# ═══════════════════════════════════


async def notify_job_progress(user_db_id: int, job, row: dict = None):
    """
    اطلاع‌رسانی پیشرفت job ادامه‌یافته به کاربر.
    (وقتی سرور ری‌استارت می‌شود، job خودکار ادامه پیدا می‌کند و اینجا
    پیشرفت به کاربر گزارش می‌شود)
    """
    from telegram import Bot
    from config import BOT_TOKEN

    chat_id = job.chat_id or (row or {}).get("chat_id")
    message_id = job.message_id or (row or {}).get("message_id")
    if not chat_id:
        return

    from core import forwarder
    from core import cache_forward

    # job دو مرحله‌ای (کش روی سرور) خلاصه‌ی خودش را دارد
    if isinstance(job, cache_forward.CacheForwardJob):
        stats = None
        if job.db_id and job.phase == "send":
            try:
                stats = await db.cache_stats(job.db_id)
            except Exception as e:
                logger.debug(f"cache stats skipped: {e}")
        text = cache_forward.cache_job_summary(job, stats)
    else:
        text = forwarder.job_summary(job)

    from bot.keyboards import fwd_running_kb, fwd_done_kb
    markup = (
        fwd_running_kb(job) if job.state in ("running", "waiting")
        else fwd_done_kb(paused=job.state != "done")
    )

    bot = Bot(BOT_TOKEN)
    try:
        if message_id:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=text, reply_markup=markup, parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.debug(f"job progress notify skipped: {e}")
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


# ═══════════════════════════════════
# دروازه‌ی مسدودی — قبل از همه‌ی هندلرها
# ═══════════════════════════════════

_BAN_CACHE: dict[int, tuple[float, bool]] = {}
_BAN_TTL = 20.0


def invalidate_ban_cache(telegram_id: int) -> None:
    _BAN_CACHE.pop(telegram_id, None)


async def _is_banned(telegram_id: int) -> bool:
    import time as _time
    now = _time.monotonic()
    hit = _BAN_CACHE.get(telegram_id)
    if hit and now - hit[0] < _BAN_TTL:
        return hit[1]
    user = await db.get_user(telegram_id)
    banned = bool(user and user.get("is_banned"))
    if len(_BAN_CACHE) > 5000:
        _BAN_CACHE.clear()
    _BAN_CACHE[telegram_id] = (now, banned)
    return banned


async def ban_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    کاربر مسدود هیچ هندلری را اجرا نمی‌کند (قبلاً مسدودی فقط یک فلگ در
    DB بود و کاربر همچنان منوها، خرید و فوروارد را داشت).
    """
    tg_user = update.effective_user
    if not tg_user or is_admin_id(tg_user.id):
        return
    try:
        banned = await _is_banned(tg_user.id)
    except Exception as e:
        logger.debug(f"ban gate skipped: {e}")
        return
    if not banned:
        return

    try:
        if update.callback_query:
            await update.callback_query.answer("⛔ حساب شما مسدود است.", show_alert=True)
        elif update.inline_query:
            await update.inline_query.answer([], cache_time=0, is_personal=True)
        elif update.effective_message and update.effective_chat and \
                update.effective_chat.type == "private" and \
                check_rate_limit(tg_user.id, "ban_notice", 1, 3600):
            await update.effective_message.reply_text("⛔ حساب شما مسدود است.")
    except Exception:
        pass
    raise ApplicationHandlerStop


def register_handlers(app: Application):
    """ثبت همه هندلرها با اولویت درست"""

    # ── 0. مسدودی (group=-1 → قبل از همه) ──
    app.add_handler(TypeHandler(Update, ban_gate), group=-1)

    # ── 1. Commands ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("users", cmd_users))

    # ── 2. Callback queries ──
    # کیبورد لاگین
    app.add_handler(CallbackQueryHandler(cb_code_digit, pattern=r"^code_[0-9]$"))
    app.add_handler(CallbackQueryHandler(cb_code_back, pattern="^code_back$"))
    app.add_handler(CallbackQueryHandler(cb_code_cancel, pattern="^code_cancel$"))

    # noop
    async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
    app.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))

    # منوی اصلی
    app.add_handler(CallbackQueryHandler(cb_back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(cb_connect, pattern="^connect$"))
    app.add_handler(CallbackQueryHandler(cb_panel, pattern="^panel$"))
    app.add_handler(CallbackQueryHandler(cb_features, pattern="^features$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_feature, pattern=r"^toggle_"))

    # ذخیره‌سازی — ترتیب مهمه!
    app.add_handler(CallbackQueryHandler(cb_storage, pattern="^storage$"))
    app.add_handler(CallbackQueryHandler(cb_monitor_menu, pattern="^storage_monitor_menu$"))
    app.add_handler(CallbackQueryHandler(cb_recents_clear, pattern="^recents_clear$"))
    app.add_handler(CallbackQueryHandler(cb_recents_clear_ok, pattern="^recents_clear_ok$"))
    app.add_handler(CallbackQueryHandler(cb_storage_target_saved, pattern=r"^starget_.+_saved$"))
    app.add_handler(CallbackQueryHandler(cb_storage_target_custom, pattern=r"^starget_.+_custom$"))
    app.add_handler(CallbackQueryHandler(cb_storage_target_owned, pattern=r"^starget_.+_own$"))
    app.add_handler(CallbackQueryHandler(cb_storage_owned_page, pattern=r"^starget_.+_own_p\d+$"))
    app.add_handler(CallbackQueryHandler(cb_storage_owned_pick, pattern=r"^starget_.+_own_i\d+$"))
    app.add_handler(CallbackQueryHandler(cb_storage_feature, pattern=r"^storage_"))

    # مانیتور
    app.add_handler(CallbackQueryHandler(cb_mon_add, pattern="^mon_add$"))
    app.add_handler(CallbackQueryHandler(cb_mon_toggle, pattern=r"^mon_toggle_"))
    app.add_handler(CallbackQueryHandler(cb_mon_delete, pattern=r"^mon_delete_"))
    app.add_handler(CallbackQueryHandler(cb_mon_confirm_del, pattern=r"^mon_confirm_del_"))

    # سایر
    app.add_handler(CallbackQueryHandler(cb_subscription, pattern="^subscription$"))
    app.add_handler(CallbackQueryHandler(cb_status, pattern="^status$"))
    app.add_handler(CallbackQueryHandler(cb_disconnect, pattern="^disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_confirm_disconnect, pattern="^confirm_disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(cb_help_nav, pattern=r"^hlp:"))

    # راهنمای inline (پشتوانه‌ی `.راهنما` در همه‌ی چت‌ها)
    app.add_handler(InlineQueryHandler(inline_help, pattern=r"^help"))

    # ── 3. خرید اشتراک ──
    app.add_handler(CallbackQueryHandler(cb_buy_plan, pattern=r"^buyplan_"))
    app.add_handler(CallbackQueryHandler(cb_send_receipt, pattern=r"^sendreceipt_"))
    app.add_handler(CallbackQueryHandler(cb_my_requests, pattern="^my_requests$"))

    # ── 4. پنل ادمین ──
    app.add_handler(CallbackQueryHandler(cb_admin, pattern="^admin$"))
    app.add_handler(CallbackQueryHandler(cb_admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(cb_admin_requests, pattern="^admin_requests$"))
    app.add_handler(CallbackQueryHandler(cb_admin_users, pattern=r"^admin_users_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_user, pattern=r"^admin_user_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_approve, pattern=r"^adm_ok_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_reject, pattern=r"^adm_no_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_grant, pattern=r"^admsub_"))
    app.add_handler(CallbackQueryHandler(cb_admin_cancel_sub, pattern=r"^admcancel_"))
    app.add_handler(CallbackQueryHandler(cb_admin_ban, pattern=r"^admban_"))

    # ── 5. فوروارد محتوا ──
    app.add_handler(CallbackQueryHandler(cb_fwd_start, pattern="^fwd_start$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_nav, pattern=r"^fwd_show_(src|dst)$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_page, pattern=r"^fwd_(src|dst)_p\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_pick, pattern=r"^fwd_(src|dst)_i\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_filter, pattern="^fwd_flt$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_search, pattern="^fwd_srch$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_clear_search, pattern="^fwd_srch_clr$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_dst_saved, pattern="^fwd_dst_saved$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_manual, pattern=r"^fwd_(src|dst)_manual$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_join, pattern=r"^fwd_join_"))
    app.add_handler(CallbackQueryHandler(cb_fwd_cancel_join, pattern="^fwd_cancel_join$"))
    app.add_handler(CallbackQueryHandler(
        cb_fwd_opt, pattern=r"^fwd_(limit|mode|media|cache|cachemedia|speed)$"
    ))
    app.add_handler(CallbackQueryHandler(cb_fwd_go, pattern="^fwd_go$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_stop, pattern=r"^fwd_stop_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_resume, pattern="^fwd_resume$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_unlock, pattern="^fwd_unlock$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_delete, pattern="^fwd_delete$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_delete_confirm, pattern="^fwd_delete_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_fwd_delete_cancel, pattern="^fwd_delete_cancel$"))

    # ── 6. عکس رسید پرداخت ──
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.IMAGE) & filters.ChatType.PRIVATE,
        handle_receipt_photo,
    ))

    # ── 7. Text Router (state-based) ──
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_text_router,
    ))

    logger.info("All handlers registered")