"""
تست پوشش راهنما — هر دستوری که در plugins/ ثبت شده باید در راهنما باشد.

اجرا (بدون نیاز به .env یا دیتابیس):
    python -m pytest tests/            یا    python tests/test_help_coverage.py

اگر دستور جدیدی اضافه کردی و این تست شکست خورد، آن را به
bot/help_content.py (COMMANDS + بخش مربوطه) اضافه کن.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot import help_content as H  # noqa: E402

PATTERN_RE = re.compile(r'pattern\s*=\s*\(?\s*r"\^\\\.([^"]+)"')


def _top_alternatives(body: str) -> list[str] | None:
    """
    اگر الگو با یک گروه شروع شود، گزینه‌های آن را برمی‌گرداند.
    پرانتزهای تودرتو (مثل (?:مود|سطح)) درست حساب می‌شوند.
    """
    if not body.startswith("("):
        return None
    start = 3 if body.startswith("(?:") else 1
    depth = 1
    i = start
    alts: list[str] = []
    current = ""
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            current += body[i:i + 2]
            i += 2
            continue
        if body.startswith("(?:", i):
            depth += 1
            current += "(?:"
            i += 3
            continue
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            if depth == 0:
                alts.append(current)
                return [a for a in alts if a]
            current += ch
        elif ch == "|" and depth == 1:
            # فقط جداکننده‌های سطح بالا؛ | داخل گروه‌های تودرتو حساب نمی‌شود
            alts.append(current)
            current = ""
        else:
            current += ch
        i += 1
    return None


def _plugin_commands() -> set[str]:
    """اولین کلمه(های) ثابت هر الگوی دستور در plugins/"""
    cmds: set[str] = set()
    pdir = os.path.join(ROOT, "plugins")
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".py"):
            continue
        # خطوط کامنت‌شده (دستورهای غیرفعال) حساب نمی‌شوند
        src = "\n".join(
            line for line in open(os.path.join(pdir, fn), encoding="utf-8").read().splitlines()
            if not line.lstrip().startswith("#")
        )
        for body in PATTERN_RE.findall(src):
            alts = _top_alternatives(body)
            if alts is not None:
                # ^\.(دشمن|لیست دشمن)   یا   ^\.(?:ai|تنظیم\s+(?:مود|سطح))
                for alt in alts:
                    words = []
                    for tok in re.split(r"\\s[+*]", alt):
                        lit = re.match(r"[^\\()\[$?]+", tok)
                        if not lit:
                            break
                        words.append(lit.group(0).strip())
                        if lit.group(0) != tok:
                            break
                    if words:
                        cmds.add(" ".join(w for w in words if w))
                continue
            # ^\.فوروارد\s+حذف\s*$  →  «فوروارد حذف»
            words = []
            for tok in re.split(r"\\s[+*]", body):
                lit = re.match(r"[^\\()\[$?]+", tok)
                if not lit:
                    break
                words.append(lit.group(0).strip())
                if lit.group(0) != tok:
                    break
            cmds.add(" ".join(w for w in words if w))
    return {c for c in cmds if c}


def _help_blob() -> str:
    parts = [H.MAIN_TEXT] + [s["text"] for s in H.SECTIONS.values()]
    return "\n".join(parts)


def test_every_plugin_command_is_documented():
    commands_page = H.SECTIONS["commands"]["text"]
    blob = _help_blob()
    missing = [
        c for c in sorted(_plugin_commands())
        if f".{c}" not in commands_page or f".{c}" not in blob
    ]
    assert not missing, f"دستورات بدون راهنما: {missing}"


def test_sections_fit_telegram_limits():
    for key, sec in H.SECTIONS.items():
        assert len(sec["text"]) < 4000, (key, len(sec["text"]))
        assert len(sec["title"].encode()) <= 64


def test_layout_references_exist():
    keys = {k for row in H.MAIN_LAYOUT for k in row}
    for rows in H.CHILDREN.values():
        keys |= {k for row in rows for k in row}
    assert keys <= set(H.SECTIONS), keys - set(H.SECTIONS)
    # هر بخش باید از جایی قابل دسترسی باشد
    assert set(H.SECTIONS) <= keys, set(H.SECTIONS) - keys
    # callback_data زیر ۶۴ بایت
    for k in H.SECTIONS:
        assert len(f"hlp:{k}:{2**63}".encode()) <= 64


def test_aliases_resolve():
    for alias, key in H.ALIASES.items():
        assert key in H.SECTIONS, (alias, key)
        assert H.resolve_section(alias) == key, alias


def test_panel_always_on_matches_plugin_manager():
    src = open(os.path.join(ROOT, "core", "plugin_manager.py"), encoding="utf-8").read()
    block = src[src.index("ALWAYS_ON_PLUGINS"):src.index("TOGGLEABLE_PLUGINS")]
    manager = set(re.findall(r'"(\w+)":', block)) - {"panel"}
    panel_src = open(os.path.join(ROOT, "plugins", "panel.py"), encoding="utf-8").read()
    m = re.search(r"^ALWAYS_ON = \{([^}]*)\}", panel_src, re.M)
    panel = set(re.findall(r'"(\w+)"', m.group(1)))
    assert manager == panel, (manager, panel)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
    print("commands found:", sorted(_plugin_commands()))
