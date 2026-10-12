"""
پنل «کاربر» — کارت پروفایل و دکمه‌ها (سمت ربات کنترلی).

کار اصلی (پیدا کردن کاربر، ساخت پیش‌نویس، ارسال پیام) در
plugins/user_info.py انجام می‌شود؛ این‌جا فقط رابط کاربری است.

قاعده‌ی مهم این فایل: هیچ‌وقت خودش پیام به طرف مقابل نمی‌فرستد.
تنها جایی که ارسال انجام می‌شود، دکمه‌ی «✅ ارسال کن» است که کاربر
خودش می‌زند. (خواسته‌ی صریح کاربر: «نمی‌خوام به کاربر چیزی ارسال بشه
و مطلع بشه»)
"""

from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("user_panel")

MAX_TEXT = 4000

# متن پیش‌نویسِ پیش‌فرض. پیش‌نویس را فقط خودت می‌بینی و طرف مقابل هیچ
# اطلاعی نمی‌گیرد؛ ولی اگر روزی اشتباهی «ارسال» شد، این متن بی‌خطر است.
DEFAULT_DRAFT = "🤍"


def _esc(value) -> str:
    return html.escape(str(value or ""))


def _mention(uid: int, label: str) -> str:
    """لینک منشن — در اپ تلگرام با یک فشار باز می‌شود (هیچ پیامی نمی‌رود)"""
    return f'<a href="tg://user?id={int(uid)}">{label}</a>'


# ═══════════ متن کارت ═══════════

def card_text(info: dict, note: str = "") -> str:
    """کارت پروفایل (یا کارت «پیدا نشد»)"""
    if not info.get("ok"):
        lines = ["🔍 <b>کاربر پیدا نشد</b>", ""]
        if info.get("target"):
            lines.append(f"ورودی: <code>{_esc(info['target'])}</code>")
        if info.get("reason"):
            lines.append(f"دلیل: {_esc(info['reason'])}")
        lines += [
            "",
            "💡 <b>چرا؟</b> تلگرام آیدیِ کسی را که هیچ نقطه‌ی مشترکی با اکانتت "
            "ندارد (نه چت، نه مخاطب، نه گروه مشترک) به ربات نمی‌دهد — این "
            "محدودیت خود تلگرام است، نه ربات.",
            "• اگر یوزرنیم طرف را داری: <code>.کاربر @username</code>",
            "• اگر قبلاً چت داشته‌اید و پاکش کرده‌ای: "
            "<code>.کاربر ‹آیدی› کامل</code> (جست‌وجوی عمیق‌تر)",
        ]
        if info.get("hint"):
            lines += ["", _esc(info["hint"])]
        if note:
            lines += ["", note]
        return "\n".join(lines)

    uid = int(info["id"])
    name = info.get("name") or "بدون نام"
    badges = ""
    if info.get("verified"):
        badges += " ☑️"
    if info.get("bot"):
        badges += " 🤖"
    if info.get("premium"):
        badges += " 💎"
    if info.get("scam"):
        badges += " ⚠️"
    if info.get("fake"):
        badges += " 🚫"

    lines = [f"👤 <b>{_esc(name)}</b>{badges}", ""]
    lines.append(f"🆔 آیدی: <code>{uid}</code>")
    if info.get("username"):
        lines.append(f"🔗 یوزرنیم: @{_esc(info['username'])}")
    lines.append("👆 " + _mention(uid, "باز کردن پروفایل (بدون ارسال پیام)"))

    phone = info.get("phone")
    lines.append(f"📞 شماره: <code>{_esc(phone)}</code>" if phone
                 else "📞 شماره: <i>مخفی (حریم خصوصی)</i>")

    if info.get("last_seen"):
        lines.append(f"🕐 آخرین بازدید: {_esc(info['last_seen'])}")

    bio = (info.get("bio") or "").strip()
    lines.append("📝 بیو: " + (f"<i>{_esc(bio[:400])}</i>" if bio
                               else "<i>خالی یا مخفی</i>"))

    ch = info.get("personal_channel") or None
    if ch:
        piece = _esc(ch.get("title") or "کانال شخصی")
        if ch.get("username"):
            piece += f" — @{_esc(ch['username'])}"
        elif ch.get("id"):
            piece += f" — <code>{_esc(ch['id'])}</code>"
        lines.append(f"📢 کانال شخصی: {piece}")

    if info.get("common_chats") is not None:
        lines.append(f"👥 چت‌های مشترک: {_esc(info['common_chats'])}")

    if info.get("blocked"):
        lines.append("🚫 <b>تو این کاربر را بلاک کرده‌ای</b>")

    state = info.get("has_dialog")
    if state is True:
        lines.append("💬 چت: <b>در لیستت هست</b> ✅")
    elif state is False:
        lines.append("💬 چت: <b>در لیستت نیست</b> — با «پیش‌نویس» یا ارسال پیام ساخته می‌شود")
    else:
        lines.append("💬 چت: <i>نامعلوم</i>")

    if info.get("msgs"):
        extra = f"📨 {int(info['msgs'])} پیام در سابقه"
        if (info.get("kind") or "") == "group":
            extra += " (از گپ‌ها — موقت)"
        lines.append(extra)
    if info.get("msg_link"):
        lines.append(f"↩️ <a href=\"{_esc(info['msg_link'])}\">باز کردن آخرین "
                     "پیامش</a>")

    if info.get("how"):
        lines.append(f"🔎 پیدا شد: <i>{_esc(info['how'])}</i>")

    lines += [
        "",
        "🛡 هیچ پیامی خودکار فرستاده نمی‌شود. «آماده‌کردن چت» فقط یک "
        "<b>پیش‌نویس</b> می‌سازد: طرف مقابل هیچ اطلاعی نمی‌گیرد و چت در "
        "لیست تلگرام خودت بالا می‌آید.",
    ]
    if note:
        lines += ["", note]
    return "\n".join(lines)


