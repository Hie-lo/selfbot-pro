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


# ═══════ پنل قابلیت‌ها ═══════

# لیست قابلیت‌ها
FEATURES = [
    ("dice", "🎲 تاس تقلبی"),
    ("banner", "📢 بنر تبلیغاتی"),
    ("timed_saver", "⏳ ذخیره تایم‌دار"),
    ("auto_download", "📥 دانلود خودکار"),
    ("anti_delete", "🗑 ضد حذف"),
    ("anti_edit", "✏️ ضد ویرایش"),
    ("save_from_link", "🔗 ذخیره از لینک"),
    ("sticker_convert", "🖼 تبدیل استیکر"),
    ("heart_animation", "❤️ قلب متحرک"),
    ("channel_monitor", "📡 مانیتور کانال"),
    ("auto_response", "💬 پاسخ خودکار"),
    ("upload_url", "📤 آپلود از لینک"),
]


def features_kb(enabled_features: dict) -> InlineKeyboardMarkup:
    """
    enabled_features = {"dice": True, "banner": False, ...}
    """
    buttons = []
    for feat_key, feat_name in FEATURES:
        is_on = enabled_features.get(feat_key, False)
        status = "✅" if is_on else "❌"
        buttons.append([
            InlineKeyboardButton(
                f"{status} {feat_name}",
                callback_data=f"toggle_{feat_key}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="back_main"),
    ])

    return InlineKeyboardMarkup(buttons)


# ═══════ مسیر ذخیره‌سازی ═══════

STORAGE_FEATURES = [
    ("anti_delete", "🗑 ضد حذف"),
    ("anti_edit", "✏️ ضد ویرایش"),
    ("timed_saver", "⏳ تایم‌دار"),
    ("auto_download", "📥 دانلود خودکار"),
    ("save_from_link", "🔗 ذخیره از لینک"),
    ("channel_monitor", "📡 مانیتور کانال"),
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
        [InlineKeyboardButton(
            "🔙 بازگشت", callback_data="storage",
        )],
    ])


# ═══════ تایید ═══════

def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"confirm_{action}"),
            InlineKeyboardButton("❌ انصراف", callback_data="back_main"),
        ]
    ])


# ═══════ بازگشت ═══════

def back_kb(target: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data=target)],
    ])