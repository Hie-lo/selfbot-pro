"""
هندلرهای ربات تلگرام — نسخه کیبورد مجازی
"""

import asyncio
import logging
from datetime import datetime, timezone

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
    FEATURES,
)
from database import db
from core.security import (
    validate_phone,
    validate_2fa_password,
    check_rate_limit,
    hash_phone,
)
from core.crypto import encrypt
from core import client_manager

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

    # noop برای دکمه‌های غیرفعال
    if feature_name == "noop" or query.data == "noop":
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
    await db.audit_log(
        user["id"], "feature_toggle",
        f"{feature_name} -> {'ON' if new_state else 'OFF'}",
    )

    # load/unload plugin در لحظه
    from core.client_manager import get_client
    from core.plugin_manager import enable_plugin, disable_plugin

    client = await get_client(user["id"])
    if client:
        if new_state:
            await enable_plugin(user["id"], feature_name, client)
        else:
            await disable_plugin(user["id"], feature_name)

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
    query = update.callback_query
    await query.answer()
    try:
        feature_name = extract_storage_feature(query.data, "_custom")
    except ValueError:
        await query.edit_message_text(t("error_general"), reply_markup=back_kb("storage"))
        return ConversationHandler.END
    context.user_data["storage_feature"] = feature_name
    await query.edit_message_text(
        "📢 آیدی عددی یا یوزرنیم مقصد:\n\n"
        "مثال: `-1001234567890` یا `@channel`\n\n"
        "/cancel برای انصراف",
        parse_mode="Markdown",
    )
    return STATE_STORAGE_TARGET


async def handle_storage_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update)
    feature_name = context.user_data.get("storage_feature")
    if not feature_name:
        await update.message.reply_text(t("error_general"), reply_markup=back_kb())
        return ConversationHandler.END
    text = update.message.text.strip()
    target_id = 0
    target_title = text
    if text.startswith("@"):
        target_title = text
    elif text.lstrip("-").isdigit():
        target_id = int(text)
        target_title = str(target_id)
    else:
        await update.message.reply_text("❌ فرمت نامعتبر.")
        return STATE_STORAGE_TARGET
    await db.set_storage_target(user["id"], feature_name, "custom", target_id, target_title)
    await db.audit_log(user["id"], "storage_set", f"{feature_name} -> {target_title}")
    await update.message.reply_text(
        t("storage_set", feature=feature_name, target=target_title),
        reply_markup=main_menu_kb(True),
    )
    context.user_data.pop("storage_feature", None)
    return ConversationHandler.END


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
            user_db_id=user["id"],
            phone=phone,
        )
        context.user_data["phone_code_hash"] = phone_code_hash
    except ValueError as e:
        await safe_send(
            update.effective_chat,
            t("login_failed", error=str(e)),
            reply_markup=main_menu_kb(False),
        )
        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Login code error user {user['id']}: {type(e).__name__}: {e}")
        await client_manager.cleanup_pending(user["id"])
        context.user_data.clear()
        await safe_send(
            update.effective_chat,
            t("login_failed", error="خطا در ارسال کد"),
            reply_markup=main_menu_kb(False),
        )
        return ConversationHandler.END

    await db.audit_log(user["id"], "login_code_sent", "")

    # نمایش کیبورد مجازی
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

    # از اینجا به بعد conversation تمام می‌شود
    # و ادامه لاگین از طریق callback query (کیبورد مجازی) انجام می‌شود
    return ConversationHandler.END


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
        reply_markup=main_menu_kb(False),
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
            reply_markup=main_menu_kb(False),
        )
        return
    except Exception as e:
        logger.error(f"Login verify failed user {user['id']}: {type(e).__name__}: {e}")
        await client_manager.cleanup_pending(user["id"])
        context.user_data.clear()
        await query.edit_message_text(
            t("login_failed", error="کد اشتباه یا منقضی شده"),
            reply_markup=main_menu_kb(False),
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
            reply_markup=main_menu_kb(True),
        )
    except Exception as e:
        logger.error(f"Finalize failed user {user['id']}: {type(e).__name__}: {e}")
        await query.edit_message_text(
            t("login_failed", error="خطا در ذخیره سشن"),
            reply_markup=main_menu_kb(False),
        )


