"""
هندلرهای ربات تلگرام — نسخه کیبورد مجازی
"""
import html
import asyncio
import logging
from datetime import datetime, timezone
from bot.keyboards import monitor_menu_kb, mon_confirm_delete_kb
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
    ALL_FEATURES,
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
    fname = ALL_FEATURES.get(feature_name, feature_name)
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
# دریافت و پردازش آیدی/یوزرنیم کانال ارسالی کاربر
# ============================================================
async def handle_storage_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update)
    feature_name = context.user_data.get("awaiting_storage_target")
    
    if not feature_name:
        return  # پیام عادی است، نادیده بگیر

    text = update.message.text.strip()
    safe_text = html.escape(text)

    # اعتبارسنجی اولیه فرمت ورودی
    if not (text.startswith("@") or text.lstrip("-").isdigit()):
        await update.message.reply_text(
            "❌ <b>فرمت نامعتبر است.</b>\n"
            "لطفاً فقط یوزرنیم با @ یا آیدی عددی با -100 بفرستید.\n\n"
            "برای انصراف /cancel بزنید.",
            parse_mode="HTML"
        )
        return

    # دریافت کلاینت تلگرام کاربر برای بررسی دسترسی به کانال
    from core.client_manager import get_client
    client = await get_client(user["id"])

    if not client:
        await update.message.reply_text(
            "❌ اکانت متصل نیست. ابتدا اکانت خود را وصل کنید.",
            reply_markup=main_menu_kb(False),
        )
        context.user_data.pop("awaiting_storage_target", None)
        return

    target_id = 0
    target_title = text

    try:
        # بررسی وجود کانال/گروه در کلاینت کاربر (Resolve Entity)
        entity = await client.get_entity(text)
        target_id = entity.id

        if hasattr(entity, "title") and entity.title:
            target_title = entity.title
        elif hasattr(entity, "first_name") and entity.first_name:
            target_title = entity.first_name

        logger.info(f"Resolved storage target: {text} -> {target_id} ({target_title})")

    except Exception as e:
        logger.warning(f"Resolve failed for {text}: {e}")
        await update.message.reply_text(
            f"❌ کانال یا گروه «<b>{safe_text}</b>» پیدا نشد.\n\n"
            f"📌 <b>دلایل احتمالی:</b>\n"
            f"۱. اکانت سلف‌بات شما هنوز عضو این کانال نشده است.\n"
            f"۲. یوزرنیم یا آیدی وارد شده اشتباه است.\n\n"
            f"لطفاً مجدداً ارسال کنید یا برای انصراف /cancel بزنید.",
            parse_mode="HTML"
        )
        return

    if not target_id:
        await update.message.reply_text("❌ آیدی مقصد نامعتبر است. مجدداً تلاش کنید.")
        return

    # پاکسازی امن ورودی‌ها برای دیتابیس و نمایش
    safe_title = html.escape(target_title)
    safe_feature = html.escape(feature_name)

    # ذخیره در دیتابیس PostgreSQL
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

    # حذف وضعیت انتظار از حافظه موقت
    context.user_data.pop("awaiting_storage_target", None)

    # ارسال پیام موفقیت با فرمت امن HTML
    await update.message.reply_text(
        f"✅ مسیر ذخیره‌سازی «<b>{safe_feature}</b>» با موفقیت تنظیم شد:\n\n"
        f"📂 نام مقصد: <b>{safe_title}</b>\n"
        f"🆔 آیدی عددی: <code>{target_id}</code>",
        reply_markup=main_menu_kb(True),
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
 # اولویت ۱: storage target
    if context.user_data.get("awaiting_storage_target"):
        await handle_storage_target_input(update, context)
        return

    # اولویت ۲: monitor source
    if context.user_data.get("mon_source_ref"):
        # ... (کد قبلی monitor)
        return

    # اولویت ۳: 2FA
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
    """لغو هر عملیاتی"""
    user = await get_or_create_user(update)

    try:
        await client_manager.cleanup_pending(user["id"])
    except Exception:
        pass

    # پاکسازی همه state ها
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
    await query.answer()

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
    await query.answer()

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
    await query.answer()

    await query.edit_message_text(
        "📡 **اضافه کردن مسیر جدید**\n\n"
        "آیدی یا یوزرنیم **کانال منبع** را بفرستید:\n\n"
        "مثال: `@channel_name` یا `-1001234567890`\n\n"
        "/cancel برای انصراف",
        parse_mode="Markdown",
    )
    return STATE_MON_SOURCE


async def handle_mon_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت کانال منبع"""
    text = update.message.text.strip()

    context.user_data["mon_source_ref"] = text
    await update.message.reply_text(
        "✅ منبع دریافت شد.\n\n"
        "حالا آیدی یا یوزرنیم **مقصد** را بفرستید:\n\n"
        "مثال: `@dest_channel` یا `-1001234567890`\n\n"
        "/cancel برای انصراف",
        parse_mode="Markdown",
    )
    return STATE_MON_DEST


async def handle_mon_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت مقصد و ثبت مسیر"""
    user = await get_or_create_user(update)
    src_ref = context.user_data.get("mon_source_ref", "")
    dst_ref = update.message.text.strip()

    from core.client_manager import get_client
    client = await get_client(user["id"])

    if not client:
        await update.message.reply_text(
            "❌ اکانت متصل نیست.",
            reply_markup=main_menu_kb(False),
        )
        context.user_data.clear()
        return ConversationHandler.END

    try:
        src_entity = await client.get_entity(src_ref)
        dst_entity = await client.get_entity(dst_ref)

        src_id = src_entity.id
        dst_id = dst_entity.id
        src_title = getattr(src_entity, "title", src_ref)
        dst_title = getattr(dst_entity, "title", dst_ref)

        await db.set_channel_route(
            user["id"], src_id, src_title, "custom", dst_id, dst_title
        )

        # اطلاع به پلاگین
        from core.plugin_manager import get_active_plugins
        plugins = get_active_plugins(user["id"])
        monitor = plugins.get("channel_monitor")
        if monitor:
            await monitor.reload_routes()

        await update.message.reply_text(
            f"✅ مسیر ثبت شد:\n📥 منبع: {src_title}\n📤 مقصد: {dst_title}",
            reply_markup=main_menu_kb(True),
        )

    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)[:100]}")

    context.user_data.clear()
    return ConversationHandler.END

