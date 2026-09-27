"""
موتور پاسخ هوشمند — بدون وابستگی به Telethon تا مستقل تست شود.

  • ماتریس «جایگاه → رفتار» (لحن، طول، سطح آزادی، ایموجی، احتمال جواب)
  • مودها (خوشحال / playfull / رسمی / ...)
  • ساخت پرامپت از سطوح حافظه (پیام خام + خلاصه + فکت + پروفایل)
  • قاعده‌ی «چیزی که یادم نیست را تایید نکن»
  • شکستن پاسخ به پیام‌های کوتاه (مثل آدم)
"""

import json
import re

# ═══════════════ مودها ═══════════════

MODES: dict[str, dict] = {
    "عادی":     {"desc": "حالت طبیعی و معمولی خودت", "temperature": 0.8},
    "خوشحال":   {"desc": "شاد، پرانرژی، مثبت", "temperature": 0.9},
    "playfull": {"desc": "بازیگوش، شیطون، اذیت‌کننده", "temperature": 0.95},
    "شوخ":      {"desc": "طنز و تیکه‌انداز", "temperature": 0.95},
    "رسمی":     {"desc": "محترمانه و مؤدب", "temperature": 0.5},
    "کاری":     {"desc": "مستقیم، کوتاه، بدون حاشیه", "temperature": 0.4},
    "عاشقانه":  {"desc": "گرم، محبت‌آمیز، پراحساس", "temperature": 0.9},
    "آرام":     {"desc": "کم‌حوصله، کوتاه، سرد", "temperature": 0.6},
    "ناراحت":   {"desc": "دلخور و ناراحت، کم‌حرف", "temperature": 0.55},
    "خشمگین":   {"desc": "عصبانی و تند", "temperature": 0.65},
    "هیجان":    {"desc": "هیجان‌زده، پر از ایموجی و علامت", "temperature": 0.95},
}

MODE_ALIASES = {
    "happy": "خوشحال", "شاد": "خوشحال", "خوش": "خوشحال",
    "playful": "playfull", "بازیگوش": "playfull", "شیطون": "playfull",
    "بامزه": "شوخ", "طنز": "شوخ", "joke": "شوخ",
    "formal": "رسمی", "اداری": "رسمی", "جدی": "کاری", "work": "کاری",
    "love": "عاشقانه", "رمانتیک": "عاشقانه",
    "normal": "عادی", "default": "عادی", "معمولی": "عادی",
    "کوتاه": "کاری", "سرد": "آرام", "کم‌حرف": "آرام",
    "دلگیر": "ناراحت", "دلخور": "ناراحت", "ناراحتی": "ناراحت", "غمگین": "ناراحت",
    "عصبانی": "خشمگین", "خشم": "خشمگین", "عصبی": "خشمگین", "angry": "خشمگین",
    "casual": "عادی", "natural": "عادی",
}

