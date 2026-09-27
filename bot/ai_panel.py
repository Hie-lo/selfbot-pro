"""
پنل «🧠 هوش مصنوعی» در پیوی ربات کنترلی.

اینجا جای شخصیت‌نویسی است: پرسونا را می‌نویسی/ویرایش می‌کنی، مود و سطح
آزادی بیان را می‌چینی، حافظه‌ی هر مخاطب را می‌بینی و فکت‌ها را تأیید یا
حذف می‌کنی. پیش‌نویس‌های پاسخ هم از همین‌جا (دکمه‌دار) تأیید می‌شوند.
"""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import db
from plugins import ai_engine as E
from plugins.ai_reply import drop_draft, get_draft

logger = logging.getLogger("bot.ai_panel")


def _esc(text) -> str:
    return html.escape(str(text or ""))


def ai_menu_kb(enabled: bool = True, draft_only: bool = True,
               emoji_level: int = 1) -> InlineKeyboardMarkup:
    send_label = ("📤 حالت ارسال: پیشنهاد به من" if draft_only
                  else "📤 حالت ارسال: خودکار ⚡")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ شخصیت من (پرسونا)", callback_data="ai:persona")],
        [InlineKeyboardButton("🎭 مود", callback_data="ai:modes"),
         InlineKeyboardButton("🌡 سطح آزادی بیان", callback_data="ai:tones")],
        [InlineKeyboardButton(f"😊 ایموجی ({emoji_level})", callback_data="ai:emoji"),
         InlineKeyboardButton(send_label, callback_data="ai:draft")],
        [InlineKeyboardButton("👥 مخاطبین و حافظه", callback_data="ai:contacts"),
         InlineKeyboardButton("🔑 سرویس‌ها", callback_data="ai:providers")],
        [InlineKeyboardButton("🧪 تست پاسخ", callback_data="ai:test"),
         InlineKeyboardButton("🔍 عیب‌یابی", callback_data="ai:diag"),
         InlineKeyboardButton("🔄 بازخوانی", callback_data="ai:menu")],
        [InlineKeyboardButton("⬅️ بازگشت به منوی ربات", callback_data="back_main")],
    ])


def emoji_kb(current: int, allowed: str = "") -> InlineKeyboardMarkup:
    rows = []
    for lvl, label in E.EMOJI_LEVELS.items():
        short = label.split("—")[0].strip()
        rows.append([InlineKeyboardButton(
            ("✅ " if lvl == current else "") + f"{lvl} — {short}",
            callback_data=f"ai:setemoji:{lvl}",
        )])
    if allowed:
        rows.append([InlineKeyboardButton("🗑 برداشتن محدودیت ایموجی‌ها",
                                          callback_data="ai:setemoji:allow_clear")])
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="ai:menu")])
    return InlineKeyboardMarkup(rows)


def build_emoji_text(cfg: dict) -> str:
    cur = E.norm_emoji_level(cfg.get("emoji_level"), 1)
    allow = (cfg.get("allowed_emojis") or "").strip()
    lines = [
        "😊 <b>ایموجی</b>\n",
        f"سطح فعلی: <b>{cur}</b> — {_esc(E.EMOJI_LEVELS[cur])}\n",
    ]
    if allow:
        lines.append(f"فقط این ایموجی‌ها مجازند: {_esc(allow)}")
    else:
        lines.append("ایموجی‌های مجاز: هر ایموجی (فقط سطح بالا اعمال می‌شود)")
    lines.append(
        "\n<b>سطح‌ها:</b>\n"
        + "\n".join(f"<code>{k}</code> — {_esc(v)}" for k, v in E.EMOJI_LEVELS.items())
        + "\n\n💡 برای اینکه فقط از ایموجی‌های <b>خودت</b> استفاده کنم، لیست را "
          "در چت بفرست:\n<code>.تنظیم ایموجی 😂❤️🙏</code>\n"
          "(هر ایموجی خارج از لیست و همچنین ایموجی‌های اضافه، خودکار حذف می‌شوند)"
    )
    return "\n".join(lines)


