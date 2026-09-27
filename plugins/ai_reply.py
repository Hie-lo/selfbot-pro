"""
پلاگین پاسخ هوشمند (AI) — به‌جای من جواب می‌دهد، با لحن خودم و حافظه‌ی هر مخاطب.

کامندها:
  .ai روشن / .ai خاموش        — فعال/غیرفعال کردن در همین چت
  .ai وضعیت                   — وضعیت و تنظیمات همین چت
  .ai شخص ‹جایگاه›            — جایگاه این شخص (رفیق نزدیک، همکار، خانواده، ...)
  .ai خودکار / .ai دستی       — ارسال خودکار بدون تایید
  .ai سرچ ‹متن›               — جست‌وجو در حافظه
  .ai حافظه                   — وضعیت حافظه
  .ai سبک‌سازی                — فشرده‌کردن حافظه‌ی قدیمی (خودکار هم اجرا می‌شود)
  .ai فراموش کن               — پاک کردن حافظه‌ی همین چت
  .ai مود ‹نام› / .تنظیم مود ‹نام›
  .ai سطح ‹۰-۳› / .تنظیم سطح ‹۰-۳›
  .ai بس                      — سکوت تا وقتی خودم روشن کنم

⚠️ هر دستور/قابلیت جدید یا تغییرکرده → bot/help_content.py را هم به‌روز کنید
"""

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timezone

from telethon import events, functions
from telethon.errors import FloodWaitError
from telethon.tl import types

from core import ai_providers
from core import runtime
from database import db
from plugins import ai_engine as E
from plugins.base import BasePlugin

logger = logging.getLogger("plugin.ai_reply")

# ── پیش‌فرض‌ها ──
DEFAULT_CONFIG = {
    "persona": "",
    "mode": "عادی",
    "tone_level": 2,
    "emoji_level": 1,        # ۰ هیچ، ۱ خیلی کم، ۲ متوسط، ۳ آزاد
    "allowed_emojis": "",    # لیست ایموجی‌های خودم: «😂❤️🙏»
    "draft_only": True,      # پیش‌فرض: پیشنهاد به من، نه ارسال خودکار
    "all_private": False,    # اگر True: همه‌ی پیوی‌ها (وگرنه فقط چت‌های روشن‌شده)
    "rpm": 6, "rph": 60, "rpd": 300,
    "quiet_hours": "1-8",    # ساعاتی که جواب نمی‌دهد (به وقت محلی سرور)
    "min_delay": 1.2, "max_delay": 4.5,
    "typing_speed": 11.0,
    "raw_limit": 120,        # سقف پیام خام قبل از سبک‌سازی
    "keep_after_compact": 30,
    "fact_limit": 40,
    "stop_word": "#ساکت",
}

# پاسخ‌های جانشین: هیچ‌وقت بی‌جواب نمی‌ماند حتی وقتی همه‌ی سرویس‌ها خطا دهند
FALLBACK_REPLIES = [
    "الان دستم بند نیس، بعداً حرف می‌زنیم",
    "ببخش الان درگیرم، جواب می‌دم",
    "الان نمیتونم، بعداً",
    "چشم، بعداً کامل جوابت رو می‌دم",
]

# رجیستری پیش‌نویس‌ها — ربات کنترلی از این‌جا دکمه‌های ارسال/ویرایش می‌سازد
DRAFTS: dict[str, dict] = {}
_draft_seq = [0]


def _next_draft_id() -> str:
    _draft_seq[0] += 1
    return str(_draft_seq[0])


def get_draft(draft_id: str) -> dict | None:
    return DRAFTS.get(draft_id)


def drop_draft(draft_id: str) -> None:
    DRAFTS.pop(draft_id, None)