# ═══════════════════════════════════
# ثبت هندلرها
# ═══════════════════════════════════


def register_handlers(app: Application):
    # ── Conversations اول ──

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
        per_chat=True,
        conversation_timeout=LOGIN_TIMEOUT,
    )


    monitor_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_mon_add, pattern="^mon_add$"),
        ],
        states={
            STATE_MON_SOURCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mon_source),
            ],
            STATE_MON_DEST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mon_dest),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
        ],
        per_user=True,
        per_chat=True,
        conversation_timeout=300,
    )

    # ── ثبت به ترتیب اولویت ──

    # 1. Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("users", cmd_users))

    # 2. Conversations (باید قبل از MessageHandler عمومی باشن)
    app.add_handler(login_conv)
    app.add_handler(CallbackQueryHandler(cb_storage_target_custom, pattern=r"^starget_.+_custom$"))
    app.add_handler(monitor_conv)

    # 3. Callback queries
    app.add_handler(CallbackQueryHandler(cb_code_digit, pattern=r"^code_[0-9]$"))
    app.add_handler(CallbackQueryHandler(cb_code_back, pattern="^code_back$"))
    app.add_handler(CallbackQueryHandler(cb_code_cancel, pattern="^code_cancel$"))

    async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()

    app.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))

    app.add_handler(CallbackQueryHandler(cb_back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(cb_panel, pattern="^panel$"))
    app.add_handler(CallbackQueryHandler(cb_features, pattern="^features$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_feature, pattern=r"^toggle_"))
    app.add_handler(CallbackQueryHandler(cb_storage, pattern="^storage$"))
    app.add_handler(CallbackQueryHandler(cb_monitor_menu, pattern="^storage_monitor_menu$"))
    app.add_handler(CallbackQueryHandler(cb_mon_toggle, pattern=r"^mon_toggle_"))
    app.add_handler(CallbackQueryHandler(cb_mon_delete, pattern=r"^mon_delete_"))
    app.add_handler(CallbackQueryHandler(cb_mon_confirm_del, pattern=r"^mon_confirm_del_"))
    app.add_handler(CallbackQueryHandler(cb_storage_feature, pattern=r"^storage_"))
    app.add_handler(CallbackQueryHandler(cb_storage_target_saved, pattern=r"^starget_.+_saved$"))
    app.add_handler(CallbackQueryHandler(cb_subscription, pattern="^subscription$"))
    app.add_handler(CallbackQueryHandler(cb_status, pattern="^status$"))
    app.add_handler(CallbackQueryHandler(cb_disconnect, pattern="^disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_confirm_disconnect, pattern="^confirm_disconnect$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))

    # 4. Message handler عمومی (فقط برای 2FA — آخرین اولویت)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_2fa_message,
    ))

    logger.info("All handlers registered")