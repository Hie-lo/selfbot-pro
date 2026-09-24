"""
پلاگین پنل و راهنما از داخل selfbot
کامندها:
  .پنل            — نمایش وضعیت و قابلیت‌ها
  .راهنما         — راهنمای دکمه‌ای (از طریق inline mode ربات کنترلی)
  .راهنما ‹بخش›   — مستقیم یک بخش (مثل: .راهنما فوروارد)
  .روشن نام       — روشن کردن قابلیت (فقط با اشتراک فعال)
  .خاموش نام      — خاموش کردن قابلیت
  .وضعیت          — وضعیت اکانت و سرور
  .recents        — پاکسازی استیکرهای اخیر
"""

import os
import re

import psutil
from telethon import events

from bot import help_content as H
from core import runtime
from database import db
from plugins.base import BasePlugin

# نام‌های فارسی قابلیت‌ها
FEATURE_NAMES = {
    "dice": "🎲 تاس",
    "heart_animation": "❤️ قلب متحرک",
    "save_from_link": "🔗 ذخیره از لینک",
    "sticker_convert": "🖼 تبدیل استیکر",
    "forward_channel": "📥 فوروارد",
    "banner": "📢 بنر",
    "timed_saver": "⏳ تایم‌دار",
    "anti_delete": "🗑 ضد حذف",
    "anti_edit": "✏️ ضد ویرایش",
    "auto_response": "💬 دشمن",
    "channel_monitor": "📡 مانیتور",
}

# قابلیت‌های همیشه روشن که به کاربر نمایش داده می‌شوند.
# باید با core.plugin_manager.ALWAYS_ON_PLUGINS یکی باشد (پنل خودش نمایش
# داده نمی‌شود) — تست tests/test_help_coverage.py این را چک می‌کند.
ALWAYS_ON = {"dice", "heart_animation", "save_from_link", "sticker_convert", "forward_channel"}

# نام‌های دستی برای .روشن / .خاموش
_MANUAL_NAMES = {
    "تاس": "dice",
    "قلب": "heart_animation",
    "ذخیره": "save_from_link",
    "استیکر": "sticker_convert",
    "فوروارد": "forward_channel",
    "بنر": "banner",
    "تایم‌دار": "timed_saver",
    "تایمدار": "timed_saver",
    "تایم دار": "timed_saver",
    "ضدحذف": "anti_delete",
    "ضد حذف": "anti_delete",
    "ضدویرایش": "anti_edit",
    "ضد ویرایش": "anti_edit",
    "دشمن": "auto_response",
    "پاسخ خودکار": "auto_response",
    "مانیتور": "channel_monitor",
}


