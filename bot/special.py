"""
⭐ ویژگی‌های ویژه — بخش جدید پنل ربات.

این‌جا «قابلیت‌های خاص» جمع می‌شوند. برای شروع، فقط «🏷 شناسنامه‌ی گروه و
کانال» این‌جاست (همان قابلیت «مالک» که قبلاً فقط با دستور `.مالک` در دسترس
بود). بعداً هر قابلیت خاص دیگری هم به همین منو اضافه می‌شود.

مسیر حرکت کاربر:
    منوی اصلی → «⭐ ویژگی‌های ویژه»      (callback: sp:menu)
      → «🏷 شناسنامه‌ی گروه و کانال»      (callback: sp:own)
        → «📢 کانال‌ها» / «👥 گروه‌ها»     (callback: sp:l:ch:0 / sp:l:gr:0)
          → انتخاب یک چت                  (callback: sp:p:{idx})
            → کارت اطلاعات + مالک + دکمه‌های دامپ و لیسن

قاعده‌های طراحی (رعایت‌شده):
  • لیست چت‌ها از همان منبع «دامپ» (core.forwarder.load_dialogs) می‌آید؛ پس
    کش مشترک است، اسکن تکراری زده نمی‌شود و تجربه‌ی صفحه‌بندی یکسان است.
  • در «کارت» هیچ درخواست اضافه‌ای برای پیدا کردن دوباره‌ی چت زده نمی‌شود
    (entity از قبل در حافظه است → chat_owner_from_entity).
  • مالک فقط وقتی خوانده می‌شود که یک چت انتخاب شده باشد (نه برای کل لیست).
  • دکمه‌ی «دامپ» کاربر را به همان جریان فوروارد موجود وصل می‌کند و دکمه‌ی
    «لیسن» یک مسیر مانیتور واقعی می‌سازد و پلاگین را در لحظه روشن می‌کند.
"""

from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger("special")

PER_PAGE = 8                      # مثل لیست دامپ
STATE_KEY = "sp"                  # وضعیت کوتاه هر کاربر (صفحه/نوع لیست)
KIND_LABEL = {"ch": "📢 کانال‌ها", "gr": "👥 گروه‌ها"}
KIND_MATCH = {"ch": "channel", "gr": "group"}
KIND_OF = {"channel": "ch", "group": "gr"}


def _esc(v) -> str:
    return html.escape(str(v or ""))


def _short(name: str, limit: int = 34) -> str:
    name = (name or "").strip() or "—"
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    st = context.user_data.get(STATE_KEY)
    if not st:
        st = {"page": 0, "kind": "ch", "idx": None}
        context.user_data[STATE_KEY] = st
    return st


# ═══════════════════════════════════
# کیبوردها
# ═══════════════════════════════════


def special_home_kb() -> InlineKeyboardMarkup:
    """منوی خانه‌ی «ویژگی‌های ویژه» — قابلیت‌های خاص این‌جا لیست می‌شوند"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏷 شناسنامه‌ی گروه و کانال", callback_data="sp:own")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])


def special_menu_kb() -> InlineKeyboardMarkup:
    """منوی شناسنامه: کانال‌ها / گروه‌ها / بازگشت (خواسته‌ی کاربر)"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 کانال‌ها", callback_data="sp:l:ch:0"),
         InlineKeyboardButton("👥 گروه‌ها", callback_data="sp:l:gr:0")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="sp:menu")],
    ])


def special_list_kb(rows: list[tuple[int, str]], page: int, pages: int,
                    kind: str, has_any: bool) -> InlineKeyboardMarkup:
    """لیست چت‌ها — دقیقاً مثل لیست دامپ (آیکن + نام + صفحه‌بندی)"""
    buttons: list[list[InlineKeyboardButton]] = []
    if not rows:
        buttons.append([InlineKeyboardButton("📭 چیزی پیدا نشد",
                                             callback_data="sp:noop")])
    for idx, icon, name in rows:
        buttons.append([InlineKeyboardButton(
            f"{icon} {_short(name)}", callback_data=f"sp:p:{idx}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"sp:l:{kind}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="sp:noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"sp:l:{kind}:{page + 1}"))
    buttons.append(nav)

    other = "gr" if kind == "ch" else "ch"
    if has_any:
        buttons.append([InlineKeyboardButton(
            f"🔁 دیدن {KIND_LABEL[other]}", callback_data=f"sp:l:{other}:0")])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="sp:own")])
    return InlineKeyboardMarkup(buttons)


