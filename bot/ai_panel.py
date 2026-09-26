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


def ai_menu_kb(enabled: bool = True) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ شخصیت من (پرسونا)", callback_data="ai:persona")],
        [InlineKeyboardButton("🎭 مود", callback_data="ai:modes"),
         InlineKeyboardButton("🌡 سطح آزادی بیان", callback_data="ai:tones")],
        [InlineKeyboardButton("👥 مخاطبین و حافظه", callback_data="ai:contacts"),
         InlineKeyboardButton("🔑 سرویس‌ها", callback_data="ai:providers")],
        [InlineKeyboardButton("🧪 تست پاسخ", callback_data="ai:test"),
         InlineKeyboardButton("🔄 بازخوانی", callback_data="ai:menu")],
    ])


MODE_ROWS = [["عادی", "خوشحال", "playfull"],
             ["شوخ", "رسمی", "کاری"],
             ["عاشقانه", "آرام", "هیجان"]]


def modes_kb(current: str) -> InlineKeyboardMarkup:
    rows = []
    for row in MODE_ROWS:
        rows.append([
            InlineKeyboardButton(("✅ " if m == current else "") + m,
                                 callback_data=f"ai:mode:{m}")
            for m in row
        ])
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="ai:menu")])
    return InlineKeyboardMarkup(rows)


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
        name = c.get("name") or str(c["target_id"])
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


async def show_ai_menu(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       user_id: int, edit: bool = True):
    cfg = await _ai_config(user_id)
    persona = (cfg.get("persona") or "").strip()
    persona_show = (persona[:200] + "…") if len(persona) > 200 else (persona or "— خالی —")
    mode = cfg.get("mode", "عادی")
    tone = int(cfg.get("tone_level", 2))
    chats = await db.list_ai_chats(user_id)
    draft = "پیشنهاد به من" if cfg.get("draft_only", True) else "خودکار (چت‌های علامت‌خورده)"

    text = (
        "🧠 <b>پاسخ هوشمند (AI)</b>\n\n"
        "اینجا لحن و شخصیت من را می‌نویسی تا وقتی جواب می‌دهم، شبیه خودم باشم.\n\n"
        f"✍️ <b>شخصیت:</b> {_esc(persona_show)}\n"
        f"🎭 <b>مود:</b> {_esc(mode)}\n"
        f"🌡 <b>سطح آزادی بیان:</b> {tone} — {_esc(E.TONE_LABELS.get(tone, ''))}\n"
        f"📤 <b>حالت ارسال:</b> {_esc(draft)}\n"
        f"👥 <b>مخاطبین با حافظه:</b> {len(chats)}\n\n"
        "💡 برای فعال کردن در یک چت، از خود سلف‌بات <code>.ai روشن</code> را بفرست."
    )
    kb = ai_menu_kb()
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.effective_chat.send_message(text, reply_markup=kb, parse_mode="HTML")


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
            f"🎭 <b>مود فعلی:</b> {_esc(cfg.get('mode', 'عادی'))}\n\nیک مود انتخاب کن:",
            reply_markup=modes_kb(cfg.get("mode", "عادی")), parse_mode="HTML")
        await q.answer()
        return True
    if action == "mode":
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
            await q.edit_message_text(
                "❌ هیچ سرویسی جواب نداد. کلید/مدل/اتصال را چک کن.",
                reply_markup=ai_menu_kb(), parse_mode="HTML")
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
        await client.send_message(draft["chat_id"], draft["reply"])
        await db.add_ai_message(user_id, draft["chat_id"], draft["reply"], is_out=True)
        drop_draft(draft_id)
        await q.edit_message_text(f"✅ ارسال شد به {_esc(draft['name'])}:\n\n{_esc(draft['reply'])}")
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
