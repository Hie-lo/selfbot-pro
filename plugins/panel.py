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
import psutil
from telethon import events
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

        # ── .راهنما ──
        async def help_cmd(event):
            if not event.out:
                return

            text = """📖 **راهنمای کامل**

── مدیریت ──
`.پنل` — پنل مدیریت و وضعیت قابلیت‌ها
`.راهنما` — همین راهنما
`.وضعیت` — وضعیت سرور (RAM / CPU / کلاینت‌ها)
`.روشن نام` — روشن کردن قابلیت
`.خاموش نام` — خاموش کردن قابلیت

── اشتراک ──
از منوی «💎 اشتراک» در ربات:
انتخاب پلن → کارت به کارت → ارسال عکس رسید → تایید ادمین

── فوروارد محتوا ──
`.فوروارد <مبدأ> به <مقصد>`
  ⤶ فوروارد واقعی با نام منبع

`.فوروارد با آیدی <مبدأ> به <مقصد>`
  ⤶ کپی + هدر مشخصات (چت، فرستنده، تاریخ، لینک پیام)

نمونه هدر:
  📥 از: گروه خانواده (-1001234567890)
  👤 فرستنده: علی رضایی — @ali (123456789)
  🕒 2026-09-22 14:22 (UTC)
  🔗 https://t.me/c/1234567890/55
  ──────────────
  (متن اصلی پیام)

  ⤶ اولویت شناسه فرستنده: یوزرنیم → آیدی عددی → نام
  ⤶ مبدأ و مقصد می‌توانند این‌ها باشند:
     `@username` · آیدی عددی · لینک کانال
     · لینک پیام (t.me/name/123)
     · لینک دعوت گروه خصوصی (t.me/+AbCdEf)

📥 **دکمه «فوروارد محتوا» در ربات** (برای گروه‌های بدون لینک):
  ⤶ انتخاب چت از لیست چت‌های اکانت
  ⤶ فیلتر «فقط کانال و گروه» یا همه چت‌ها
  ⤶ جستجو با نام چت
  ⤶ ورود دستی لینک دعوت برای مبدأ یا مقصد
  ⤶ انتخاب مقصد: چت دیگر / Saved Messages / ورود دستی
  ⤶ تنظیم سه حالت ارسال:
       🏷 کپی با مشخصات فرستنده (پیش‌فرض)
       ↪️ فوروارد با نام منبع
       🔁 کپی بدون نام منبع
  ⤶ تعداد پیام + فقط مدیا + توقف در میانه کار
  ⤶ ⚡ سرعت: محتاط / متعادل / سریع / حداکثری
       موتور خودش با بازخورد تلگرام تنظیم می‌کند (محدودیت خورد
       → کندتر، چند پیام بی‌مشکل رفت → تندتر)
  ⤶ 🖼 استیکر و گیف خودکار از Recents پاک می‌شوند
  ⤶ 🗄 ذخیره روی سرور (پیش‌فرض روشن):
       اول همه‌ی پیام‌ها روی سرور کش می‌شوند و بعد ارسال
       می‌شوند؛ اگر وسط کار از کانال بیرون بیفتید یا دسترسی
       قطع شود، ارسالِ آنچه کش شده ادامه پیدا می‌کند.
  ⤶ 🖼 کش مدیا: دانلود فایل‌ها روی دیسک سرور
       (فضا مصرف می‌کند؛ برای مبدأهایی که ممکن است از بین بروند)

  🔓 **فوروارد گیرکرده:** اگر ربات گفت «یک فوروارد نیمه‌کاره
     جلوی کار را گرفته»، دو راه داری:
       ▶️ ادامه‌ی همان فوروارد (از همان‌جا که مانده)
       🔓 بستن آن و شروع فوروارد جدید
     در چت هم: `.فوروارد بستن`

     ⤶ ردیف‌های مرده (سرور ری‌استارت شده و اجرا ادامه نیافته)
       خودکار آزاد می‌شوند — دیگر گیر نمی‌کنی.

  🔒 در هر دو حالت هیچ پیامی به چت مبدأ ارسال نمی‌شود و
  پیام‌ها «خوانده‌شده» نمی‌شوند. گزارش فقط در Saved Messages
  یا چت ربات نمایش داده می‌شود.

  ♻️ فورواردهای بزرگ:
    • بدون سقف تعداد — تا پایان می‌رود
    • محدودیت تلگرام (FloodWait) → خواب و ادامه خودکار
    • پیشرفت روی سرور ذخیره می‌شود → پس از ری‌استارت
      از همان پیام ادامه می‌دهد (بدون ارسال تکراری)
    • ⏹ توقف (قابل ادامه) و ▶️ ادامه فوروارد

── Recents (استیکر و گیف‌های اخیر) ──
`.recents` — پاکسازی لیست استیکرهای اخیر
  (از ربات: 📂 ذخیره‌سازی → 🧹 پاکسازی)

  ✅ به‌صورت پیش‌فرض هیچ استیکر/گیفی به لیست Recents
  اضافه نمی‌شود:
    • استیکر و گیف به‌صورت «فایل» فرستاده می‌شوند
    • حتی در «فوروارد واقعی»، استیکر/گیف به‌جای
      فوروارد شدن، دانلود و به‌صورت فایل ارسال می‌شود
    • ویدیو و عکس مثل قبل فوروارد می‌شوند

  برای تغییر رفتار: CLEAN_RECENTS_MODE در .env
    document (پیش‌فرض) | cleanup | off

── تاس ──
`.تاس 6` — تاس معمولی
`.تاس 🎲 5` — تاس با ایموجی
`.تاس 🎰 32` — اسلات

── قلب ──
`.قلب` — قلب متحرک (ریپلای هم میشه)

── ذخیره ──
`.ذخیره لینک` — ذخیره پیام از لینک
`.استیکر` — تبدیل استیکر (ریپلای)

── دشمن ──
`.دشمن` — ریپلای: اضافه کردن
`.دشمن @user` — اضافه با یوزرنیم
`.دشمن حذف` — ریپلای: حذف
`.لیست دشمن` — لیست دشمنان
`.بکنش` — ریپلای: شروع اسپم
`.بس` — توقف اسپم

── بنر ──
`.تنظیم بنر 300` — ریپلای: هر 300ث
`.لیست بنر` — لیست بنرها
`.پاکسازی بنر` — حذف بنرها

── مانیتور کانال ──
`.مانیتور @src @dst` — ست مسیر
`.مانیتور حذف @src` — حذف
`.لیست مانیتور` — لیست

── ذخیره‌سازی ──
مسیر ذخیره هر قابلیت (Saved Messages یا چت دلخواه)
از دکمه «📂 ذخیره‌سازی» در ربات قابل تنظیم است."""
            await event.edit(text)

        self._add_handler(
            help_cmd,
            events.NewMessage(pattern=r"^\.راهنما$", outgoing=True),
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