def special_card_kb(idx: int, kind: str, page: int, owner_id: int | None,
                    username: str | None) -> InlineKeyboardMarkup:
    """دکمه‌های کارت: دامپ، لیسن، پروفایل مالک، بازگشت (به همان صفحه)"""
    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([
        InlineKeyboardButton("📥 دامپ این چت", callback_data=f"sp:f:{idx}"),
        InlineKeyboardButton("👂 لیسن (مانیتور)", callback_data=f"sp:m:{idx}"),
    ])
    row = []
    if owner_id:
        row.append(InlineKeyboardButton("👤 پروفایل مالک",
                                        callback_data=f"u:card:{owner_id}"))
    if username:
        row.append(InlineKeyboardButton("🔗 باز کردن",
                                        url=f"https://t.me/{username}"))
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(
        "🔙 بازگشت به لیست", callback_data=f"sp:l:{kind}:{page}")])
    return InlineKeyboardMarkup(buttons)


def special_monitor_kb(idx: int, has_target: bool) -> InlineKeyboardMarkup:
    """انتخاب مقصد برای «لیسن»"""
    rows = [[InlineKeyboardButton("💾 پیام‌های ذخیره‌شده",
                                  callback_data=f"sp:mg:{idx}:saved")]]
    if has_target:
        rows.append([InlineKeyboardButton("📂 مقصد ذخیره‌سازی",
                                          callback_data=f"sp:mg:{idx}:target")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"sp:p:{idx}")])
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════
# ابزارهای مشترک
# ═══════════════════════════════════


async def _edit(query, text: str, kb=None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=kb,
                                      parse_mode="HTML",
                                      disable_web_page_preview=True)
    except Exception as e:                          # noqa: BLE001
        logger.debug(f"sp edit failed: {e}")
        try:
            await query.message.reply_text(text, reply_markup=kb,
                                           parse_mode="HTML")
        except Exception:
            pass


async def _dialogs(user_id: int, force: bool = False) -> list[dict]:
    """لیست چت‌ها از همان منبع دامپ (کش مشترک)"""
    from core import forwarder
    return await forwarder.load_dialogs(user_id, force=force)


def _filter(items: list[dict], kind_code: str) -> list[tuple[int, dict]]:
    """(ایندکس اصلی، آیتم) برای کانال‌ها یا گروه‌ها"""
    want = KIND_MATCH.get(kind_code, "channel")
    return [(i, it) for i, it in enumerate(items) if it.get("kind") == want]


async def _plugin(user_id: int):
    """پلاگین کاربر_info (کارت مالک آن‌جاست)"""
    from core.plugin_manager import get_active_plugins
    try:
        return get_active_plugins(user_id).get("user_info")
    except Exception as e:                          # noqa: BLE001
        logger.debug(f"user_info plugin lookup failed: {e}")
        return None


async def _not_ready(query, user_id: int) -> bool:
    """اکانت وصل نیست؟ پیام روشن + خروج"""
    try:
        await _dialogs(user_id)
        return False
    except ValueError as e:
        msg = ("❌ اکانت متصل نیست. اول از منوی اصلی «🔗 اتصال اکانت» را بزن."
               if str(e) == "account_not_connected"
               else "❌ خواندن لیست چت‌ها ناموفق بود. چند لحظه بعد دوباره امتحان کن.")
        await _edit(query, msg, kb=special_home_kb())
        return True


# ═══════════════════════════════════
# مسیریاب
# ═══════════════════════════════════


