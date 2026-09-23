"""
کیبوردهای inline ربات
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ═══════ منوی اصلی ═══════

def main_menu_kb(has_account: bool = False, is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if has_account:
        buttons.append([
            InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="panel"),
        ])
        buttons.append([
            InlineKeyboardButton("🧩 قابلیت‌ها", callback_data="features"),
            InlineKeyboardButton("📂 ذخیره‌سازی", callback_data="storage"),
        ])
        buttons.append([
            InlineKeyboardButton("📥 فوروارد محتوا", callback_data="fwd_start"),
        ])
        buttons.append([
            InlineKeyboardButton("📊 وضعیت", callback_data="status"),
            InlineKeyboardButton("🔌 قطع اکانت", callback_data="disconnect"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton("🔗 اتصال اکانت", callback_data="connect"),
        ])
    buttons.append([
        InlineKeyboardButton("💎 اشتراک", callback_data="subscription"),
        InlineKeyboardButton("📖 راهنما", callback_data="help"),
    ])
    if is_admin:
        buttons.append([
            InlineKeyboardButton("🛠 پنل ادمین", callback_data="admin"),
        ])
    return InlineKeyboardMarkup(buttons)


# ═══════ کیبورد مجازی ═══════

def numpad_kb(entered_digits: str = "") -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("1", callback_data="code_1"),
            InlineKeyboardButton("2", callback_data="code_2"),
            InlineKeyboardButton("3", callback_data="code_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data="code_4"),
            InlineKeyboardButton("5", callback_data="code_5"),
            InlineKeyboardButton("6", callback_data="code_6"),
        ],
        [
            InlineKeyboardButton("7", callback_data="code_7"),
            InlineKeyboardButton("8", callback_data="code_8"),
            InlineKeyboardButton("9", callback_data="code_9"),
        ],
        [
            InlineKeyboardButton("⌫", callback_data="code_back"),
            InlineKeyboardButton("0", callback_data="code_0"),
            InlineKeyboardButton("❌ لغو", callback_data="code_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def format_code_display(entered: str, total: int = 5) -> str:
    display = ""
    for i in range(total):
        if i < len(entered):
            display += "● "
        else:
            display += "○ "
    return display.strip()


def code_entry_text(entered: str = "", total: int = 5) -> str:
    dots = format_code_display(entered, total)
    count = len(entered)
    return (
        f"🔢 **کد تایید را وارد کنید:**\n\n"
        f"    {dots}\n\n"
        f"    ({count} از {total} رقم)\n\n"
        f"💡 کد تایید به اپ تلگرام شما ارسال شده."
    )


# ═══════ قابلیت‌ها ═══════

# قابلیت‌های همیشه روشن (command-based)
ALWAYS_ON_FEATURES = [
    ("dice", "🎲 تاس تقلبی", ".تاس"),
    ("save_from_link", "🔗 ذخیره از لینک", ".ذخیره"),
    ("sticker_convert", "🖼 تبدیل استیکر", ".استیکر"),
    ("heart_animation", "❤️ قلب متحرک", ".قلب"),
]

# قابلیت‌های قابل روشن/خاموش
TOGGLEABLE_FEATURES = [
    ("banner", "📢 بنر تبلیغاتی", ".بنر"),
    ("timed_saver", "⏳ ذخیره تایم‌دار", None),
    ("anti_delete", "🗑 ضد حذف", ".ضدحذف"),
    ("anti_edit", "✏️ ضد ویرایش", ".ضدویرایش"),
    # ("channel_monitor", "📡 مانیتور کانال", ".مانیتور"),
    ("auto_response", "💬 پاسخ خودکار", ".دشمن"),
]

# همه قابلیت‌ها
ALL_FEATURES = {f[0]: f[1] for f in ALWAYS_ON_FEATURES + TOGGLEABLE_FEATURES}


def features_kb(enabled_features: dict) -> InlineKeyboardMarkup:
    buttons = []

    # همیشه روشن
    buttons.append([
        InlineKeyboardButton("── همیشه فعال ──", callback_data="noop"),
    ])
    for feat_key, feat_name, cmd in ALWAYS_ON_FEATURES:
        cmd_text = f" ({cmd})" if cmd else ""
        buttons.append([
            InlineKeyboardButton(
                f"✅ {feat_name}{cmd_text}",
                callback_data="noop",
            )
        ])

    # قابل تنظیم
    buttons.append([
        InlineKeyboardButton("── قابل تنظیم ──", callback_data="noop"),
    ])
    for feat_key, feat_name, cmd in TOGGLEABLE_FEATURES:
        is_on = enabled_features.get(feat_key, False)
        status = "✅" if is_on else "❌"
        cmd_text = f" ({cmd})" if cmd else ""
        buttons.append([
            InlineKeyboardButton(
                f"{status} {feat_name}{cmd_text}",
                callback_data=f"toggle_{feat_key}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)


# ═══════ ذخیره‌سازی ═══════

STORAGE_FEATURES = [
    ("anti_delete", "🗑 ضد حذف"),
    ("anti_edit", "✏️ ضد ویرایش"),
    ("timed_saver", "⏳ تایم‌دار"),
    ("auto_download", "📥 دانلود خودکار"),
    ("save_from_link", "🔗 ذخیره از لینک"),
]


def storage_menu_kb() -> InlineKeyboardMarkup:
    buttons = []
    for feat_key, feat_name in STORAGE_FEATURES:
        buttons.append([
            InlineKeyboardButton(
                f"📂 {feat_name}",
                callback_data=f"storage_{feat_key}",
            )
        ])
    # مانیتور کانال جداست
    buttons.append([
        InlineKeyboardButton(
            "📡 مانیتور کانال (per-channel)",
            callback_data="storage_monitor_menu",
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            "🧹 پاکسازی استیکرهای Recent", callback_data="recents_clear"
        ),
    ])
    buttons.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)


def recents_clear_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ پاک کن", callback_data="recents_clear_ok")],
        [InlineKeyboardButton("❌ انصراف", callback_data="storage")],
    ])


def storage_target_kb(feature_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💾 Saved Messages",
            callback_data=f"starget_{feature_name}_saved",
        )],
        [InlineKeyboardButton(
            "📢 چنل/گروه (ارسال آیدی)",
            callback_data=f"starget_{feature_name}_custom",
        )],
        [InlineKeyboardButton(
            "👑 کانال‌ها و گروه‌های من (فقط مالکم)",
            callback_data=f"starget_{feature_name}_own",
        )],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="storage")],
    ])


def storage_owned_kb(items: list, page: int, pages: int, feature_name: str) -> InlineKeyboardMarkup:
    """لیست فقط کانال/گروه‌های مالک برای انتخاب مسیر ذخیره‌سازی"""
    from core.forwarder import dialog_icon
    buttons = []
    if not items:
        buttons.append([
            InlineKeyboardButton("📭 کانال/گروهی که مالکش باشی پیدا نشد", callback_data="noop")
        ])
    else:
        for idx, item in items:
            buttons.append([
                InlineKeyboardButton(
                    f"{dialog_icon(item['kind'])} {_short(item['name'])}",
                    callback_data=f"starget_{feature_name}_own_i{idx}",
                )
            ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"starget_{feature_name}_own_p{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"starget_{feature_name}_own_p{page+1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"storage_{feature_name}")])
    return InlineKeyboardMarkup(buttons)


# ═══════ تایید و بازگشت ═══════

def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"confirm_{action}"),
            InlineKeyboardButton("❌ انصراف", callback_data="back_main"),
        ]
    ])


def back_kb(target: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=target)],
    ])


# ═══════ مانیتور کانال ═══════


def monitor_menu_kb(routes: list) -> InlineKeyboardMarkup:
    """
    لیست مسیرهای مانیتور با دکمه فعال/غیرفعال
    routes: لیست دیکشنری‌ها از DB
    """
    buttons = []

    if not routes:
        buttons.append([
            InlineKeyboardButton("📭 مسیری تنظیم نشده", callback_data="noop"),
        ])
    else:
        for r in routes:
            src_title = r.get("source_channel_title", str(r["source_channel_id"]))
            dst_title = r.get("destination_title", "نامشخص")
            is_active = r.get("is_active", True)
            status = "✅" if is_active else "❌"
            src_id = r["source_channel_id"]

            buttons.append([
                InlineKeyboardButton(
                    f"{status} {src_title} → {dst_title}",
                    callback_data=f"mon_toggle_{src_id}",
                ),
                InlineKeyboardButton(
                    "🗑",
                    callback_data=f"mon_delete_{src_id}",
                ),
            ])

    buttons.append([
        InlineKeyboardButton(
            "➕ اضافه کردن مسیر جدید",
            callback_data="mon_add",
        ),
    ])
    buttons.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="storage"),
    ])

    return InlineKeyboardMarkup(buttons)


# ═══════ خرید اشتراک ═══════

def plans_kb(plans: dict, price_suffix: str = "تومان") -> InlineKeyboardMarkup:
    """لیست پلن‌های قابل خرید"""
    buttons = []
    for key, plan in plans.items():
        price = f"{plan['price']:,}"
        buttons.append([
            InlineKeyboardButton(
                f"💎 {plan['title']} — {price} {price_suffix}",
                callback_data=f"buyplan_{key}",
            )
        ])
    buttons.append([
        InlineKeyboardButton("🧾 درخواست‌های من", callback_data="my_requests"),
    ])
    buttons.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)


def plan_confirm_kb(plan_key: str, has_session: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    if has_session:
        buttons.append([
            InlineKeyboardButton(
                "📸 ارسال عکس رسید", callback_data=f"sendreceipt_{plan_key}"
            )
        ])
    buttons.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="subscription"),
    ])
    return InlineKeyboardMarkup(buttons)


def admin_review_kb(req_id: int) -> InlineKeyboardMarkup:
    """دکمه‌های تایید/رد برای ادمین"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید", callback_data=f"adm_ok_{req_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"adm_no_{req_id}"),
    ]])