class PanelPlugin(BasePlugin):
    name = "panel"
    description = "پنل و راهنما"
    always_on = True

    async def start(self):
        # ثبت آیدی اکانت برای مجاز بودن inline query راهنما
        try:
            me = await self.client.get_me()
            self._account_id = me.id
            runtime.selfbot_accounts[me.id] = self.user_id
        except Exception:
            self._account_id = None

        # ── .پنل ──
        async def panel_cmd(event):
            if not event.out:
                return

            features = await db.get_features(self.user_id)
            enabled = {f["feature_name"] for f in features if f["is_enabled"]}

            text = "⚙️ **پنل مدیریت**\n\n── همیشه فعال ──\n"
            for key in FEATURE_NAMES:
                if key in ALWAYS_ON:
                    text += f"  ✅ {FEATURE_NAMES[key]}\n"

            text += "\n── قابل تنظیم ──\n"
            for key, fname in FEATURE_NAMES.items():
                if key in ALWAYS_ON:
                    continue
                status = "✅" if key in enabled else "❌"
                text += f"  {status} {fname}\n"

            text += (
                "\n── دستورات ──\n"
                "  `.روشن ضد حذف` — روشن کردن\n"
                "  `.خاموش ضد حذف` — خاموش کردن\n"
                "  `.راهنما` — راهنمای کامل دکمه‌ای\n"
                "  `.وضعیت` — وضعیت سرور\n"
            )
            await event.edit(text)

        self._add_handler(
            panel_cmd,
            events.NewMessage(pattern=r"^\.پنل$", outgoing=True),
        )

        # ── .راهنما [بخش] ──
        async def help_cmd(event):
            if not event.out:
                return
            arg = (event.pattern_match.group(1) or "").strip()
            key = "main"
            if arg:
                key = H.resolve_section(arg)
                if not key:
                    await event.edit(
                        f"❓ بخش «{arg[:40]}» پیدا نشد.\n\n" + H.text_index(),
                        parse_mode="html",
                    )
                    return

            if await self._send_inline_help(event, key):
                return

            # حالت متنی (inline mode ربات خاموش است یا در دسترس نیست)
            if key == "main":
                text = H.text_index()
            else:
                text = (
                    H.section_text(key)
                    + "\n\n──────────────\n📖 فهرست بخش‌ها: <code>.راهنما</code>"
                )
            await event.edit(text, parse_mode="html", link_preview=False)

        self._add_handler(
            help_cmd,
            events.NewMessage(pattern=r"^\.راهنما(?:\s+(.+))?$", outgoing=True),
        )

        # ── .روشن ──
        async def enable_cmd(event):
            if not event.out:
                return

            feat_name = event.pattern_match.group(1).strip()
            feat_key = self._resolve_feature(feat_name)

            if not feat_key:
                await event.edit(f"❌ قابلیت «{feat_name}» پیدا نشد.")
                return

            if feat_key in ALWAYS_ON:
                await event.edit(f"✅ «{FEATURE_NAMES[feat_key]}» همیشه فعاله.")
                return

            # اشتراک باید معتبر باشد (قبلاً این مسیر اشتراک را چک نمی‌کرد و
            # قابلیت‌های پولی بدون پرداخت روشن می‌شدند)
            from core.access import eligible
            user = await db.get_user_by_db_id(self.user_id)
            ok, reason = eligible(user)
            if not ok:
                await event.edit(
                    "❌ برای روشن کردن این قابلیت اشتراک فعال لازم است.\n"
                    "از ربات → «💎 اشتراک» تمدید کنید."
                )
                return

            from core.plugin_manager import enable_plugin
            loaded = await enable_plugin(self.user_id, feat_key, self.client)
            if not loaded:
                await event.edit("❌ فعال‌سازی ناموفق بود.")
                return

            await db.set_feature(self.user_id, feat_key, True)
            await db.audit_log(self.user_id, "feature_toggle", f"{feat_key} -> ON (chat)")
            await event.edit(f"✅ «{FEATURE_NAMES.get(feat_key, feat_key)}» روشن شد.")

        self._add_handler(
            enable_cmd,
            events.NewMessage(pattern=r"^\.روشن\s+(.+)$", outgoing=True),
        )

        # ── .خاموش ──
        async def disable_cmd(event):
            if not event.out:
                return

            feat_name = event.pattern_match.group(1).strip()
            feat_key = self._resolve_feature(feat_name)

            if not feat_key:
                await event.edit(f"❌ قابلیت «{feat_name}» پیدا نشد.")
                return

            if feat_key in ALWAYS_ON:
                await event.edit(f"❌ «{FEATURE_NAMES[feat_key]}» قابل خاموش شدن نیست.")
                return

            await db.set_feature(self.user_id, feat_key, False)

            from core.plugin_manager import disable_plugin
            await disable_plugin(self.user_id, feat_key)
            await db.audit_log(self.user_id, "feature_toggle", f"{feat_key} -> OFF (chat)")

            await event.edit(f"❌ «{FEATURE_NAMES.get(feat_key, feat_key)}» خاموش شد.")

        self._add_handler(
            disable_cmd,
            events.NewMessage(pattern=r"^\.خاموش\s+(.+)$", outgoing=True),
        )

        # ── .وضعیت ──
        async def status_cmd(event):
            if not event.out:
                return

            process = psutil.Process(os.getpid())
            ram = process.memory_info().rss / 1024 / 1024
            cpu = process.cpu_percent()

            from core.client_manager import active_clients
            n_clients = len(active_clients)

            features = await db.get_features(self.user_id)
            enabled_count = sum(
                1 for f in features
                if f["is_enabled"] and f["feature_name"] not in ALWAYS_ON
            )

            text = (
                f"📊 **وضعیت**\n\n"
                f"👥 کلاینت‌های فعال: {n_clients}\n"
                f"💾 RAM: {ram:.0f} MB\n"
                f"⚡ CPU: {cpu:.1f}%\n"
                f"🧩 قابلیت‌های روشن: {enabled_count + len(ALWAYS_ON)}\n"
            )
            await event.edit(text)

        self._add_handler(
            status_cmd,
            events.NewMessage(pattern=r"^\.وضعیت$", outgoing=True),
        )

        # ── .recents ──
        async def recents_cmd(event):
            if not event.out:
                return

            from core.media import clear_recent_stickers
            ok = await clear_recent_stickers(self.client)
            await event.edit(
                "✅ لیست استیکرهای اخیر پاک شد."
                if ok else "❌ پاکسازی ناموفق بود."
            )

        self._add_handler(
            recents_cmd,
            events.NewMessage(pattern=r"^\.recents$", outgoing=True),
        )

        self.logger.info("loaded")

    async def _send_inline_help(self, event, key: str) -> bool:
        """
        ارسال راهنمای دکمه‌ای از طریق inline mode ربات کنترلی.
        (اکانت کاربر خودش نمی‌تواند دکمه‌ی شیشه‌ای بفرستد.)
        """
        bot_username = runtime.bot_username
        if not bot_username:
            return False

        query = "help" if key == "main" else f"help {key}"
        try:
            results = await self.client.inline_query(bot_username, query)
            if not results:
                return False
            reply_to = event.reply_to_msg_id
            try:
                await results[0].click(event.chat_id, reply_to=reply_to, hide_via=True)
            except Exception:
                await results[0].click(event.chat_id, reply_to=reply_to)
        except Exception as e:
            self.logger.info(f"inline help unavailable ({type(e).__name__}: {e})")
            return False

        try:
            await event.delete()
        except Exception:
            pass
        return True

    async def stop(self):
        if getattr(self, "_account_id", None):
            runtime.selfbot_accounts.pop(self._account_id, None)
        await super().stop()

    def _resolve_feature(self, name: str) -> str | None:
        """تبدیل نام فارسی/انگلیسی به کلید"""
        name = name.strip().lower()

        if name in _MANUAL_NAMES:
            return _MANUAL_NAMES[name]
        if name in FEATURE_NAMES:
            return name

        name_clean = re.sub(r"[^\w]", "", name)
        if not name_clean:
            return None
        for key, fname in FEATURE_NAMES.items():
            clean = re.sub(r"[^\w]", "", fname.lower())
            if name_clean == clean or name_clean in clean:
                return key
        return None