# قواعد رفتاری هر مود — مود فقط یک برچسب نیست؛ لحن، طول و نوع جمله را عوض می‌کند.
MODE_RULES = {
    "عادی": "طبیعی و روان مثل همیشه. نه خشک، نه پرانرژی.",
    "خوشحال": "انرژی بالا، خوش‌بین، جمله‌های کوتاه شاد. بیشتر از حد معمول ایموجی و «😄😂» بامزه — ولی نه در هر جمله.",
    "playfull": "شیطون و بازیگوش: تیکه می‌ندازی، کمی اذیت می‌کنی، سؤال بازیگوشانه می‌پرسی، ولی همیشه با مزه و غیرآزاردهنده.",
    "شوخ": "با هر پیام یک تیکه یا شوخی کوتاه می‌اندازی. شوخی مرتبط با همان حرف، نه شوخی الکی و بی‌ربط.",
    "رسمی": "مؤدب و درست: «سلام»، «ممنونم»، «بفرمایید». بدون شکسته‌نویسی، بدون شوخی، بدون ایموجی زیاد.",
    "کاری": "خیلی مستقیم و کوتاه؛ فقط پاسخ یا اطلاعات لازم. بدون احوال‌پرسی اضافه و بدون شوخی.",
    "عاشقانه": "گرم و پراحساس: دلتنگی، محبت، لقب‌های عاشقانه. جمله‌های کوتاه ولی پر از احساس.",
    "آرام": "کم‌حوصله و سرد ولی مؤدب. جواب‌های کوتاه، بی‌حاشیه، بدون ایموجی. طوری که «حوصله ندارم» از لحنش بیاید، ولی بی‌ادبی نکن.",
    "ناراحت": "دلخوری و ناراحتی. جواب‌ها کوتاه و سرد، با فاصله‌گذاری. «باشه»، «هرجور راحتی»، «ولش کن». بدون فحش و بدون تیکه‌ی شاد. اگر طرف دلجویی کرد، سنگین ولی کمی نرم‌تر جواب بده (زود آشتی نکن).",
    "خشمگین": "عصبانی و تند. جمله‌های کوتاه و بریده، با اعتراض و گلایه‌ی روشن. لحن جدی و تند ولی بدون تهدید واقعی و بدون بی‌احترامی به خانواده (مگر سطح آزادی بیان و جایگاه اجازه بدهد). وسط دعوا شوخی و ایموجی شاد نگذار.",
    "هیجان": "هیجان‌زده: «وااای»، «نه بابا!»، علامت تعجب زیاد، ایموجی‌های هیجانی. سریع و پرانرژی.",
}

# ═══════════════ جایگاه‌ها ═══════════════

# tone: سطح آزادی بیان (۰ مسالمت‌آمیز … ۳ بی‌سقف)
# length: یک‌کلمه / کوتاه / متوسط
# emoji: هیچ / کم / متوسط / زیاد
# reply_chance: چند درصد پیام‌ها جواب بگیرند (۱.۰ = همیشه)
RELATIONSHIPS: dict[str, dict] = {
    "spouse": {
        "label": "همسر/شریک زندگی", "tone": 1, "length": "متوسط",
        "emoji": "زیاد", "intimacy": 5, "reply_chance": 1.0,
        "style": "گرم، پراحساس، غیررسمی؛ اگر پیام کوتاه بود تو هم کوتاه ولی مهربان",
    },
    "family": {
        "label": "خانواده", "tone": 0, "length": "کوتاه", "emoji": "متوسط",
        "intimacy": 4, "reply_chance": 1.0,
        "style": "صمیمی ولی محترمانه؛ فحش و شوخی سنگین ممنوع",
    },
    "close_friend": {
        "label": "رفیق نزدیک", "tone": 3, "length": "کوتاه", "emoji": "کم",
        "intimacy": 5, "reply_chance": 0.95,
        "style": "رک و بی‌تعارف، فحش آزاد، تیکه‌انداختن عادی است",
    },
    "friend": {
        "label": "رفیق معمولی", "tone": 2, "length": "کوتاه", "emoji": "کم",
        "intimacy": 3, "reply_chance": 0.9,
        "style": "خودی و راحت، شوخی می‌کنی ولی نه خیلی تند",
    },
    "colleague": {
        "label": "همکار", "tone": 1, "length": "کوتاه", "emoji": "هیچ",
        "intimacy": 2, "reply_chance": 1.0,
        "style": "مؤدب، مستقیم و کوتاه؛ شوخی فقط اگر خودش شروع کرد",
    },
    "customer": {
        "label": "مشتری/طرف کاری", "tone": 0, "length": "کوتاه", "emoji": "هیچ",
        "intimacy": 1, "reply_chance": 1.0,
        "style": "کاملاً رسمی و محترمانه؛ هیچ شوخی و فحشی نه",
    },
    "familiar": {
        "label": "آشنا", "tone": 1, "length": "کوتاه", "emoji": "کم",
        "intimacy": 2, "reply_chance": 0.9,
        "style": "مؤدب ولی خودی، بدون حاشیه‌ی اضافه",
    },
    "stranger": {
        "label": "غریبه", "tone": 1, "length": "یک‌کلمه", "emoji": "کم",
        "intimacy": 1, "reply_chance": 0.7,
        "style": "کوتاه، بی‌تمایل و بی‌حاشیه؛ زیاد درگیر نمی‌شوی",
    },
}

