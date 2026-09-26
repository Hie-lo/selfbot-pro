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
    "دلگیر":    {"desc": "ناراحت، کم‌حرف", "temperature": 0.6},
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
9) فارسی محاوره‌ای و شکسته بنویس؛ غلط تایپی طبیعی اشکالی ندارد.
10) هیچ‌وقت نگو که «در حال تایپ» یا «جواب دادن» هستی؛ فقط پاسخ را بده، بدون مقدمه‌چینی."""

PERSONA_HEAD = """—— شخصیت و لحن من ——
{persona}"""

CONTACT_HEAD = """—— این شخص کیه ——
نام: {name}
جایگاه در زندگی من: {rel_label} (صمیمیت {intimacy}/۵)
لحن من با او: {style}
سطح آزادی بیان: {tone_label}
طول پیام معمول من: {length}
ایموجی: {emoji}
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
                       default_tone: int = 2) -> dict:
    """ترکیب پیش‌فرض‌های جایگاه با تنظیمات دستی کاربر"""
    prof = profile or {}
    rel_key = prof.get("relationship") or "familiar"
    rel = RELATIONSHIPS.get(rel_key, RELATIONSHIPS["familiar"])

    tone = prof.get("tone_level")
    if tone is None or int(tone) < 0:
        tone = rel["tone"]
    intimacy = prof.get("intimacy") or rel["intimacy"]

    return {
        "relationship": rel_key,
        "rel": rel,
        "tone": int(tone),
        "intimacy": int(intimacy),
        "length": prof.get("reply_length") or rel["length"],
        "emoji": prof.get("emoji_level") or rel["emoji"],
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


def build_messages(*, my_name: str, persona: str, mode: str,
                   settings: dict, notes: list[dict], facts: list[dict],
                   pending: list[dict] | None, history: list[dict],
                   now_line: str = "", no_memory: bool = False) -> list[dict]:
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
            emoji=prof.get("emoji", "کم"),
            extra=(" ".join(extra_bits) or ""),
        ),
        KNOWLEDGE_HEAD.format(knowledge=build_knowledge(notes, facts, pending)),
        f"—— وضعیت و مود الان من ——\nمود: {mode} ({MODES.get(mode, {}).get('desc', '')})\n"
        f"{'زمان: ' + now_line if now_line else ''}\n"
        f"طول پاسخ: {LENGTH_HINT.get(prof.get('length', 'کوتاه'), LENGTH_HINT['کوتاه'])}",
        NO_MEMORY_HINT if no_memory else "",
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
