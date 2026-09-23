TEXTS = {
    "welcome": (
        "👋 سلام {name}!\n\n"
        "به سلف‌بات پرو خوش آمدید.\n\n"
        "برای شروع، اشتراک تهیه کنید."
    ),
    "no_subscription": (
        "❌ اشتراک فعال ندارید.\n\n"
        "💰 قیمت: {price} تومان / ماهانه\n\n"
        "برای تهیه با ادمین تماس بگیرید."
    ),
    "subscription_active": "✅ اشتراک فعال\n📅 انقضا: {expires}",
    "subscription_expired": "⚠️ اشتراک منقضی شده.",
    "login_start": (
        "📱 شماره تلفن خود را وارد کنید.\n\n"
        "مثال: +989121234567"
    ),
    "login_code_sent": (
        "✅ کد تایید ارسال شد.\n\n"
        "🔢 کد ۵ رقمی را وارد کنید:\n"
        "⏱ مهلت: {timeout} ثانیه"
    ),
    "login_2fa": (
        "🔐 رمز دوعاملی را وارد کنید:\n"
        "⏱ مهلت: {timeout} ثانیه"
    ),
    "login_success": "✅ اکانت متصل شد!",
    "login_failed": "❌ خطا: {error}",
    "login_timeout": "⏱ زمان تمام شد.",
    "login_too_many": "🚫 تلاش بیش از حد.",
    "login_invalid_phone": "❌ شماره نامعتبر.",
    "login_invalid_code": "❌ کد نامعتبر.",
    "login_already": "⚠️ قبلاً متصل شده.",
    "panel_title": (
        "⚙️ **پنل مدیریت**\n\n"
        "اکانت: {status}\n"
        "اشتراک: {plan}"
    ),
    "feature_on": "✅ «{name}» فعال شد.",
    "feature_off": "❌ «{name}» غیرفعال شد.",
    "storage_set": "✅ مسیر «{feature}»: {target}",
    "storage_ask": (
        "📂 مسیر ذخیره «{feature}»:\n\n"
        "۱. Saved Messages\n"
        "۲. آیدی/یوزرنیم چنل/گروه"
    ),
    "status_connected": "🟢 متصل",
    "status_disconnected": "🔴 قطع",
    "error_general": "❌ خطا رخ داد.",
    "error_banned": "🚫 مسدود شدید.",
    "error_no_account": "❌ اول اکانت متصل کنید.",
    "btn_panel": "⚙️ پنل",
    "btn_connect": "🔗 اتصال",
    "btn_disconnect": "🔌 قطع",
    "btn_status": "📊 وضعیت",
    "btn_help": "📖 راهنما",
    "btn_back": "🔙 بازگشت",
    "btn_confirm": "✅ تایید",
    "btn_cancel": "❌ انصراف",
    "btn_features": "🧩 قابلیت‌ها",
    "btn_storage": "📂 ذخیره‌سازی",
    "btn_subscription": "💎 اشتراک",
    "fname_dice": "🎲 تاس",
    "fname_banner": "📢 بنر",
    "fname_timed_saver": "⏳ تایم‌دار",
    "fname_anti_delete": "🗑 ضد حذف",
    "fname_anti_edit": "✏️ ضد ویرایش",
    "fname_save_from_link": "🔗 ذخیره از لینک",
    "fname_sticker_convert": "🖼 تبدیل استیکر",
    "fname_heart_animation": "❤️ قلب متحرک",
    "fname_channel_monitor": "📡 مانیتور کانال",
    "fname_auto_response": "💬 پاسخ خودکار",
    # ── خرید اشتراک ──
    "sub_choose_plan": (
        "💎 <b>خرید اشتراک</b>\n\n"
        "پلن مورد نظر را انتخاب کنید:\n\n"
        "{plans}"
    ),
    "sub_plan_detail": (
        "💎 <b>پلن {title}</b>\n\n"
        "💰 مبلغ: <b>{price} تومان</b>\n"
        "📅 مدت: {days} روز\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🏦 <b>پرداخت کارت به کارت</b>\n\n"
        "💳 شماره کارت:\n<code>{card}</code>\n"
        "👤 به نام: <b>{holder}</b>{bank}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📌 <b>مراحل:</b>\n"
        "۱. مبلغ <b>{price} تومان</b> را کارت به کارت کنید\n"
        "۲. روی دکمه «📸 ارسال عکس رسید» بزنید\n"
        "۳. <b>عکس رسید</b> را در همین چت بفرستید\n"
        "۴. پس از بررسی و تایید ادمین، اشتراک فعال می‌شود\n\n"
        "⚠️ فقط عکس رسید بفرستید؛ شماره کارت را به کسی ندهید."
    ),
    "sub_ask_receipt": (
        "📸 <b>عکس رسید پرداخت را بفرستید</b>\n\n"
        "پلن: <b>{title}</b>\n"
        "مبلغ: <b>{price} تومان</b>\n\n"
        "عکس رسید را همین‌جا در چت ارسال کنید.\n"
        "برای انصراف /cancel بزنید."
    ),
    "sub_receipt_invalid": (
        "❌ لطفاً <b>عکس</b> رسید را بفرستید (jpg / png / سند عکس)."
    ),
    "sub_receipt_received": (
        "✅ <b>درخواست شما ثبت شد!</b>\n\n"
        "🆔 شماره درخواست: <code>{req_id}</code>\n"
        "💎 پلن: {title}\n"
        "💰 مبلغ: {price} تومان\n\n"
        "⏳ در انتظار تایید ادمین. نتیجه به شما اطلاع داده می‌شود."
    ),
    "sub_already_pending": (
        "⏳ شما یک درخواست در انتظار بررسی دارید.\n"
        "لطفاً تا بررسی ادمین صبر کنید."
    ),
    "sub_approved": (
        "🎉 <b>اشتراک شما فعال شد!</b>\n\n"
        "💎 پلن: {title}\n"
        "📅 انقضا: <b>{expires}</b>\n"
        "⏳ روزهای باقی‌مانده: {days}"
    ),
    "sub_rejected": (
        "❌ <b>درخواست اشتراک شما رد شد.</b>\n\n"
        "{reason}\n\n"
        "در صورت اعتراض با پشتیبانی تماس بگیرید."
    ),
    "sub_no_request": "📭 هنوز درخواست اشتراکی ثبت نکرده‌اید.",
    "sub_my_requests": (
        "🧾 <b>درخواست‌های اشتراک شما</b>\n\n"
        "🆔 {req_id} | 💎 {title} | {status} | {date}"
    ),
    "status_pending": "⏳ در انتظار بررسی",
    "status_approved": "✅ تایید شده",
    "status_rejected": "❌ رد شده",
    # ── پنل ادمین ──
    "admin_only": "🚫 این بخش فقط برای ادمین است.",
    "admin_panel": (
        "🛠 <b>پنل ادمین</b>\n\n"
        "📥 درخواست‌های در انتظار: <b>{pending}</b>\n\n"
        "یک گزینه را انتخاب کنید:"
    ),
    "admin_stats": (
        "📊 <b>آمار</b>\n\n"
        "👥 کل کاربران: <b>{total}</b>\n"
        "💎 اشتراک فعال: <b>{premium}</b>\n"
        "🚫 مسدود: <b>{banned}</b>\n"
        "🔌 اکانت متصل: <b>{sessions}</b>\n"
        "📥 درخواست در انتظار: <b>{pending}</b>"
    ),
    "admin_no_requests": "📭 درخواست در انتظار بررسی وجود ندارد.",
    "admin_users_title": (
        "👥 <b>مدیریت مشتریان</b>\n\n"
        "صفحه {page} — {total} کاربر\n"
        "💎 اشتراک فعال · ⚪ بدون اشتراک · 🚫 مسدود\n\n"
        "برای مدیریت روی هر کاربر بزنید:"
    ),
    "admin_user_title": (
        "👤 <b>{name}</b>{username}\n\n"
        "🆔 عددی: <code>{tg_id}</code>\n"
        "📦 پلن: <b>{plan}</b>\n"
        "📅 انقضا: <b>{expires}</b>\n"
        "📊 وضعیت: {status}\n"
        "🧩 قابلیت‌های روشن: {features}\n\n"
        "برای فعال‌سازی اشتراک، پلن را انتخاب کنید:"
    ),
    "admin_user_granted": (
        "✅ اشتراک <b>{plan}</b> برای <code>{tg_id}</code> فعال شد.\n"
        "📅 انقضای جدید: <b>{expires}</b>"
    ),
    "admin_user_cancelled": "➖ اشتراک کاربر حذف شد.",
    "admin_user_banned": "🚫 کاربر مسدود شد.",
    "admin_user_unbanned": "✅ مسدودی کاربر برداشته شد.",
    "admin_req_approved": "✅ درخواست #{req_id} تایید شد.",
    "admin_req_rejected": "❌ درخواست #{req_id} رد شد.",
    "admin_req_ask_reject": "علت رد درخواست را بنویسید:\n(یا /cancel بزنید)",
    "admin_req_new": (
        "🔔 <b>درخواست اشتراک جدید</b>\n\n"
        "🆔 درخواست: <code>{req_id}</code>\n"
        "👤 کاربر: {name}{username}\n"
        "🆔 عددی: <code>{tg_id}</code>\n"
        "💎 پلن: <b>{title}</b> ({days} روز)\n"
        "💰 مبلغ: <b>{price} تومان</b>\n"
        "📅 تاریخ: {date}\n\n"
        "عکس رسید در پیام بعدی است."
    ),
    "admin_req_note": "🧾 رسید درخواست #{req_id} — کاربر {tg_id}",
    "admin_reversed": "⚠️ این درخواست قبلاً بررسی شده است.",
    "recents_ask": (
        "🧹 <b>پاکسازی لیست «استیکرهای اخیر»</b>\n\n"
        "این کار کل لیست استیکرهای Recent اکانت را پاک می‌کند.\n"
        "فایل‌های ذخیره‌شده در Saved Messages دست‌نخورده می‌مانند.\n\n"
        "ادامه می‌دهید؟"
    ),
    "recents_done": "✅ لیست استیکرهای اخیر پاک شد.",
    "recents_failed": "❌ پاکسازی ناموفق بود.",
    # ── فوروارد محتوا ──
    "fwd_loading": "⏳ در حال خواندن لیست چت‌های اکانت...",
    "fwd_no_dialogs": (
        "📭 چتی پیدا نشد.\n"
        "مطمئن شوید اکانت متصل است و دوباره تلاش کنید."
    ),
    "fwd_pick_source": (
        "📥 <b>فوروارد محتوا — انتخاب چت مبدأ</b>\n\n"
        "🗂 کل چت‌ها: {total} | نمایش: {found}\n"
        "📄 صفحه {page} از {pages}\n"
        "{filter}\n"
        "🔎 جستجو: {query}\n\n"
        "پیشنهاد: <code>@channel</code>، گروه‌های خصوصی و پیوی هم در لیست هستند.\n"
        "🔒 هیچ پیامی به چت مبدأ ارسال نمی‌شود."
    ),
    "fwd_pick_dest": (
        "📥 <b>فوروارد محتوا — انتخاب چت مقصد</b>\n\n"
        "📤 مبدأ: <b>{src}</b>\n"
        "🗂 کل چت‌ها: {total} | نمایش: {found}\n"
        "📄 صفحه {page} از {pages}\n"
        "{filter}\n"
        "🔎 جستجو: {query}"
    ),
    "fwd_filter_on": "🔍 فیلتر: فقط کانال و گروه",
    "fwd_filter_off": "🗂 فیلتر: همه چت‌ها",
    "fwd_search_prompt": (
        "🔎 <b>جستجوی چت</b>\n\n"
        "بخشی از نام چت را بفرستید.\n"
        "مثلاً: <code>خانواده</code> یا <code>news</code>\n\n"
        "برای انصراف /cancel بزنید."
    ),
    "fwd_search_empty": (
        "📭 چتی با نام «{query}» پیدا نشد.\n\n"
        "دوباره تلاش کنید یا فیلتر را عوض کنید."
    ),
    "fwd_manual_prompt": (
        "✍️ <b>مقصد را دستی وارد کنید</b>\n\n"
        "یکی از این‌ها را بفرستید:\n"
        "<code>@channel</code>\n"
        "<code>-1001234567890</code>\n"
        "<code>https://t.me/channel</code>\n\n"
        "/cancel برای انصراف"
    ),
    "fwd_src_manual_note": (
        "✍️ <b>مبدأ را دستی وارد کنید</b>\n\n"
        "یوزرنیم، آیدی عددی، لینک کانال یا <b>لینک دعوت</b> گروه خصوصی:\n"
        "<code>@channel</code>\n"
        "<code>-1001234567890</code>\n"
        "<code>https://t.me/+AbCdEfGhIj</code>"
    ),
    "fwd_join_prompt": (
        "🔒 <b>{title}</b>\n\n"
        "این چت با لینک دعوت شناسایی شد ولی اکانت شما عضو آن نیست.\n"
        "برای دامپ کردن باید عضو شوید.\n\n"
        "⚠️ <b>توجه:</b> عضویت شما برای اعضای گروه قابل مشاهده است "
        "(پیام «به گروه پیوست» ثبت می‌شود).\n\n"
        "ادامه می‌دهید؟"
    ),
    "fwd_join_done": "✅ عضو شدید: <b>{title}</b>",
    "fwd_join_failed": "❌ عضویت ناموفق بود.",
    "fwd_join_cancelled": "❌ عضویت لغو شد.",
    "fwd_dest_invalid": (
        "❌ فرمت نامعتبر. یوزرنیم با @ یا آیدی عددی یا لینک t.me بفرستید."
    ),
    "fwd_dest_not_found": (
        "❌ چت پیدا نشد.\n"
        "مطمئن شوید اکانت عضو آن کانال/گروه است یا آیدی درست است."
    ),
    "fwd_confirm": (
        "📥 <b>تایید فوروارد محتوا</b>\n\n"
        "📤 از: <b>{src}</b>\n"
        "📥 به: <b>{dst}</b>\n\n"
        "🔢 تعداد: {limit}\n"
        "🏷 روش: {mode}\n"
        "🖼 فقط مدیا: {media}\n\n"
        "🗄 ذخیره روی سرور:\n{cache}\n\n"
        "⚡ سرعت: {speed}\n\n"
        "نمونه هدر مشخصات:\n<pre>{sample}</pre>\n"
        "━━━━━━━━━━━━━━━\n"
        "🔒 چت مبدأ <b>هیچ پیامی</b> دریافت نمی‌کند\n"
        "👁 پیام‌ها «خوانده‌شده» نمی‌شوند (تیک آبی نمی‌خورد)\n"
        "📊 گزارش فقط همین‌جا (چت ربات) نمایش داده می‌شود\n"
        "━━━━━━━━━━━━━━━\n\n"
        "⚠️ اگر مقصد کانال است، اکانت باید ادمین باشد."
    ),
}