"""
«کاربر» — پیدا کردن کاربر با آیدی عددی، دیدن پروفایل، و آماده‌کردن/ارسال پیام.

دستورها (فقط از طرف خودت اجرا می‌شوند):
  .کاربر 123456789             → کارت پروفایل + دکمه‌ها
  .کاربر @username
  .کاربر https://t.me/username
  .کاربر 123456789 کامل        → جست‌وجوی عمیق‌تر در چت‌ها
  .کاربر 123456789 پیام سلام    → متن آماده‌ی ارسال (فقط با تأیید خودت)
  .کاربر 123456789 پیش‌نویس سلام → متن پیش‌نویس دلخواه

سه قاعده‌ی مهم:
  1) هیچ پیامی بدون فشردن «✅ ارسال کن» توسط خودت فرستاده نمی‌شود.
  2) «آماده‌کردن چت» = ذخیره‌ی پیش‌نویس (Draft). پیش‌نویس را فقط صاحب
     اکانت می‌بیند؛ طرف مقابل هیچ اعلان/پیامی نمی‌گیرد، ولی چت در لیست
     تلگرام خودت بالا می‌آید و همان‌جا می‌توانی بنویسی.
  3) خروجی به پیوی ربات کنترلی می‌رود (فالبک: پیام‌های ذخیره‌شده) و پیام
     خودِ دستور هم پاک می‌شود؛ پس در چتی که دستور را زدی چیزی نمی‌ماند.

چرا بعضی آیدی‌ها پیدا نمی‌شوند؟ تلگرام آیدیِ کاربری که هیچ نقطه‌ی مشترکی
با اکانتت ندارد (نه چت، نه مخاطب، نه گروه مشترک) را نمی‌دهد. این محدودیت
خود تلگرام است؛ با یوزرنیم یا با «کامل» بیشتر می‌شود.
"""

from __future__ import annotations

import html
import io
import time

from telethon import events, functions
from telethon import utils as tl_utils
from telethon.errors import FloodWaitError
from telethon.tl import types as tl

from core import runtime
from core.digits import to_ascii
from plugins.base import BasePlugin

MAX_TEXT = 4000
DIALOG_SCAN_LIMIT = 400          # جست‌وجوی معمولی در چت‌ها
DIALOG_SCAN_LIMIT_DEEP = 4000    # «کامل»
LOOKUP_COOLDOWN = 5.0            # ثانیه بین دو جست‌وجو (ضد FloodWait)
DEFAULT_DRAFT = "🤍"             # حتماً از bot/user_panel هم خوانده می‌شود


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
        if not hasattr(self, "_last_lookup"):
            self._last_lookup = {}
        if not hasattr(self, "_owner_tg"):
            self._owner_tg = None
        if not hasattr(self, "_me_id"):
            self._me_id = None

    async def start(self):
        self._ensure_state()

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
        self.logger.info("loaded")

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
        raw = to_ascii((target or "").strip())
        if not raw:
            return {"ok": False, "reason": "آیدی یا یوزرنیم ندادی"}

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
            return await self._lookup_username(username)

        # ── آیدی عددی ──
        uid = int(raw) if raw.lstrip("-").isdigit() else None
        if uid is None:
            return {"ok": False, "reason": "آیدی عددی یا یوزرنیم معتبر نیست"}
        if self._me_id and abs(uid) == self._me_id:
            return {"ok": False, "reason": "این آیدی خودت است 🙂"}

        now = time.monotonic()
        if now - self._last_lookup.get(uid, 0) < LOOKUP_COOLDOWN:
            return {"ok": False, "reason": f"{LOOKUP_COOLDOWN:.0f} ثانیه بین دو "
                                           "جست‌وجو باید فاصله باشد (ضد محدودیت تلگرام)"}
        self._last_lookup[uid] = now

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
            return {
                "ok": False,
                "reason": "آیدی را نشناخت (نه در چت‌ها، نه مخاطبین، نه نشست)",
                "hint": "اگر طرف یوزرنیم دارد، <code>.کاربر @username</code> را "
                        "امتحان کن.",
            }

        return await self._profile(ent, uid, how)

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

        return info

    # ═══════════ کارها (API عمومی برای پنل) ═══════════

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
        kb = card_kb(uid, payload.get("username"))

        if photo and self._owner_tg:
            if await runtime.notify_photo(self._owner_tg, photo, text, **{"reply_markup": kb}):
                return
        await self._send_owner(text, kb=kb)

    @staticmethod
    def _usage() -> str:
        return (
            "👤 <b>کاربر با آیدی عددی</b>\n\n"
            "<code>.کاربر 123456789</code> — کارت پروفایل + دکمه‌ها\n"
            "<code>.کاربر @username</code> — با یوزرنیم\n"
            "<code>.کاربر 123456789 کامل</code> — جست‌وجوی عمیق‌تر در چت‌ها\n"
            "<code>.کاربر 123456789 پیام سلام</code> — متن را آماده کن "
            "(ارسال فقط با تأیید خودت)\n"
            "<code>.کاربر 123456789 پیش‌نویس سلام</code> — پیش‌نویس دلخواه\n\n"
            "🛡 هیچ پیامی خودکار فرستاده نمی‌شود. برای این‌که طرف مقابل "
            "<b>هیچ اطلاعی نگیرد</b>، دکمه‌ی «📂 آماده‌کردن چت» را بزن: یک "
            "پیش‌نویس ساخته می‌شود و چت در لیست تلگرام خودت بالا می‌آید."
        )
