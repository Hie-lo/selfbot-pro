"""
کیبوردهای inline ربات
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ═══════ منوی اصلی ═══════

def main_menu_kb(has_account: bool = False) -> InlineKeyboardMarkup:
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
    ("upload_url", "📤 آپلود از لینک", ".آپلود"),
]

# قابلیت‌های قابل روشن/خاموش
TOGGLEABLE_FEATURES = [
    ("banner", "📢 بنر تبلیغاتی", ".بنر"),
    ("timed_saver", "⏳ ذخیره تایم‌دار", None),
    ("auto_download", "📥 دانلود خودکار", None),
    ("anti_delete", "🗑 ضد حذف", ".ضدحذف"),
    ("anti_edit", "✏️ ضد ویرایش", ".ضدویرایش"),
    ("channel_monitor", "📡 مانیتور کانال", ".مانیتور"),
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
        InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)


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
        [InlineKeyboardButton("🔙 بازگشت", callback_data="storage")],
    ])


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


def mon_confirm_delete_kb(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ حذف", callback_data=f"mon_confirm_del_{source_id}"),
            InlineKeyboardButton("❌ انصراف", callback_data="storage_monitor_menu"),
        ]
    ])