# ═══════════ کیبوردها ═══════════

def card_kb(uid: int | None, username: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if uid:
        rows.append([InlineKeyboardButton(
            "📂 آماده‌کردن چت (بدون ارسال پیام)", callback_data=f"u:draft:{uid}")])
        rows.append([
            InlineKeyboardButton("✉️ نوشتن پیام", callback_data=f"u:msg:{uid}"),
            InlineKeyboardButton("🔗 لینک چت", callback_data=f"u:link:{uid}"),
        ])
        if username:
            rows.append([InlineKeyboardButton(
                "🌐 باز کردن با یوزرنیم", url=f"https://t.me/{username}")])
    rows.append([InlineKeyboardButton("🗑 بستن", callback_data=f"u:close:{uid or 0}")])
    return InlineKeyboardMarkup(rows)


def anti_delete_kb(uid: int, link: str | None = None) -> InlineKeyboardMarkup:
    """
    دکمه‌های کارتِ «پیام حذف‌شده» — برای وقتی طرف چتش را پاک کرده و
    می‌خواهی دوباره پیام بدهی یا چت را بی‌صدا باز کنی.

    link: لینک مستقیم همان پیام حذف‌شده (اگر داشته باشیم) — با یک ضربه
    پیام در تلگرام باز می‌شود.
    """
    rows = [[InlineKeyboardButton("✉️ پیام بده", callback_data=f"u:msg:{uid}")],
            [InlineKeyboardButton("📂 آماده‌کردن چت (بی‌صدا)",
                                  callback_data=f"u:draft:{uid}")]]
    bottom = [InlineKeyboardButton("🔗 لینک چت", callback_data=f"u:link:{uid}"),
              InlineKeyboardButton("🗑 بستن", callback_data=f"u:close:{uid}")]
    rows.append(bottom)
    if link:
        rows.insert(0, [InlineKeyboardButton("↩️ باز کردن همان پیام", url=link)])
    return InlineKeyboardMarkup(rows)


def confirm_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ارسال کن", callback_data=f"u:send:{uid}"),
        InlineKeyboardButton("✏️ عوض کن", callback_data=f"u:msg:{uid}"),
    ], [
        InlineKeyboardButton("❌ لغو", callback_data=f"u:abort:{uid}"),
    ]])


def cancel_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ لغو", callback_data=f"u:abort:{uid}"),
    ]])


# ═══════════ ابزارها ═══════════

def _plugin(user_id: int):
    """پلاگین کاربر (کار واقعی آن‌جاست)"""
    try:
        from core.plugin_manager import get_active_plugins
        return get_active_plugins(user_id).get("user_info")
    except Exception as e:                      # noqa: BLE001
        logger.debug(f"plugin lookup failed: {e}")
        return None


async def _edit(q, text: str, kb=None) -> None:
    try:
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML",
                                  disable_web_page_preview=True)
    except Exception as e:                      # noqa: BLE001
        logger.debug(f"edit failed: {e}")
        try:
            await q.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


# ═══════════ مسیریاب دکمه‌ها ═══════════

