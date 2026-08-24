"""
هندلرهای ربات تلگرام — نسخه نهایی بدون ConversationHandler برای storage/monitor
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.error import NetworkError, TimedOut, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from config import ADMIN_TELEGRAM_ID, MONTHLY_PRICE_TOMAN, LOGIN_TIMEOUT
from bot.texts import t
from bot.keyboards import (
    main_menu_kb,
    features_kb,
    storage_menu_kb,
    storage_target_kb,
    confirm_kb,
    back_kb,
    numpad_kb,
    code_entry_text,
    monitor_menu_kb,
    mon_confirm_delete_kb,
    FEATURES,
    ALL_FEATURES,
)
from database import db
from core.security import (
    validate_phone,
    validate_telegram_code,
    validate_2fa_password,
    check_rate_limit,
    hash_phone,
)
from core.crypto import encrypt
from core import client_manager

logger = logging.getLogger("bot.handlers")

# ── Conversation States (فقط برای لاگین) ──
STATE_PHONE = 0


# ═══════════════════════════════════
# Helpers
# ═══════════════════════════════════

async def check_subscription(user: dict) -> bool:
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
        reply_markup=main_menu_kb(session is not None),
    )


# ═══════════════════════════════════
# Callbacks: منو
# ═══════════════════════════════════

async def cb_back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    user = await get_or_create_user(update)
    session = await db.get_session(user["id"])
    name = update.effective_user.first_name or "کاربر"
    await query.edit_message_text(
        t("welcome", name=name),
        reply_markup=main_menu_kb(session is not None),
    )


async def cb_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    has_sub = await check_subscription(user)
    if has_sub:
        text = t("subscription_active", expires=str(user["plan_expires_at"])[:10])
    else:
        text = t("no_subscription", price=f"{MONTHLY_PRICE_TOMAN:,}")
    await query.edit_message_text(text, reply_markup=back_kb())


async def cb_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    session = await db.get_session(user["id"])
    if session:
        status = t("status_connected")
        status += f"\n📌 وضعیت: {session['status']}"
        if session.get("error_message"):
            status += f"\n⚠️ {session['error_message']}"
    else:
        status = t("status_disconnected")
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
    status = t("status_connected") if session["is_connected"] else t("status_disconnected")
    has_sub = await check_subscription(user)
    plan_text = "✅ فعال" if has_sub else "❌ ندارید"
    await query.edit_message_text(
        t("panel_title", status=status, plan=plan_text),
        reply_markup=main_menu_kb(True),
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
    if query.data == "noop" or feature_name == "noop":
        await query.answer()
        return
    user = await get_or_create_user(update)
    has_sub = await check_subscription(user)
    if not has_sub:
        await query.answer("❌ اشتراک ندارید", show_alert=True)
        return
    is_on = await db.is_feature_enabled(user["id"], feature_name)
    new_state = not is_on
    await db.set_feature(user["id"], feature_name, new_state)
    await db.audit_log(user["id"], "feature_toggle", f"{feature_name} -> {'ON' if new_state else 'OFF'}")

    from core.client_manager import get_client
    from core.plugin_manager import enable_plugin, disable_plugin
    client = await get_client(user["id"])
    if client:
        if new_state:
            await enable_plugin(user["id"], feature_name, client)
        else:
            await disable_plugin(user["id"], feature_name)

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
# ذخیره‌سازی — بدون ConversationHandler
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
    from bot.keyboards import STORAGE_FEATURES
    fname = dict(STORAGE_FEATURES).get(feature_name, feature_name)
    await query.edit_message_text(
        f"📂 **{fname}**\n\nمسیر فعلی: {current}\n\nمقصد جدید:",
        reply_markup=storage_target_kb(feature_name),
        parse_mode="Markdown",
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


async def cb_storage_target_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    دکمه چنل/گروه — فقط state ست می‌کنه و پیام می‌ده
    هیچ ConversationHandler درگیر نیست
    """
    query = update.callback_query
    await query.answer()

    try:
        feature_name = extract_storage_feature(query.data, "_custom")
    except ValueError:
        logger.error(f"Invalid callback data: {query.data}")
        await query.edit_message_text(t("error_general"), reply_markup=back_kb("storage"))
        return

    # ست کردن state ساده
    context.user_data["awaiting_storage_target"] = feature_name

    logger.info(f"Storage target mode activated for feature={feature_name}, user={query.from_user.id}")

    await query.edit_message_text(
        f"📢 **تنظیم مقصد «{feature_name}»**\n\n"
        f"آیدی عددی یا یوزرنیم مقصد را بفرستید:\n\n"
        f"مثال:\n"
        f"`-1001234567890`\n"
        f"`@my_channel`\n\n"
        f"برای انصراف /cancel بزنید.",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════
# مانیتور کانال — بدون ConversationHandler
# ═══════════════════════════════════

async def cb_monitor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    routes = await db.get_all_channel_routes(user["id"])
    await query.edit_message_text(
        "📡 **مانیتور کانال**\n\n"
        "هر کانال را به یک مقصد وصل کنید.",
        reply_markup=monitor_menu_kb(routes),
        parse_mode="Markdown",
    )


async def cb_mon_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    src_id = int(query.data.replace("mon_toggle_", ""))
    user = await get_or_create_user(update)
    new_state = await db.toggle_channel_route(user["id"], src_id)
    status = "فعال ✅" if new_state else "غیرفعال ❌"
    await query.answer(f"مسیر {status} شد", show_alert=False)
    from core.client_manager import get_client
    from core.plugin_manager import get_active_plugins
    client = await get_client(user["id"])
    if client:
        plugins = get_active_plugins(user["id"])
        monitor = plugins.get("channel_monitor")
        if monitor:
            await monitor.reload_routes()
    routes = await db.get_all_channel_routes(user["id"])
    await query.edit_message_text(
        "📡 **مانیتور کانال**",
        reply_markup=monitor_menu_kb(routes),
        parse_mode="Markdown",
    )


async def cb_mon_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    src_id = int(query.data.replace("mon_delete_", ""))
    await query.edit_message_text(
        f"⚠️ حذف مسیر `{src_id}`?",
        reply_markup=mon_confirm_delete_kb(src_id),
        parse_mode="Markdown",
    )


async def cb_mon_confirm_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    src_id = int(query.data.replace("mon_confirm_del_", ""))
    user = await get_or_create_user(update)
    await db.delete_channel_route(user["id"], src_id)
    from core.client_manager import get_client
    from core.plugin_manager import get_active_plugins
    client = await get_client(user["id"])
    if client:
        plugins = get_active_plugins(user["id"])
        monitor = plugins.get("channel_monitor")
        if monitor:
            await monitor.reload_routes()
    await query.answer("✅ حذف شد", show_alert=True)
    routes = await db.get_all_channel_routes(user["id"])
    await query.edit_message_text(
        "📡 **مانیتور کانال**",
        reply_markup=monitor_menu_kb(routes),
        parse_mode="Markdown",
    )


async def cb_mon_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمه اضافه کردن مسیر — state ست می‌کنه"""
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_monitor_source"] = True
    logger.info(f"Monitor add mode activated, user={query.from_user.id}")
    await query.edit_message_text(
        "📡 **اضافه کردن مسیر**\n\n"
        "آیدی یا یوزرنیم **کانال منبع** را بفرستید:\n\n"
        "مثال: `@channel` یا `-1001234567890`\n\n"
        "/cancel برای انصراف",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════
# لاگین — ConversationHandler (فقط لاگین)
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
        return ConversationHandler.END
    session = await db.get_session(user["id"])
    if session:
        await query.edit_message_text(t("login_already"), reply_markup=back_kb())
        return ConversationHandler.END
    if not check_rate_limit(user["telegram_id"], "login", 3, 300):
        await query.edit_message_text(t("login_too_many"), reply_markup=back_kb())
        return ConversationHandler.END
    await query.edit_message_text(t("login_start"))
    return STATE_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update)
    raw_phone = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    phone = validate_phone(raw_phone)
    if not phone:
        await safe_send(update.effective_chat, t("login_invalid_phone"))
        return STATE_PHONE

    context.user_data["login_phone"] = phone
    context.user_data["login_phone_hash"] = hash_phone(phone)

    try:
        phone_code_hash = await client_manager.request_login_code(
            user_db_id=user["id"], phone=phone,
        )
        context.user_data["phone_code_hash"] = phone_code_hash
    except ValueError as e:
        await safe_send(update.effective_chat, t("login_failed", error=str(e)), reply_markup=main_menu_kb(False))
        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Login code error user {user['id']}: {type(e).__name__}: {e}")
        await client_manager.cleanup_pending(user["id"])
        context.user_data.clear()
        await safe_send(update.effective_chat, t("login_failed", error="خطا در ارسال کد"), reply_markup=main_menu_kb(False))
        return ConversationHandler.END

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
        logger.warning(f"Numpad display failed: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════
# کیبورد مجازی لاگین
# ═══════════════════════════════════

async def cb_code_digit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    digit = query.data.replace("code_", "")
    entered = context.user_data.get("entered_code", "")
    if len(entered) >= 5:
        await query.answer("کد کامل شده")
        return
    entered += digit
    context.user_data["entered_code"] = entered
    await query.answer(f"رقم {digit}")
    await query.edit_message_text(
        code_entry_text(entered),
        reply_markup=numpad_kb(entered),
        parse_mode="Markdown",
    )
    if len(entered) == 5:
        await _process_code(update, context, entered)


async def cb_code_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    await client_manager.cleanup_pending(user["id"])
    context.user_data.clear()
    await query.edit_message_text("❌ ورود لغو شد.", reply_markup=main_menu_kb(False))


async def _process_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    query = update.callback_query
    user = await get_or_create_user(update)
    await query.edit_message_text("⏳ **در حال بررسی...**", parse_mode="Markdown")

    try:
        result = await client_manager.complete_login(user_db_id=user["id"], code=code)
    except ValueError as e:
        context.user_data.clear()
        await query.edit_message_text(t("login_failed", error=str(e)), reply_markup=main_menu_kb(False))
        return
    except Exception as e:
        logger.error(f"Login verify failed: {e}")
        await client_manager.cleanup_pending(user["id"])
        context.user_data.clear()
        await query.edit_message_text(t("login_failed", error="کد اشتباه یا منقضی"), reply_markup=main_menu_kb(False))
        return

    if result == "2fa_required":
        context.user_data["awaiting_2fa"] = True
        await query.edit_message_text(t("login_2fa", timeout=LOGIN_TIMEOUT))
        return

    try:
        await _finalize_and_save(user, context)
        await query.edit_message_text(t("login_success"), reply_markup=main_menu_kb(True))
    except Exception as e:
        logger.error(f"Finalize failed: {e}")
        await query.edit_message_text(t("login_failed", error="خطا در ذخیره سشن"), reply_markup=main_menu_kb(False))


# ═══════════════════════════════════
# Finalize & Save
# ═══════════════════════════════════

async def _finalize_and_save(user: dict, context: ContextTypes.DEFAULT_TYPE):
    from config import TELEGRAM_API_ID, TELEGRAM_API_HASH
    session_string = await client_manager.finalize_login(user["id"])
    try:
        await db.save_session(
            user_id=user["id"],
            phone_hash=context.user_data.get("login_phone_hash", ""),
            session_data_enc=encrypt(session_string),
            api_id_enc=encrypt(str(TELEGRAM_API_ID)),
            api_hash_enc=encrypt(TELEGRAM_API_HASH),
        )
        await db.update_session_status(user["id"], "connected")
        await db.audit_log(user["id"], "login_success", "")
    except Exception:
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
        "⚠️ آیا مطمئنید؟",
        reply_markup=confirm_kb("disconnect"),
    )


async def cb_confirm_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    from core.plugin_manager import unload_all_for_user
    await unload_all_for_user(user["id"])
    try:
        await client_manager.disconnect_client(user["id"])
    except Exception as e:
        logger.error(f"Disconnect error: {e}")
    await db.delete_session(user["id"])
    await db.audit_log(user["id"], "disconnect", "")
    await query.edit_message_text("✅ اکانت قطع شد.", reply_markup=main_menu_kb(False))


# ═══════════════════════════════════
# راهنما
# ═══════════════════════════════════

async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    help_text = """📖 **راهنما**

**شروع:**
۱. اشتراک تهیه کنید
۲. اکانت متصل کنید
۳. از پنل قابلیت‌ها را مدیریت کنید

**قابلیت‌ها:**
🎲 تاس · 📢 بنر · ⏳ تایم‌دار
📥 دانلود · 🗑 ضد حذف · ✏️ ضد ویرایش
🔗 ذخیره لینک · 🖼 استیکر · ❤️ قلب
📡 مانیتور · 💬 پاسخ خودکار · 📤 آپلود"""
    await query.edit_message_text(help_text, reply_markup=back_kb(), parse_mode="Markdown")


# ═══════════════════════════════════
# Cancel
# ═══════════════════════════════════

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update)
    try:
        await client_manager.cleanup_pending(user["id"])
    except Exception:
        pass
    context.user_data.clear()
    session = await db.get_session(user["id"])
    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_menu_kb(session is not None),
    )
    return ConversationHandler.END


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
    except ValueError:
        await update.message.reply_text("Invalid args")
        return
    user = await db.get_user(target_id)
    if not user:
        await update.message.reply_text("User not found")
        return
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    await db.update_user(target_id, plan="premium", plan_expires_at=expires)
    await db.audit_log(user["id"], "subscription_activated", f"days={days}")
    await update.message.reply_text(
        f"✅ اشتراک فعال شد\nکاربر: {target_id}\nروز: {days}\nانقضا: {expires.strftime('%Y-%m-%d')}"
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return
    users = await db.get_all_active_users()
    if not users:
        await update.message.reply_text("No users")
        return
    text = "👥 **کاربران:**\n\n"
    for i, u in enumerate(users, 1):
        has_sub = await check_subscription(u)
        sub = "💎" if has_sub else "⚪"
        text += f"{i}. {sub} `{u['telegram_id']}` {u.get('first_name', '')} @{u.get('username', '-')}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════
# Message Handler عمومی — پردازش state ها
# ═══════════════════════════════════

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    هندلر واحد برای همه پیام‌های متنی
    بر اساس state در user_data تصمیم می‌گیره
    """
    user = await get_or_create_user(update)
    text = update.message.text.strip()

    # ── State 1: Storage Target ──
    if context.user_data.get("awaiting_storage_target"):
        feature_name = context.user_data["awaiting_storage_target"]
        logger.info(f"Processing storage target input: {text} for feature={feature_name}")

        if not (text.startswith("@") or text.lstrip("-").isdigit()):
            await update.message.reply_text(
                "❌ فرمت نامعتبر. @username یا آیدی عددی بفرستید.\n/cancel برای انصراف",
            )
            return

        from core.client_manager import get_client
        client = await get_client(user["id"])
        if not client:
            await update.message.reply_text("❌ اکانت متصل نیست.", reply_markup=main_menu_kb(False))
            context.user_data.clear()
            return

        try:
            entity = await client.get_entity(text)
            target_id = entity.id
            target_title = getattr(entity, "title", "") or getattr(entity, "first_name", text)
            logger.info(f"Resolved: {text} -> {target_id} ({target_title})")
        except Exception as e:
            logger.warning(f"Resolve failed: {text}: {e}")
            await update.message.reply_text(
                f"❌ «{text}» پیدا نشد.\n"
                f"• عضو هستید؟\n"
                f"• یوزرنیم درسته؟\n\n"
                f"دوباره امتحان کنید یا /cancel",
            )
            return

        await db.set_storage_target(user["id"], feature_name, "custom", target_id, target_title)
        await db.audit_log(user["id"], "storage_set", f"{feature_name} -> {target_title} ({target_id})")
        context.user_data.clear()

        await update.message.reply_text(
            f"✅ مسیر «{feature_name}» تنظیم شد:\n📂 {target_title}\n🆔 `{target_id}`",
            reply_markup=main_menu_kb(True),
            parse_mode="Markdown",
        )
        return

    # ── State 2: Monitor Source ──
    if context.user_data.get("awaiting_monitor_source"):
        logger.info(f"Processing monitor source: {text}")
        context.user_data["mon_source_ref"] = text
        context.user_data.pop("awaiting_monitor_source", None)
        context.user_data["awaiting_monitor_dest"] = True
        await update.message.reply_text(
            f"✅ منبع: {text}\n\nحالا **مقصد** را بفرستید:\n/cancel برای انصراف",
        )
        return

    # ── State 3: Monitor Dest ──
    if context.user_data.get("awaiting_monitor_dest"):
        src_ref = context.user_data.get("mon_source_ref", "")
        dst_ref = text
        logger.info(f"Processing monitor dest: {dst_ref}")

        from core.client_manager import get_client
        client = await get_client(user["id"])
        if not client:
            await update.message.reply_text("❌ اکانت متصل نیست.")
            context.user_data.clear()
            return

        try:
            src_entity = await client.get_entity(src_ref)
            dst_entity = await client.get_entity(dst_ref)
            await db.set_channel_route(
                user["id"], src_entity.id,
                getattr(src_entity, "title", src_ref),
                "custom", dst_entity.id,
                getattr(dst_entity, "title", dst_ref),
            )
            from core.plugin_manager import get_active_plugins
            plugins = get_active_plugins(user["id"])
            monitor = plugins.get("channel_monitor")
            if monitor:
                await monitor.reload_routes()
            await update.message.reply_text(
                f"✅ مسیر ثبت شد:\n📥 {getattr(src_entity, 'title', src_ref)}\n📤 {getattr(dst_entity, 'title', dst_ref)}",
                reply_markup=main_menu_kb(True),
            )
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {str(e)[:100]}")

        context.user_data.clear()
        return

    # ── State 4: 2FA ──
    if context.user_data.get("awaiting_2fa"):
        password = validate_2fa_password(text)
        if not password:
            await update.message.reply_text("❌ رمز نامعتبر.")
            return

        try:
            await client_manager.complete_2fa(user_db_id=user["id"], password=password)
            await _finalize_and_save(user, context)
            await safe_send(update.effective_chat, t("login_success"), reply_markup=main_menu_kb(True))
        except Exception as e:
            logger.error(f"2FA failed: {e}")
            await client_manager.cleanup_pending(user["id"])
            context.user_data.clear()
            await safe_send(update.effective_chat, t("login_failed", error="رمز اشتباه"), reply_markup=main_menu_kb(False))
        return


# ═══════════════════════════════════
# Noop
# ═══════════════════════════════════

async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ═══════════════════════════════════
# ثبت هندلرها
# ═══════════════════════════════════

def register_handlers(app: Application):
    # 1. Login conversation (فقط شماره)
    login_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_connect, pattern="^connect$"),
        ],
        states={
            STATE_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        per_chat=True,
        conversation_timeout=LOGIN_TIMEOUT,
    )

    # 2. Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("users", cmd_users))

    # 3. Login conversation
    app.add_handler(login_conv)

    # 4. Numpad callbacks
    app.add_handler(CallbackQueryHandler(cb_code_digit, pattern=r"^code_[0-9]$"))
    app.add_handler(CallbackQueryHandler(cb_code_back, pattern="^code_back$"))
    app.add_handler(CallbackQueryHandler(cb_code_cancel, pattern="^code_cancel$"))

    # 5. Menu callbacks
    app.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(cb_back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(cb_panel, pattern="^panel$"))
    app.add_handler(CallbackQueryHandler(cb_features, pattern="^features$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_feature, pattern=r"^toggle_"))
    app.add_handler(CallbackQueryHandler(cb_subscription, pattern="^subscription$"))
    app.add_handler(CallbackQueryHandler(cb_status, pattern="^status$"))
    app.add_handler(CallbackQueryHandler(cb_disconnect, pattern="^disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_confirm_disconnect, pattern="^confirm_disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))

    # 6. Storage callbacks
    app.add_handler(CallbackQueryHandler(cb_storage, pattern="^storage$"))
    app.add_handler(CallbackQueryHandler(cb_storage_feature, pattern=r"^storage_"))
    app.add_handler(CallbackQueryHandler(cb_storage_target_saved, pattern=r"^starget_.+_saved$"))
    app.add_handler(CallbackQueryHandler(cb_storage_target_custom, pattern=r"^starget_.+_custom$"))

    # 7. Monitor callbacks
    app.add_handler(CallbackQueryHandler(cb_monitor_menu, pattern="^storage_monitor_menu$"))
    app.add_handler(CallbackQueryHandler(cb_mon_toggle, pattern=r"^mon_toggle_"))
    app.add_handler(CallbackQueryHandler(cb_mon_delete, pattern=r"^mon_delete_"))
    app.add_handler(CallbackQueryHandler(cb_mon_confirm_del, pattern=r"^mon_confirm_del_"))
    app.add_handler(CallbackQueryHandler(cb_mon_add, pattern="^mon_add$"))

    # 8. Message handler عمومی (آخرین اولویت)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_text_message,
    ))

    logger.info("All handlers registered")