REL_ALIASES = {
    "همسر": "spouse", "زوج": "spouse", "زوجه": "spouse", "شریک": "spouse",
    "خانواده": "family", "فامیل": "family", "مادر": "family", "پدر": "family",
    "رفیق": "friend", "دوست": "friend",
    "رفیق نزدیک": "close_friend", "صمیمی": "close_friend", "بهترین دوست": "close_friend",
    "همکار": "colleague", "کارمند": "colleague", "رئیس": "colleague",
    "مشتری": "customer", "طرف کاری": "customer", "بیزینس": "customer",
    "آشنا": "familiar", "معمولی": "familiar",
    "غریبه": "stranger", "ناشناس": "stranger",
}

TONE_LABELS = {
    0: "مسالمت‌آمیز — بدون هیچ فحش و توهین",
    1: "شوخ و خودی — تیکه و طعنه‌ی رفاقتی، بدون فحش جنسی/خانوادگی",
    2: "رکیک — فحش رکیک آزاد، ولی توهین جنسی صریح نه",
    3: "بی‌سقف — فحش‌های رکیک بدون محدودیت (مثل لیست خودم)",
}

# ─────────────── ایموجی ───────────────
# سطح‌بندی + لیست ایموجی‌های خودم. سطح را دستی می‌شود عوض کرد
# (`.تنظیم ایموجی ۰` تا `.تنظیم ایموجی ۳`) و لیست را هم خودم می‌دهم
# (`.ai ایموجی 😂❤️🙏`) تا AI فقط از ایموجی‌های من استفاده کند.

EMOJI_LEVELS = {
    0: "بدون ایموجی — حتی یک ایموجی هم نگذار",
    1: "خیلی کم — بیشتر وقت‌ها هیچ، نهایتاً یکی",
    2: "متوسط — یکی-دو تا، طبیعی",
    3: "آزاد — چند تا هم اشکالی ندارد",
}

EMOJI_RULES = {
    0: ("ایموجی: هیچ. حتی یک ایموجی هم نگذار — نه 😂، نه ❤️، نه 🙂 — "
        "هیچ کاراکتر تزئینی و شکلی."),
    1: ("ایموجی: تقریباً هیچ. در بیشتر پیام‌ها صفر ایموجی؛ اگر واقعاً لازم بود، "
        "فقط *یک* ایموجی ساده."),
    2: "ایموجی: کم و طبیعی — حداکثر دو ایموجی در پیام، و همیشه مرتبط با همان جمله.",
    3: "ایموجی: آزاد — می‌توانی چند ایموجی بگذاری، ولی نه پشت سر هم و نه بی‌ربط.",
}

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U0000200D\U00002B50\U00002764\U0000203C\U00002049"
    "\U0001F3FB-\U0001F3FF"
    "]+"
)

_EMOJI_STRIP_RE = re.compile(r"[\s\u200c]+")


def pick_value(*values):
    """
    اولین مقدار «واقعاً داده‌شده» را برمی‌گرداند.
    ستون‌های متنی دیتابیس وقتی خالی‌اند رشته‌ی توخالی می‌دهند (نه None) و
    همین باعث می‌شد تنظیم کلی (مثلاً ایموجی) روی هیچ چتی اثر نکند.
    """
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _as_int(value, default: int) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    fa = "۰۱۲۳۴۵۶۷۸۹"
    if isinstance(value, str) and any(ch in fa for ch in value):
        try:
            n = int("".join(str(fa.index(ch)) if ch in fa else ch for ch in value.strip()))
        except ValueError:
            return default
    return n


