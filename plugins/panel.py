"""
پلاگین پنل و راهنما از داخل selfbot
کامندها:
  .پنل        — نمایش وضعیت و قابلیت‌ها
  .راهنما     — لیست کامندها
  .روشن نام   — روشن کردن قابلیت
  .خاموش نام  — خاموش کردن قابلیت
  .وضعیت      — وضعیت اکانت و سرور
"""

import os
import re
import psutil
from telethon import Button, events
from plugins.base import BasePlugin
from database import db


# نام‌های فارسی قابلیت‌ها
FEATURE_NAMES = {
    "dice": "🎲 تاس",
    "heart_animation": "❤️ قلب متحرک",
    "save_from_link": "🔗 ذخیره از لینک",
    "sticker_convert": "🖼 تبدیل استیکر",
    "banner": "📢 بنر",
    "timed_saver": "⏳ تایم‌دار",
    "anti_delete": "🗑 ضد حذف",
    "anti_edit": "✏️ ضد ویرایش",
    "auto_response": "💬 دشمن",
    "channel_monitor": "📡 مانیتور",
}

# قابلیت‌هایی که همیشه روشنن
ALWAYS_ON = {"dice", "heart_animation", "save_from_link", "sticker_convert"}


class PanelPlugin(BasePlugin):
    name = "panel"
    description = "پنل و راهنما"
    always_on = True

    async def start(self):

        # ── .پنل ──
        async def panel_cmd(event):
            if not event.out:
                return

            features = await db.get_features(self.user_id)
            enabled = {f["feature_name"] for f in features if f["is_enabled"]}

            text = "⚙️ **پنل مدیریت**\n\n"

            text += "── همیشه فعال ──\n"
            for key in ALWAYS_ON:
                name = FEATURE_NAMES.get(key, key)
                text += f"  ✅ {name}\n"

            text += "\n── قابل تنظیم ──\n"
            for key, name in FEATURE_NAMES.items():
                if key in ALWAYS_ON:
                    continue
                status = "✅" if key in enabled else "❌"
                text += f"  {status} {name}\n"

            text += (
                "\n── دستورات ──\n"
                "  `.روشن ضد حذف` — روشن کردن\n"
                "  `.خاموش ضد حذف` — خاموش کردن\n"
                "  `.راهنما` — لیست کامل دستورات\n"
                "  `.وضعیت` — وضعیت سرور\n"
            )

            await event.edit(text)

        self._add_handler(
            panel_cmd,
            events.NewMessage(pattern=r"^\.پنل$", outgoing=True),
        )

        # ── .راهنما — پنل دکمه‌ای ──
        from telethon import Button

        HELP_MAIN = (
            "📖 **راهنما — پنل اصلی**\n\n"
            "یک دسته رو انتخاب کن:\n"
            "⚙️ مدیریت · 💎 اشتراک · 📥 فوروارد · 📂 ذخیره‌سازی\n"
            "🎲 سرگرمی · 🛡️ ضدحذف/ویرایش · 💬 دشمن · 📢 بنر/مانیتور\n\n"
            "💡 نکته: همه‌ی دستورات با `.` شروع می‌شن و فقط برای خودت کار می‌کنن."
        )

        HELP_TEXTS = {
            "mgmt": (
                "⚙️ **مدیریت**\n\n"
                "`.پنل` — پنل مدیریت و وضعیت قابلیت‌ها\n"
                "`.راهنما` — همین پنل\n"
                "`.وضعیت` — وضعیت سرور (RAM/CPU/کلاینت‌ها)\n"
                "`.روشن نام` — روشن کردن قابلیت\n"
                "`.خاموش نام` — خاموش کردن\n\n"
                "نام‌های قابل استفاده: ضد حذف، ضد ویرایش، تایم‌دار، بنر، دشمن، مانیتور و…\n"
                "همیشه فعال: 🎲 تاس، ❤️ قلب، 🔗 ذخیره از لینک، 🖼 استیکر"
            ),
            "sub": (
                "💎 **اشتراک**\n\n"
                "از ربات → «💎 اشتراک»: پلن رو انتخاب کن → کارت به کارت → عکس رسید رو بفرست → ادمین تایید می‌کنه.\n\n"
                "بعد از تایید، همه‌ی قابلیت‌های پولی باز می‌شه و `.پنل` وضعیت رو نشون می‌ده."
            ),
            "forward": (
                "📥 **فوروارد محتوا**\n\n"
                "`.فوروارد <مبدأ> به <مقصد>` — فوروارد واقعی با نام منبع\n"
                "`.فوروارد با آیدی <مبدأ> به <مقصد>` — کپی + هدر مشخصات\n\n"
                "نمونه هدر:\n"
                "  📥 از: گروه خانواده (-100...)\n"
                "  👤 فرستنده: علی — @ali (123456789)\n"
                "  🕒 2026-09-22 14:22 (UTC)\n"
                "  🔗 https://t.me/c/.../55\n"
                "  ──────────────\n\n"
                "📋 **ربات → 📥 فوروارد محتوا:**\n"
                "  لیست چت‌های اکانت (حتی بدون لینک)، فیلتر «فقط کانال/گروه»، جستجو، ورود دستی لینک دعوت،\n"
                "  ۳ حالت: 🏷 کپی با مشخصات / ↪️ فوروارد / 🔁 کپی بدون نام\n"
                "  ⚡ سرعت: محتاط/متعادل/سریع/حداکثری (خودکار با FloodWait)\n"
                "  🖼 استیکر/گیف خودکار از Recents پاک می‌شه\n"
                "  🗄 کش روی سرور: اول همه روی سرور کش، بعد ارسال — قطع دسترسی هم ادامه می‌ده\n"
                "  🔓 گیر کرد؟ ▶️ ادامه یا 🔓 بستن / `.فوروارد بستن` — ردیف مرده خودکار آزاد می‌شه\n"
                "  🗑 حذف کامل: دکمه‌ی «حذف کامل» یا `.فوروارد حذف`\n"
                "  ♻️ بدون سقف، FloodWait خودکار، پیشرفت ذخیره، ⏹/▶️ توقف/ادامه"
            ),
            "storage": (
                "📂 **ذخیره‌سازی (مسیرها)**\n\n"
                "هر قابلیت مقصدش جدا تنظیم می‌شه: Saved Messages یا کانال/گروه دلخواه\n\n"
                "📍 تنظیم: ربات → 📂 ذخیره‌سازی → انتخاب قابلیت →\n"
                "  • 💾 Saved Messages\n"
                "  • 📢 چنل/گروه (ارسال آیدی @ یا -100...)\n"
                "  • 👑 کانال‌ها/گروه‌های من (فقط مالکم) — لیست صفحه‌بندی\n\n"
                "قابلیت‌ها: ضد حذف، ضد ویرایش، تایم‌دار، دانلود خودکار، ذخیره از لینک، مانیتور\n"
                "مانیتور: `.مانیتور @src @dst` / `.مانیتور حذف @src` / `.لیست مانیتور`"
            ),
            "fun": (
                "🎲 **سرگرمی**\n\n"
                "`.تاس 6` — تاس معمولی\n"
                "`.تاس 🎲 5` — با ایموجی\n"
                "`.تاس 🎰 32` — اسلات\n\n"
                "❤️ **قلب:**\n"
                "`.قلب` — قلب متحرک (روی ریپلای هم میشه)\n"
                "  بعد از **سین زدنِ طرف مقابل** انیمیشن شروع می‌شه (تا 120ث صبر می‌کنه، بعد خودش شروع می‌کنه)\n"
                "  ۱۲ قلب رنگی × ۳ دور، در گروه بدون انتظار سین"
            ),
            "protect": (
                "🛡️ **ضد حذف / ضد ویرایش / Recents**\n\n"
                "🗑 ضد حذف (فقط پی‌وی): پیامِ حذف‌شده با ترتیب اصلی ذخیره می‌شه\n"
                "  هدر مثل فوروارد: 📥 از، 👤 فرستنده — @user (id)، 🕒 تاریخ، 🔗 لینک/ID\n"
                "  مدیا با Recents پاک\n"
                "✏️ ضد ویرایش: متنِ قبل و بعد ذخیره می‌شه\n\n"
                "🧹 **Recents:**\n"
                "`.recents` یا ربات → 📂 ذخیره‌سازی → 🧹 پاکسازی\n"
                "  پیش‌فرض هیچ استیکر/گیفی به Recents اضافه نمی‌شه (فایل + پاکسازی خودکار)\n"
                "  حالت‌ها: CLEAN_RECENTS_MODE=document/cleanup/off"
            ),
            "spam": (
                "💬 **دشمن / اسپم**\n\n"
                "`.دشمن` (ریپلای) — اضافه\n"
                "`.دشمن @user` — با یوزرنیم\n"
                "`.دشمن حذف` (ریپلای) — حذف\n"
                "`.لیست دشمن` — لیست\n"
                "`.بکنش` (ریپلای) — شروع اسپم\n"
                "`.بس` — توقف\n\n"
                "📢 **بنر:**\n"
                "`.تنظیم بنر 300` (ریپلای) — هر 300ث\n"
                "`.لیست بنر` — لیست\n"
                "`.پاکسازی بنر` — حذف همه"
            ),
            "save": (
                "🔗 **ذخیره**\n\n"
                "`.ذخیره لینک` — ذخیره پیام از لینک (حتی خصوصی اگر عضو باشی)\n"
                "`.استیکر` (ریپلای) — تبدیل عکس/گیف به استیکر\n\n"
                "📡 **مانیتور:**\n"
                "`.مانیتور @src @dst` — ست\n"
                "`.مانیتور حذف @src` — حذف\n"
                "`.لیست مانیتور` — لیست"
            ),
            "all": None,  # پر می‌شود پایین
        }
        # متن کامل برای دکمه همه
        HELP_TEXTS["all"] = (
            "📖 **راهنمای کامل — همه دستورات**\n\n"
            + HELP_TEXTS["mgmt"] + "\n\n"
            + HELP_TEXTS["sub"] + "\n\n"
            + HELP_TEXTS["forward"] + "\n\n"
            + HELP_TEXTS["protect"] + "\n\n"
            + HELP_TEXTS["fun"] + "\n\n"
            + HELP_TEXTS["save"] + "\n\n"
            + HELP_TEXTS["spam"] + "\n\n"
            + HELP_TEXTS["storage"]
        )

        def help_kb(page="main"):
            if page == "main":
                return [
                    [Button.inline("⚙️ مدیریت", b"help_mgmt"), Button.inline("💎 اشتراک", b"help_sub")],
                    [Button.inline("📥 فوروارد", b"help_forward"), Button.inline("📂 ذخیره‌سازی", b"help_storage")],
                    [Button.inline("🎲/❤️ سرگرمی", b"help_fun"), Button.inline("🛡️ ضدحذف", b"help_protect")],
                    [Button.inline("💬 دشمن", b"help_spam"), Button.inline("🔗 ذخیره/بنر", b"help_save")],
                    [Button.inline("📖 همه", b"help_all"), Button.inline("❌ بستن", b"help_close")],
                ]
            else:
                return [
                    [Button.inline("🔙 بازگشت", b"help_main"), Button.inline("❌ بستن", b"help_close")],
                ]

        async def help_cmd(event):
            if not event.out:
                return
            # پیام راهنما با دکمه — اول کامند رو پاک کن و پنل بفرست
            try:
                await event.delete()
            except Exception:
                pass
            await self.client.send_message(event.chat_id, HELP_MAIN, buttons=help_kb("main"), parse_mode="md")

        self._add_handler(
            help_cmd,
            events.NewMessage(pattern=r"^\.راهنما$", outgoing=True),
        )

        async def help_callback(event):
            # فقط برای صاحب اکانت
            if not event.is_private and event.sender_id != self.user_id:
                # در گروه، فقط صاحب سلف اجازه دارد — بقیه نادیده
                try:
                    me = await self.client.get_me()
                    if event.sender_id != me.id:
                        await event.answer("فقط صاحب اکانت", alert=True)
                        return
                except Exception:
                    pass
            data = event.data.decode() if isinstance(event.data, bytes) else str(event.data)
            if data == "help_close":
                try:
                    await event.delete()
                except Exception:
                    try:
                        await self.client.delete_messages(event.chat_id, event.message_id)
                    except Exception:
                        pass
                await event.answer()
                return
            if data == "help_main":
                await event.edit(HELP_MAIN, buttons=help_kb("main"), parse_mode="md")
                await event.answer()
                return
            key = data.replace("help_", "", 1)
            text = HELP_TEXTS.get(key)
            if text:
                # تلگرام سقف 4096 کاراکتر دارد — متن‌ها کوتاه نگه داشته شدند
                await event.edit(text, buttons=help_kb(key), parse_mode="md")
                await event.answer()
            else:
                await event.answer("پیدا نشد", alert=True)

        self._add_handler(
            help_callback,
            events.CallbackQuery(pattern=re.compile(b"help_")),
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

            await db.set_feature(self.user_id, feat_key, True)

            # load plugin
            from core.client_manager import get_client
            from core.plugin_manager import enable_plugin
            client = await get_client(self.user_id)
            if client:
                await enable_plugin(self.user_id, feat_key, client)

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
            enabled_count = sum(1 for f in features if f["is_enabled"])

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

    def _resolve_feature(self, name: str) -> str | None:
        """تبدیل نام فارسی/انگلیسی به کلید"""
        name = name.strip().lower()

        # چک مستقیم کلید
        if name in FEATURE_NAMES:
            return name

        # چک نام فارسی
        for key, fname in FEATURE_NAMES.items():
            # حذف ایموجی و فاصله
            clean = fname.replace(" ", "").lower()
            # حذف ایموجی‌ها
            import re
            clean = re.sub(r'[^\w]', '', clean)
            name_clean = re.sub(r'[^\w]', '', name)

            if name_clean in clean or clean in name_clean:
                return key

        # مپ دستی
        manual = {
            "تاس": "dice",
            "قلب": "heart_animation",
            "ذخیره": "save_from_link",
            "استیکر": "sticker_convert",
            "بنر": "banner",
            "تایم‌دار": "timed_saver",
            "تایمدار": "timed_saver",
            "ضدحذف": "anti_delete",
            "ضد حذف": "anti_delete",
            "ضدویرایش": "anti_edit",
            "ضد ویرایش": "anti_edit",
            "دشمن": "auto_response",
            "مانیتور": "channel_monitor",
        }

        return manual.get(name, None)