def modes_kb(current: str) -> InlineKeyboardMarkup:
    """
    همه‌ی مودهای موتور (E.MODES) — دستی لیست نمی‌کنیم تا مود جدید جا نماند
    (قبلاً «دلگیر» در موتور بود ولی در پنل نبود).
    """
    names = list(E.MODES)
    rows = []
    for i in range(0, len(names), 3):
        rows.append([
            InlineKeyboardButton(("✅ " if m == current else "") + m,
                                 callback_data=f"ai:mode:{m}")
            for m in names[i:i + 3]
        ])
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="ai:menu")])
    return InlineKeyboardMarkup(rows)


def build_modes_text(cfg: dict) -> str:
    cur = E.norm_mode(cfg.get("mode", "عادی")) or "عادی"
    lines = [f"🎭 <b>مود</b>\n", f"مود فعلی: <b>{_esc(cur)}</b>\n"]
    for name, info in E.MODES.items():
        mark = "✅ " if name == cur else ""
        lines.append(f"{mark}<b>{_esc(name)}</b> — {_esc(info['desc'])}")
    lines.append(
        "\n💡 مود لحن و طول پاسخ‌ها را عوض می‌کند و روی همه‌ی چت‌ها اعمال می‌شود.\n"
        "با دستور هم می‌شود: <code>.تنظیم مود ناراحت</code> / <code>.تنظیم مود خشمگین</code>")
    return "\n".join(lines)


def tones_kb(current: int) -> InlineKeyboardMarkup:
    rows = []
    for lvl, label in E.TONE_LABELS.items():
        short = label.split("—")[0].strip()
        rows.append([InlineKeyboardButton(
            ("✅ " if lvl == current else "") + f"{lvl} — {short}",
            callback_data=f"ai:tone:{lvl}",
        )])
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="ai:menu")])
    return InlineKeyboardMarkup(rows)


def contacts_kb(contacts: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for c in contacts[:20]:
        raw = (c.get("name") or "").strip()
        # اگر نام ذخیره‌شده همان id عددی بود، یعنی اسم واقعی نداریم
        name = raw if raw and not raw.lstrip("-").isdigit() else f"بدون نام ({c['target_id']})"
        flags = ("⚡" if c.get("auto_mode") else "👤") + ("🟢" if c.get("enabled", True) else "🔴")
        rows.append([InlineKeyboardButton(
            f"{flags} {name} ({c['msgs']}پیام/{c['facts']}فکت)",
            callback_data=f"ai:chat:{c['target_id']}",
        )])
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="ai:menu")])
    return InlineKeyboardMarkup(rows)


def chat_kb(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 فکت‌ها", callback_data=f"ai:facts:{target_id}"),
         InlineKeyboardButton("📝 خلاصه‌ها", callback_data=f"ai:notes:{target_id}")],
        [InlineKeyboardButton("🔀 جایگاه", callback_data=f"ai:rel:{target_id}"),
         InlineKeyboardButton("⚡ خودکار/دستی", callback_data=f"ai:auto:{target_id}")],
        [InlineKeyboardButton("🗑 پاک کردن حافظه", callback_data=f"ai:wipe:{target_id}")],
        [InlineKeyboardButton("📛 نام این مخاطب", callback_data=f"ai:editname:{target_id}")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="ai:contacts")],
    ])