class AiReplyPlugin(BasePlugin):
    name = "ai_reply"
    description = "پاسخ هوشمند AI"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._cfg: dict = {}
        self._my_name_cache: str = ""
        self._my_id: int | None = None
        self._owner_tg: int | None = None
        self._paused: set[int] = set()        # چت‌هایی که با .ai بس ساکت شده‌اند
        self._last_activity: dict[int, float] = {}   # آخرین پیام خودم در هر چت
        self._usage: list[float] = []         # زمان استفاده‌ها برای سقف‌ها
        self._usage_day: list[float] = []
        self._busy: set[int] = set()          # چت‌هایی که همین حالا در حال پاسخ‌اند
        self._personas: dict[int, float] = {}  # آخرین ارسال پیشنهاد به هر چت

    # ═══════════ راه‌اندازی ═══════════

    async def start(self):
        try:
            me = await self.client.get_me()
            self._my_id = me.id if me else None
        except Exception as e:
            self.logger.warning(f"get_me failed: {e}")

        try:
            owner = await db.get_user_by_db_id(self.user_id)
            self._owner_tg = (owner or {}).get("telegram_id")
        except Exception as e:
            self.logger.warning(f"owner lookup failed: {e}")

        await self._load_config()

        # ── پیام‌های ورودی: پاسخ هوشمند ──
        self._add_handler(self._on_incoming, events.NewMessage)

        # ── پیام‌های خودم: ثبت در حافظه + تشخیص همزمانی ──
        self._add_handler(self._on_outgoing, events.NewMessage(outgoing=True))

        # ── کامندها ──
        self._add_handler(
            self._on_command,
            events.NewMessage(
                pattern=r"^\.ai(?:\s+[\s\S]+)?$",
                outgoing=True,
            ),
        )
        self._add_handler(
            self._on_command,
            events.NewMessage(
                pattern=r"^\.تنظیم\s+(?:مود|سطح|ایموجی)(?:\s+[\s\S]+)?$",
                outgoing=True,
            ),
        )

        # ── سبک‌سازی دوره‌ای حافظه ──
        self._compact_task = asyncio.create_task(self._compact_loop())

        self.logger.info("loaded")

    async def stop(self):
        task = getattr(self, "_compact_task", None)
        if task:
            task.cancel()
        self._paused.clear()
        self._busy.clear()
        await super().stop()

    # ═══════════ تنظیمات ═══════════

    async def _my_name(self) -> str:
        """نام واقعی خودم از اکانت تلگرام (نه «من»)"""
        if self._my_name_cache:
            return self._my_name_cache
        try:
            me = await self.client.get_me()
            name = (getattr(me, "first_name", "") or getattr(me, "username", "") or "").strip()
        except Exception as e:
            self.logger.warning(f"get_me failed: {type(e).__name__}")
            name = ""
        self._my_name_cache = name or "من"
        return self._my_name_cache

    def _name_override(self, chat_id) -> str:
        """نام دستی که خودم گذاشته‌ام (اگر باشد، اسم خودکار جای آن نمی‌نشیند)"""
        overrides = self._cfg.get("name_overrides") or {}
        if isinstance(overrides, str):
            try:
                import json
                overrides = json.loads(overrides)
            except Exception:
                overrides = {}
        return str(overrides.get(str(chat_id)) or "").strip()

    async def _refresh_contact_name(self, event, profile: dict):
        """
        نام طرف را تازه می‌کند. قبلاً فقط یک‌بار موقع ساخت پروفایل خوانده
        می‌شد و اگر آن موقع در دسترس نبود، تا همیشه id عددی یا اسم غلط
        می‌مانْد.
        """
        try:
            ent = await event.get_sender()
        except Exception:
            return profile
        def _extract(obj) -> str:
            if obj is None:
                return ""
            first = (getattr(obj, "first_name", "") or "").strip()
            last = (getattr(obj, "last_name", "") or "").strip()
            full = f"{first} {last}".strip()
            return (full or (getattr(obj, "title", "") or "").strip()
                    or (getattr(obj, "username", "") or "").strip())

        name = _extract(ent)
        if not name:
            try:
                name = _extract(await event.get_chat())
            except Exception:
                pass
        if not name:
            # آخرین تلاش: موجودیت را مستقیم از خود تلگرام بگیر
            try:
                name = _extract(await self.client.get_entity(event.chat_id))
            except Exception:
                pass
        if not name or self._name_override(event.chat_id):
            return profile
        old = (profile or {}).get("target_name") or ""
        # نام عددی یعنی «اسم واقعی نداریم» — باید جایگزین شود
        if str(old).lstrip("-").isdigit():
            old = ""
        if name != old:
            await db.upsert_ai_profile(self.user_id, event.chat_id, target_name=name)
            profile = dict(profile or {})
            profile["target_name"] = name
            self.logger.info(f"contact name set: {old!r} → {name!r}")
        return profile

    async def _load_config(self):
        cfg = dict(DEFAULT_CONFIG)
        try:
            rows = await db.get_features(self.user_id)
            for f in rows:
                if f["feature_name"] == self.name:
                    stored = f.get("config_json") or {}
                    if isinstance(stored, str):
                        import json
                        stored = json.loads(stored)
                    cfg.update({k: v for k, v in stored.items() if v is not None})
        except Exception as e:
            self.logger.warning(f"load config failed: {e}")
        self._cfg = cfg

    async def _save_config(self, **changes) -> None:
        self._cfg.update(changes)
        try:
            await db.set_feature(self.user_id, self.name, True, self._cfg)
        except Exception as e:
            self.logger.error(f"save config failed: {e}")

    def _mode(self, profile: dict | None = None) -> str:
        # مود مخصوص چت اگر تنظیم شده باشد، وگرنه مود کلی
        per_chat = (profile or {}).get("red_lines")  # جای مود چت در ستون notes نگه داشته می‌شود
        return self._cfg.get("mode", "عادی")

    # ═══════════ ورودی ─══════════

    async def _on_incoming(self, event):
        try:
            # تنظیمات را تازه بخوان: ممکن است از پنل پیوی ربات (دکمه‌ی حالت
            # ارسال / ایموجی / پرسونا) عوض شده باشد و نسخه‌ی حافظه قدیمی باشد.
            await self._load_config()
            await self._handle_incoming(event)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            self.logger.error(f"incoming handler error: {type(e).__name__}: {e}")

    async def _handle_incoming(self, event):
        if event.out or not event.message or not event.raw_text:
            return
        if not event.sender_id or event.sender_id == self._my_id:
            return

        chat_id = event.chat_id
        sender_id = event.sender_id
        text = event.raw_text.strip()

        # کلمه‌ی توقف: تا خودم روشن کنم ساکت می‌شود
        if text == self._cfg.get("stop_word", "#سکوت"):
            self._paused.add(chat_id)
            self.logger.info(f"paused by stop word in {chat_id}")
            return

        if chat_id in self._paused or chat_id in self._busy:
            return

        profile = await db.get_ai_profile(self.user_id, chat_id)
        if not self._chat_enabled(event, profile):
            return

        # در گروه فقط وقتی مستقیم به من اشاره شده
        if not event.is_private:
            me_mention = bool(getattr(event.message, "mentioned", False))
            reply_to_me = False
            if event.is_reply:
                try:
                    rep = await event.get_reply_message()
                    reply_to_me = bool(rep and rep.out)
                except Exception:
                    reply_to_me = False
            if not (me_mention or reply_to_me):
                return

        # اگر همین حالا خودم در این چت فعال بودم، AI وارد نشود
        if time.time() - self._last_activity.get(chat_id, 0) < 90:
            return

        # همزیستی با «دشمن»: اگر آن پلاگین روی این شخص فعال است، AI ساکت می‌ماند
        try:
            from core.plugin_manager import get_active_plugins
            ar = get_active_plugins(self.user_id).get("auto_response")
            if ar is not None and sender_id in getattr(ar, "_enemies", {}):
                return
        except Exception:
            pass

        if self._in_quiet_hours():
            return
        if not self._quota_ok():
            self.logger.info("quota reached — skipping AI reply")
            return

        # اولین بار: پروفایل خودکار با حدس جایگاه
        if profile is None:
            profile = await self._create_default_profile(event)

        # ثبت پیام طرف در حافظه
        await db.add_ai_message(self.user_id, chat_id, text, is_out=False,
                                msg_id=event.message.id)

        # نوع پاسخ: پیش‌نویس یا ارسال خودکار
        auto = bool(profile.get("auto_mode")) and not self._cfg.get("draft_only", True)
        self._busy.add(chat_id)
        try:
            profile = await self._refresh_contact_name(event, profile)
            reply = await self._generate(event, profile)
            if not reply:
                return
            if auto:
                reply_to = event.message.id if self._cfg.get("reply_to_incoming", True) else None
                await self._send_to_chat(event.chat_id, reply, reply_to=reply_to)
            else:
                await self._send_draft(event, profile, reply)
        finally:
            self._busy.discard(chat_id)
            if self._raw_count_needed(profile):
                asyncio.create_task(self._maybe_compact(chat_id))

    async def _create_default_profile(self, event) -> dict:
        """پروفایل پیش‌فرض: پیوی = آشنا، گروه = غریبه (قابل تغییر با .ai شخص)"""
        rel = "familiar" if event.is_private else "stranger"
        try:
            name = ""
            ent = await event.get_sender()
            name = (getattr(ent, "first_name", "") or getattr(ent, "title", "") or "").strip()
        except Exception:
            name = ""
        await db.upsert_ai_profile(
            self.user_id, event.chat_id,
            target_name=name or str(event.chat_id), relationship=rel,
        )
        return (await db.get_ai_profile(self.user_id, event.chat_id)) or {}

    async def _on_outgoing(self, event):
        """پیام‌های خودم: به حافظه می‌رود + AI از چت عقب می‌کشد"""
        try:
            text = (event.raw_text or "").strip()
            if not text:
                return
            # دستورها (.) نه «فعالیت من در چت» هستند و نه به حافظه می‌روند
            if text.startswith("."):
                return
            chat_id = event.chat_id
            self._last_activity[chat_id] = time.time()
            profile = await db.get_ai_profile(self.user_id, chat_id)
            if profile and profile.get("enabled"):
                await db.add_ai_message(self.user_id, chat_id, text,
                                        is_out=True, msg_id=event.message.id)
        except Exception as e:
            self.logger.warning(f"outgoing handler error: {e}")

    def _chat_enabled(self, event, profile: dict | None) -> bool:
        if profile is not None:
            return bool(profile.get("enabled", True))
        # چت بدون پروفایل: فقط اگر همه‌ی پیوی‌ها روشن باشد
        return bool(event.is_private and self._cfg.get("all_private"))

    def _in_quiet_hours(self) -> bool:
        spec = str(self._cfg.get("quiet_hours") or "").strip()
        if not spec:
            return False
        try:
            start_s, end_s = spec.split("-")
            start, end = int(start_s), int(end_s)
        except Exception:
            return False
        hour = datetime.now().hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end     # بازه‌ی شب‌رو

    def _quota_ok(self) -> bool:
        now = time.time()
        self._usage = [t for t in self._usage if now - t < 3600]
        self._usage_day = [t for t in self._usage_day if now - t < 86400]
        if len([t for t in self._usage if now - t < 60]) >= int(self._cfg.get("rpm", 6)):
            return False
        if len(self._usage) >= int(self._cfg.get("rph", 60)):
            return False
        if len(self._usage_day) >= int(self._cfg.get("rpd", 300)):
            return False
        return True

    def _note_usage(self) -> None:
        now = time.time()
        self._usage.append(now)
        self._usage_day.append(now)

    # ═══════════ تولید پاسخ ═══════════

    async def _generate(self, event, profile: dict) -> str | None:
        chat_id = event.chat_id
        settings = E.effective_settings(
            profile, self._cfg.get("mode", "عادی"),
            int(self._cfg.get("tone_level", 2)),
            global_emoji=self._cfg.get("emoji_level"),
            allowed_emojis=self._cfg.get("allowed_emojis"),
        )
        settings["target_name"] = (self._name_override(chat_id)
                                   or profile.get("target_name") or str(chat_id))
        history = await db.get_ai_messages(self.user_id, chat_id, limit=15)
        notes = await db.get_ai_notes(self.user_id, chat_id, limit=10)
        facts = await db.get_ai_facts(self.user_id, chat_id, limit=int(self._cfg.get("fact_limit", 40)))
        pending = await db.get_ai_facts(self.user_id, chat_id, limit=5, statuses=("pending",))

        text = event.raw_text or ""
        no_memory = False
        if E.needs_no_memory_hint(text):
            # «آیا از قبل چیزی از این موضوع می‌دانم؟»
            # فقط دانسته‌های واقعی (خلاصه/فکت) و پیام‌های قدیمی‌تر — نه همین
            # پیامی که الان ثبت شد، وگرنه همیشه فکر می‌کرد می‌داند.
            hits = await db.search_ai_memory(
                self.user_id, chat_id, text, limit=3,
                exclude_id=event.message.id, kinds=("note", "fact"),
            )
            no_memory = not hits

        mode = E.norm_mode(self._cfg.get("mode", "عادی")) or "عادی"
        now_line = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        my_name = await self._my_name()
        messages = E.build_messages(
            my_name=my_name, persona=self._cfg.get("persona", ""), mode=mode,
            settings=settings, notes=notes, facts=facts, pending=pending,
            history=history, now_line=now_line, no_memory=no_memory,
            incoming_text=text,
        )

        temperature = E.MODES.get(mode, {}).get("temperature", 0.8)
        if len((text or "").strip()) <= 20:
            # پیام کوتاه/مبهم → خلاقیت کمتر، جواب مرتبط‌تر
            temperature = max(0.35, min(temperature, 0.5))
        reply, provider = await ai_providers.chat(
            messages, config=self._cfg, temperature=temperature,
            max_tokens=int(self._cfg.get("max_reply_tokens", 200) or 200),
        )
        self._note_usage()

        if not reply:
            # نردبان: وقتی همه‌ی سرویس‌ها شکست خوردند، آدم بی‌جواب نمی‌ماند
            reply = random.choice(FALLBACK_REPLIES)
            self.logger.warning("all providers failed — fallback reply used")
            await self._notify_owner(
                "⚠️ <b>هیچ سرویس AI جواب نداد</b> — یک پاسخ جانشین فرستاده شد.\n"
                "providerها و کلیدها را با <code>.ai وضعیت</code> چک کن."
            )

        reply = E.clean_reply(reply)
        # فیلتر ایموجی: سطح کاربر + لیست ایموجی‌های خودش (مدل‌ها همیشه
        # حرف‌گوش‌کن نیستند، پس اجبار می‌کنیم)
        reply = E.strip_emoji(reply, settings.get("emoji_level", 1),
                              settings.get("allowed_emojis"))
        if not reply:
            return None
        self.logger.info(f"AI reply ({provider or 'fallback'}) in {chat_id}: {reply[:60]}")
        return reply

    # ═══════════ ارسال ═══════════

    async def _send_to_chat(self, chat_id, reply: str, reply_to: int | None = None) -> None:
        """ارسال انسانی: تایپینگ + تأخیر + تکه‌تکه (پیام اول ریپلای روی پیام طرف)"""
        parts = E.split_messages(reply)
        speed = float(self._cfg.get("typing_speed", 11.0))
        for i, part in enumerate(parts):
            delay = E.typing_seconds(part, speed,
                                     float(self._cfg.get("min_delay", 1.2)),
                                     float(self._cfg.get("max_delay", 4.5)))
            try:
                async with self.client.action(chat_id, "typing"):
                    await asyncio.sleep(delay)
                # فقط قطعه‌ی اول ریپلای می‌شود؛ بقیه پیام‌های ساده‌اند
                await self.client.send_message(
                    chat_id, part, reply_to=reply_to if i == 0 else None)
                self._last_activity[chat_id] = time.time()
                await db.add_ai_message(self.user_id, chat_id, part, is_out=True)
            except FloodWaitError as e:
                self.logger.warning(f"flood on send, waiting {e.seconds}s")
                await asyncio.sleep(e.seconds + 1)
                return
            except Exception as e:
                self.logger.error(f"send failed: {type(e).__name__}: {e}")
                return
            if i < len(parts) - 1:
                await asyncio.sleep(random.uniform(0.6, 1.6))

    async def _send_draft(self, event, profile: dict, reply: str) -> None:
        """پیش‌فرض: پاسخ به پیوی ربات می‌رود تا من تایید کنم"""
        draft_id = _next_draft_id()
        DRAFTS[draft_id] = {
            "user_id": self.user_id,
            "chat_id": event.chat_id,
            "name": profile.get("target_name") or str(event.chat_id),
            "incoming": (event.raw_text or "")[:300],
            "reply": reply,
            "reply_to": (event.message.id
                         if self._cfg.get("reply_to_incoming", True) else None),
            "created": time.time(),
        }
        if len(DRAFTS) > 50:       # جلوگیری از رشد بی‌نهایت
            for k in sorted(DRAFTS, key=lambda k: DRAFTS[k]["created"])[:10]:
                DRAFTS.pop(k, None)

        text = (
            f"🧠 <b>پیشنهاد پاسخ</b> → {self._esc(profile.get('target_name') or event.chat_id)}\n\n"
            f"🗣 پیام او: {self._esc((event.raw_text or '')[:200])}\n\n"
            f"✅ پاسخ پیشنهادی:\n{self._esc(reply)}"
        )
        await self._notify_owner(text, draft_id=draft_id)

    async def _notify_owner(self, text: str, draft_id: str | None = None) -> None:
        """اعلان به پیوی ربات کنترلی (فالبک: سیو مسیج)"""
        sent = False
        if self._owner_tg:
            kwargs = {}
            if draft_id:
                kwargs = self._draft_keyboard(draft_id)
            sent = await runtime.notify_user(self._owner_tg, text, **kwargs)
        if not sent:
            try:
                await self.client.send_message("me", text, parse_mode="html")
            except Exception as e:
                self.logger.error(f"notify failed: {e}")

    def _draft_keyboard(self, draft_id: str) -> dict:
        """کیبورد دکمه‌ای برای پیش‌نویس (با telegram.InlineKeyboardMarkup)"""
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ ارسال", callback_data=f"ai_send:{draft_id}"),
                InlineKeyboardButton("✏️ ویرایش", callback_data=f"ai_edit:{draft_id}"),
                InlineKeyboardButton("🗑 رد", callback_data=f"ai_drop:{draft_id}"),
            ]])
            return {"reply_markup": kb}
        except Exception:
            return {}

    @staticmethod
    def _esc(text) -> str:
        import html
        return html.escape(str(text or ""))

    # ═══════════ کامندها ═══════════

    async def _on_command(self, event):
        try:
            await self._load_config()      # هم‌گام‌سازی با تغییرات پنل پیوی ربات
            await self._handle_command(event)
        except Exception as e:
            self.logger.error(f"command error: {type(e).__name__}: {e}")

    async def _handle_command(self, event):
        text = (event.raw_text or "").strip()
        chat_id = event.chat_id
        profile = await db.get_ai_profile(self.user_id, chat_id)
        arg = ""

        m = re.match(r"^\.تنظیم\s+مود(?:\s+([\s\S]+))?$", text)
        if m:
            await self._cmd_set_mode(event, (m.group(1) or "").strip(), profile, chat_id)
            return
        m = re.match(r"^\.تنظیم\s+سطح(?:\s+(\d+))?$", text)
        if m:
            await self._cmd_set_tone(event, (m.group(1) or "").strip(), profile, chat_id)
            return
        m = re.match(r"^\.تنظیم\s+ایموجی(?:\s+([\s\S]+))?$", text)
        if m:
            await self._cmd_set_emoji(event, (m.group(1) or "").strip(), profile, chat_id)
            return
        m = re.match(r"^\.ai(?:\s+([\s\S]+))?$", text)
        if not m:
            return

        parts = (m.group(1) or "").strip().split(maxsplit=1)
        sub = parts[0] if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("روشن", "on"):
            await self._cmd_enable(event, profile, chat_id)
        elif sub in ("خاموش", "off"):
            await self._cmd_disable(event, chat_id)
        elif sub in ("بس", "ساکت"):
            self._paused.add(chat_id)
            await self._reply_notify(f"🤐 در این چت ساکت شدم. برای فعال‌کردن: <code>.ai روشن</code>")
        elif sub in ("وضعیت", "status", ""):
            await self._cmd_status(event, chat_id)
        elif sub in ("شخص", "جایگاه", "رابطه"):
            await self._cmd_relationship(event, arg, profile, chat_id)
        elif sub in ("خودکار", "auto"):
            await db.set_ai_auto_mode(self.user_id, chat_id, True)
            note = ""
            if self._cfg.get("draft_only", True):
                # کلید کلی «فقط پیش‌نویس» هم باید باز شود، وگرنه این دستور
                # هیچ اثری ندارد و کاربر فکر می‌کند خودکار کار نمی‌کند.
                await self._save_config(draft_only=False)
                note = "\n(حالت کلی هم شد «خودکار» — از پنل پیوی ربات هم قابل تغییر است)"
            await self._reply_notify(
                "⚡ از این به بعد در این چت <b>خودکار</b> جواب می‌دم (بدون تایید)." + note)
        elif sub in ("دستی", "manual"):
            await db.set_ai_auto_mode(self.user_id, chat_id, False)
            await self._reply_notify("👤 برگشت به حالت <b>پیشنهاد</b>: اول به خودت نشون می‌دم.")
        elif sub in ("مود", "mode"):
            await self._cmd_set_mode(event, arg, profile, chat_id)
        elif sub in ("سطح", "level"):
            await self._cmd_set_tone(event, arg, profile, chat_id)
        elif sub in ("ایموجی", "اموجی", "emoji"):
            await self._cmd_set_emoji(event, arg, profile, chat_id)
        elif sub in ("نام", "اسم", "name"):
            await self._cmd_set_name(event, arg, profile, chat_id)
        elif sub in ("سرچ", "search"):
            await self._cmd_search(event, arg, chat_id)
        elif sub in ("حافظه", "memory"):
            await self._cmd_memory(event, chat_id)
        elif sub in ("سبک‌سازی", "سبک", "compact"):
            await self._cmd_compact(event, chat_id)
        elif sub in ("فراموش", "فراموشی", "forget"):
            await self._cmd_forget(event, arg, chat_id)
        elif sub in ("کلید", "provider", "سرویس"):
            await self._cmd_providers(event)
        else:
            await self._reply_notify("❓ زیرفرمان‌ها: روشن، خاموش، وضعیت، شخص، نام، خودکار، دستی، مود، سطح، ایموجی، سرچ، حافظه، سبک‌سازی، فراموش، کلید، بس")

    async def _cmd_enable(self, event, profile, chat_id):
        self._paused.discard(chat_id)
        if profile is None:
            profile = await self._create_default_profile(event)
        else:
            await db.upsert_ai_profile(self.user_id, chat_id, enabled=True)
        rel = E.RELATIONSHIPS.get(profile.get("relationship", "familiar"), {})
        await self._reply_notify(
            f"✅ پاسخ هوشمند در این چت روشن شد.\n"
            f"👤 جایگاه فعلی: <b>{rel.get('label', 'آشنا')}</b> "
            f"(عوض کردن: <code>.ai شخص رفیق نزدیک</code>)\n"
            f"🎭 مود: <b>{self._cfg.get('mode', 'عادی')}</b> · "
            f"سطح: <b>{E.TONE_LABELS.get(int(self._cfg.get('tone_level', 2)))}</b>\n"
            f"🧪 فعلاً پاسخ‌ها به پیوی ربات می‌آید تا تایید کنی (خودکار: <code>.ai خودکار</code>)"
        )

    async def _cmd_disable(self, event, chat_id):
        await db.upsert_ai_profile(self.user_id, chat_id, enabled=False)
        await self._reply_notify("❌ پاسخ هوشمند در این چت خاموش شد.")

    async def _cmd_status(self, event, chat_id):
        profile = await db.get_ai_profile(self.user_id, chat_id)
        counts = await db.count_ai_memory(self.user_id, chat_id)
        rel = E.RELATIONSHIPS.get((profile or {}).get("relationship", "familiar"), {})
        providers = ai_providers.load_providers(self._cfg)
        await self._reply_notify(
            f"🧠 <b>وضعیت پاسخ هوشمند</b>\n"
            f"چت: <code>{chat_id}</code> — {'روشن ✅' if (profile or {}).get('enabled', False) else 'خاموش ❌'}\n"
            f"جایگاه: <b>{rel.get('label', '—')}</b>\n"
            f"مود: <b>{self._cfg.get('mode', 'عادی')}</b> · "
            f"سطح: <b>{E.TONE_LABELS.get(int(self._cfg.get('tone_level', 2)), '—')}</b>\n"
            f"ارسال: <b>{'خودکار' if (profile or {}).get('auto_mode') else 'پیشنهاد به من'}</b>\n"
            f"حافظه: {counts['msg']} پیام · {counts['note']} خلاصه · {counts['fact']} فکت\n"
            f"سرویس: {', '.join(p.name for p in providers) or 'تنظیم نشده'}"
        )

    async def _cmd_relationship(self, event, arg, profile, chat_id):
        key = E.norm_relationship(arg)
        if not key:
            opts = " · ".join(f"<code>{v['label']}</code>" for v in E.RELATIONSHIPS.values())
            await self._reply_notify(f"جایگاه‌های موجود:\n{opts}\n\nمثال: <code>.ai شخص رفیق نزدیک</code>")
            return
        await db.upsert_ai_profile(self.user_id, chat_id, relationship=key)
        await self._reply_notify(
            f"✅ جایگاه این شخص شد: <b>{E.RELATIONSHIPS[key]['label']}</b>\n"
            f"لحن پیش‌فرض: {E.RELATIONSHIPS[key]['style']}"
        )

    async def _cmd_set_mode(self, event, arg, profile, chat_id):
        mode = E.norm_mode(arg)
        if not mode:
            modes = " · ".join(f"<code>{m}</code>" for m in E.MODES)
            await self._reply_notify(f"🎭 مودها:\n{modes}\n\nمثال: <code>.تنظیم مود playfull</code>")
            return
        await self._save_config(mode=mode)
        await self._reply_notify(f"🎭 مود شد <b>{mode}</b> — {E.MODES[mode]['desc']}")

    async def _cmd_set_tone(self, event, arg, profile, chat_id):
        if not arg.isdigit() or not (0 <= int(arg) <= 3):
            lines = "\n".join(f"<code>{k}</code> — {v}" for k, v in E.TONE_LABELS.items())
            await self._reply_notify(f"🌡 سطح آزادی بیان:\n{lines}\n\nمثال: <code>.تنظیم سطح 2</code>")
            return
        level = int(arg)
        if profile:
            await db.upsert_ai_profile(self.user_id, chat_id, tone_level=level)
            where = "همین چت"
        else:
            await self._save_config(tone_level=level)
            where = "همه‌ی چت‌ها"
        await self._reply_notify(f"🌡 سطح {level} ({where}): {E.TONE_LABELS[level]}")

    async def _cmd_set_name(self, event, arg, profile, chat_id):
        """
        نام این مخاطب را دستی تعیین می‌کند:
          .ai نام علی        → از این به بعد او را «علی» می‌شناسم
          .ai نام خودکار     → برگشت به نام واقعی تلگرام
        """
        name = (arg or "").strip()
        overrides = dict(self._cfg.get("name_overrides") or {})
        if not name:
            cur = overrides.get(str(chat_id)) or (profile or {}).get("target_name") or "—"
            await self._reply_notify(
                f"📛 نام فعلی این مخاطب: <b>{self._esc(cur)}</b>\n\n"
                f"تغییر: <code>.ai نام علی</code>\n"
                f"برگشت به نام تلگرام: <code>.ai نام خودکار</code>")
            return
        if name in ("خودکار", "auto", "تلگرام", "-"):
            overrides.pop(str(chat_id), None)
            await self._save_config(name_overrides=overrides)
            live = ""
            try:
                # ⚠️ از get_sender استفاده نکن: فرستنده‌ی این پیام خودِ من است
                # (دستور را با اکانت خودم فرستادم)، پس نام *خودم* برمی‌گشت.
                ent = await self.client.get_entity(chat_id)
                first = (getattr(ent, "first_name", "") or "").strip()
                last = (getattr(ent, "last_name", "") or "").strip()
                live = (f"{first} {last}".strip()
                        or (getattr(ent, "title", "") or "").strip()
                        or (getattr(ent, "username", "") or "").strip())
            except Exception as e:
                self.logger.warning(f"get_entity for name failed: {type(e).__name__}")
            if live:
                await db.upsert_ai_profile(self.user_id, chat_id, target_name=live)
            await self._reply_notify(
                f"📛 برگشت به نام تلگرام: <b>{self._esc(live or '—')}</b>"
                + ("" if live else " (اسمی از تلگرام نگرفتم؛ هر پیام جدید خودش دوباره تلاش می‌کند)"))
            return
        if len(name) > 40:
            name = name[:40]
        overrides[str(chat_id)] = name
        await self._save_config(name_overrides=overrides)
        await db.upsert_ai_profile(self.user_id, chat_id, target_name=name)
        await self._reply_notify(f"📛 از این به بعد این مخاطب <b>{self._esc(name)}</b> است.")

    async def _cmd_set_emoji(self, event, arg, profile, chat_id):
        """
        سطح ایموجی را عوض می‌کند؛ اگر arg خودش ایموجی باشد، یعنی «فقط همین‌ها».

          .تنظیم ایموجی ۰ | ۱ | ۲ | ۳   → سطح کلی (همه‌ی چت‌ها)
          .تنظیم ایموجی 😂❤️🙏          → لیست ایموجی‌های مجاز من
          .تنظیم ایموجی هیچ             → لیست را خالی کن (هر ایموجی مجاز می‌شود)
        """
        raw = (arg or "").strip()
        if not raw:
            cur = E.norm_emoji_level(self._cfg.get("emoji_level"), 1)
            allow = self._cfg.get("allowed_emojis") or "—"
            lines = "\n".join(f"<code>{k}</code> — {v}" for k, v in E.EMOJI_LEVELS.items())
            await self._reply_notify(
                f"😊 <b>سطح ایموجی:</b> {cur} — {E.EMOJI_LEVELS[cur]}\n"
                f"😊 <b>ایموجی‌های من:</b> {self._esc(allow)}\n\n{lines}\n\n"
                "مثال: <code>.تنظیم ایموجی 0</code> (بدون ایموجی)\n"
                "برای محدود کردن به ایموجی‌های خودت: <code>.تنظیم ایموجی 😂❤️🙏</code>\n"
                "برای برداشتن محدودیت: <code>.تنظیم ایموجی هیچ</code>")
            return

        emojis = E.parse_emoji_list(raw)
        if emojis:
            if len(emojis) > 20:
                emojis = emojis[:20]
            await self._save_config(allowed_emojis="".join(emojis))
            await self._reply_notify(
                f"😊 از این به بعد فقط از این ایموجی‌ها استفاده می‌کنم: {''.join(emojis)}\n"
                f"هر ایموجی دیگری حذف می‌شود. (سطح فعلی: "
                f"{E.norm_emoji_level(self._cfg.get('emoji_level'), 1)})")
            return

        if raw in ("هیچ", "خالی", "پاک", "بدون", "none", "clear", "off", "-"):
            await self._save_config(allowed_emojis="")
            await self._reply_notify("😊 محدودیت ایموجی برداشته شد (فقط سطح کلی اعمال می‌شود).")
            return

        if raw.isdigit() or raw in ("کم", "متوسط", "زیاد") or E.norm_emoji_level(raw, -1) >= 0:
            level = E.norm_emoji_level(raw, -1)
            if level < 0:
                await self._reply_notify("😊 سطح باید بین ۰ تا ۳ باشد.")
                return
            await self._save_config(emoji_level=level)
            await self._reply_notify(f"😊 سطح ایموجی شد <b>{level}</b> — {E.EMOJI_LEVELS[level]}")
            return

        await self._reply_notify(
            "❓ مثال: <code>.تنظیم ایموجی 0</code> یا <code>.تنظیم ایموجی 😂❤️</code>")

    async def _cmd_search(self, event, arg, chat_id):
        if not arg:
            await self._reply_notify("🔍 مثال: <code>.ai سرچ کوه</code>")
            return
        rows = await db.search_ai_memory(self.user_id, chat_id, arg, limit=10)
        if not rows:
            await self._reply_notify(f"🔍 چیزی درباره‌ی «{self._esc(arg)}» در حافظه نیست.")
            return
        kinds = {"msg": "💬", "note": "📝", "fact": "📌"}
        out = [f"🔍 <b>{self._esc(arg)}</b> — {len(rows)} نتیجه:\n"]
        for r in rows:
            who = "من" if r.get("is_out") else "او"
            out.append(f"{kinds.get(r['kind'], '•')} {who}: {self._esc(r['content'][:160])}")
        await self._reply_notify("\n".join(out))

    async def _cmd_memory(self, event, chat_id):
        counts = await db.count_ai_memory(self.user_id, chat_id)
        limit = int(self._cfg.get("raw_limit", 120))
        await self._reply_notify(
            f"🧠 <b>حافظه‌ی این چت</b>\n"
            f"💬 پیام خام: {counts['msg']} (سقف {limit})\n"
            f"📝 خلاصه: {counts['note']}\n"
            f"📌 فکت: {counts['fact']}\n\n"
            f"سبک‌سازی خودکار وقتی پیام خام از سقف بگذرد انجام می‌شود؛ "
            f"دستی: <code>.ai سبک‌سازی</code>\n"
            f"مدیریت فکت‌ها در پیوی ربات → 🧠 هوش مصنوعی"
        )

    async def _cmd_compact(self, event, chat_id):
        await self._reply_notify("🧠 دارم حافظه را سبک می‌کنم…")
        n = await self._maybe_compact(chat_id, force=True)
        await self._reply_notify(f"✅ سبک‌سازی انجام شد ({n} پیام فشرده شد).")

    async def _cmd_forget(self, event, arg, chat_id):
        target = chat_id
        n = await db.delete_ai_memory(self.user_id, target)
        await self._reply_notify(f"🗑 حافظه‌ی این چت پاک شد ({n} رکورد).")

    async def _cmd_providers(self, event):
        providers = ai_providers.load_providers(self._cfg)
        lines = ai_providers.status_lines(providers)
        await self._reply_notify("🔑 <b>سرویس‌های AI</b>\n" + "\n".join(lines) +
                                 "\n\nبرای عوض کردن کلید: <code>AI_PROVIDERS</code> در .env")

    async def _reply_notify(self, text: str):
        """خروجی دستورها: در پیوی ربات (چت دستور شلوغ نمی‌شود)"""
        await self._notify_owner(text)

    # ═══════════ حافظه: سبک‌سازی ═══════════

    def _raw_count_needed(self, profile: dict) -> bool:
        return True

    async def _compact_loop(self):
        """هر ۱۰ دقیقه چت‌های پرحجم را سبک‌سازی می‌کند"""
        while True:
            try:
                await asyncio.sleep(600)
                chats = await db.list_ai_chats(self.user_id)
                for c in chats:
                    if c["msgs"] > int(self._cfg.get("raw_limit", 120)):
                        await self._maybe_compact(c["target_id"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.warning(f"compact loop error: {e}")

    async def _maybe_compact(self, chat_id: int, force: bool = False) -> int:
        """
        سبک‌سازی: قدیمی‌ترین پیام‌های خام → خلاصه + فکت (با AI) → حذف پیام خام.
        هیچ‌چیز مهمی گم نمی‌شود، فقط فشرده می‌شود.
        """
        keep = int(self._cfg.get("keep_after_compact", 30))
        limit = int(self._cfg.get("raw_limit", 120))
        counts = await db.count_ai_memory(self.user_id, chat_id)
        if not force and counts["msg"] <= limit:
            return 0

        old, cutoff = await db.get_ai_msgs_older_than(self.user_id, chat_id, keep)
        if not old or not cutoff:
            return 0

        profile = await db.get_ai_profile(self.user_id, chat_id) or {}
        name = profile.get("target_name") or str(chat_id)
        lines = [f"{'من' if r['is_out'] else 'او'}: {r['content']}" for r in old]

        summary, facts = "", []
        try:
            raw, _ = await ai_providers.chat(
                E.summarizer_messages(name, lines), config=self._cfg,
                max_tokens=500, temperature=0.3,
            )
            if raw:
                summary, facts = E.parse_summary(raw)
        except Exception as e:
            self.logger.warning(f"summarize failed: {e}")

        if not summary:
            # اگر AI نبود، خلاصه‌ی متنی ساده نگه می‌داریم تا چیزی گم نشود
            summary = "گفت‌وگوی قبلی: " + " | ".join(
                l[:60] for l in lines[-20:]
            )[:800]

        await db.add_ai_note(self.user_id, chat_id, summary,
                             source=f"سبک‌سازی {len(old)} پیام")
        for f in facts:
            status = "pending" if f.startswith("ادعا") else "approved"
            await db.add_ai_fact(self.user_id, chat_id, f, source="خلاصه‌سازی", status=status)

        removed = await db.delete_ai_msgs_upto(self.user_id, chat_id, cutoff)
        self.logger.info(f"compacted {chat_id}: {removed} msgs → 1 note + {len(facts)} facts")

        # محدود کردن تعداد خلاصه‌ها: قدیمی‌ترها در یک خلاصه‌ی «تاریخی» ادغام می‌شوند
        notes = await db.get_ai_notes(self.user_id, chat_id, limit=40)
        if len(notes) > 25:
            merge = notes[12:]
            merged_text = "خلاصه‌ی قدیمی‌تر: " + " ".join(n["content"][:200] for n in merge)[:1500]
            await db.add_ai_note(self.user_id, chat_id, merged_text, source="ادغام خلاصه‌ها")
            for n in merge:
                await db.delete_ai_memory(self.user_id, chat_id, mem_id=n["id"])
        return removed