def norm_emoji_level(value, default: int = 1) -> int:
    """«۰»/«کم»/2/None → عدد ۰ تا ۳"""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(3, max(0, value))
    v = str(value).strip()
    fa = "۰۱۲۳۴۵۶۷۸۹"
    v = "".join(str(fa.index(ch)) if ch in fa else ch for ch in v)
    if v.isdigit():
        return min(3, max(0, int(v)))
    words = {
        "هیچ": 0, "بدون": 0, "صفر": 0, "no": 0, "none": 0, "off": 0,
        "کم": 1, "خیلی کم": 1, "low": 1, "کمی": 1,
        "متوسط": 2, "معمولی": 2, "normal": 2, "medium": 2, "mid": 2,
        "زیاد": 3, "زیادی": 3, "آزاد": 3, "high": 3, "full": 3, "همه": 3,
    }
    return words.get(v, default)


# کاراکترهایی که «چسبیده» به ایموجی قبلی‌اند و ایموجی جدید حساب نمی‌شوند
_EMOJI_ATTACH = {"\ufe0e", "\ufe0f"} | {chr(c) for c in range(0x1F3FB, 0x1F400)}


def split_emojis(text: str) -> list[str]:
    """
    ایموجی‌های چسبیده را از هم جدا می‌کند: «😂❤️🙏» → ['😂','❤️','🙏']
    (بدون این، سه ایموجی پشت‌سرهم یک تکه شمرده می‌شدند.)
    """
    out: list[str] = []
    for run in _EMOJI_RE.findall(text or ""):
        cur = ""
        i = 0
        while i < len(run):
            ch = run[i]
            if cur and ch == "\u200d":          # ترکیب ZWJ: با قبلی می‌ماند
                cur += ch
                i += 1
                if i < len(run):
                    cur += run[i]
                    i += 1
                continue
            if cur and ch in _EMOJI_ATTACH:       # تغییر رنگ/تنوع
                cur += ch
                i += 1
                continue
            if cur:
                out.append(cur)
            cur = ch
            i += 1
        if cur:
            out.append(cur)
    return out


def parse_emoji_list(value) -> list[str]:
    """لیست ایموجی‌های مجاز را از ورودی کاربر در می‌آورد (فقط کاراکترهای ایموجی)"""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [e for e in value if e]
    out: list[str] = []
    for e in split_emojis(str(value)):
        if e not in out:
            out.append(e)
    return out


def emoji_count(text: str) -> int:
    return len(split_emojis(text or ""))


_MAX_KEEP = {0: 0, 1: 1, 2: 2, 3: 6}


def strip_emoji(text: str, level: int = 1, allowed=None) -> str:
    """
    فیلتر واقعی (نه فقط توصیه در پرامپت) — مدل‌ها همیشه حرف‌گوش‌کن نیستند:

      سطح ۰ → همه‌ی ایموجی‌ها حذف
      سطح ۱ → نهایتاً یکی
      سطح ۲ → نهایتاً دو تا  (یکی-دو تای طبیعی)
      سطح ۳ → آزاد

    اگر لیست مجاز `allowed` بدهی، هر ایموجی خارج از آن هم حذف می‌شود —
    این‌طور AI فقط از ایموجی‌های خودِ من استفاده می‌کند.
    """
    if not text:
        return ""
    lvl = int(level or 0)
    max_keep = _MAX_KEEP.get(lvl, _MAX_KEEP[3])
    allow = set(allowed or [])
    kept = [0]                          # تعداد ایموجی‌های باقی‌مانده (تکراری هم می‌شمارد)

    def keep_it(e: str) -> bool:
        if allow and e not in allow:
            return False
        if kept[0] >= max_keep:
            return False
        kept[0] += 1
        return True

    out = []
    pos = 0
    for m in _EMOJI_RE.finditer(text):
        out.append(text[pos:m.start()])
        for e in split_emojis(m.group(0)):
            if keep_it(e):
                out.append(e)
        pos = m.end()
    out.append(text[pos:])

    cleaned = "".join(out)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()