def relationship_kb(target_id: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, rel in E.RELATIONSHIPS.items():
        row.append(InlineKeyboardButton(rel["label"], callback_data=f"ai:setrel:{target_id}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=f"ai:chat:{target_id}")])
    return InlineKeyboardMarkup(rows)


# ═══════════ نمایش ═══════════

async def _ai_config(user_id: int) -> dict:
    """تنظیمات فعلی AI از دیتابیس (پرسونا/مود/سطح)"""
    from plugins.ai_reply import DEFAULT_CONFIG
    cfg = dict(DEFAULT_CONFIG)
    rows = await db.get_features(user_id)
    for f in rows:
        if f["feature_name"] == "ai_reply":
            stored = f.get("config_json") or {}
            if isinstance(stored, str):
                import json
                try:
                    stored = json.loads(stored)
                except Exception:
                    stored = {}
            cfg.update({k: v for k, v in stored.items() if v is not None})
    return cfg


async def _save_ai_config(user_id: int, changes: dict) -> dict:
    """
    تنظیمات AI را در دیتابیس ذخیره می‌کند (پرسونا/مود/سطح/ایموجی/حالت ارسال).
    لیست ایموجی‌های مجاز به‌صورت رشته ذخیره می‌شود تا با JSON سازگار بماند.
    """
    cfg = await _ai_config(user_id)
    cfg.update(changes)
    await db.set_feature(user_id, "ai_reply", True, cfg)
    return cfg


async def show_ai_menu(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       user_id: int, edit: bool = True):
    cfg = await _ai_config(user_id)
    persona = (cfg.get("persona") or "").strip()
    persona_show = (persona[:200] + "…") if len(persona) > 200 else (persona or "— خالی —")
    mode = cfg.get("mode", "عادی")
    tone = int(cfg.get("tone_level", 2))
    emoji_lvl = E.norm_emoji_level(cfg.get("emoji_level"), 1)
    allow = (cfg.get("allowed_emojis") or "").strip()
    chats = await db.list_ai_chats(user_id)
    draft_only = bool(cfg.get("draft_only", True))
    draft = "پیشنهاد به من (اول تأیید می‌کنی)" if draft_only else "خودکار ⚡ (خودم می‌فرستم)"

    text = (
        "🧠 <b>پاسخ هوشمند (AI)</b>\n\n"
        "اینجا لحن و شخصیت من را می‌نویسی تا وقتی جواب می‌دهم، شبیه خودم باشم.\n\n"
        f"✍️ <b>شخصیت:</b> {_esc(persona_show)}\n"
        f"🎭 <b>مود:</b> {_esc(mode)}\n"
        f"🌡 <b>سطح آزادی بیان:</b> {tone} — {_esc(E.TONE_LABELS.get(tone, ''))}\n"
        f"😊 <b>ایموجی:</b> {emoji_lvl} — {_esc(E.EMOJI_LEVELS[emoji_lvl])}"
        + (f" · فقط: {_esc(allow)}" if allow else "") + "\n"
        f"📤 <b>حالت ارسال:</b> {_esc(draft)}\n"
        f"👥 <b>مخاطبین با حافظه:</b> {len(chats)}\n\n"
        "💡 برای فعال کردن در یک چت، از خود سلف‌بات <code>.ai روشن</code> را بفرست."
    )
    kb = ai_menu_kb(draft_only=draft_only, emoji_level=emoji_lvl)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.effective_chat.send_message(text, reply_markup=kb, parse_mode="HTML")


def build_diag_text(cfg: dict | None = None) -> str:
    """
    متن «🔍 عیب‌یابی» — نشان می‌دهد هر سرویس چه آدرس/مدل/کلیدی دارد و
    آخرین خطایش چه بوده. بدون این، کاربر فقط «جوابی نیامد» را می‌دید.
    """
    from core import ai_providers
    providers = ai_providers.load_providers(cfg)
    lines = ["🔍 <b>عیب‌یابی سرویس‌های AI</b>\n"]
    if not providers:
        lines.append("هیچ سرویسی تنظیم نشده. در <code>.env</code> مقدارهای "
                     "<code>AI_BASE_URL</code> / <code>AI_API_KEY</code> / "
                     "<code>AI_MODEL</code> را پر کن.")
        return "\n".join(lines)

    src = {"config": "تنظیمات دیتابیس", "env-json": "AI_PROVIDERS در .env",
           "env": "متغیرهای .env"}.get(ai_providers.PROVIDER_SOURCE, "—")
    lines.append(f"منبع تنظیمات: {_esc(src)}\n")
    for p in providers:
        ok_url = ai_providers.looks_like_url(p.base_url)
        lines.append(f"• <b>{_esc(p.name)}</b> ({_esc(p.kind)})")
        lines.append(f"   آدرس: <code>{_esc(p.base_url or '—')}</code> "
                     f"{'✅' if ok_url else '❌ نامعتبر!'}")
        lines.append(f"   مدل: <code>{_esc(p.model or '—')}</code>")
        if p.keys:
            lines.append(f"   کلید: <code>{_esc(ai_providers.mask_key(p.keys[0]))}</code>")
        else:
            lines.append("   کلید: ➖ ندارد (برای سرویس محلی طبیعی است)")
        seen = set()
        candidates = [p.last_error] + list(reversed(ai_providers.error_history(p.name)))
        for err in candidates:
            if err and err not in seen:
                seen.add(err)
                lines.append(f"   ⚠️ {_esc(err)}")
                if len(seen) >= 2:      # فقط دو خطای آخر برای خوانا ماندن
                    break
        if not seen and p.blocked:
            lines.append("   🔴 در کول‌داون (چند دقیقه صبر کن)")
    lines.append("\n💡 آدرس باید فقط <code>https://…</code> باشد؛ اگر از چت "
                 "کپی شده و براکت/پرانتز دارد، دوباره بنویس.")
    lines.append("💡 تست دقیق روی سرور: <code>python tools/ai_check.py</code>")
    return "\n".join(lines)


async def cb_ai(update: Update, context: ContextTypes.DEFAULT_TYPE,
                user_id: int) -> bool:
    """
    مدیریت همه‌ی دکمه‌های ai:*
    خروجی: True اگر مدیریت شد (تا هندلرهای دیگر ادامه ندهند)
    """
    q = update.callback_query
    data = q.data or ""
    if not data.startswith("ai:"):
        return False

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"
    arg = parts[2] if len(parts) > 2 else ""
    cfg = await _ai_config(user_id)

    from plugins.ai_reply import DEFAULT_CONFIG
    import json

    async def save(**changes):
        cfg.update(changes)
        await db.set_feature(user_id, "ai_reply", True, cfg)

    # ── منو ──
    if action == "menu":
        await show_ai_menu(update, context, user_id)
        await q.answer()
        return True

    # ── شخصیت ──
    if action == "persona":
        context.user_data["awaiting_ai_persona"] = True
        await q.edit_message_text(
            "✍️ <b>شخصیت من</b>\n\n"
            "همه‌چیز درباره‌ی خودت را بنویس: چطور حرف می‌زنی، چه کلمه‌هایی استفاده می‌کنی، "
            "شوخ هستی یا کم‌حرف، اهل ایموجی هستی یا نه، تیکه‌های همیشگی‌ات، "
            "شغلت، سن، شهر — هرچه بیشتر، جواب‌ها شبیه‌تر.\n\n"
            "مثال:\n<i>من ۲۸ ساله، کم‌حرف، جواب‌هام کوتاهه، فحش رکیک می‌دم، "
            "اهل ایموجی نیستم، شکسته می‌نویسم و «بابا» زیاد می‌گم.</i>\n\n"
            "📝 متن را همین‌جا بفرست یا /cancel بزن.",
            parse_mode="HTML",
        )
        await q.answer()
        return True

    # ── مودها ──
    if action == "modes":
        await q.edit_message_text(
            build_modes_text(cfg),
            reply_markup=modes_kb(E.norm_mode(cfg.get("mode", "عادی")) or "عادی"),
            parse_mode="HTML")
        await q.answer()
        return True
    if action == "mode":
        if not E.norm_mode(arg):
            await q.answer("مود ناشناس", show_alert=True)
            return True
        await save(mode=arg)
        await q.answer(f"مود شد {arg}")
        await q.edit_message_text(
            f"🎭 <b>مود:</b> {_esc(arg)} — {_esc(E.MODES.get(arg, {}).get('desc', ''))}",
            reply_markup=modes_kb(arg), parse_mode="HTML")
        return True

    # ── سطح آزادی بیان ──
    if action == "tones":
        tone = int(cfg.get("tone_level", 2))
        await q.edit_message_text(
            "🌡 <b>سطح آزادی بیان</b>\n\nهرچه بالاتر، راحت‌تر و رکیک‌تر:\n"
            "• ۰ مسالمت‌آمیز • ۱ شوخی خودی • ۲ رکیک • ۳ بی‌سقف\n\n"
            f"<i>سطح فعلی: {tone}</i>",
            reply_markup=tones_kb(tone), parse_mode="HTML")
        await q.answer()
        return True
    if action == "tone":
        try:
            lvl = int(arg)
        except ValueError:
            lvl = 2
        await save(tone_level=lvl)
        await q.answer(f"سطح {lvl}")
        await q.edit_message_text(
            f"🌡 <b>سطح {lvl}</b> — {_esc(E.TONE_LABELS.get(lvl, ''))}",
            reply_markup=tones_kb(lvl), parse_mode="HTML")
        return True

    # ── مخاطبین ──
    if action == "contacts":
        chats = await db.list_ai_chats(user_id)
        if not chats:
            await q.edit_message_text(
                "👥 هنوز هیچ مخاطبی حافظه ندارد.\n\n"
                "از خود سلف‌بات در چت طرف <code>.ai روشن</code> بفرست.",
                reply_markup=ai_menu_kb(), parse_mode="HTML")
        else:
            await q.edit_message_text("👥 <b>مخاطبین</b> — یکی را انتخاب کن:",
                                      reply_markup=contacts_kb(chats), parse_mode="HTML")
        await q.answer()
        return True

    if action == "chat":
        target = int(arg)
        prof = await db.get_ai_profile(user_id, target) or {}
        counts = await db.count_ai_memory(user_id, target)
        rel = E.RELATIONSHIPS.get(prof.get("relationship", "familiar"), {})
        name = prof.get("target_name") or str(target)
        await q.edit_message_text(
            f"👤 <b>{_esc(name)}</b>\n\n"
            f"جایگاه: <b>{_esc(rel.get('label', '—'))}</b>\n"
            f"صمیمیت: {prof.get('intimacy', '—')}/۵\n"
            f"ارسال: {'⚡ خودکار' if prof.get('auto_mode') else '👤 پیشنهاد به من'}\n"
            f"حافظه: {counts['msg']} پیام · {counts['note']} خلاصه · {counts['fact']} فکت",
            reply_markup=chat_kb(target), parse_mode="HTML")
        await q.answer()
        return True

    if action == "rel":
        await q.edit_message_text("🔀 جایگاه این شخص در زندگی من:",
                                  reply_markup=relationship_kb(int(arg)), parse_mode="HTML")
        await q.answer()
        return True

    if action == "setrel":
        target, key = int(parts[2]), parts[3]
        await db.upsert_ai_profile(user_id, target, relationship=key)
        await q.answer(f"جایگاه: {E.RELATIONSHIPS[key]['label']}")
        rel = E.RELATIONSHIPS[key]
        await q.edit_message_text(
            f"✅ جایگاه شد <b>{_esc(rel['label'])}</b>\n\n"
            f"لحن: {_esc(rel['style'])}\n"
            f"سطح پیش‌فرض: {rel['tone']} · طول: {_esc(rel['length'])} · ایموجی: {_esc(rel['emoji'])}",
            reply_markup=chat_kb(target), parse_mode="HTML")
        return True

    if action == "auto":
        target = int(arg)
        prof = await db.get_ai_profile(user_id, target) or {}
        new_state = not prof.get("auto_mode", False)
        await db.set_ai_auto_mode(user_id, target, new_state)
        await q.answer("خودکار شد ⚡" if new_state else "برگشت به پیشنهاد 👤")
        await q.edit_message_text(
            ("⚡ <b>خودکار</b> — از این به بعد بدون تایید تو جواب می‌دم.\n"
             "⚠️ ریسکش بیشتره؛ فقط برای آدم‌های مطمئن." if new_state else
             "👤 <b>پیشنهاد به من</b> — هر پاسخ اول به پیوی ربات میاد."),
            reply_markup=chat_kb(target), parse_mode="HTML")
        return True

    # ── فکت‌ها ──
    if action in ("facts", "notes"):
        target = int(arg)
        if action == "facts":
            rows = await db.get_ai_facts(user_id, target, limit=30,
                                         statuses=("approved", "pending"))
            title = "📌 <b>فکت‌ها</b>"
            empty = "هیچ فکتی ذخیره نشده."
        else:
            rows = await db.get_ai_notes(user_id, target, limit=15)
            title = "📝 <b>خلاصه‌ها</b>"
            empty = "هیچ خلاصه‌ای نیست."

        if not rows:
            await q.edit_message_text(f"{title}\n\n{empty}",
                                      reply_markup=chat_kb(target), parse_mode="HTML")
            await q.answer()
            return True

        buttons = []
        lines = [f"{title}\n"]
        for r in rows[:12]:
            mark = "⏳" if r.get("status") == "pending" else ("📌" if r.get("pinned") else "•")
            lines.append(f"{mark} {_esc(r['content'][:160])}")
            row = [InlineKeyboardButton(
                "✏️ " + r["content"][:22],
                callback_data=f"ai:editfact:{r['id']}:{target}",
            )]
            if r.get("status") == "pending":
                row.append(InlineKeyboardButton("✅ تأیید", callback_data=f"ai:approve:{r['id']}"))
            row.append(InlineKeyboardButton("🗑", callback_data=f"ai:delfact:{r['id']}:{target}"))
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=f"ai:chat:{target}")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode="HTML")
        await q.answer()
        return True

    if action == "editfact":
        mem_id, target = int(parts[2]), int(parts[3])
        context.user_data["awaiting_ai_fact_edit"] = {"mem_id": mem_id, "target": target}
        rows = await db.get_ai_facts(user_id, target, limit=50,
                                     statuses=("approved", "pending"))
        current = next((r["content"] for r in rows if r["id"] == mem_id), "")
        await q.edit_message_text(
            "✏️ <b>ویرایش فکت</b>\n\n"
            f"متن فعلی:\n<i>{_esc(current)}</i>\n\n"
            "متن جدید را بفرست (یا /cancel برای انصراف):",
            parse_mode="HTML")
        await q.answer()
        return True

    if action == "approve":
        ok = await db.update_ai_memory(int(arg), user_id, status="approved")
        await q.answer("تأیید شد ✅" if ok else "نشد")
        return True

    if action == "delfact":
        mem_id, target = int(parts[2]), int(parts[3])
        await db.delete_ai_memory(user_id, target, mem_id=mem_id)
        await q.answer("حذف شد 🗑")
        return True

    if action == "wipe":
        target = int(arg)
        n = await db.delete_ai_memory(user_id, target)
        await q.answer(f"{n} رکورد پاک شد")
        await q.edit_message_text(
            f"🗑 حافظه‌ی این مخاطب ({n} رکورد) پاک شد. پروفایل و جایگاهش ماند.",
            reply_markup=chat_kb(target), parse_mode="HTML")
        return True

    # ── سرویس‌ها و تست ──
    if action == "providers":
        from core import ai_providers
        providers = ai_providers.load_providers(cfg)
        from core.plugin_manager import get_active_plugins
        from core.client_manager import active_clients
        lines = ai_providers.status_lines(providers)
        await q.edit_message_text(
            "🔑 <b>سرویس‌های AI</b>\n\n" + "\n".join(lines) +
            "\n\nمحل تنظیم: <code>AI_PROVIDERS</code> در فایل .env\n"
            "نمونه:\n<code>AI_PROVIDERS=[{\"name\":\"openrouter\",\"kind\":\"openai\","
            "\"base_url\":\"https://openrouter.ai/api/v1\","
            "\"model\":\"meta-llama/llama-3.1-8b-instruct:free\",\"keys\":[\"sk-...\"]}]</code>",
            reply_markup=ai_menu_kb(), parse_mode="HTML")
        await q.answer()
        return True

    if action == "emoji":
        await q.edit_message_text(build_emoji_text(cfg),
                                  reply_markup=emoji_kb(
                                      E.norm_emoji_level(cfg.get("emoji_level"), 1),
                                      cfg.get("allowed_emojis") or ""),
                                  parse_mode="HTML")
        await q.answer()
        return True

    if action == "setemoji":
        value = parts[2] if len(parts) > 2 else ""
        if value == "allow_clear":
            await _save_ai_config(user_id, {"allowed_emojis": ""})
            await q.answer("محدودیت ایموجی‌ها برداشته شد ✅")
        else:
            lvl = E.norm_emoji_level(value, -1)
            if lvl < 0:
                await q.answer("مقدار نامعتبر", show_alert=True)
                return True
            await _save_ai_config(user_id, {"emoji_level": lvl})
            await q.answer(f"سطح ایموجی شد {lvl} ✅")
        cfg = await _ai_config(user_id)
        await q.edit_message_text(build_emoji_text(cfg),
                                  reply_markup=emoji_kb(
                                      E.norm_emoji_level(cfg.get("emoji_level"), 1),
                                      cfg.get("allowed_emojis") or ""),
                                  parse_mode="HTML")
        return True

    if action == "editname":
        target = int(parts[2])
        context.user_data["awaiting_ai_contact_name"] = target
        profile = await db.get_ai_profile(user_id, target) or {}
        overrides = cfg.get("name_overrides") or {}
        cur = overrides.get(str(target)) or profile.get("target_name") or "—"
        await q.edit_message_text(
            f"📛 <b>نام این مخاطب</b>\n\n"
            f"نام فعلی: <b>{_esc(cur)}</b>\n\n"
            "نام درست را بفرست.\n"
            "(برای برگشت به نام واقعی تلگرام، کلمه «خودکار» را بفرست)",
            parse_mode="HTML")
        await q.answer()
        return True

    if action == "draft":
        new_state = not bool(cfg.get("draft_only", True))
        await _save_ai_config(user_id, {"draft_only": new_state})
        if new_state:
            await q.answer("حالا اول پیش‌نویس را به تو نشان می‌دهم ✅")
        else:
            # وقتی «خودکار» می‌شود، چت‌های موجود هم خودکار می‌شوند تا واقعاً
            # جواب دادن خودکار شروع شود (وگرنه تا وقتی .ai خودکار را نزنی
            # فقط پیش‌نویس می‌ماند و کاربر فکر می‌کند کار نمی‌کند).
            n = await db.set_ai_auto_mode_all(user_id, True)
            await q.answer(f"حالا خودم خودکار جواب می‌دم ⚡ ({n} چت فعال شد)",
                           show_alert=True)
        await show_ai_menu(update, context, user_id, edit=True)
        return True

    if action == "diag":
        await q.edit_message_text(build_diag_text(cfg)[:3900],
                                  reply_markup=ai_menu_kb(), parse_mode="HTML")
        await q.answer()
        return True

    if action == "test":
        from core import ai_providers
        providers = ai_providers.load_providers(cfg)
        if not providers:
            await q.answer("اول سرویس AI را در .env تنظیم کن", show_alert=True)
            return True
        await q.edit_message_text("🧪 دارم تست می‌کنم…", parse_mode="HTML")
        msgs = [
            {"role": "system", "content":
                "تو یک آدم واقعی در چت تلگرامی. کوتاه، محاوره‌ای و بدون هیچ اشاره‌ای به "
                "ربات/AI بودن جواب بده.\nشخصیت من: " +
                (cfg.get("persona") or "طبیعی و معمولی") +
                f"\nمود: {cfg.get('mode', 'عادی')} | سطح آزادی: {cfg.get('tone_level', 2)}"},
            {"role": "user", "content": "سلام، خوبی؟ چیکار می‌کنی؟"},
        ]
        reply, provider = await ai_providers.chat(msgs, config=cfg, max_tokens=120)
        if reply:
            await q.edit_message_text(
                f"🧪 <b>تست سرویس</b> (از {_esc(provider)})\n\n"
                f"👤 او: سلام، خوبی؟ چیکار می‌کنی؟\n"
                f"🤖 من: {_esc(reply[:400])}",
                reply_markup=ai_menu_kb(), parse_mode="HTML")
        else:
            errs = ai_providers.last_errors()
            lines = ["❌ <b>هیچ سرویسی جواب نداد.</b>\n"]
            if errs:
                lines.append("<b>خطای دقیق سرویس‌ها:</b>")
                for name, err in list(errs.items())[:6]:
                    lines.append(f"• <b>{_esc(name)}</b>: {_esc(err)}")
            else:
                lines.append("سرویسی پاسخ نداد ولی خطایی هم ثبت نشد "
                             "— با «🔍 عیب‌یابی» جزئیات را ببین.")
            lines.append("\n🔍 برای جزئیات بیشتر: دکمه‌ی «🔍 عیب‌یابی»")
            await q.edit_message_text(
                "\n".join(lines), reply_markup=ai_menu_kb(), parse_mode="HTML")
        await q.answer()
        return True

    await q.answer()
    return True


# ═══════════ پیش‌نویس‌ها ═══════════

async def cb_ai_draft(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      user_id: int) -> bool:
    """دکمه‌های پیش‌نویس: ارسال / ویرایش / رد"""
    q = update.callback_query
    data = q.data or ""
    if not data.startswith("ai_send:") and not data.startswith("ai_drop:") and not data.startswith("ai_edit:"):
        return False

    action, draft_id = data.split(":", 1)
    draft = get_draft(draft_id)
    if not draft:
        await q.answer("این پیش‌نویس منقضی شده", show_alert=True)
        return True

    from core.client_manager import get_client

    if action == "ai_drop":
        drop_draft(draft_id)
        await q.edit_message_text("🗑 پیش‌نویس رد شد.")
        await q.answer()
        return True

    if action == "ai_edit":
        context.user_data["awaiting_ai_draft_edit"] = draft_id
        await q.edit_message_text(
            "✏️ <b>ویرایش پاسخ</b>\n\nمتن جدید را بفرست (همان‌جا در چت طرف ارسال می‌شود):",
            parse_mode="HTML")
        await q.answer()
        return True

    # ارسال
    client = await get_client(user_id)
    if not client:
        await q.answer("اکانت وصل نیست", show_alert=True)
        return True
    try:
        parts = E.split_messages(draft["reply"])
        r_to = draft.get("reply_to")
        for i, part in enumerate(parts):
            await client.send_message(draft["chat_id"], part,
                                      reply_to=r_to if i == 0 else None)
            await db.add_ai_message(user_id, draft["chat_id"], part, is_out=True)
        drop_draft(draft_id)
        await q.edit_message_text(
            f"✅ ارسال شد به {_esc(draft['name'])}"
            + (" (ریپلای روی پیامش)" if r_to else "") + f":\n\n{_esc(draft['reply'])}")
    except Exception as e:
        logger.error(f"draft send failed: {e}")
        await q.answer(f"خطا: {type(e).__name__}", show_alert=True)
    return True


async def handle_ai_text(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         user_id: int) -> bool:
    """
    ورودی‌های متنی مربوط به AI در پیوی ربات (پرسونا / ویرایش پیش‌نویس)
    خروجی: True اگر مصرف شد.
    """
    text = (update.message.text or "").strip()

    if context.user_data.get("awaiting_ai_persona"):
        context.user_data.pop("awaiting_ai_persona", None)
        cfg = await _ai_config(user_id)
        cfg["persona"] = text[:4000]
        await db.set_feature(user_id, "ai_reply", True, cfg)
        await update.message.reply_text(
            "✅ شخصیت ذخیره شد.\n\n"
            f"<i>{_esc(text[:300])}</i>\n\n"
            "از این به بعد پاسخ‌ها با همین لحن ساخته می‌شوند.",
            parse_mode="HTML")
        await show_ai_menu(update, context, user_id, edit=False)
        return True

    name_target = context.user_data.get("awaiting_ai_contact_name")
    if name_target:
        context.user_data.pop("awaiting_ai_contact_name", None)
        new_name = text.strip()[:40]
        cfg2 = await _ai_config(user_id)
        overrides = dict(cfg2.get("name_overrides") or {})
        if new_name in ("خودکار", "auto", "تلگرام", "-"):
            overrides.pop(str(name_target), None)
            overrides.pop(name_target, None)
            await _save_ai_config(user_id, {"name_overrides": overrides})
            await update.message.reply_text("✅ برگشت به نام واقعی تلگرام.")
        else:
            overrides[str(name_target)] = new_name
            overrides.pop(name_target, None)
            await _save_ai_config(user_id, {"name_overrides": overrides})
            await db.upsert_ai_profile(user_id, name_target, target_name=new_name)
            await update.message.reply_text(f"✅ نام این مخاطب شد «{new_name}».")
        await show_ai_menu(update, context, user_id, edit=False)
        return True

    fact_state = context.user_data.get("awaiting_ai_fact_edit")
    if fact_state:
        context.user_data.pop("awaiting_ai_fact_edit", None)
        ok = await db.update_ai_memory(fact_state["mem_id"], user_id, content=text[:1000])
        await update.message.reply_text(
            "✅ فکت ویرایش شد." if ok else "❌ فکت پیدا نشد.")
        await show_ai_menu(update, context, user_id, edit=False)
        return True

    draft_id = context.user_data.get("awaiting_ai_draft_edit")
    if draft_id:
        context.user_data.pop("awaiting_ai_draft_edit", None)
        draft = get_draft(draft_id)
        if not draft:
            await update.message.reply_text("این پیش‌نویس منقضی شده.")
            return True
        from core.client_manager import get_client
        client = await get_client(user_id)
        if not client:
            await update.message.reply_text("❌ اکانت وصل نیست.")
            return True
        try:
            await client.send_message(draft["chat_id"], text)
            await db.add_ai_message(user_id, draft["chat_id"], text, is_out=True)
            drop_draft(draft_id)
            await update.message.reply_text(f"✅ ارسال شد به {_esc(draft['name'])}.")
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {type(e).__name__}")
        return True

    return False