def admin_menu_kb(pending: int = 0) -> InlineKeyboardMarkup:
    pend_text = f"📥 درخواست‌های اشتراک ({pending})" if pending else "📥 درخواست‌های اشتراک"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(pend_text, callback_data="admin_requests")],
        [InlineKeyboardButton("👥 مدیریت مشتریان", callback_data="admin_users_0")],
        [InlineKeyboardButton("📊 آمار", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])


def admin_users_kb(users: list, page: int, per_page: int, total: int) -> InlineKeyboardMarkup:
    """لیست مشتریان با دکمه انتخاب هر کاربر"""
    buttons = []
    for u in users:
        banned = u.get("is_banned")
        sub = u.get("_has_sub")
        mark = "🚫" if banned else ("💎" if sub else "⚪")
        name = (u.get("first_name") or "").strip() or "بدون نام"
        username = f" @{u['username']}" if u.get("username") else ""
        buttons.append([
            InlineKeyboardButton(
                f"{mark} {name[:18]}{username}"[:60],
                callback_data=f"admin_user_{u['id']}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"admin_users_{page - 1}"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"admin_users_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin")])
    return InlineKeyboardMarkup(buttons)


def admin_user_kb(telegram_id: int, is_banned: bool, plans: dict) -> InlineKeyboardMarkup:
    """دکمه‌های مدیریت یک مشتری"""
    buttons = []
    for key, plan in plans.items():
        buttons.append([
            InlineKeyboardButton(
                f"➕ {plan['title']} ({plan['days']} روز)",
                callback_data=f"admsub_{telegram_id}_{key}",
            )
        ])
    buttons.append([
        InlineKeyboardButton("➖ لغو اشتراک", callback_data=f"admcancel_{telegram_id}"),
    ])
    ban_text = "✅ رفع مسدودی" if is_banned else "🚫 مسدود کردن"
    buttons.append([
        InlineKeyboardButton(ban_text, callback_data=f"admban_{telegram_id}"),
    ])
    buttons.append([
        InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"admin_user_{telegram_id}"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users_0"),
    ])
    return InlineKeyboardMarkup(buttons)


# ═══════ فوروارد محتوا ═══════

# نام‌های طولانی برای دکمه کوتاه می‌شوند
def _short(text: str, limit: int = 34) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fwd_dialogs_kb(
    items: list,
    page: int,
    pages: int,
    target: str,
    chats_only: bool = True,
    has_query: bool = False,
) -> InlineKeyboardMarkup:
    """
    لیست چت‌ها برای انتخاب مبدأ/مقصد
    items: لیست (ایندکس اصلی، آیتم دیکشنری)
    """
    from core.forwarder import dialog_icon

    buttons = []

    # ── جستجو و فیلتر ──
    row = [InlineKeyboardButton("🔎 جستجوی نام", callback_data="fwd_srch")]
    if has_query:
        row.append(InlineKeyboardButton("🧹 پاک کردن جستجو", callback_data="fwd_srch_clr"))
    buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            "🔍 فقط کانال و گروه" + (" ✅" if chats_only else ""),
            callback_data="fwd_flt",
        )
    ])

    # ── چت‌ها ──
    if not items:
        buttons.append([
            InlineKeyboardButton("📭 چیزی پیدا نشد", callback_data="noop")
        ])
    for idx, item in items:
        buttons.append([
            InlineKeyboardButton(
                f"{dialog_icon(item['kind'])} {_short(item['name'])}",
                callback_data=f"fwd_{target}_i{idx}",
            )
        ])

    # ── صفحه‌بندی ──
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"fwd_{target}_p{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"fwd_{target}_p{page + 1}"))
    buttons.append(nav)

    # ── گزینه‌های مخصوص مقصد ──
    if target == "dst":
        buttons.append([
            InlineKeyboardButton("💾 Saved Messages", callback_data="fwd_dst_saved"),
        ])
        buttons.append([
            InlineKeyboardButton(
                "✍️ ورود دستی لینک / یوزرنیم / آیدی",
                callback_data="fwd_dst_manual",
            ),
        ])
        buttons.append([
            InlineKeyboardButton("🔙 بازگشت به انتخاب مبدأ", callback_data="fwd_show_src"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton(
                "✍️ ورود دستی لینک / یوزرنیم / لینک دعوت",
                callback_data="fwd_src_manual",
            ),
        ])
        buttons.append([
            InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"),
        ])

    return InlineKeyboardMarkup(buttons)


def fwd_join_kb(invite_hash: str) -> InlineKeyboardMarkup:
    """تایید صریح عضویت در گروه خصوصی (عضویت دیده می‌شود)"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔓 عضویت و ادامه", callback_data=f"fwd_join_{invite_hash}"
        )],
        [InlineKeyboardButton("❌ انصراف", callback_data="fwd_cancel_join")],
    ])


MODE_LABELS = {
    "forward": "↪️ فوروارد با نام منبع",
    "attributed": "🏷 کپی با مشخصات فرستنده",
    "copy": "🔁 کپی بدون نام منبع",
}


def fwd_confirm_kb(
    limit: int,
    mode: str,
    media_only: bool,
    cache: bool = True,
    cache_media: bool = False,
    speed: str = "balanced",
) -> InlineKeyboardMarkup:
    limit_txt = "همه" if limit == 0 else f"{limit:,}"
    mode_txt = MODE_LABELS.get(mode, mode)

    rows = [
        [InlineKeyboardButton("🚀 شروع فوروارد", callback_data="fwd_go")],
        [
            InlineKeyboardButton(
                f"🔢 تعداد: {limit_txt}", callback_data="fwd_limit"
            ),
        ],
        [
            InlineKeyboardButton(f"🏷 حالت: {mode_txt}", callback_data="fwd_mode"),
        ],
        [
            InlineKeyboardButton(
                f"🗄 ذخیره روی سرور: {'✅' if cache else '❌'}",
                callback_data="fwd_cache",
            ),
        ],
        [
            InlineKeyboardButton(
                f"⚡ سرعت: {_speed_label(speed)} ({_speed_rate(speed)})",
                callback_data="fwd_speed",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🖼 کش مدیا (فضای دیسک): {'✅' if cache_media else '❌'}",
                callback_data="fwd_cachemedia",
            ),
        ],
        [
            InlineKeyboardButton(
                f"📎 فقط مدیا: {'بله' if media_only else 'خیر'}",
                callback_data="fwd_media",
            )
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="fwd_show_dst"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def _speed_label(speed: str) -> str:
    from core.pacing import label
    return label(speed)


def _speed_rate(speed: str) -> str:
    from core.pacing import describe
    return describe(speed).replace("≈ ", "").replace(" پیام در دقیقه", "/دقیقه")


def fwd_running_kb(job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ توقف (قابل ادامه)", callback_data=f"fwd_stop_{job.id}")],
    ])


def fwd_done_kb(paused: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if paused:
        rows.append([
            InlineKeyboardButton("▶️ ادامه فوروارد", callback_data="fwd_resume"),
        ])
        rows.append([
            InlineKeyboardButton("🗑 حذف کامل", callback_data="fwd_delete"),
        ])
    rows.append([
        InlineKeyboardButton("📥 فوروارد جدید", callback_data="fwd_start"),
        InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(rows)


def fwd_delete_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله، برای همیشه حذف کن", callback_data="fwd_delete_confirm")],
        [InlineKeyboardButton("❌ انصراف", callback_data="fwd_delete_cancel")],
    ])


def fwd_blocked_kb() -> InlineKeyboardMarkup:
    """فوروارد گیرکرده: ادامه یا آزادسازی"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ ادامه‌ی همان فوروارد", callback_data="fwd_resume")],
        [InlineKeyboardButton("🔓 بستن آن و شروع فوروارد جدید",
                              callback_data="fwd_unlock")],
        [InlineKeyboardButton("🗑 حذف کامل", callback_data="fwd_delete")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_main")],
    ])


def mon_confirm_delete_kb(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ حذف", callback_data=f"mon_confirm_del_{source_id}"),
            InlineKeyboardButton("❌ انصراف", callback_data="storage_monitor_menu"),
        ]
    ])