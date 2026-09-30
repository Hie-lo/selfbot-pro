"""
ارقام فارسی/عربی → انگلیسی.

کاربر با کیبورد فارسی عدد می‌نویسد (۲، ۳، …) و «۲» با «2» برابر نیست.
هر جا دستوری عدد می‌گیرد، اول متن را از این تابع رد کنید؛ وگرنه مقایسه‌ی
رشته‌ای (مثل انتخاب انیمیشن قلب) بی‌صدا به حالت پیش‌فرض می‌افتد.

نکته: int("۲") در پایتون کار می‌کند، ولی «۲» == «2» نه؛ پس فقط جاهایی که
مقایسه‌ی رشته‌ای یا dict-key عددی داریم واقعاً می‌شکنند.
"""

# ارقام فارسی/عربی‌هندی: U+06F0..U+06F9 و U+0660..U+0669
_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_ASCII = "0123456789"

_TABLE = str.maketrans(
    {p: a for p, a in zip(_PERSIAN, _ASCII)} | {a: b for a, b in zip(_ARABIC_INDIC, _ASCII)}
)


def to_ascii(text: str) -> str:
    """«۱۴:۳۰» → «14:30» (اگر متن None بود، رشته‌ی خالی)"""
    return (text or "").translate(_TABLE)


def as_int(text: str, default=None):
    """تبدیل مطمئن به عدد؛ None اگر عدد نبود («_» و فاصله هم پذیرفته نمی‌شود)"""
    try:
        return int(to_ascii(str(text)).strip())
    except (TypeError, ValueError):
        return default
