"""
«کاربر» — پیدا کردن کاربر با آیدی عددی، دیدن پروفایل، و آماده‌کردن/ارسال پیام.

دستورها (فقط از طرف خودت اجرا می‌شوند):
  .کاربر 123456789             → کارت پروفایل + دکمه‌ها
  .کاربر @username
  .کاربر +989121234567         → با شماره‌ی تلفن
  .کاربر https://t.me/username
  .کاربر 123456789 کامل        → جست‌وجوی عمیق‌تر در چت‌ها
  .کاربر 123456789 پیام سلام    → متن آماده‌ی ارسال (فقط با تأیید خودت)
  .کاربر 123456789 پیش‌نویس سلام → متن پیش‌نویس دلخواه
  .کاربر سابقه                 → کاربرانی که دیده‌ایم (تا بعداً پیداشان کنی)

چطور بدون گروه مشترک کار می‌کند؟
  تلگرام برای کار با یک کاربر «access_hash» می‌خواهد، نه فقط آیدی عددی.
  این پلاگین هر کاربری را که ببیند (پیام ورودی، چت‌ها، جست‌وجو، مخاطبین)
  با hash در دیتابیس ذخیره می‌کند؛ پس بعداً — حتی اگر طرف چت را پاک کرده
  باشد — می‌توان پروفایلش را گرفت و به او پیام داد، بدون هیچ گروه مشترکی.

سه قاعده‌ی مهم:
  1) هیچ پیامی بدون فشردن «✅ ارسال کن» توسط خودت فرستاده نمی‌شود.
  2) «آماده‌کردن چت» = ذخیره‌ی پیش‌نویس (Draft). پیش‌نویس را فقط صاحب
     اکانت می‌بیند؛ طرف مقابل هیچ اعلان/پیامی نمی‌گیرد، ولی چت در لیست
     تلگرام خودت بالا می‌آید و همان‌جا می‌توانی بنویسی.
  3) خروجی به پیوی ربات کنترلی می‌رود (فالبک: پیام‌های ذخیره‌شده) و پیام
     خودِ دستور هم پاک می‌شود؛ پس در چتی که دستور را زدی چیزی نمی‌ماند.

⚠️ جست‌وجو با «شماره» یک مخاطب موقت می‌سازد و بلافاصله پاکش می‌کند؛
تلگرام این کار را محدود می‌کند (سقف روزانه + فاصله‌ی اجباری).
"""

from __future__ import annotations

import html
import io
import re
import time

import random

from telethon import events, functions
from telethon import utils as tl_utils
from telethon.errors import FloodWaitError
from telethon.tl import types as tl
from telethon.tl.functions.contacts import (
    DeleteContactsRequest, ImportContactsRequest)
from telethon.tl.types import InputPhoneContact

from core import runtime
from core.digits import to_ascii
from plugins.base import BasePlugin

MAX_TEXT = 4000
DIALOG_SCAN_LIMIT = 200          # جست‌وجوی معمولی در چت‌ها (سبک)
DIALOG_SCAN_LIMIT_DEEP = 2000    # فقط با «کامل» — و آن هم محدود
DIALOG_SCAN_GAP = 3.0            # ثانیه؛ بین دو اسکن چت‌ها (ضد درخواست پشت‌سرهم)
LOOKUP_COOLDOWN = 2.0            # ثانیه بین دو جست‌وجو (ضد FloodWait)
# جست‌وجو با شماره: هرکدام یک ImportContacts است و تلگرام محدودش می‌کند
PHONE_COOLDOWN = 45.0            # فاصله‌ی اجباری بین دو جست‌وجوی شماره
PHONE_DAILY_LIMIT = 25           # سقف روزانه (ضد محدودیت اکانت)
DEFAULT_DRAFT = "🤍"             # حتماً از bot/user_panel هم خوانده می‌شود
# شماره‌های ایرانی: 09xxxxxxxxx ↔ +989xxxxxxxxx
IRAN_CC = "98"


def looks_like_phone(raw: str) -> bool:
    """آیا ورودی شماره‌ی تلفن است؟ (۰۹…، +۹۸…، ۹۸…، ۹…، ۰۰۹۸…)"""
    t = to_ascii(raw or "")          # ۰۹۱۲… → 0912… (کیبورد فارسی)
    t = t.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not t or t.startswith("@"):
        return False
    digits = t.lstrip("+")
    if not digits.isdigit():
        return False
    if t.startswith("+") and len(digits) >= 10:
        return True
    if t.startswith("0") and len(digits) == 11:
        return True
    if t.startswith("9") and len(digits) == 10:
        return True
    return False


def normalize_phone(raw: str) -> str:
    """
    «۰۹۱۲۱۲۳۴۵۶۷» → «+989121234567»
    شماره‌های خارجی که با + می‌آیند دست‌نخورده (فقط فاصله‌ها حذف) می‌مانند.
    """
    t = to_ascii((raw or "").strip())
    t = t.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if t.startswith("00"):
        t = "+" + t[2:]
    if t.startswith("+"):
        return "+" + t[1:].lstrip("0")
    if t.startswith("0"):                     # 09121234567
        return "+" + IRAN_CC + t[1:]
    if t.startswith(IRAN_CC):                 # 989121234567
        return "+" + t
    if t.startswith("9") and len(t) == 10:    # 9121234567
        return "+" + IRAN_CC + t
    if t.startswith("8") and len(t) == 10:    # 8904799614 (تلفن ثابت بدون ۰)
        return "+" + IRAN_CC + t
    return "+" + t


def _esc(value) -> str:
    return html.escape(str(value or ""))