LENGTH_HINT = {
    "یک‌کلمه": "فقط ۱ تا ۳ کلمه. نقطه هم نگذار.",
    "خیلی کوتاه": "حداکثر ۵ کلمه.",
    "کوتاه": "۱ تا ۸ کلمه؛ مثل پیامک معمولی، نه جمله‌ی کامل ادبی.",
    "متوسط": "۱ تا ۱۵ کلمه، حداکثر دو جمله‌ی کوتاه.",
}

# ═══════════════ ریشه‌ی پرامپت ═══════════════

BASE_RULES = """تو یک انسان واقعی هستی که دارد در تلگرام چت می‌کند. تو «{name}» هستی و پیام‌ها را به‌جای او می‌نویسی.

قواعد انکارناپذیر:
1) هرگز نگو ربات/هوش مصنوعی/دستیار هستی. حتی شوخی هم نکن.
2) کوتاه بنویس، مثل چت واقعی تلگرام. پاراگراف نمی‌نویسی. جمله‌های کامل و ادبی نمی‌نویسی.
3) از هیچ چیزی که در «دانسته‌های من» نیست مطمئن حرف نزن. اگر کسی از گذشته می‌پرسد و چیزی یادت نیست، تایید نکن؛ مبهم جواب بده یا بپرس یا بگو یادم نمیاد. هرگز نگو «یادم هست» وقتی چیزی یادت نیست.
4) هیچ قول، قرار، پول، نقل‌قول یا تعهدی که به ضرر من است نده. اگر کسی چیزی را به تو نسبت داد که یادت نیست (مثلاً «قول دادی پول بدی»)، سریع تایید نکن؛ بگو یادم نمیاد / کجا گفتم؟
5) هیچ اطلاعات فنی یا خصوصی (رمز، مکان دقیق، شماره، این‌که چه کسی هستی) را فاش نکن.
6) نقل‌قول حرف‌های من را دقیق نگو؛ پیام‌های خام خودت مرجع نیستند، فقط همان چیزی که در دانسته‌ها است.
7) لینک، فایل، شماره کارت یا اطلاعات پرداخت نفرست.
8) اگر پیام طرف تهدید، اخاذی، یا درخواست کار خلاف قانون بود، کوتاه رد کن و بحث را ادامه نده.
9) فارسی محاوره‌ای و شکسته بنویس — ولی **درست**: غلط املایی واضح، کلمه‌ی بی‌معنی، حرف اضافه/جابه‌جا یا جمله‌ی نامفهوم ننویس.
11) **مستقیماً به همان پیام آخری که طرف فرستاده جواب بده.** اگر پرسیده «چطوری؟»، جواب سلام و احوال است. اگر پرسیده «کجایی؟»، جواب همان. موضوع را خودسرانه عوض نکن و از خودت سؤال بی‌ربط نپرس.
12) هیچ اطلاعات، خاطره، اسم، عدد یا جزئیاتی از خودت اضافه نکن که در دانسته‌ها یا پیام‌های طرف نیست (مثلاً اسم نبر، برنامه‌ی ساختگی نگو).
13) اگر پیام کوتاه یا مبهم است (مثل «چی؟»، «ها؟»، «بیا»)، کوتاه و بی‌حاشیه جواب بده و اگر لازم است یک سؤال ساده بپرس — نه اینکه داستان بسازی.
14) یک پاسخ = یک موضوع. پشت سر هم چند موضوع باز نکن و چند سؤال پشت هم نپرس.
15) اسم طرف را فقط همان‌طور که در «نام طرف» آمده به کار ببر. اگر آن‌جا نامی نبود یا مبهم بود، **هیچ اسمی صدایش نزن** و از خودت اسم نساز. اسم را هم در هر پیام تکرار نکن.
16) اگر کسی اسم خودت را پرسید، همان «{name}» (نام واقعی من) را بگو؛ چیز دیگری از خودت نگو.
10) هیچ‌وقت نگو که «در حال تایپ» یا «جواب دادن» هستی؛ فقط پاسخ را بده، بدون مقدمه‌چینی."""

