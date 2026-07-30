"""
مدیریت متن‌ها بر اساس زبان
"""

from i18n.fa import TEXTS as FA
from i18n.en import TEXTS as EN

_LANGS = {
    "fa": FA,
    "en": EN,
}


def t(key: str, lang: str = "fa", **kwargs) -> str:
    texts = _LANGS.get(lang, FA)
    text = texts.get(key, FA.get(key, key))
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        return text