async def cb_user(update, context, user_id: int) -> bool:
    """دکمه‌های u:* را مدیریت می‌کند. True = مصرف شد."""
    q = update.callback_query
    data = (q.data or "") if q else ""
    if not data.startswith("u:"):
        return False
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    uid = (int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit()
           else None)

    if action == "close":
        try:
            await q.message.delete()
        except Exception:
            await q.answer("بسته شد")
        return True

    plugin = _plugin(user_id)

    if action == "draft":
        if plugin is None:
            await q.answer("قابلیت کاربر بالا نیامده — ربات را ری‌استارت کن",
                           show_alert=True)
            return True
        await q.answer("دارم پیش‌نویس می‌سازم…")
        res = await plugin.prepare_chat(uid)
        if res.get("ok"):
            await _edit(
                q,
                "✅ <b>چت آماده شد</b>\n\n"
                f"• مقصد: <code>{uid}</code>\n"
                f"• متن پیش‌نویس: <code>{_esc(res.get('draft'))}</code>\n\n"
                "برو در تلگرام؛ چت این کاربر بالای لیستت با برچسب "
                "«پیش‌نویس» دیده می‌شود و می‌توانی همان‌جا تایپ کنی.\n"
                "🛡 <b>هیچ پیامی برای طرف فرستاده نشد و او هیچ اطلاعی نگرفت.</b>",
                kb=card_kb(uid))
        else:
            await _edit(q, f"❌ پیش‌نویس ساخته نشد.\nدلیل: {_esc(res.get('reason'))}",
                        kb=card_kb(uid))
        return True

    if action == "link":
        if plugin is None:
            await q.answer("قابلیت کاربر بالا نیامده", show_alert=True)
            return True
        links = plugin.chat_links(uid)
        text = ["🔗 <b>لینک‌های این کاربر</b>", ""]
        if links.get("mention"):
            text.append(links["mention"])
        text.append("• باز کردن چت: <code>" + _esc(links.get("deep")) + "</code>")
        if links.get("username_url"):
            text.append("• با یوزرنیم: " + _esc(links["username_url"]))
        text += ["", "این لینک هیچ پیامی نمی‌فرستد؛ فقط چت/پروفایل را باز می‌کند."]
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id,
                                           text="\n".join(text),
                                           parse_mode="HTML",
                                           disable_web_page_preview=True)
            await q.answer("لینک فرستاده شد")
        except Exception:
            await q.answer(links.get("deep") or "لینک ساخته نشد", show_alert=True)
        return True

    if action == "msg":
        context.user_data["awaiting_user_msg"] = uid
        context.user_data.pop("user_msg_text", None)
        await _edit(
            q,
            "✍️ <b>متن پیام را بنویس و همین‌جا بفرست</b>\n\n"
            f"مقصد: <code>{uid}</code>\n\n"
            "بعد از نوشتن، متن را نشانت می‌دهم با دکمه‌ی «✅ ارسال کن». "
            "تا آن دکمه را نزنی <b>هیچ چیزی برای طرف نمی‌رود</b>.",
            kb=cancel_kb(uid))
        await q.answer()
        return True

    if action == "abort":
        context.user_data.pop("awaiting_user_msg", None)
        context.user_data.pop("user_msg_text", None)
        context.user_data.pop("user_msg_target", None)
        await _edit(q, "❌ لغو شد. هیچ پیامی فرستاده نشد.", kb=card_kb(uid))
        await q.answer("لغو شد")
        return True

    if action == "send":
        text = (context.user_data.get("user_msg_text") or "").strip()
        if not text:
            await q.answer("متنی ذخیره نشده — دکمه‌ی «نوشتن پیام» را بزن",
                           show_alert=True)
            return True
        if plugin is None:
            await q.answer("قابلیت کاربر بالا نیامده", show_alert=True)
            return True
        await q.answer("دارم می‌فرستم…")
        res = await plugin.send_message(uid, text)
        if res.get("ok"):
            context.user_data.pop("user_msg_text", None)
            context.user_data.pop("user_msg_target", None)
            await _edit(
                q,
                "✅ <b>پیام فرستاده شد</b>\n\n"
                f"• مقصد: <code>{uid}</code>\n"
                f"• شناسه پیام: <code>{_esc(res.get('msg_id'))}</code>\n\n"
                "⚠️ این یک پیام واقعی بود و طرف مقابل اعلانش را گرفت — "
                "تلگرام برای پیوی «ارسال بی‌صدا» ندارد. برای حالتی که "
                "نمی‌خواهی مطلع شود، «آماده‌کردن چت» (پیش‌نویس) را بزن.",
                kb=card_kb(uid, res.get("username")))
        else:
            await q.answer(f"❌ ارسال نشد: {res.get('reason')}", show_alert=True)
        return True

    await q.answer()
    return True


# ═══════════ ورودی متن (پیام دستی) ═══════════

async def handle_user_text(update, context, user_id: int) -> bool:
    """متنی که برای ارسال دستی نوشته شده را می‌گیرد. True = مصرف شد."""
    uid = context.user_data.get("awaiting_user_msg")
    if not uid:
        return False

    text = (update.message.text or "").strip()[:MAX_TEXT]
    if not text:
        await update.message.reply_text("متن خالی بود؛ دوباره بنویس:")
        return True

    context.user_data.pop("awaiting_user_msg", None)
    # پیام خودت را پاک کن (حریم خصوصی) — متن فقط در کارت می‌ماند
    try:
        await update.message.delete()
    except Exception:
        pass

    context.user_data["user_msg_text"] = text
    context.user_data["user_msg_target"] = uid
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=("📝 <b>پیش‌نمایش پیام</b>\n\n"
                  f"مقصد: <code>{uid}</code>\n\n"
                  f"{_esc(text)}\n\n"
                  "با «✅ ارسال کن» از اکانت خودت برای همین کاربر فرستاده "
                  "می‌شود (و اعلان می‌گیرد)."),
            reply_markup=confirm_kb(uid),
            parse_mode="HTML")
    except Exception as e:                      # noqa: BLE001
        logger.error(f"preview send failed: {e}")
        await update.message.reply_text("خطا در ساخت پیش‌نمایش؛ دوباره تلاش کن.")
    return True