async def route_special_callbacks(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    مسیریاب callback های «sp:*» (⭐ ویژگی‌های ویژه).
    مثل u:/ai: در گروه ۱ ثبت می‌شود و اگر مربوط نبود، سریع رد می‌شود.
    """
    q = update.callback_query
    if not q or not q.data or not q.data.startswith("sp:"):
        return

    from bot.handlers import get_or_create_user

    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    try:
        user = await get_or_create_user(update)
    except Exception as e:                          # noqa: BLE001
        logger.error(f"special: user lookup failed: {e}")
        await q.answer("خطا", show_alert=True)
        return

    # ── خانه‌ی ویژگی‌های ویژه ──
    if action == "menu":
        await q.answer()
        await _edit(q, SPECIAL_HOME_TEXT, kb=special_home_kb())
        return

    # ── منوی شناسنامه (کانال‌ها / گروه‌ها) ──
    if action == "own":
        await q.answer()
        await _edit(q, OWN_MENU_TEXT, kb=special_menu_kb())
        return

    if action == "noop":
        await q.answer()
        return

    # از این‌جا به بعد همه به لیست چت‌ها نیاز دارند
    if await _not_ready(q, user["id"]):
        return

    # ── لیست کانال‌ها / گروه‌ها ──
    if action == "l":
        st = _state(context)
        kind = parts[2] if len(parts) > 2 and parts[2] in KIND_LABEL else st["kind"]
        page_req = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        await q.answer()

        items = await _dialogs(user["id"])
        filtered = _filter(items, kind)
        pages = max(1, (len(filtered) + PER_PAGE - 1) // PER_PAGE)
        page = max(0, min(page_req, pages - 1))

        st.update({"kind": kind, "page": page})
        chunk = filtered[page * PER_PAGE:(page + 1) * PER_PAGE]

        from core.forwarder import dialog_icon
        has_other = bool(_filter(items, "gr" if kind == "ch" else "ch"))
        rows = [(idx, dialog_icon(it.get("kind")), it.get("name") or "")
                for idx, it in chunk]

        if not filtered:
            text = (f"{KIND_LABEL[kind]}\n\n"
                    "📭 در این دسته چیزی پیدا نشد.\n"
                    "<i>فقط کانال‌ها و گروه‌هایی که اکانتت عضو آن‌هاست این‌جا "
                    "می‌آیند.</i>")
        else:
            text = (f"{KIND_LABEL[kind]} — صفحه {page + 1} از {pages}\n"
                    f"<i>مجموع {len(filtered)} مورد · برای دیدن اطلاعات و مالک، "
                    "روی هرکدام بزن</i>")
        await _edit(q, text, kb=special_list_kb(rows, page, pages, kind, has_other))
        return

    # ── کارت یک چت ──
    if action == "p":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        plugin = await _plugin(user["id"])
        if plugin is None:
            await q.answer("قابلیت «کاربر» بالا نیامده — ربات را ری‌استارت کن",
                           show_alert=True)
            return
        from core import forwarder
        item = forwarder.get_dialog(user["id"], idx) if idx is not None else None
        if not item:
            await q.answer("لیست قدیمی شده؛ دوباره بازش کن", show_alert=True)
            return
        await q.answer("دارم اطلاعات را می‌خوانم…")

        st = _state(context)
        res = await plugin.chat_owner_from_entity(item.get("entity"), "لیست پنل")
        if not res.get("ok"):
            await q.answer(f"پیدا نشد: {res.get('reason')}", show_alert=True)
            return

        st["idx"] = idx
        # نوع و صفحه را از وضعیت بگیر تا «بازگشت» به همان لیستِ قبلی برگردد
        kind = KIND_OF.get(item.get("kind"), st.get("kind") or "ch")
        page = st.get("page") or 0
        kb = special_card_kb(idx, kind, page, res.get("owner_id"),
                             getattr(item.get("entity"), "username", None))
        await _edit(q, res["text"], kb=kb)
        return

    # ── دامپ: وصل شدن به جریان فوروارد موجود ──
    if action == "f":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        from core import forwarder
        item = forwarder.get_dialog(user["id"], idx) if idx is not None else None
        if not item:
            await q.answer("لیست قدیمی شده؛ دوباره بازش کن", show_alert=True)
            return

        from bot.forward import _render_dialogs, _state as fwd_state
        fst = fwd_state(context)
        fst.update({
            "src": {"name": item.get("name"), "entity": item.get("entity")},
            "dst": None, "dst_page": 0, "query": "",
            "chat_id": q.message.chat_id, "msg_id": q.message.message_id,
        })
        await q.answer("حالا مقصد را انتخاب کن")
        await _render_dialogs(context, user, "dst")
        return

    # ── لیسن: انتخاب مقصد ──
    if action == "m":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        from core import forwarder
        item = forwarder.get_dialog(user["id"], idx) if idx is not None else None
        if not item:
            await q.answer("لیست قدیمی شده؛ دوباره بازش کن", show_alert=True)
            return
        await q.answer()
        has_target = await _storage_target(user["id"]) is not None
        await _edit(
            q,
            "👂 <b>لیسن (مانیتور) این چت</b>\n\n"
            f"📡 منبع: <b>{_esc(item.get('name'))}</b>\n\n"
            "پیام‌های جدید این چت را کجا بفرستم؟\n"
            "<i>بعد از ساخت، هر پیام تازه‌ای که در این چت بیاید، همان لحظه به "
            "مقصد انتخابی می‌رود.</i>",
            kb=special_monitor_kb(idx, has_target))
        return

    # ── لیسن: ساخت مسیر ──
    if action == "mg":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        mode = parts[3] if len(parts) > 3 else "saved"
        from core import forwarder
        item = forwarder.get_dialog(user["id"], idx) if idx is not None else None
        if not item:
            await q.answer("لیست قدیمی شده؛ دوباره بازش کن", show_alert=True)
            return

        ent = item.get("entity")
        src_id = item.get("peer_id") or getattr(ent, "id", None)
        src_title = item.get("name") or str(src_id)

        dst = await _storage_target(user["id"]) if mode == "target" else None
        if dst is None:
            dst = await _self_peer(user["id"])
        if dst is None:
            await q.answer("مقصد پیدا نشد", show_alert=True)
            return
        dst_id, dst_title = dst

        await q.answer("دارم لیسن را روشن می‌کنم…")
        ok, why = await _enable_listen(user["id"], src_id, src_title, dst_id, dst_title)
        if not ok:
            await q.answer(f"❌ {why}", show_alert=True)
            return

        await _edit(
            q,
            "✅ <b>لیسن روشن شد</b>\n\n"
            f"📡 منبع: <b>{_esc(src_title)}</b>\n"
            f"📤 مقصد: <b>{_esc(dst_title)}</b>\n\n"
            "از این به بعد هر پیام تازه‌ی این چت، همان لحظه به مقصد می‌رود.\n"
            "<i>مدیریت مسیرها: در هر چت </i><code>.لیست مانیتور</code>"
            "<i> یا </i><code>.مانیتور حذف</code>",
            kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("👂 لیسن چت دیگر", callback_data="sp:own")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="sp:menu")],
            ]))
        return

    await q.answer()


# ═══════════════════════════════════
# کمک‌کارهای لیسن (مانیتور)
# ═══════════════════════════════════


async def _self_peer(user_id: int):
    """(آیدی، عنوان) برای «پیام‌های ذخیره‌شده»"""
    try:
        from core.client_manager import get_client
        from telethon import utils
        client = await get_client(user_id)
        if not client:
            return None
        me = await client.get_me()
        return utils.get_peer_id(me), "Saved Messages"
    except Exception as e:                          # noqa: BLE001
        logger.debug(f"self peer failed: {e}")
        return None


async def _storage_target(user_id: int):
    """مقصد ذخیره‌سازیِ تنظیم‌شده (اگر کاربر تعیین کرده باشد)"""
    try:
        from database import db
        tgt = await db.get_storage_target(user_id, "channel_monitor")
        if tgt and tgt.get("target_id"):
            return tgt["target_id"], (tgt.get("target_title")
                                      or str(tgt["target_id"]))
    except Exception as e:                          # noqa: BLE001
        logger.debug(f"storage target failed: {e}")
    return None


async def _enable_listen(user_id: int, src_id, src_title: str, dst_id,
                         dst_title: str) -> tuple[bool, str]:
    """
    ساخت مسیر مانیتور + روشن کردن پلاگین در همان لحظه
    (همان کاری که مسیر متنی «.مانیتور» می‌کند، بدون نیاز به تایپ)
    """
    try:
        from database import db
        from core.client_manager import get_client
        from core.plugin_manager import enable_plugin, get_active_plugins

        client = await get_client(user_id)
        if not client:
            return False, "اکانت متصل نیست"

        await db.set_channel_route(user_id, int(src_id), src_title, "custom",
                                   int(dst_id), dst_title)

        # اگر قابلیت مانیتور خاموش است، در همین لحظه روشنش کن
        plugins = get_active_plugins(user_id)
        monitor = plugins.get("channel_monitor")
        if monitor is None:
            try:
                await enable_plugin(user_id, "channel_monitor", client)
                await db.set_feature(user_id, "channel_monitor", True)
            except Exception as e:                  # noqa: BLE001
                logger.warning(f"enable monitor failed: {e}")
        plugins = get_active_plugins(user_id)
        monitor = plugins.get("channel_monitor")
        if monitor is not None:
            await monitor.reload_routes()
        return True, ""
    except Exception as e:                          # noqa: BLE001
        logger.error(f"enable listen failed: {e}")
        return False, f"{type(e).__name__}: {str(e)[:120]}"


# ═══════════════════════════════════
# متن‌ها
# ═══════════════════════════════════

SPECIAL_HOME_TEXT = (
    "⭐ <b>ویژگی‌های ویژه</b>\n\n"
    "کاربردی‌های خاص این‌جا جمع می‌شوند.\n\n"
    "🏷 <b>شناسنامه‌ی گروه و کانال</b> — با یک کلیک ببین چه گروه‌ها و "
    "کانال‌هایی داری، اطلاعات هرکدام و <b>مالکش</b> را ببین، و همان‌جا "
    "«دامپ» یا «لیسن» را برایش روشن کن.\n\n"
    "🛡 همه‌ی این کارها از اکانت خودت انجام می‌شود و همه‌چیز در پنل همین "
    "ربات می‌ماند."
)

OWN_MENU_TEXT = (
    "🏷 <b>شناسنامه‌ی گروه و کانال</b>\n\n"
    "هر کدام را که بخواهی باز کن:\n"
    "📢 <b>کانال‌ها</b> — همه‌ی کانال‌هایی که عضوی\n"
    "👥 <b>گروه‌ها</b> — همه‌ی گروه‌هایی که عضوی\n\n"
    "<i>با انتخاب هر مورد: اطلاعات کامل (عنوان، آیدی، یوزرنیم، تعداد اعضا، "
    "تاریخ ساخت، توضیحات) + مالک + دکمه‌های دامپ و لیسن.</i>"
)