PERSONA_HEAD = """—— شخصیت و لحن من ——
{persona}"""

CONTACT_HEAD = """—— این شخص کیه ——
نام طرف: {name}
جایگاه در زندگی من: {rel_label} (صمیمیت {intimacy}/۵)
لحن من با او: {style}
سطح آزادی بیان: {tone_label}
طول پیام معمول من: {length}
{emoji_rule}
{extra}"""

KNOWLEDGE_HEAD = """—— دانسته‌های من (فقط همین‌ها را به‌عنوان حافظه دارم) ——
{knowledge}"""

NO_MEMORY_HINT = """⚠️ توجه: طرف دارد درباره‌ی چیزی حرف می‌زند که در دانسته‌های من هیچ نشانی از آن نیست
(نه در پیام‌های اخیر، نه خلاصه، نه فکت). پس: تایید نکن، توضیح نساز، و صادقانه/مبهم بگو یادت نیست.
از خودت خاطره‌ی تازه نساز."""


def norm_mode(name: str) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    if n in MODES:
        return n
    n2 = MODE_ALIASES.get(n) or MODE_ALIASES.get(name.strip())
    return n2 if n2 in MODES else None


def norm_relationship(name: str) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    if n in RELATIONSHIPS:
        return n
    return REL_ALIASES.get(name.strip()) or REL_ALIASES.get(n)


def effective_settings(profile: dict | None, default_mode: str = "عادی",
                       default_tone: int = 2, global_emoji=None,
                       allowed_emojis=None) -> dict:
    """ترکیب پیش‌فرض‌های جایگاه با تنظیمات دستی کاربر"""
    prof = profile or {}
    rel_key = prof.get("relationship") or "familiar"
    rel = RELATIONSHIPS.get(rel_key, RELATIONSHIPS["familiar"])

    tone = _as_int(pick_value(prof.get("tone_level")), -1)
    if tone < 0:
        tone = rel["tone"]
    intimacy = _as_int(pick_value(prof.get("intimacy")), rel["intimacy"])

    return {
        "relationship": rel_key,
        "rel": rel,
        "tone": int(tone),
        "intimacy": int(intimacy),
        "length": pick_value(prof.get("reply_length")) or rel["length"],
        # سطح ایموجی: تنظیم دستی همین چت → تنظیم کلی من → پیش‌فرض جایگاه
        "emoji_level": norm_emoji_level(
            pick_value(prof.get("emoji_level"), global_emoji),
            default=norm_emoji_level(rel["emoji"], 1),
        ),
        "allowed_emojis": parse_emoji_list(
            pick_value(allowed_emojis, prof.get("allowed_emojis")) or ""
        ),
        "reply_chance": rel["reply_chance"],
        "nickname": (prof.get("nickname") or "").strip(),
        "red_lines": (prof.get("red_lines") or "").strip(),
        "notes": (prof.get("notes") or "").strip(),
    }


# ═══════════════ ساخت پرامپت ═══════════════

def _fmt_msg(row: dict) -> str:
    who = "من" if row.get("is_out") else "او"
    text = (row.get("content") or "").strip().replace("\n", " ")
    return f"{who}: {text}"


def build_knowledge(notes: list[dict], facts: list[dict], pending: list[dict] | None = None) -> str:
    lines = []
    if facts:
        lines.append("چیزهایی که از این شخص می‌دانم:")
        for f in facts:
            mark = "📌 " if f.get("pinned") else ""
            lines.append(f"- {mark}{f['content']}")
    if notes:
        lines.append("\nخلاصه‌ی گفت‌وگوهای قبلی:")
        for n in notes:
            lines.append(f"- {n['content']}")
    if pending:
        lines.append("\n(اینها را طرف ادعا کرده و من تایید نکرده‌ام — به‌عنوان واقعیت قبول نکن:)")
        for p in pending:
            lines.append(f"- ادعای او: {p['content']}")
    return "\n".join(lines) if lines else "(هیچ دانسته‌ای از این شخص ندارم — تازه‌آشنا هستیم)"


