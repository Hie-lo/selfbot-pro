"""
تست مسیریابی هندلرهای ربات کنترلی — بدون نیاز به دیتابیس.

⚠️ چرا این تست وجود دارد: در python-telegram-bot «در هر گروه فقط یک هندلر
اجرا می‌شود». اگر یک هندلر «همه‌گیر» (TypeHandler(Update,...)) وسط گروه ۰
ثبت شود، بقیه‌ی دکمه‌ها و پیام‌ها هرگز اجرا نمی‌شوند و ربات ظاهراً «خاموش»
به‌نظر می‌رسد. این تست تضمین می‌کند هر آپدیت نمونه، دقیقاً به هندلر
درست برسد و هیچ هندلر دیگری جلوی آن را نگیرد.

اجرا:
    python tests/test_bot_dispatch.py
"""

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "42")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault("DB_NAME", "x")
os.environ.setdefault("DB_USER", "x")
os.environ.setdefault("DB_PASS", "x")
os.environ.setdefault("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")

from telegram import CallbackQuery, Chat, Message, Update, User   # noqa: E402
from telegram.ext import Application                               # noqa: E402

from bot.handlers import register_handlers                         # noqa: E402

USER = User(id=99, first_name="T", is_bot=False)
CHAT = Chat(id=99, type=Chat.PRIVATE)


def cb_update(data: str) -> Update:
    q = CallbackQuery(id="1", from_user=USER, chat_instance="ci", data=data)
    q._unfreeze()
    q.message = Message(message_id=1, date=None, chat=CHAT)
    q.message._unfreeze()
    u = Update(update_id=1, callback_query=q)
    u._unfreeze()
    return u


def text_update(text: str = "سلام") -> Update:
    m = Message(message_id=2, date=None, chat=CHAT, from_user=USER, text=text)
    m._unfreeze()
    u = Update(update_id=2, message=m)
    u._unfreeze()
    return u


# آپدیت نمونه → نام هندلری که باید *اولین* تطبیق باشد
EXPECTED = {
    "callback:panel": ("^panel$", "cb_panel"),
    "callback:features": ("^features$", "cb_features"),
    "callback:toggle_anti_delete": ("^toggle_", "cb_toggle_feature"),
    "callback:subscription": ("^subscription$", "cb_subscription"),
    "callback:help": ("^help$", "cb_help"),
    "callback:ai:menu": ("^ai:", "route_ai_callbacks"),
    "callback:ai:tone:2": ("^ai:", "route_ai_callbacks"),
    "callback:ai_send:7": (r"^ai_(send|edit|drop):", "route_ai_callbacks"),
    "callback:ai_drop:7": (r"^ai_(send|edit|drop):", "route_ai_callbacks"),
    "text:سلام": (None, "handle_text_router"),
    "text:.ai روشن": (None, "handle_text_router"),
}

DATA = {
    "callback:panel": "panel",
    "callback:features": "features",
    "callback:toggle_anti_delete": "toggle_anti_delete",
    "callback:subscription": "subscription",
    "callback:help": "help",
    "callback:ai:menu": "ai:menu",
    "callback:ai:tone:2": "ai:tone:2",
    "callback:ai_send:7": "ai_send:7",
    "callback:ai_drop:7": "ai_drop:7",
}


def first_handler_name(app, update) -> str | None:
    """اولین هندلری که در گروه اصلی این آپدیت را قبول می‌کند"""
    for group in sorted(app.handlers):
        if group < 0:
            continue
        for handler in app.handlers[group]:
            try:
                check = handler.check_update(update)
            except Exception:
                continue
            if check is not None and check is not False:
                cb = getattr(handler, "callback", None)
                return getattr(cb, "__name__", type(handler).__name__)
    return None


def catch_all_in_main_group(app) -> list[str]:
    """
    هندلرهای «همه‌گیر» در گروه اصلی — این‌ها بقیه را می‌بلعند و ممنوع‌اند
    (به‌جز آخرین هندلر گروه).
    """
    bad = []
    for group in sorted(app.handlers):
        if group < 0:
            continue
        handlers = app.handlers[group]
        for h in handlers[:-1]:          # آخرین هندلر گروه مشکلی ندارد
            w = [h.check_update(u) is not False and h.check_update(u) is not None
                 for u in (cb_update("هرچیزی"), text_update("هرچیزی"))]
            if all(w):
                cb = getattr(h, "callback", None)
                bad.append(getattr(cb, "__name__", type(h).__name__))
    return bad


async def main():
    app = Application.builder().token("1:x").build()

    # ⚠️ مهم: دقیقاً مثل main.py صدا زده می‌شود (بدون await).
    # اگر register_handlers روزی async شود و main.py آن را await نکند،
    # ربات بالا می‌آید ولی صفر هندلر دارد و کاملاً ساکت می‌ماند.
    import inspect
    _res = register_handlers(app)
    if inspect.isawaitable(_res):
        await _res

    results = []

    # ۰) هندلرها واقعاً ثبت شده‌اند؟ (مسیر صدا زدن main.py)
    total = sum(len(g) for g in app.handlers.values())
    import inspect as _ins
    results.append(("register_handlers همگام است (خطر «صفر هندلر»)",
                    not _ins.iscoroutinefunction(register_handlers),
                    "async است و main.py await نمی‌کند → ربات ساکت!"))
    results.append(("با صدا زدن مثل main.py هندلر ثبت می‌شود", total > 0, f"total={total}"))

    # ۱) هیچ هندلر همه‌گیری نباید وسط گروه اصلی باشد
    bad = catch_all_in_main_group(app)
    results.append(("هیچ هندلر همه‌گیری وسط گروه اصلی نیست", not bad, str(bad)))

    # ۲) هر آپدیت نمونه باید به هندلر درست برسد
    for key, (pattern, expected) in EXPECTED.items():
        update = text_update(key.split(":", 1)[1]) if key.startswith("text:") else cb_update(DATA[key])
        got = first_handler_name(app, update)
        results.append((f"{key} → {expected}", got == expected, f"got={got}"))

    # ۳) هندلر ai نباید آپدیت‌های غیر-AI را بردارد
    for data in ("panel", "features", "help", "hlp:main:1", "noop"):
        got = first_handler_name(app, cb_update(data))
        results.append((f"آپدیت «{data}» به هندلر AI نمی‌رود",
                        got != "route_ai_callbacks", f"got={got}"))

    # ۳.۵) هندلرهای AI باید در گروه جدا باشند (نه گروه ۰)
    ai_groups = [g for g, hs in app.handlers.items()
                 if any(getattr(getattr(h, "callback", None), "__name__", "") == "route_ai_callbacks"
                        for h in hs)]
    results.append(("دکمه‌های AI در گروه جدا هستند (ضد بلعیده‌شدن)",
                    ai_groups == [1], f"groups={ai_groups}"))

    # ۴) تعداد هندلرهای گروه اصلی تغییری نکرده باشد (ضد حذف ناخواسته)
    n_main = sum(len(app.handlers[g]) for g in app.handlers if g >= 0)
    results.append(("تعداد هندلرهای گروه ≥ ۶۰", n_main >= 60, str(n_main)))

    ok = sum(1 for _, o, _ in results if o)
    for name, passed, extra in results:
        print(("✅ " if passed else "❌ ") + name + (f"   ({extra})" if not passed and extra else ""))
    print(f"\n{ok}/{len(results)} تست موفق")
    if ok != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