class UserInfoPlugin(BasePlugin):
    name = "user_info"
    description = "کاربر با آیدی عددی"
    always_on = True

    # ═══════════ بالا آمدن ═══════════

    def _ensure_state(self) -> None:
        """اگر متدی قبل از start() صدا زده شد، وضعیت را بساز (ضد AttributeError)"""
        if not hasattr(self, "_known"):
            self._known = {}
        if not hasattr(self, "_owner_tg"):
            self._owner_tg = None
        if not hasattr(self, "_me_id"):
            self._me_id = None
        if not hasattr(self, "_last_ok"):
            self._last_ok = {}
        if not hasattr(self, "_last_fail"):
            self._last_fail = {}
        if not hasattr(self, "_phone_last"):
            self._phone_last = 0.0
        if not hasattr(self, "_phone_day"):
            self._phone_day = ""
        if not hasattr(self, "_phone_count"):
            self._phone_count = 0
        if not hasattr(self, "_seen_recent"):
            self._seen_recent = {}
        if not hasattr(self, "_my_msgs"):
            self._my_msgs = {}
        if not hasattr(self, "_report_cache"):
            self._report_cache = {}
        if not hasattr(self, "_record_fail"):
            self._record_fail = None

    async def start(self):
        self._ensure_state()
        self._last_ok: dict[int, float] = {}
        self._last_fail: dict[int, float] = {}

        try:
            me = await self.client.get_me()
            self._me_id = me.id if me else None
        except Exception as e:                   # noqa: BLE001
            self.logger.warning(f"get_me failed: {e}")

        try:
            from database import db
            owner = await db.get_user_by_db_id(self.user_id)
            self._owner_tg = (owner or {}).get("telegram_id")
        except Exception as e:                   # noqa: BLE001
            self.logger.warning(f"owner lookup failed: {e}")

        self._add_handler(
            self._on_command,
            events.NewMessage(pattern=r"^\.کاربر(?:\s.*)?$", outgoing=True),
        )
        # هر پیام ورودی → یک سابقه‌ی ماندگار از فرستنده (نام، یوزرنیم،
        # access_hash). همین سابقه است که بعداً اجازه می‌دهد چتِ پاک‌شده را
        # دوباره باز کنیم یا بدون گروه مشترک پروفایل/پیام داشته باشیم.
        # پیام‌های خودم در گپ‌ها: فقط برای تشخیص «ریپلای به من» (بدون
        # هیچ نوشتنی در دیتابیس؛ در حافظه و سبک).
        self._add_handler(self._remember_own,
                          events.NewMessage(outgoing=True))
        self.logger.info("loaded")

    # ═══════════ ثبت سابقه‌ی کاربران ═══════════

    async def _remember_event(self, event):
        """
        سابقه‌گیری هوشمند و سبک.

        قاعده‌ها (خواسته‌ی کاربر):
          • پیوی: هر کسی که در پیوی پیام بدهد ثبت می‌شود — «همیشه».
          • گپ/کانال: **فقط** کسی که در همان گپ به پیامِ خودت ریپلای زده باشد،
            و ثبتش هم موقتی است (چند روز، با `prune_directory` پاک می‌شود).
            این‌طور سابقه با هر پیام گپ‌ها شلوغ نمی‌شود.
          • جای دیگر نه.
        """
        try:
            if not event.message or getattr(event.message, "action", None):
                return
            sender = None
            try:
                sender = await event.get_sender()
            except Exception:
                sender = None
            if sender is None or not isinstance(sender, tl.User):
                return                      # گروه/کانال → کاربر نیست
            if getattr(sender, "bot", False) or getattr(sender, "is_self", False):
                return

            uid = sender.id
            is_pv = bool(getattr(event, "is_private", False))
            if not is_pv:
                # در گپ‌ها فقط ریپلای به پیام خودم ارزش ثبت دارد
                if not self._is_reply_to_me(event):
                    return

            now = time.monotonic()
            last = self._seen_recent.get(uid, 0.0)
            first_time = uid not in self._seen_recent
            self._seen_recent[uid] = now
            if len(self._seen_recent) > 3000:
                cutoff = now - 3600
                self._seen_recent = {k: v for k, v in self._seen_recent.items()
                                     if v >= cutoff}
            # در گپ‌ها فقط هر ۳۰ دقیقه یک‌بار می‌نویسیم (سبک و کم‌هزینه)
            gap = 600 if is_pv else 1800
            heavy = first_time or (now - last) > gap
            if not heavy and not is_pv:
                return

            from database import db
            await db.remember_user(
                self.user_id, uid,
                access_hash=getattr(sender, "access_hash", None),
                first_name=getattr(sender, "first_name", None),
                last_name=getattr(sender, "last_name", None),
                username=getattr(sender, "username", None),
                phone=getattr(sender, "phone", None),
                is_bot=bool(getattr(sender, "bot", False)),
                chat_id=event.chat_id,
                source="پیام پیوی" if is_pv else "ریپلای در گپ",
                kind="pv" if is_pv else "group",
                last_msg_id=getattr(event.message, "id", None),
                last_text=((event.raw_text or "")[:400] if heavy else None),
                count_msg=heavy,
            )
            await self._fix_numeric_name(uid, sender)
        except Exception as e:              # noqa: BLE001
            self.logger.debug(f"remember failed: {type(e).__name__}: {e}")

    async def _remember_own(self, event):
        """
        پیام‌های خودم:
          • در **پیوی**: اگر طرف را در سابقه نداشته باشیم، همین‌جا با
            access_hash ذخیره‌اش می‌کنیم. این «مسیر بازیابی» است: وقتی خودت
            چتی را از لینک/جست‌وجوی تلگرام باز کردی و یک پیام فرستادی، از
            همان لحظه ربات می‌تواند هر جا پیدایش کند.
          • در **گپ**: فقط آیدی پیام‌هایم را نگه می‌دارم تا بفهمم کی به آن‌ها
            ریپلای زده (بدون درخواست به تلگرام/دیتابیس).
        """
        try:
            if not event.message or getattr(event.message, "action", None):
                return

            if getattr(event, "is_private", False):
                peer = None
                try:
                    peer = await event.get_chat()
                except Exception:
                    peer = None
                uid = getattr(peer, "id", None)
                if not uid or getattr(peer, "bot", False):
                    return
                now = time.monotonic()
                last = self._seen_recent.get(uid, 0.0)
                if uid in self._seen_recent and (now - last) < 600:
                    return                      # همین چند دقیقه پیش ثبتش کردیم
                self._seen_recent[uid] = now
                from database import db
                await db.remember_user(
                    self.user_id, uid,
                    access_hash=getattr(peer, "access_hash", None),
                    first_name=getattr(peer, "first_name", None),
                    last_name=getattr(peer, "last_name", None),
                    username=getattr(peer, "username", None),
                    chat_id=event.chat_id, source="پیام خودم", kind="pv",
                    last_msg_id=getattr(event.message, "id", None),
                    count_msg=True,
                )
                return

            cid = event.chat_id
            bucket = self._my_msgs.setdefault(cid, {})
            bucket[int(event.message.id)] = time.monotonic()
            if len(bucket) > 300:
                for mid in sorted(bucket, key=lambda k: bucket[k])[:-250]:
                    bucket.pop(mid, None)
            if len(self._my_msgs) > 300:
                for cid_old in list(self._my_msgs)[:-250]:
                    self._my_msgs.pop(cid_old, None)
        except Exception:
            pass

    def _is_reply_to_me(self, event) -> bool:
        """آیا این پیام، ریپلای به پیام خودم است؟"""
        try:
            reply = getattr(event.message, "reply_to", None)
            if reply is None:
                return False
            from_id = None
            for attr in ("reply_to_msg_id", "reply_to_top_id"):
                if getattr(reply, attr, None):
                    from_id = getattr(reply, attr)
                    break
            if not from_id:
                return False
            # در سابقه‌ی کش‌شده‌ی خودمان بگرد (بدون درخواست به تلگرام)
            for msgs in self._my_msgs.values():
                if from_id in msgs:
                    return True
            # ردیف‌های دیتابیس: پیام‌هایی که خودم در این گپ فرستاده‌ام
            return False
        except Exception:
            return False

    async def _fix_numeric_name(self, uid: int, sender) -> None:
        """
        اگر جایی نامِ این کاربر فقط «آیدی عددی» ذخیره شده، با نام واقعی
        درستش می‌کند (شکایت کاربر: «گاهی فقط آیدی عددی ثبت می‌شود»).
        """
        full = " ".join(x for x in (getattr(sender, "first_name", None),
                                    getattr(sender, "last_name", None)) if x).strip()
        if not full:
            return
        try:
            from database import db
            prof = await db.get_ai_profile(self.user_id, uid)
            if prof and (prof.get("target_name") or "").lstrip("-").isdigit():
                await db.upsert_ai_profile(self.user_id, uid, target_name=full)
                self.logger.info(f"نام مخاطب {uid} از آیدی به «{full}» اصلاح شد")
        except Exception as e:              # noqa: BLE001
            self.logger.debug(f"name repair skipped: {e}")

    # ═══════════ دستور ═══════════

    async def _on_command(self, event):
        self._ensure_state()
        raw = to_ascii((event.raw_text or "").strip())
        parts = raw.split(maxsplit=1)
        body = parts[1].strip() if len(parts) > 1 else ""

        # پیام دستور پاک شود — ممکن است در چت کسی زده شده باشد
        try:
            await event.delete()
        except Exception:
            pass

        if not body:
            await self._send_owner(self._usage())
            return

        first_word = body.split(maxsplit=1)[0]

        # «.کاربر دکمه روشن|خاموش|وضعیت» → دکمه‌های کارت ضدحذف
        if first_word in ("دکمه", "دکمه‌ها", "دکمه ها"):
            await self._toggle_buttons(body)
            return

        # «.کاربر سابقه [جست‌وجو|پاکسازی]» → منوی تعاملی کسانی که دیده‌ایم
        if first_word in ("سابقه", "تاریخچه", "لیست"):
            rest = body.split(maxsplit=1)[1].strip() if len(body.split(maxsplit=1)) > 1 else ""
            if rest in ("پاکسازی", "پاک", "تمیز"):
                from database import db
                n = await db.purge_group_rows(self.user_id)
                await self._send_owner(
                    f"🧹 <b>{n} ردیف گپ</b> از سابقه پاک شد.\n"
                    "پیوی‌ها دست‌نخورده ماندند.")
                return
            page = await self.history_page(0, rest)
            await self._send_owner(page["text"], kb=self._history_kb(page))
            return

        # «.کاربر بازیابی» → از گزارش‌های ضدحذف، کاربران قدیمی را برگردان
        if first_word in ("بازیابی", "ریکاوری", "recover"):
            await self._send_owner("🔎 دارم گزارش‌های ضدحذف را می‌خوانم…")
            n = await self.recover_reports()
            if not n:
                await self._send_owner(
                    "📭 چیزی در گزارش‌های ضدحذف پیدا نشد.\n"
                    "اگر پیامِ حذف‌شده‌ای داری، آیدی‌اش را بده:\n"
                    "<code>.کاربر ‹آیدی›</code>")
                return
            page = await self.history_page(0)
            await self._send_owner(
                f"✅ <b>{n} کاربر</b> از گزارش‌های ضدحذف به سابقه برگشتند "
                "(با آیدی همان پیام).\n\n" + page["text"],
                kb=self._history_kb(page))
            return

        target, action, payload, deep = self._parse(body)
        if not target:
            await self._send_owner(self._usage())
            return

        res = await self.lookup(target, deep=deep)

        if not res.get("ok"):
            await self._send_card(res, target)
            return

        note = ""
        if action == "send":
            if payload:
                note = (
                    "📝 <b>متنی که می‌خواهی بفرستی:</b>\n"
                    f"{_esc(payload)}\n\n"
                    "برای تأیید، دکمه‌ی «✉️ نوشتن پیام» را بزن و همین متن را "
                    "بفرست (یا مسیر زیر را عوض کن)."
                )
            else:
                note = ("✉️ برای فرستادن پیام، دکمه‌ی «✉️ نوشتن پیام» را بزن — "
                        "متن را می‌نویسی، پیش‌نمایش می‌بینی و بعد تأیید می‌کنی.")
        elif action == "draft":
            got = await self.prepare_chat(res["id"], payload or None)
            note = ("✅ پیش‌نویس ساخته شد؛ چت در لیست تلگرامت بالا آمد. "
                    "طرف مقابل هیچ اطلاعی نگرفت." if got.get("ok")
                    else f"❌ پیش‌نویس ساخته نشد: {_esc(got.get('reason'))}")

        await self._send_card(res, target, note=note)

    @staticmethod
    def _parse(body: str) -> tuple[str | None, str | None, str, bool]:
        """
        ورودی را به (هدف، کار، متن، جست‌وجوی عمیق) تبدیل می‌کند.
          «123 پیام سلام»    → ("123", "send", "سلام", False)
          «123 پیش‌نویس متن»  → ("123", "draft", "متن", False)
          «123 کامل»          → ("123", None, "", True)
        """
        parts = body.split(maxsplit=2)
        target = parts[0].strip()
        action = None
        payload = ""
        deep = False

        if len(parts) >= 2 and parts[1] in ("پیام", "پیش‌نویس", "پیام بده"):
            action = "draft" if parts[1] == "پیش‌نویس" else "send"
            payload = parts[2].strip() if len(parts) > 2 else ""
        elif len(parts) >= 2 and parts[1] in ("کامل", "بگرد"):
            deep = True
        return target, action, payload, deep

    # ═══════════ پیدا کردن کاربر ═══════════

    async def _entity_for(self, uid: int):
        self._ensure_state()
        """entity ذخیره‌شده یا تلاش دوباره"""
        ent = self._known.get(uid)
        if ent is not None:
            return ent
        res = await self.lookup(str(uid))
        return self._known.get(uid) if res.get("ok") else None

    async def lookup(self, target: str, deep: bool = False) -> dict:
        self._ensure_state()
        self._record_fail = None          # وضعیت جست‌وجوی قبلی پاک شود
        raw = to_ascii((target or "").strip())
        if not raw:
            return {"ok": False, "reason": "آیدی یا یوزرنیم ندادی"}

        # ── شماره‌ی تلفن ──
        if looks_like_phone(raw):
            return await self.lookup_phone(raw)

        # ── یوزرنیم / لینک ──
        username = None
        if raw.startswith("@"):
            username = raw[1:].strip()
        elif "t.me/" in raw or "telegram.me/" in raw:
            tail = raw.rstrip("/").split("/")[-1]
            if tail.startswith("+") or tail.lower().startswith("joinchat"):
                return {"ok": False, "reason": "این لینکِ دعوت گروه/کانال است، نه کاربر"}
            username = tail
        if username and not username.isdigit():
            # اول در سابقه بگرد (سریع، بدون درخواست به تلگرام، و با hash)
            from database import db
            rec = await db.find_user_record(self.user_id, username=username)
            if rec and rec.get("access_hash"):
                got = await self._from_record(rec, how="سابقه")
                if got.get("ok"):
                    return got
            return await self._lookup_username(username)

        # ── آیدی عددی ──
        uid = int(raw) if raw.lstrip("-").isdigit() else None
        if uid is None:
            return {"ok": False, "reason": "آیدی عددی یا یوزرنیم معتبر نیست"}
        if self._me_id and abs(uid) == self._me_id:
            return {"ok": False, "reason": "این آیدی خودت است 🙂"}

        # ── اول سابقه‌ی خودمان (بدون هیچ درخواستی به تلگرام) ──
        from database import db
        rec = await db.get_user_record(self.user_id, uid)
        if rec and rec.get("access_hash"):
            got = await self._from_record(rec, how="سابقه (قبلاً دیده‌شده)")
            if got.get("ok"):
                return got
            # نشد — ولی یادت بماند که سابقه داشتیم (برای پیام نهایی)
            self._record_fail = uid

        # زمان انتظار فقط برای جلوگیری از هجوم درخواست‌هاست؛ جست‌وجوی قبلیِ
        # «ناموفق» پاک می‌شود تا کاربر برای تلاش دوباره گیر نکند.
        now = time.monotonic()
        last_ok = self._last_ok.get(uid, 0.0)
        last_fail = self._last_fail.get(uid, 0.0)
        if now - last_ok < LOOKUP_COOLDOWN or now - last_fail < 0.8:
            wait = max(LOOKUP_COOLDOWN - (now - last_ok), 0.8 - (now - last_fail))
            return {"ok": False,
                    "reason": f"همین حالا جست‌وجو شد؛ {wait:.0f} ثانیه صبر کن",
                    "cooldown": True}

        ent, how = self._known.get(uid), "حافظه‌ی ربات"

        if ent is None:
            try:
                ent = await self.client.get_entity(uid)
                how = "نشست تلگرام"
            except FloodWaitError as e:
                return {"ok": False, "reason": f"محدودیت تلگرام — {e.seconds} ثانیه صبر کن"}
            except Exception:
                ent = None

        if ent is None:
            ent = await self._find_in_contacts(uid)
            if ent is not None:
                how = "مخاطبینت"

        if ent is None:
            ent = await self._find_in_dialogs(uid, deep)
            if ent is not None:
                how = "چت‌هایت"

        if ent is None:
            # تلاش آخر: اگر در سابقه یوزرنیم داریم، با همان امتحان کن
            # (یوزرنیم همیشه جواب می‌دهد، حتی بدون access_hash).
            try:
                from database import db
                rec_u = await db.get_user_record(self.user_id, uid)
                uname = (rec_u or {}).get("username")
                if uname:
                    got_u = await self._lookup_username(uname)
                    if got_u.get("ok"):
                        got_u["how"] = "یوزرنیم (از سابقه)"
                        return got_u
            except Exception as e:                 # noqa: BLE001
                self.logger.debug(f"username fallback failed: {e}")

        if ent is None:
            self._last_fail[uid] = time.monotonic()
            # شاید شماره‌ی تلفن باشد که بدون صفر اول نوشته شده
            # (مثل 8904799614) — یک بار دیگر به‌عنوان شماره امتحان کن.
            fb = await self._phone_fallback(raw)
            if fb is not None and fb.get("ok"):
                return fb
            note = ""
            if fb is not None and not fb.get("cooldown"):
                note = (f"<br>🔎 به‌عنوان شماره هم امتحان شد: "
                        f"{_esc(fb.get('reason') or '')}")
            # لینکِ باز کردن چت: اول از سابقه، بعد از گزارش‌های ضدحذف، و در
            # آخر حداقل لینک خود چت. با یک ضربه در اپ تلگرامِ خودت باز
            # می‌شود و یک پیام کوتاه، هویتش را دوباره برای ربات ثبت می‌کند.
            link = await self._recovery_link(uid)
            return {
                "ok": False,
                "id": uid,
                "msg_link": link,
                "reason": (f"سابقه دارد ولی تلگرام قبولش نکرد"
                           if getattr(self, "_record_fail", None) == uid
                           else "آیدی را نشناخت (نه در چت‌ها، نه مخاطبین، نه سابقه، نه نشست)"),
                "hint": "• اگر شماره‌اش را داری: <code>.کاربر 09121234567</code>\n"
                        "• اگر یوزرنیم دارد: <code>.کاربر @username</code>\n"
                        "• اگر تا حالا به تو پیام داده، با «.کاربر سابقه» پیدایش کن.\n"
                        "• اگر هیچ‌کدام نشد: روی لینک چت (یا کارت ضدحذف) بزن و "
                        "در تلگرام خودت یک پیام کوتاه برایش بفرست — از همان "
                        "لحظه ربات هویت او را ذخیره می‌کند و دیگر گم نمی‌شود."
                        + note,
            }

        self._last_ok[uid] = time.monotonic()
        return await self._profile(ent, uid, how)

    # ═══════════ شماره‌ی تلفن ═══════════

    @staticmethod
    def _looks_like_phone(raw: str) -> bool:
        return looks_like_phone(raw)

    @staticmethod
    def normalize_phone(raw: str) -> str:
        return normalize_phone(raw)

    def _phone_quota_ok(self) -> tuple[bool, str]:
        """سقف روزانه و فاصله‌ی اجباری جست‌وجوی شماره"""
        today = time.strftime("%Y-%m-%d")
        if self._phone_day != today:
            self._phone_day, self._phone_count = today, 0
        if self._phone_count >= PHONE_DAILY_LIMIT:
            return False, (f"سقف روزانه‌ی جست‌وجو با شماره پر شد "
                           f"({PHONE_DAILY_LIMIT} بار). فردا دوباره امتحان کن.")
        waited = time.monotonic() - getattr(self, "_phone_last", 0.0)
        if waited < PHONE_COOLDOWN:
            return False, (f"{PHONE_COOLDOWN - waited:.0f} ثانیه صبر کن "
                           "(تلگرام این کار را محدود می‌کند)")
        return True, ""

    async def _phone_fallback(self, raw: str) -> dict | None:
        """
        وقتی عددی به‌عنوان آیدی پیدا نشد، ممکن است شماره‌ی تلفن باشد که
        بدون صفر/+ نوشته شده. اما این کار سهمیه‌ی روزانه (۲۵ بار) را مصرف
        می‌کند، پس فقط وقتی امتحان می‌شود که شکل شماره‌ی محلی داشته باشد:

          • ۱۰ رقم که با ۹ شروع شود → موبایل بدون صفر (‎9121234567)
          • ۱۰ رقم که با ۸ شروع شود → تلفن ثابت بدون صفر (‎8904799614)
          • ۱۲ رقم که با ۹۸ شروع شود
          • ۱۱ رقم که با ۰ شروع شود

        آیدی‌های واقعی تلگرام امروزی ۹ رقمی‌اند و با ۵/۶/۷ شروع می‌شوند؛
        پس با این شرط، دیگر هیچ آیدی‌ای بی‌دلیل «مخاطب موقت» نمی‌سازد.
        """
        digits = (raw or "").replace(" ", "").replace("-", "")
        if not digits.isdigit():
            return None
        # پیش‌شماره‌های واقعی موبایل ایران: 090/091/092/093/099
        mobile2 = digits[1] if len(digits) > 1 else ""
        mobile3 = digits[2] if len(digits) > 2 else ""
        looks = (
            (len(digits) == 10 and digits[0] == "9" and mobile2 in "01239")
            or (len(digits) == 11 and digits[0] == "0" and digits[1] == "9"
                and mobile3 in "01239")
            # تلفن ثابت: 0 + کد شهر (۲ تا ۳ رقم) + شماره
            or (len(digits) == 11 and digits[0] == "0"
                and digits[1] in "12345678")
        )
        if not looks:
            # آیدی‌های تلگرام (حتی ۱۰ رقمی مثل 8904799614) این‌جا نمی‌آیند تا
            # سهمیه‌ی روزانه‌ی جست‌وجوی شماره بی‌دلیل مصرف نشود.
            return None
        return await self.lookup_phone(digits)

    async def lookup_phone(self, raw: str) -> dict:
        """
        پیدا کردن کاربر با شماره‌ی تلفن.

        روش: شماره به‌عنوان «مخاطب موقت» ثبت می‌شود (ImportContacts)، تلگرام
        اگر شماره در تلگرام باشد کاربر را برمی‌گرداند، و بعد مخاطب موقت
        بلافاصله حذف می‌شود. این کار را تلگرام محدود می‌کند، پس:
          • بین دو جست‌وجو ۴۵ ثانیه فاصله
          • حداکثر ۲۵ بار در روز
        """
        self._ensure_state()
        phone = normalize_phone(raw)
        digits = phone.lstrip("+")

        ok, why = self._phone_quota_ok()
        if not ok:
            return {"ok": False, "reason": why, "cooldown": True}

        # شاید قبلاً همین شماره را دیده باشیم → بدون درخواست به تلگرام
        from database import db
        rec = (await db.find_user_record(self.user_id, phone=phone)
               or await db.find_user_record(self.user_id, phone=digits))
        if rec and rec.get("access_hash"):
            got = await self._from_record(rec, how="سابقه (شماره)")
            if got.get("ok"):
                return got

        self._phone_last = time.monotonic()
        self._phone_count += 1

        try:
            res = await self.client(ImportContactsRequest(
                contacts=[InputPhoneContact(client_id=random.randint(1, 10 ** 9),
                                            phone=phone,
                                            first_name=".", last_name="")]))
        except FloodWaitError as e:
            self._phone_count = PHONE_DAILY_LIMIT     # تا فردا قفل کن
            return {"ok": False,
                    "reason": f"تلگرام محدودت کرد؛ {e.seconds} ثانیه صبر کن "
                              "(ممکن است موقت باشد)"}
        except Exception as e:                        # noqa: BLE001
            return {"ok": False, "reason": f"جست‌وجو با شماره نشد — "
                                           f"{type(e).__name__}: {e}"}

        users = list(getattr(res, "users", None) or [])
        if not users:
            return {"ok": False,
                    "reason": "با این شماره حسابی در تلگرام پیدا نشد "
                              "(یا طرف «پیدا شدن با شماره» را بسته)"}

        user = users[0]
        # مخاطب موقت را پاک کن: هم لیست مخاطبینت شلوغ نشود، هم اثر کمتری بماند
        try:
            await self.client(DeleteContactsRequest(
                id=[await self.client.get_input_entity(user)]))
        except Exception as e:                        # noqa: BLE001
            self.logger.warning(f"temp contact cleanup failed: {e}")

        await db.remember_user(
            self.user_id, user.id,
            access_hash=getattr(user, "access_hash", None),
            first_name=getattr(user, "first_name", None),
            last_name=getattr(user, "last_name", None),
            username=getattr(user, "username", None),
            phone=phone, is_bot=bool(getattr(user, "bot", False)),
            source="شماره")

        got = await self._profile(user, user.id, f"شماره ({phone})")
        if got.get("ok"):
            self._last_ok[user.id] = time.monotonic()
            got["phone_input"] = phone
        return got

    # ═══════════ بازیابی از گزارش‌های ضدحذف ═══════════
    #
    # چرا لازم است؟ وقتی طرف چتش را پاک کرده و تلگرام هم access_hash نمی‌دهد،
    # هیچ راه API‌ای برای رسیدن به او نیست. اما گزارش‌های ضدحذفِ قدیمی که در
    # چت ذخیره‌سازی مانده‌اند، «آیدی کاربر» و «آیدی همان پیام» را دارند
    # (tg://openmessage?user_id=X&message_id=Y). با همان لینک، اپ تلگرامِ
    # خودِ کاربر چت را باز می‌کند و با یک پیام، هویت طرف دوباره ثبت می‌شود.

    REPORT_LINK_RE = re.compile(r"user_id=(-?\d+)(?:&(?:amp;)?message_id=(\d+))?")
    REPORT_NAME_RE = re.compile(r"فرستنده:\s*(.+?)\s*\((\d{5,})\)")

    async def _storage_peer(self):
        """چتی که گزارش‌های ضدحذف در آن ذخیره می‌شوند (پیش‌فرض: پیام‌های ذخیره‌شده)"""
        try:
            from database import db
            tgt = await db.get_storage_target(self.user_id, "anti_delete")
            tid = (tgt or {}).get("target_id")
        except Exception:                          # noqa: BLE001
            tid = None
        if not tid or tid == self._me_id:
            return "me"
        for cand in (tid, int(f"-100{tid}") if isinstance(tid, int) and tid > 0 else tid):
            try:
                return await self.client.get_entity(cand)
            except Exception:                      # noqa: BLE001
                continue
        return "me"

    @classmethod
    def parse_report(cls, text: str) -> dict | None:
        """استخراج آیدی کاربر/پیام/نام از متن یک گزارش ضدحذف"""
        if not text or "user_id=" not in text:
            return None
        m = cls.REPORT_LINK_RE.search(text)
        if not m:
            return None
        uid = int(m.group(1))
        msg_id = int(m.group(2)) if m.group(2) else None
        name = None
        nm = cls.REPORT_NAME_RE.search(text)
        if nm:
            name = (nm.group(1) or "").strip()
            if name.isdigit():
                name = None
        return {"uid": uid, "msg_id": msg_id, "name": name,
                "link": (f"tg://openmessage?user_id={uid}"
                         + (f"&message_id={msg_id}" if msg_id else ""))}

    async def find_in_reports(self, uid: int) -> dict | None:
        """
        جست‌وجوی یک کاربر در گزارش‌های ضدحذف (سبک: با search، حداکثر ۳۰ پیام).
        نتیجه در حافظه کش می‌شود تا جست‌وجوی بعدی صفر درخواست باشد.
        """
        self._ensure_state()
        if uid in self._report_cache:
            return self._report_cache[uid]
        out = None
        try:
            peer = await self._storage_peer()
            async for msg in self.client.iter_messages(peer, search=str(uid),
                                                       limit=30):
                info = self.parse_report(msg.raw_text or "")
                if info and info["uid"] == uid:
                    out = info
                    break
        except Exception as e:                     # noqa: BLE001
            self.logger.debug(f"report search failed: {type(e).__name__}: {e}")
            return None
        if len(self._report_cache) > 200:
            for k in list(self._report_cache)[:100]:
                self._report_cache.pop(k, None)
        self._report_cache[uid] = out
        if out and out.get("msg_id"):
            try:
                from database import db
                await db.set_directory_link(self.user_id, uid, out["msg_id"])
            except Exception:                      # noqa: BLE001
                pass
        return out

    async def recover_reports(self, limit: int = 400) -> int:
        """
        کل گزارش‌های ضدحذف را می‌خواند و از آن‌ها سابقه می‌سازد
        (آیدی + لینک همان پیام + نام). برای «.کاربر بازیابی».
        """
        self._ensure_state()
        found: dict[int, dict] = {}
        try:
            peer = await self._storage_peer()
            async for msg in self.client.iter_messages(peer, limit=limit):
                info = self.parse_report(msg.raw_text or "")
                if not info:
                    continue
                cur = found.get(info["uid"])
                # جدیدترین پیام (msg_id بزرگ‌تر) را نگه دار
                if cur is None or (info["msg_id"] or 0) > (cur["msg_id"] or 0):
                    found[info["uid"]] = info
        except Exception as e:                     # noqa: BLE001
            self.logger.warning(f"recover_reports failed: {type(e).__name__}: {e}")
            return 0

        if not found:
            return 0
        from database import db
        for uid, info in found.items():
            self._report_cache[uid] = info
            try:
                await db.remember_user(
                    self.user_id, uid,
                    first_name=info.get("name"), kind="pv",
                    source="گزارش ضدحذف", last_msg_id=info.get("msg_id"))
            except Exception as e:                 # noqa: BLE001
                self.logger.debug(f"report remember failed: {e}")
        return len(found)

    async def _recovery_link(self, uid: int) -> str | None:
        """
        لینکِ باز کردن چتِ یک کاربر غیرقابل‌دسترس:
          • از سابقه (last_msg_id) اگر داشته باشیم — همان پیام
          • وگرنه از گزارش‌های ضدحذف
          • وگرنه حداقل لینک باز کردن خود چت (بدون آیدی پیام)
        """
        try:
            from database import db
            rec = await db.get_user_record(self.user_id, uid)
            if rec and rec.get("last_msg_id"):
                return (f"tg://openmessage?user_id={uid}"
                        f"&message_id={int(rec['last_msg_id'])}")
        except Exception:                          # noqa: BLE001
            pass
        info = await self.find_in_reports(uid)
        if info and info.get("link"):
            return info["link"]
        return f"tg://openmessage?user_id={uid}"

    # ═══════════ سابقه (دایرکتوری) ═══════════

    async def _from_record(self, rec: dict, how: str = "سابقه") -> dict:
        """
        کاربر را از سابقه‌ی خودمان می‌سازد — با access_hash.
        مزیت مهم: هیچ گروه/چت مشترکی لازم نیست (شکایت کاربر) و بعد از پاک
        شدن چت هم کار می‌کند.
        """
        uid = int(rec.get("tg_id"))
        hash_ = rec.get("access_hash")
        ent = None
        if hash_:
            try:
                # مستقیم InputPeerUser: بدون هیچ جست‌وجوی اضافه، حتی وقتی
                # هیچ چت/گروه مشترکی نیست (کلید حل مشکل کاربر).
                peer = tl.InputPeerUser(user_id=uid, access_hash=int(hash_))
                ent = await self.client.get_entity(peer)
                if ent is not None:
                    self._known[uid] = ent
            except Exception as e:                # noqa: BLE001
                self.logger.debug(f"hash lookup failed for {uid}: "
                                  f"{type(e).__name__}: {e}")
                ent = None
        if ent is None:
            # ناموفق بود → جست‌وجوی محدود در چت‌ها (نه اسکن کامل)
            ent = await self._find_in_dialogs(uid, False)
        if ent is None:
            return {"ok": False, "reason": "سابقه دارد ولی تلگرام قبولش نکرد"}

        got = await self._profile(ent, uid, how)
        if got.get("ok"):
            # فیلدهای سابقه را پر کن (اگر تلگرام نداشت)
            got["phone"] = got.get("phone") or rec.get("phone")
            got["msgs"] = rec.get("msgs")
            got["kind"] = rec.get("kind")
            # لینک مستقیم پریدن به همان آخرین پیام
            if rec.get("last_msg_id"):
                got["msg_link"] = (f"tg://openmessage?user_id={uid}"
                                   f"&message_id={int(rec['last_msg_id'])}")
            got["last_seen"] = got.get("last_seen") or (
                rec["last_seen"].strftime("%Y-%m-%d %H:%M") if rec.get("last_seen") else None)
            got["from_history"] = True
        return got

    @staticmethod
    def _history_kb(page: dict):
        """دکمه‌های منوی سابقه (این‌جا ساخته می‌شود تا وابستگی به ربات کم شود)"""
        try:
            from bot.user_panel import history_kb
            return history_kb(page)
        except Exception as e:                     # noqa: BLE001
            import logging
            logging.getLogger("user_info").debug(f"history_kb failed: {e}")
            return None

    async def history_page(self, page: int = 0, query: str = "",
                           per_page: int = 8) -> dict:
        """
        یک صفحه از سابقه (برای منوی تعاملی) — مرتب بر اساس **آخرین پیام**.
        خروجی: {ok, text, rows:[(uid, label)], page, pages, total, groups}
        """
        from database import db
        try:
            await db.prune_directory(self.user_id)
        except Exception as e:                     # noqa: BLE001
            self.logger.debug(f"prune skipped: {e}")

        page = max(0, int(page or 0))
        total = await db.directory_count(self.user_id, exclude_kind="group")
        try:
            groups = await db.directory_count(self.user_id, kind="group")
        except Exception:                          # noqa: BLE001
            groups = 0

        if query:
            rows = await db.search_directory(self.user_id, query, limit=20,
                                             exclude_kind="group")
            pages = 1
            page = 0
        else:
            pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, pages - 1)
            rows = await db.list_directory(self.user_id, limit=per_page,
                                           offset=page * per_page,
                                           exclude_kind="group")

        if not rows:
            extra = (f"\n\n({groups} نفر از گپ‌ها موقتاً نگه داشته شده‌اند و "
                     "اینجا نمایش داده نمی‌شوند.)") if groups else ""
            return {"ok": True, "rows": [], "page": 0, "pages": 1, "total": 0,
                    "groups": groups, "text":
                    ("📇 <b>سابقه خالی است</b>\n\nهر کسی که در <b>پیوی</b> به "
                     "تو پیام بدهد یا هر کاربری که خودت جست‌وجو کنی، اینجا "
                     "ذخیره می‌شود تا حتی بعد از پاک کردن چت پیدایش کنی."
                     + extra)}

        lines = [f"📇 <b>سابقه‌ی کاربران</b> — صفحه {page + 1} از {pages}",
                 f"<i>مرتب بر اساس آخرین پیام · مجموع {total} نفر</i>", ""]
        kb_rows = []
        start = page * per_page if not query else 0
        for i, r in enumerate(rows, start=start + 1):
            name = (" ".join(x for x in (r.get("first_name"), r.get("last_name"))
                             if x).strip()
                    or (f"@{r['username']}" if r.get("username")
                        else f"بی‌نام ({r.get('tg_id')})"))
            when = str(r.get("last_seen") or "")[:16]
            extra = []
            if r.get("msgs"):
                extra.append(f"{r['msgs']} پیام")
            if (r.get("kind") or "") == "group":
                extra.append("گپ")
            lines.append(f"{i}. 👤 <b>{_esc(name)}</b> · "
                         f"<code>{r.get('tg_id')}</code>"
                         + (" · " + " · ".join(extra) if extra else "")
                         + f"\n    🕐 {_esc(when)}")
            label = name if len(name) <= 28 else name[:27] + "…"
            kb_rows.append((int(r.get("tg_id")), f"{i}. {label}"))

        if groups:
            lines.append("")
            lines.append(f"<i>({groups} نفر از گپ‌ها موقتاً نگه داشته شده‌اند و "
                         "این‌جا نمایش داده نمی‌شوند.)</i>")
        if pages > 1 and not query:
            lines.append("")
            lines.append("<i>برای دیدن بقیه، با دکمه‌های پایین صفحه عوض کن — "
                         "یا اسمش را جست‌وجو کن: <code>.کاربر سابقه ‹اسم›</code></i>")

        return {"ok": True, "text": "\n".join(lines), "rows": kb_rows,
                "page": page, "pages": pages, "total": total, "groups": groups,
                "query": query}

    async def history_card(self, query: str = "") -> str:
        """کارت «سابقه» — کسانی که دیده‌ایم (برای وقتی چت پاک شده)"""
        from database import db
        # سابقه را کوتاه نگه دار (خواسته‌ی کاربر: «هر دفعه یک عالمه کاربر
        # سرچ نشه و تاریخچه خیلی عقب نره»): آدم‌های گپِ قدیمی پاک می‌شوند.
        try:
            await db.prune_directory(self.user_id)
        except Exception as e:                 # noqa: BLE001
            self.logger.debug(f"prune skipped: {e}")

        # فقط پیوی‌ها (و کسانی که خودت جست‌وجو کرده‌ای). آدم‌های گپ این‌جا
        # نمی‌آیند تا سابقه شلوغ نشود — خواسته‌ی صریح کاربر.
        if query:
            rows = await db.search_directory(self.user_id, query, limit=20,
                                             exclude_kind="group")
        else:
            rows = await db.list_directory(self.user_id, limit=25,
                                           exclude_kind="group")
        groups = 0
        try:
            groups = await db.directory_count(self.user_id, kind="group")
        except Exception:
            groups = 0
        if not rows:
            extra = ""
            if groups:
                extra = (f"\n\n({groups} نفر هم در گپ‌ها به تو ریپلای زده‌اند که "
                         "به‌صورت موقت نگه داشته می‌شوند و این‌جا نمی‌آیند.)")
            return ("📇 <b>سابقه خالی است</b>\n\nاز این به بعد هر کسی که در "
                    "<b>پیوی</b> به تو پیام بدهد یا هر کاربری که خودت جست‌وجو "
                    "کنی، این‌جا ذخیره می‌شود تا حتی بعد از پاک کردن چت هم "
                    "بتوانی پیدایش کنی." + extra)
        total = await db.directory_count(self.user_id, exclude_kind="group")
        lines = [f"📇 <b>سابقه‌ی کاربران</b> (نمایش {len(rows)} از {total})", ""]
        for r in rows:
            name = " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x) \
                   or f"بدون نام ({r.get('tg_id')})"
            bits = [f"👤 <b>{_esc(name)}</b>"]
            if r.get("username"):
                bits.append(f"@{_esc(r['username'])}")
            bits.append(f"<code>{r.get('tg_id')}</code>")
            if r.get("phone"):
                bits.append(f"📞 <code>{_esc(r['phone'])}</code>")
            if r.get("msgs"):
                bits.append(f"{r['msgs']} پیام")
            bits.append(f"🕐 {_esc(str(r.get('last_seen') or '')[:16])}")
            lines.append(" • " + " · ".join(bits))
        lines += ["", "برای کارت کامل هر نفر: <code>.کاربر ‹آیدی›</code>"]
        if groups:
            lines.append(f"<i>({groups} نفر از گپ‌ها موقتاً نگه داشته شده‌اند "
                         "و این‌جا نمایش داده نمی‌شوند.)</i>")
        return "\n".join(lines)

    async def _lookup_username(self, username: str) -> dict:
        try:
            ent = await self.client.get_entity(username)
        except FloodWaitError as e:
            return {"ok": False, "reason": f"محدودیت تلگرام — {e.seconds} ثانیه صبر کن"}
        except Exception as e:                   # noqa: BLE001
            return {"ok": False, "reason": f"با این یوزرنیم چیزی پیدا نشد "
                                           f"({type(e).__name__})"}
        if not isinstance(ent, tl.User):
            kind = "کانال" if isinstance(ent, tl.Channel) else "گروه"
            return {"ok": False, "reason": f"این یوزرنیم مربوط به یک {kind} است، "
                                           "نه کاربر"}
        return await self._profile(ent, ent.id, "یوزرنیم")

    async def _find_in_contacts(self, uid: int):
        try:
            res = await self.client(functions.contacts.GetContactsRequest(hash=0))
        except Exception as e:                   # noqa: BLE001
            self.logger.debug(f"contacts failed: {e}")
            return None
        for u in getattr(res, "users", None) or []:
            if getattr(u, "id", None) == uid:
                return u
        return None

    async def _find_in_dialogs(self, uid: int, deep: bool = False):
        # گارد نرخ: اگر همین چند لحظه پیش اسکن کرده‌ایم، دوباره نزن
        now = time.monotonic()
        if not deep and (now - getattr(self, "_last_scan", 0.0)) < DIALOG_SCAN_GAP:
            self.logger.debug("dialog scan skipped (rate guard)")
            return None
        self._last_scan = now
        limit = DIALOG_SCAN_LIMIT_DEEP if deep else DIALOG_SCAN_LIMIT
        try:
            async for dialog in self.client.iter_dialogs(limit=limit,
                                                         ignore_migrated=True):
                ent = getattr(dialog, "entity", None)
                if getattr(ent, "id", None) == uid:
                    return ent
        except FloodWaitError as e:
            self.logger.warning(f"dialog scan flood {e.seconds}s")
        except Exception as e:                   # noqa: BLE001
            self.logger.debug(f"dialog scan failed: {e}")
        return None

    async def _has_dialog(self, uid: int) -> bool | None:
        """آیا چتش در لیست خودت هست؟ (None = نامعلوم)"""
        ent = self._known.get(uid)
        if ent is None:
            return None
        try:
            peer = await self.client.get_input_entity(ent)
            res = await self.client(functions.messages.GetPeerDialogsRequest(
                peers=[tl.InputDialogPeer(peer)]))
            return bool(getattr(res, "dialogs", None))
        except Exception as e:                   # noqa: BLE001
            self.logger.debug(f"has_dialog failed: {e}")
        try:
            async for dialog in self.client.iter_dialogs(limit=200):
                if getattr(getattr(dialog, "entity", None), "id", None) == uid:
                    return True
        except Exception:                        # noqa: BLE001
            pass
        return None

    async def _personal_channel(self, peer):
        if not peer:
            return None
        info = {"id": getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None),
                "title": None, "username": None}
        try:
            ch = await self.client.get_entity(peer)
            info["title"] = getattr(ch, "title", None)
            info["username"] = getattr(ch, "username", None)
        except Exception:                        # noqa: BLE001
            pass
        return info

    @staticmethod
    def _last_seen(user) -> str | None:
        status = getattr(user, "status", None)
        if status is None:
            return None
        name = type(status).__name__
        if name == "UserStatusOnline":
            return "آنلاین"
        if name == "UserStatusOffline":
            when = getattr(status, "was_online", None)
            return f"آفلاین (آخرین بازدید {when:%Y-%m-%d %H:%M})" if when else "آفلاین"
        if name == "UserStatusRecently":
            return "اخیراً"
        if name == "UserStatusLastWeek":
            return "در هفته‌ی گذشته"
        if name == "UserStatusLastMonth":
            return "در ماه گذشته"
        return None

    async def _profile(self, ent, uid: int, how: str) -> dict:
        name = " ".join(x for x in (getattr(ent, "first_name", None),
                                    getattr(ent, "last_name", None)) if x) or None
        info = {
            "ok": True, "id": uid, "how": how,
            "name": name,
            "username": getattr(ent, "username", None),
            "phone": getattr(ent, "phone", None),
            "bot": bool(getattr(ent, "bot", False)),
            "verified": bool(getattr(ent, "verified", False)),
            "premium": bool(getattr(ent, "premium", False)),
            "scam": bool(getattr(ent, "scam", False)),
            "fake": bool(getattr(ent, "fake", False)),
            "last_seen": self._last_seen(ent),
            "bio": None, "common_chats": None, "blocked": None,
            "personal_channel": None, "photo": None,
        }

        # اول در حافظه ثبت شود تا _has_dialog بتواند peer را بسازد
        self._known[uid] = ent
        info["has_dialog"] = await self._has_dialog(uid)

        try:
            input_user = tl_utils.get_input_user(await self.client.get_input_entity(ent))
            full = await self.client(functions.users.GetFullUserRequest(input_user))
            fu = getattr(full, "full_user", None)
            if fu is not None:
                info["bio"] = getattr(fu, "about", None)
                info["common_chats"] = getattr(fu, "common_chats_count", None)
                info["blocked"] = bool(getattr(fu, "blocked", False))
                info["personal_channel"] = await self._personal_channel(
                    getattr(fu, "personal_chat_id", None))
        except Exception as e:                   # noqa: BLE001
            self.logger.debug(f"full profile failed: {type(e).__name__}: {e}")

        try:
            buf = io.BytesIO()
            await self.client.download_profile_photo(ent, file=buf)
            data = buf.getvalue()
            if data:
                info["photo"] = data
        except Exception as e:                   # noqa: BLE001
            self.logger.debug(f"photo failed: {type(e).__name__}")

        # هر چیزی که یاد گرفتیم را در سابقه ذخیره کن — همین است که بعداً
        # اجازه می‌دهد بدون چت/گروه مشترک پیدایش کنیم و به او پیام بدهیم.
        try:
            from database import db
            await db.remember_user(
                self.user_id, uid,
                access_hash=getattr(ent, "access_hash", None),
                first_name=getattr(ent, "first_name", None),
                last_name=getattr(ent, "last_name", None),
                username=getattr(ent, "username", None),
                phone=info.get("phone"), is_bot=bool(info.get("bot")),
                source=(info.get("how") or "جست‌وجو")[:60],
                kind="search",
            )
        except Exception as e:                    # noqa: BLE001
            self.logger.debug(f"directory write skipped: {e}")

        return info

    # ═══════════ کارها (API عمومی برای پنل) ═══════════

    async def _toggle_buttons(self, body: str) -> None:
        """روشن/خاموش کردن دکمه‌های «پیام بده» زیر کارت ضدحذف"""
        from database import db
        rows = {f["feature_name"]: f for f in await db.get_features(self.user_id)}
        row = rows.get("anti_delete") or {}
        cfg = dict(row.get("config_json") or {})
        arg = body.split(maxsplit=2)
        word = arg[1].strip() if len(arg) > 1 else ""
        if word in ("روشن", "on", "فعال"):
            cfg["quick_buttons"] = True
            await db.set_feature(self.user_id, "anti_delete", True, cfg)
            await self._send_owner("✅ دکمه‌های کارت ضدحذف روشن شد. "
                                   "از این به بعد زیر هر پیام حذف‌شده، "
                                   "«✉️ پیام بده» و «📂 آماده‌کردن چت» می‌آید.")
        elif word in ("خاموش", "off", "غیرفعال"):
            cfg["quick_buttons"] = False
            await db.set_feature(self.user_id, "anti_delete",
                                 bool(row.get("is_enabled", False)), cfg)
            await self._send_owner("🔕 دکمه‌های کارت ضدحذف خاموش شد.")
        else:
            state = "روشن ✅" if cfg.get("quick_buttons", True) else "خاموش ❌"
            await self._send_owner(
                f"🔘 دکمه‌های کارت ضدحذف: <b>{state}</b>\n\n"
                "<code>.کاربر دکمه روشن</code> · <code>.کاربر دکمه خاموش</code>")

    async def display_name(self, uid: int) -> str:
        """نام کاربر از سابقه (برای کارت‌های ضدحذف)"""
        self._ensure_state()
        from database import db
        rec = await db.get_user_record(self.user_id, uid)
        if rec:
            full = " ".join(x for x in (rec.get("first_name"), rec.get("last_name"))
                            if x).strip()
            if full:
                return full
            if rec.get("username"):
                return f"@{rec['username']}"
        ent = self._known.get(uid)
        if ent is not None:
            full = " ".join(x for x in (getattr(ent, "first_name", None),
                                        getattr(ent, "last_name", None)) if x).strip()
            if full:
                return full
        return str(uid)

    async def resolve_for_action(self, uid: int):
        """
        entity لازم برای کارها (پیش‌نویس/ارسال) از سابقه — بدون نیاز به
        گروه مشترک یا چت باقی‌مانده.
        """
        self._ensure_state()
        return await self._entity_for(uid)

    async def prepare_chat(self, uid: int, text: str | None = None) -> dict:
        self._ensure_state()
        """ساخت پیش‌نویس — طرف مقابل هیچ اطلاعی نمی‌گیرد"""
        ent = await self._entity_for(uid)
        if ent is None:
            return {"ok": False, "reason": "کاربر پیدا نشد"}
        draft = (text or DEFAULT_DRAFT).strip() or DEFAULT_DRAFT
        try:
            peer = await self.client.get_input_entity(ent)
            await self.client(functions.messages.SaveDraftRequest(
                peer=peer, message=draft[:MAX_TEXT], no_webpage=True))
            return {"ok": True, "draft": draft[:MAX_TEXT]}
        except FloodWaitError as e:
            return {"ok": False, "reason": f"محدودیت تلگرام — {e.seconds} ثانیه صبر کن"}
        except Exception as e:                   # noqa: BLE001
            return {"ok": False, "reason": f"{type(e).__name__}: {e}"}

    async def send_message(self, uid: int, text: str) -> dict:
        self._ensure_state()
        """ارسال واقعی (فقط بعد از تأیید خودت) — طرف اعلان می‌گیرد"""
        body = (text or "").strip()
        if not body:
            return {"ok": False, "reason": "متن خالی است"}
        ent = await self._entity_for(uid)
        if ent is None:
            return {"ok": False, "reason": "کاربر پیدا نشد"}
        try:
            msg = await self.client.send_message(ent, body[:MAX_TEXT])
            return {"ok": True, "msg_id": getattr(msg, "id", None),
                    "username": getattr(ent, "username", None)}
        except FloodWaitError as e:
            return {"ok": False, "reason": f"محدودیت تلگرام — {e.seconds} ثانیه صبر کن"}
        except Exception as e:                   # noqa: BLE001
            return {"ok": False, "reason": f"{type(e).__name__}: {e}"}

    def chat_links(self, uid: int) -> dict:
        self._ensure_state()
        """لینک‌های باز کردن چت (هیچ پیامی نمی‌فرستند)"""
        ent = self._known.get(uid)
        username = getattr(ent, "username", None) if ent is not None else None
        return {
            "deep": f"tg://openmessage?user_id={uid}",
            "username_url": f"https://t.me/{username}" if username else None,
            "mention": (f'<a href="tg://user?id={uid}">'
                        "👤 باز کردن پروفایل (بدون ارسال پیام)</a>"),
        }

    # ═══════════ فرستادن کارت به خودت ═══════════

    async def _send_owner(self, text: str, kb=None) -> bool:
        self._ensure_state()
        """پیوی ربات کنترلی (فالبک: پیام‌های ذخیره‌شده)"""
        if self._owner_tg:
            kwargs = {"reply_markup": kb} if kb is not None else {}
            if await runtime.notify_user(self._owner_tg, text, **kwargs):
                return True
        try:
            await self.client.send_message("me", text, parse_mode="html",
                                          link_preview=False)
            return True
        except Exception as e:                   # noqa: BLE001
            self.logger.error(f"owner notify failed: {e}")
            return False

    async def _send_card(self, info: dict, target: str, note: str = "") -> None:
        from bot.user_panel import card_kb, card_text

        payload = dict(info)
        if not payload.get("ok"):
            payload["target"] = target
        photo = payload.pop("photo", None)
        text = card_text(payload, note=note)

        uid = payload.get("id") if payload.get("ok") else None
        link = payload.get("msg_link")
        kb = card_kb(uid, payload.get("username"), link)

        if photo and self._owner_tg:
            if await runtime.notify_photo(self._owner_tg, photo, text,
                                          **{"reply_markup": kb}):
                return

        # بعضی کلاینت‌ها/نسخه‌ها دکمه‌ی URL با اسکیم tg:// را قبول نمی‌کنند
        # (BUTTON_URL_INVALID). در آن حالت بدون همان دکمه دوباره می‌فرستیم؛
        # خودِ لینک داخل متن پیام هست و از دست نمی‌رود.
        if self._owner_tg and link and not link.startswith("http"):
            sent = await runtime.send_card(self._owner_tg, text, reply_markup=kb,
                                           disable_web_page_preview=True)
            if sent is not None:
                return
            kb = card_kb(uid, payload.get("username"), None)
        await self._send_owner(text, kb=kb)

    @staticmethod
    def _usage() -> str:
        return (
            "👤 <b>کاربر با آیدی عددی</b>\n\n"
            "<code>.کاربر 123456789</code> — کارت پروفایل + دکمه‌ها\n"
            "<code>.کاربر @username</code> — با یوزرنیم\n"
            "<code>.کاربر 123456789 کامل</code> — جست‌وجوی عمیق‌تر در چت‌ها\n"
            "<code>.کاربر +989121234567</code> — با شماره‌ی تلفن\n"
            "<code>.کاربر 123456789 پیام سلام</code> — متن را آماده کن "
            "(ارسال فقط با تأیید خودت)\n"
            "<code>.کاربر 123456789 پیش‌نویس سلام</code> — پیش‌نویس دلخواه\n"
            "<code>.کاربر سابقه</code> — منوی کسانی که در پیوی دیده‌ایم "
            "(صفحه‌به‌صفحه، مرتب بر اساس آخرین پیام)\n"
            "<code>.کاربر سابقه ‹اسم›</code> — جست‌وجو در سابقه\n"
            "<code>.کاربر سابقه پاکسازی</code> — پاک کردن ردیف‌های گپ\n"
            "<code>.کاربر بازیابی</code> — برگرداندن کاربران از "
            "گزارش‌های ضدحذفِ قدیمی\n\n"
            "🛡 هیچ پیامی خودکار فرستاده نمی‌شود. برای این‌که طرف مقابل "
            "<b>هیچ اطلاعی نگیرد</b>، دکمه‌ی «📂 آماده‌کردن چت» را بزن: یک "
            "پیش‌نویس ساخته می‌شود و چت در لیست تلگرام خودت بالا می‌آید."
        )