async def handle_2fa_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    هندلر پیام متنی برای 2FA
    فقط وقتی فعاله که awaiting_2fa = True باشه
    """
    if not context.user_data.get("awaiting_2fa"):
        return

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
            reply_markup=main_menu_kb(False),
        )
        return

    try:
        await _finalize_and_save(user, context)
        await safe_send(
            update.effective_chat,
            t("login_success"),
            reply_markup=main_menu_kb(True),
        )
    except Exception as e:
        logger.error(f"Finalize 2FA failed user {user['id']}: {type(e).__name__}: {e}")
        await safe_send(
            update.effective_chat,
            t("login_failed", error="خطا در ذخیره سشن"),
            reply_markup=main_menu_kb(False),
        )


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
        "⚠️ آیا مطمئنید؟\nتمام قابلیت‌ها غیرفعال می‌شوند.",
        reply_markup=confirm_kb("disconnect"),
    )


async def cb_confirm_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_or_create_user(update)
    try:
        await client_manager.disconnect_client(user["id"])
    except Exception as e:
        logger.error(f"Disconnect error: {e}")
    from core.plugin_manager import unload_all_for_user
    await unload_all_for_user(user["id"])
    await db.delete_session(user["id"])
    await db.audit_log(user["id"], "disconnect", "")
    await query.edit_message_text(
        "✅ اکانت قطع شد.",
        reply_markup=main_menu_kb(False),
    )


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
    await client_manager.cleanup_pending(user["id"])
    context.user_data.clear()
    session = await db.get_session(user["id"])
    await update.message.reply_text(
        "❌ لغو شد.",
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
    from datetime import timedelta
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
# ثبت هندلرها
# ═══════════════════════════════════


def register_handlers(app: Application):
    # لاگین conversation (فقط شماره و 2FA از طریق متن)
    login_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_connect, pattern="^connect$"),
        ],
        states={
            STATE_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
        ],
        per_user=True,
        conversation_timeout=LOGIN_TIMEOUT,
    )

    # storage conversation
    storage_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_storage_target_custom, pattern=r"^starget_.+_custom$"),
        ],
        states={
            STATE_STORAGE_TARGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_storage_target_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
        ],
        per_user=True,
    )
    # noop — برای دکمه‌های غیرقابل کلیک
    async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()

    app.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("users", cmd_users))

    app.add_handler(login_conv)
    app.add_handler(storage_conv)

    # کیبورد مجازی — ارقام
    app.add_handler(CallbackQueryHandler(cb_code_digit, pattern=r"^code_[0-9]$"))
    app.add_handler(CallbackQueryHandler(cb_code_back, pattern="^code_back$"))
    app.add_handler(CallbackQueryHandler(cb_code_cancel, pattern="^code_cancel$"))

    # 2FA از طریق پیام متنی
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_2fa_message,
    ))

    # بقیه callbacks
    app.add_handler(CallbackQueryHandler(cb_back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(cb_panel, pattern="^panel$"))
    app.add_handler(CallbackQueryHandler(cb_features, pattern="^features$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_feature, pattern=r"^toggle_"))
    app.add_handler(CallbackQueryHandler(cb_storage, pattern="^storage$"))
    app.add_handler(CallbackQueryHandler(cb_storage_feature, pattern=r"^storage_"))
    app.add_handler(CallbackQueryHandler(cb_storage_target_saved, pattern=r"^starget_.+_saved$"))
    app.add_handler(CallbackQueryHandler(cb_subscription, pattern="^subscription$"))
    app.add_handler(CallbackQueryHandler(cb_status, pattern="^status$"))
    app.add_handler(CallbackQueryHandler(cb_disconnect, pattern="^disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_confirm_disconnect, pattern="^confirm_disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))

    logger.info("All handlers registered")