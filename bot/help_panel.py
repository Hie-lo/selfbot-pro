"""
راهنمای دکمه‌ای (دکمه‌های شیشه‌ای)

چرا از طریق ربات؟
  اکانت کاربر (Telethon) نمی‌تواند دکمه‌ی inline بفرستد و CallbackQuery هم
  فقط به ربات‌ها می‌رسد — پیاده‌سازی قبلی `.راهنما` به همین دلیل هیچ‌وقت
  دکمه نشان نمی‌داد. راه درست (مثل همه‌ی سلف‌بات‌های حرفه‌ای):

    `.راهنما` در هر چت
      → اکانت کاربر یک inline query به ربات کنترلی می‌زند (@bot help)
      → نتیجه را در همان چت ارسال می‌کند (پیام «via bot» با دکمه)
      → کلیک دکمه‌ها به همین ربات می‌رسد و پیام inline ویرایش می‌شود

  پیش‌نیاز: inline mode ربات در @BotFather روشن باشد (/setinline).
  اگر نباشد، `.راهنما` به نسخه‌ی متنی برمی‌گردد.

callback_data:  hlp:<section>:<owner_tg_id>
  owner = آیدی عددی کسی که راهنما را باز کرده؛ بقیه اجازه‌ی کلیک ندارند.
  owner = 0 → داخل چت خصوصی ربات (فقط خود کاربر آن را می‌بیند)
"""

import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LinkPreviewOptions,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot import help_content as H
from core import runtime
from database import db

logger = logging.getLogger("bot.help")

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
CLOSE_KEY = "x"


def _cb(key: str, owner: int) -> str:
    return f"hlp:{key}:{owner}"


def help_markup(key: str, owner: int = 0) -> InlineKeyboardMarkup:
    """ساخت کیبورد یک صفحه‌ی راهنما"""
    rows_keys, parent = H.layout_for(key)
    rows: list[list[InlineKeyboardButton]] = []

    for row in rows_keys:
        rows.append([
            InlineKeyboardButton(H.section_title(k), callback_data=_cb(k, owner))
            for k in row
        ])

    nav: list[InlineKeyboardButton] = []
    if parent:
        if parent != "main":
            nav.append(InlineKeyboardButton("🔙 بازگشت", callback_data=_cb(parent, owner)))
        nav.append(InlineKeyboardButton("🏠 صفحه اصلی", callback_data=_cb("main", owner)))
    if owner:
        nav.append(InlineKeyboardButton("✖️ بستن", callback_data=_cb(CLOSE_KEY, owner)))
    elif key == "main":
        nav.append(InlineKeyboardButton("🔙 منوی ربات", callback_data="back_main"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _collapsed_markup(owner: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 باز کردن راهنما", callback_data=_cb("main", owner)),
    ]])


async def _edit(query, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=markup,
            link_preview_options=_NO_PREVIEW,
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# ═══════════════════════════════════
# دکمه «📖 راهنما» در منوی ربات
# ═══════════════════════════════════


async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _edit(query, H.MAIN_TEXT, help_markup("main", 0))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help در چت ربات"""
    await update.message.reply_text(
        H.MAIN_TEXT, parse_mode="HTML",
        reply_markup=help_markup("main", 0),
        link_preview_options=_NO_PREVIEW,
    )


async def cb_help_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیمایش بین بخش‌ها — هم در چت ربات و هم روی پیام inline در هر چت"""
    query = update.callback_query
    try:
        _, key, owner_s = query.data.split(":", 2)
        owner = int(owner_s)
    except (ValueError, AttributeError):
        await query.answer()
        return

    if owner and query.from_user.id != owner:
        await query.answer(
            "🔒 این راهنما متعلق به کاربر دیگری است.\n"
            "برای خودت در هر چت بفرست: .راهنما",
            show_alert=True,
        )
        return

    if key == CLOSE_KEY:
        await query.answer()
        await _edit(query, "📖 راهنمای SelfBot Pro (بسته شد)", _collapsed_markup(owner))
        return

    if key != "main" and key not in H.SECTIONS:
        await query.answer("بخش پیدا نشد", show_alert=True)
        return

    await query.answer()
    await _edit(query, H.section_text(key), help_markup(key, owner))


# ═══════════════════════════════════
# Inline mode — پشتوانه‌ی `.راهنما` در همه‌ی چت‌ها
# ═══════════════════════════════════


async def _allowed_inline(tg_id: int) -> bool:
    """
    فقط اکانت‌های سلف‌بات متصل و کاربران ثبت‌شده‌ی ربات.
    (اکانتی که به سلف‌بات وصل شده ممکن است با اکانتی که با ربات چت
    می‌کند فرق داشته باشد؛ برای همین هر دو چک می‌شوند.)
    """
    if tg_id in runtime.selfbot_accounts:
        return True
    try:
        return bool(await db.get_user(tg_id))
    except Exception:
        return False


async def inline_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iq = update.inline_query
    if not iq:
        return

    if not await _allowed_inline(iq.from_user.id):
        await iq.answer([], cache_time=60, is_personal=True)
        return

    parts = (iq.query or "").split(maxsplit=1)
    key = "main"
    if len(parts) > 1:
        key = H.resolve_section(parts[1]) or "main"

    owner = iq.from_user.id
    title = "📖 راهنمای SelfBot Pro" if key == "main" else f"📖 {H.section_title(key)}"
    result = InlineQueryResultArticle(
        id=f"help-{key}",
        title=title,
        description="راهنمای کامل با دکمه‌های شیشه‌ای",
        input_message_content=InputTextMessageContent(
            H.section_text(key), parse_mode="HTML",
            link_preview_options=_NO_PREVIEW,
        ),
        reply_markup=help_markup(key, owner),
    )
    await iq.answer([result], cache_time=30, is_personal=True)