def _emoji_rule_text(prof: dict) -> str:
    lvl = norm_emoji_level(prof.get("emoji_level"), 1)
    rule = EMOJI_RULES.get(lvl, EMOJI_RULES[1])
    allow = prof.get("allowed_emojis") or []
    if allow:
        rule += ("\n⚠️ فقط و فقط از این ایموجی‌ها استفاده کن: " + " ".join(allow) +
                 " — هیچ ایموجی دیگری (هیچ!) نگذار. اگر ایموجی لازم نداری، هیچ‌کدام را نگذار.")
    return rule


def build_messages(*, my_name: str, persona: str, mode: str,
                   settings: dict, notes: list[dict], facts: list[dict],
                   pending: list[dict] | None, history: list[dict],
                   now_line: str = "", no_memory: bool = False,
                   incoming_text: str = "") -> list[dict]:
    """پرامپت نهایی: system(قواعد+شخصیت+مخاطب+دانسته‌ها) + تاریخچه + پیام آخر"""
    prof = settings or {}
    rel = prof.get("rel") or RELATIONSHIPS["familiar"]

    extra_bits = []
    if prof.get("nickname"):
        extra_bits.append(f"من او را «{prof['nickname']}» صدا می‌زنم.")
    if prof.get("notes"):
        extra_bits.append(f"نکته‌های مهم: {prof['notes']}")
    if prof.get("red_lines"):
        extra_bits.append(f"موضوعات ممنوع (هرگز به آن‌ها نپرداز): {prof['red_lines']}")

    system = "\n\n".join([
        BASE_RULES.format(name=my_name),
        PERSONA_HEAD.format(persona=(persona or "").strip() or "لحن طبیعی و معمولی خودم، خودم را دقیقاً مثل یک آدم عادی جا می‌زنم."),
        CONTACT_HEAD.format(
            name=(settings or {}).get("target_name") or "طرف مقابل",
            rel_label=rel["label"],
            intimacy=prof.get("intimacy", 3),
            style=rel["style"],
            tone_label=TONE_LABELS.get(int(prof.get("tone", 2)), TONE_LABELS[2]),
            length=prof.get("length", "کوتاه"),
            emoji_rule=_emoji_rule_text(prof),
            extra=(" ".join(extra_bits) or ""),
        ),
        KNOWLEDGE_HEAD.format(knowledge=build_knowledge(notes, facts, pending)),
        f"—— وضعیت و مود الان من ——\nمود: {mode} ({MODES.get(mode, {}).get('desc', '')})\n"
        f"⚠️ این مود را در *همین* پاسخ اعمال کن: {MODE_RULES.get(mode, MODE_RULES['عادی'])}\n"
        f"{'زمان: ' + now_line if now_line else ''}\n"
        f"طول پاسخ: {LENGTH_HINT.get(prof.get('length', 'کوتاه'), LENGTH_HINT['کوتاه'])}",
        NO_MEMORY_HINT if no_memory else "",
        f"یادآوری آخر: تو «{my_name}» هستی، مود الان «{mode}» است و جواب فقط "
        f"باید به این پیام طرف باشد: «{(incoming_text or (history[-1].get('content') if history else ''))[:160]}»",
    ]).strip()

    msgs = [{"role": "system", "content": system}]
    for h in history:
        msgs.append({
            "role": "assistant" if h.get("is_out") else "user",
            "content": (h.get("content") or "")[:2000],
        })
    return msgs


# ═══════════════ پاک‌سازی پاسخ ═══════════════

_BAD_PREFIX = re.compile(
    r"^(?:به عنوان|as an ai|چت ?بات|هوش مصنوعی|پاسخ[:：]|reply[:：]|"
    r"خب،? من |باشه،? من )",
    re.I,
)


def clean_reply(text: str, max_chars: int = 400) -> str:
    """حذف مقدمه‌چینی، گیومه، و برچسب‌های نامربوط"""
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"^```[\w]*\n?|```$", "", t).strip()
    t = t.strip("\u00ab\u00bb\"'` ")
    t = re.sub(r"^(?:من|{})\s*[:\uff1a]\s*".format(re.escape("{name}")), "", t).strip()
    t = re.sub(r"^\(.*?\)\s*", "", t, count=1).strip()   # (به فکر فرو رفت)
    t = _BAD_PREFIX.sub("", t).strip()
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0]
    return t


def split_messages(text: str, max_parts: int = 3, max_len: int = 120) -> list[str]:
    """
    شکستن پاسخ به ۱ تا ۳ پیام کوتاه مثل آدم‌ها.
    خط‌های خالی = مرز پیام. متن بلند هم روی علامت نگارشی بریده می‌شود.
    """
    if not text:
        return []
    parts: list[str] = []
    for chunk in re.split(r"\n{1,}", text.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        while len(chunk) > max_len and len(parts) < max_parts:
            cut = max(chunk.rfind("،", 0, max_len), chunk.rfind(".", 0, max_len),
                      chunk.rfind("؟", 0, max_len), chunk.rfind("!", 0, max_len),
                      chunk.rfind(" ", 0, max_len))
            if cut < 15:
                cut = max_len
            parts.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip(" ،.")
        if chunk:
            parts.append(chunk)
        if len(parts) >= max_parts:
            break
    return [p for p in parts if p][:max_parts]


def typing_seconds(text: str, chars_per_sec: float = 11.0,
                   minimum: float = 1.2, maximum: float = 4.5) -> float:
    """تأخیر طبیعی بر اساس طول پاسخ"""
    return max(minimum, min(maximum, len(text) / chars_per_sec + 0.6))


def needs_no_memory_hint(text: str) -> bool:
    """
    آیا طرف دارد از چیزی می‌پرسد که احتمالاً یادم نیست؟
    (سؤال از گذشته/قول/خاطره) — برای اضافه‌کردن هشدار به پرامپت
    """
    if not text:
        return False
    keys = ("یادت", "يادت", "یادته", "یادت هست", "قبلاً", "قبلا", "اون روز", "آن روز",
            "گفتی", "گفته بودی", "قول", "قول داده", "قرار بود", "همون موقع",
            "یادته که", "پارسال", "دیروز گفتم", "کی گفتم")
    t = text.lower()
    return any(k in t for k in keys)


def is_question(text: str) -> bool:
    return bool(text) and ("?" in text or "؟" in text)


def summarizer_messages(chat_name: str, lines: list[str]) -> list[dict]:
    """پرامپت سبک‌سازی: خلاصه‌ی کوتاه + فکت‌های قابل اعتماد"""
    text = "\n".join(lines)[:6000]
    system = (
        "تو یک خلاصه‌ساز دقیق هستی. از متن چت زیر دو چیز بده:\n"
        "1) summary: ۲ تا ۴ خط خلاصه‌ی فارسی، فقط چیزهای مهم و پایدار (نه سلام‌و‌احوال).\n"
        "2) facts: لیست کوتاه واقعیت‌های مفید درباره‌ی «او» یا رابطه، هر کدام یک جمله.\n"
        "قواعد: چیزی از خودت نساز؛ اگر طرف ادعایی کرد که تایید نشده، با «ادعا:» شروع کن.\n"
        "اگر مورد مهمی نبود، خلاصه‌ی خیلی کوتاه بده.\n"
        'فقط JSON بده: {"summary": "...", "facts": ["...", "..."]}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"گفت‌وگو با {chat_name}:\n{text}"},
    ]


def parse_summary(raw: str) -> tuple[str, list[str]]:
    """خواندن خروجی JSON خلاصه‌ساز (مقاوم به متن اضافه)"""
    if not raw:
        return "", []
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            summary = str(data.get("summary") or "").strip()
            facts = [str(f).strip() for f in (data.get("facts") or []) if str(f).strip()]
            return summary, facts[:8]
        except Exception:
            pass
    return clean_reply(raw, max_chars=600), []
