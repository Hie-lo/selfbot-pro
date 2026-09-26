"""
پلاگین پاسخ خودکار (دشمن) + اسپم
کامندها:
  .دشمن                  — ریپلای: اضافه کردن
  .دشمن @username        — اضافه با یوزرنیم
  .دشمن حذف              — ریپلای: حذف
  .دشمن حذف @username    — حذف با یوزرنیم
  .لیست دشمن             — نمایش لیست
  .بکنش                  — ریپلای: شروع اسپم
  .بس                    — توقف اسپم
"""

import html
import json
import random
import re
import time
import asyncio
from telethon import events
from plugins.base import BasePlugin
from database import db

# «A wait of 123 seconds is required» / «FLOOD_WAIT_123» → 123
_WAIT_RE = re.compile(r"FLOOD_WAIT_(\d+)|wait of (\d+)\s*second", re.I)
MAX_FLOOD_SLEEP = 900          # بیش از این، اسپم در همین چت خودکار می‌ایستد


# ─────────────── پیام‌ها ───────────────
# فقط فحش‌های سنگین اینجا می‌مانند؛ جمله‌های ساده‌لوحانه (برو بابا، خفه شو،
# خر، احمق، آشغال، بی‌کاری؟، چرت نگو ...) حذف شده‌اند.
#
# ➕ هر خط سنگین‌تری خواستی اضافه کنی، فقط به همین لیست اضافه کن؛ نه ایمپورت
#    جایی عوض می‌شود نه چیز دیگری. انتخاب پیام‌ها با «کیسه‌ی شافل» است، یعنی
#    هر جمله قبل از تکرار دوباره‌ی خودش، همه‌ی جمله‌های دیگر یک‌بار می‌آیند —
#    با لیست بزرگ‌تر، تنوع به‌شکل خودکار بیشتر می‌شود.
HEAVY_MESSAGES = [
    "کیرم تو کص ننت",
    "مادرجنده",
    "ننت جنده",
    "بچه کونی",
    "کصخل",
    "کونی",
    "کص ننت",
    "بیا اینو بخور 🍆",
    "بی‌ناموس",
    "بی‌شرف",
    "حرومزاده",
    "خایه‌مال",
    "سیکتیر",
]

# چاشنی‌های تصادفی — از همین جمله‌ها ترکیب‌های تازه می‌سازند تا تکراری کم شود
FLAVORS = [
    " 🤡", " 🖕", " 🖕🖕🖕", " 🤮", " 🍆", " 🤡🤡🤡",
]

# جمله‌ی اول، پاسخ خودکار به دشمن هم همین‌ها است (قبلاً یه لیست جدا و
# بی‌رنگ داشت: 🤡 😂 خفه برو بابا ...)
DEFAULT_RESPONSES = HEAVY_MESSAGES


class ShuffledBag:
    """
    قرعه‌کشی بدون تکرار: تا وقتی همه‌ی گزینه‌ها مصرف نشده‌اند هیچ‌کدام دوباره
    برنمی‌گردد و بین دو دور هم جمله‌ی آخر تکرار نمی‌شود.
    """

    def __init__(self, items):
        self._items = list(items)
        self._bag: list = []
        self._last_base: str | None = None   # جمله‌ی پایه‌ی پیام قبلی (بدون چاشنی)

    def next(self) -> str:
        if not self._bag:
            self._bag = list(self._items)
            random.shuffle(self._bag)
            # اولین جمله‌ی دور جدید نباید همان جمله‌ی پایه‌ی دور قبل باشد
            if len(self._bag) > 1 and self._bag[-1] == self._last_base:
                self._bag[0], self._bag[-1] = self._bag[-1], self._bag[0]

        item = self._bag.pop()
        base = item
        # گاهی دو جمله‌ی سنگین با هم ترکیب می‌شوند → پیام‌های متنوع‌تر
        if self._bag and random.random() < 0.35:
            extra = self._bag.pop()
            if len(item) + len(extra) + 1 <= 90:
                item = f"{item} {extra}"
            else:
                self._bag.append(extra)
        if random.random() < 0.5:
            item += random.choice(FLAVORS)
        self._last_base = base
        return item


class AutoResponsePlugin(BasePlugin):
    name = "auto_response"
    description = "پاسخ خودکار (دشمن) + اسپم"
    always_on = False

    def __init__(self, client, user_id: int):
        super().__init__(client, user_id)
        self._enemies: dict[int, dict] = {}
        self._cooldowns: dict[int, float] = {}
        self._spam_tasks: dict[int, asyncio.Task] = {}
        self._spam_targets: dict[int, dict] = {}   # chat_id → {id, name} برای اعلان توقف
        # کیسه‌ی پیام هر چت: تا همه‌ی جمله‌ها مصرف نشوند تکراری نمی‌آید
        self._spam_bags: dict[int, ShuffledBag] = {}
        self._reply_bags: dict[int, ShuffledBag] = {}   # هر دشمن کیسه‌ی خودش را دارد
        self._my_id = None
        self._owner_tg: int | None = None          # آیدی تلگرامی صاحب اکانت (پیوی ربات)

    async def start(self):
        try:
            me = await self.client.get_me()
            self._my_id = me.id if me else None
        except Exception as e:
            # خطای موقت شبکه نباید باعث شود کل پلاگین (و دستورهای دشمن) بالا نیاید
            self.logger.warning(f"get_me failed: {e}")
            self._my_id = None

        # آیدی تلگرامی صاحب اکانت — اعلان‌ها به پیوی ربات کنترلی می‌روند
        try:
            owner = await db.get_user_by_db_id(self.user_id)
            self._owner_tg = (owner or {}).get("telegram_id")
        except Exception as e:
            self.logger.warning(f"owner lookup failed: {e}")

        await self._load_rules()

        # ── کامندهای دشمن ──

        async def cmd_handler(event):
            if not event.out:
                return

            text = event.message.text.strip()

            if text == ".لیست دشمن":
                await self._list_enemies(event)
                return

            if text.startswith(".دشمن حذف"):
                await self._remove_enemy(event)
                return

            if text.startswith(".دشمن"):
                await self._add_enemy(event)
                return

        self._add_handler(
            cmd_handler,
            events.NewMessage(pattern=r"^\.(دشمن|لیست دشمن)", outgoing=True),
        )

        # ── اسپم ──

        async def spam_cmd(event):
            if not event.out:
                return
            if not event.is_reply:
                await event.delete()
                await self.client.send_message(
                    event.chat_id, "❌ روی پیام طرف ریپلای کنید."
                )
                return

            reply = await event.get_reply_message()
            if not reply or not reply.sender_id:
                await event.delete()
                return

            target_id = reply.sender_id
            if target_id == self._my_id:
                await event.delete()
                return

            chat_id = event.chat_id
            await event.delete()

            target_name = self._name_of(target_id)

            # اگه قبلاً اسپم فعاله، اول متوقف کن
            task_key = chat_id
            if task_key in self._spam_tasks:
                self._spam_tasks[task_key].cancel()

            # شروع اسپم
            self._spam_targets[chat_id] = {"id": target_id, "name": target_name}
            self._spam_tasks[task_key] = asyncio.create_task(
                self._spam_loop(chat_id, reply.id, target_id)
            )

            chat_line = ""
            if not event.is_private:
                chat_line = f"\n📍 چت: <b>{await self._chat_label(chat_id)}</b>"
            await self._notify(
                f"🔥 <b>{target_name}</b> رفت زیر کیر — اسپم شروع شد."
                f"{chat_line}\n⏹ توقف: <code>.بس</code>"
            )

            self.logger.info(f"Spam started on {target_id} in {chat_id}")

        self._add_handler(
            spam_cmd,
            events.NewMessage(pattern=r"^\.بکنش$", outgoing=True),
        )

        async def stop_spam(event):
            if not event.out:
                return

            chat_id = event.chat_id
            await event.delete()

            if chat_id in self._spam_tasks:
                self._spam_tasks[chat_id].cancel()
                del self._spam_tasks[chat_id]
                info = self._spam_targets.get(chat_id) or {}
                name = info.get("name") or str(info.get("id") or "طرف")
                await self._notify(f"⏹ اسپم روی <b>{name}</b> متوقف شد.")
                self.logger.info(f"Spam stopped in {chat_id}")
            else:
                await self._notify("ℹ️ اسپمی فعال نبود که متوقف شود.")

        self._add_handler(
            stop_spam,
            events.NewMessage(pattern=r"^\.بس$", outgoing=True),
        )

        # ── پاسخ خودکار به دشمنان ──

        async def auto_reply(event):
            if event.out:
                return

            sender_id = event.sender_id
            if sender_id not in self._enemies:
                return

            now = time.time()
            last = self._cooldowns.get(sender_id, 0)
            if now - last < 3:
                return
            self._cooldowns[sender_id] = now

            # کیسه‌ی مخصوص همین دشمن → جواب پشت‌سرهم تکراری نمی‌شود
            bag = self._reply_bags.get(sender_id)
            if bag is None:
                responses = self._enemies[sender_id].get("responses") or HEAVY_MESSAGES
                bag = self._reply_bags[sender_id] = ShuffledBag(responses)
            response = bag.next()

            try:
                await event.reply(response)
                self.logger.info(f"Auto-replied to {sender_id}")
            except Exception as e:
                self.logger.error(f"Auto-reply error: {e}")

        self._add_handler(auto_reply, events.NewMessage)

        self.logger.info("loaded")

    # ── اعلان‌ها ──

    async def _notify(self, text: str) -> None:
        """
        اعلان‌های دشمن/اسپم در همان چت فرستاده نمی‌شوند تا چت تمیز بماند.
        اول پیوی ربات کنترلی، اگر نشد (کاربر ربات را استارت نکرده) سیو مسیج.
        """
        from core import runtime
        if self._owner_tg and await runtime.notify_user(self._owner_tg, text):
            return
        try:
            await self.client.send_message("me", text, parse_mode="html")
        except Exception as e:
            self.logger.error(f"notify failed: {e}")

    def _name_of(self, target_id: int) -> str:
        """نام نمایشی طرف — از لیست دشمن، وگرنه آیدی"""
        return html.escape(str((self._enemies.get(target_id) or {}).get("name") or target_id))

    async def _chat_label(self, chat_id) -> str:
        try:
            chat = await self.client.get_entity(chat_id)
            title = (getattr(chat, "title", None)
                     or getattr(chat, "first_name", None) or chat_id)
            return html.escape(str(title))
        except Exception:
            return str(chat_id)

    # ── اسپم loop ──

    async def _spam_loop(self, chat_id, reply_msg_id, target_id):
        """ارسال پیام‌های اسپم با ریپلای"""
        bag = self._spam_bags.setdefault(chat_id, ShuffledBag(HEAVY_MESSAGES))
        try:
            count = 0
            while True:
                msg_text = bag.next()

                try:
                    await self.client.send_message(
                        chat_id,
                        msg_text,
                        reply_to=reply_msg_id,
                    )
                    count += 1
                except Exception as e:
                    err = str(e).lower()
                    if "flood" in err:
                        # FloodWait — «A wait of 123 seconds...» یا FLOOD_WAIT_123
                        # (قبلاً همه‌ی رقم‌های پیام خوانده می‌شد و عددی مثل
                        #  4203600 ساخته می‌شد → اسپم تا روزها خواب می‌رفت)
                        m = _WAIT_RE.search(str(e))
                        wait = int(next(g for g in m.groups() if g)) if m else 30
                        if wait > MAX_FLOOD_SLEEP:
                            self.logger.warning(
                                f"Spam stopped in {chat_id}: flood wait {wait}s too long")
                            name = (self._spam_targets.get(chat_id) or {}).get("name") \
                                or str(target_id)
                            await self._notify(
                                f"⏹ اسپم روی <b>{name}</b> خودکار متوقف شد — "
                                f"تلگرام {wait} ثانیه محدودیت داده.\n"
                                f"بعداً دوباره <code>.بکنش</code> بزن."
                            )
                            break
                        self.logger.warning(f"Spam flood, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    elif "slow" in err:
                        await asyncio.sleep(10)
                        continue
                    else:
                        self.logger.error(f"Spam error: {e}")
                        break

                # تاخیر تصادفی بین 1 تا 3 ثانیه
                delay = random.uniform(1.0, 3.0)
                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            self.logger.info(f"Spam cancelled in {chat_id}")
        finally:
            self._spam_tasks.pop(chat_id, None)
            self._spam_targets.pop(chat_id, None)
            self._spam_bags.pop(chat_id, None)

    # ── مدیریت دشمن ──

    async def _add_enemy(self, event):
        text = event.message.text.strip()
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        # اگه "حذف" بود، اینجا نباید باشیم
        if arg.startswith("حذف"):
            return

        target_id = None
        target_name = "نامشخص"

        if event.is_reply:
            reply = await event.get_reply_message()
            if reply and reply.sender_id:
                target_id = reply.sender_id
                try:
                    entity = await self.client.get_entity(target_id)
                    target_name = getattr(entity, "first_name", str(target_id))
                except Exception:
                    target_name = str(target_id)

        if not target_id and arg and arg.startswith("@"):
            try:
                entity = await self.client.get_entity(arg)
                target_id = entity.id
                target_name = getattr(entity, "first_name", arg)
            except Exception:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ کاربر پیدا نشد.")
                return

        if not target_id:
            await event.delete()
            await self.client.send_message(
                event.chat_id,
                "❌ روی پیام ریپلای کنید یا @username بدید."
            )
            return

        if target_id == self._my_id:
            await event.delete()
            await self.client.send_message(
                event.chat_id, "❌ این پیام مال خودته — روی پیام طرف مقابل ریپلای کن."
            )
            return

        try:
            await db.save_auto_response_rule(
                self.user_id, target_id, DEFAULT_RESPONSES, target_name,
            )
        except Exception as e:
            # خطای دیتابیس نباید بی‌صدا باشد (قبلاً فقط در لاگ می‌ماند و
            # کاربر فکر می‌کرد دستور کار نمی‌کند)
            self.logger.error(f"Save rule failed: {e}")
            await event.delete()
            await self.client.send_message(
                event.chat_id,
                f"❌ ذخیره نشد: <code>{type(e).__name__}</code>\n"
                f"<i>{str(e)[:200]}</i>",
                parse_mode="html",
            )
            return

        self._enemies[target_id] = {
            "name": target_name,
            "responses": DEFAULT_RESPONSES,
        }

        await event.delete()
        await self._notify(
            f"😈 <b>{html.escape(str(target_name))}</b> به لیست دشمن اضافه شد.\n"
            f"🆔 <code>{target_id}</code>\n"
            f"🔥 برای شروع اسپم: ریپلای روی پیامش → <code>.بکنش</code>"
        )
        self.logger.info(f"Enemy added: {target_name} ({target_id})")

    async def _remove_enemy(self, event):
        text = event.message.text.strip()
        parts = text.split(maxsplit=2)
        arg = parts[2].strip() if len(parts) > 2 else ""

        target_id = None

        if event.is_reply:
            reply = await event.get_reply_message()
            if reply:
                target_id = reply.sender_id

        if not target_id and arg and arg.startswith("@"):
            try:
                entity = await self.client.get_entity(arg)
                target_id = entity.id
            except Exception:
                pass

        if target_id and target_id in self._enemies:
            name = self._enemies.pop(target_id, {}).get("name", str(target_id))
            try:
                await db.delete_auto_response_rule(self.user_id, target_id)
            except Exception as e:
                self.logger.error(f"Delete rule failed: {e}")
            await event.delete()
            await self._notify(
                f"✅ <b>{html.escape(str(name))}</b> از لیست دشمن حذف شد."
            )
            self.logger.info(f"Enemy removed: {name}")
        else:
            await event.delete()
            await self.client.send_message(event.chat_id, "❌ در لیست نیست.")

    async def _list_enemies(self, event):
        await event.delete()

        if not self._enemies:
            await self._notify("📭 لیست دشمن خالیه.")
            return

        text = "😈 <b>لیست دشمنان:</b>\n\n"
        for i, (uid, info) in enumerate(self._enemies.items(), 1):
            name = html.escape(str(info.get("name") or uid))
            text += f"{i}. <b>{name}</b> — <code>{uid}</code>\n"
        text += f"\n👥 تعداد: {len(self._enemies)}"

        await self._notify(text)

    async def _load_rules(self):
        """خواندن لیست دشمنان از دیتابیس — هیچ خطایی نباید پلاگین را از کار بیندازد"""
        try:
            rules = await db.get_auto_response_rules(self.user_id)
        except Exception as e:
            self.logger.error(f"Load rules failed: {e}")
            return

        for r in rules:
            try:
                tid = r.get("target_user_id")
                if tid is None:
                    continue
                # نام ذخیره‌شده (قبلاً ذخیره نمی‌شد و فقط آیدی نمایش داده می‌شد)
                name = (r.get("trigger_value") or "").strip() or str(tid)

                responses = r.get("response_list") or HEAVY_MESSAGES
                if isinstance(responses, str):
                    responses = json.loads(responses)
                if not isinstance(responses, list) or not responses:
                    responses = HEAVY_MESSAGES
                # لیست ذخیره‌شده‌ی قدیمی (همان جمله‌های ساده‌لوحانه) با لیست
                # جدید سنگین عوض می‌شود تا تغییر واقعاً روی دشمن‌های قبلی هم بیاید
                if sorted(map(str, responses)) != sorted(HEAVY_MESSAGES):
                    responses = HEAVY_MESSAGES
                    try:
                        await db.save_auto_response_rule(
                            self.user_id, int(tid), HEAVY_MESSAGES, name
                        )
                    except Exception as e:
                        self.logger.warning(f"Rule refresh failed ({tid}): {e}")

                self._enemies[int(tid)] = {"name": name, "responses": responses}
            except Exception as e:
                self.logger.warning(f"Skipped bad rule {r.get('id')}: {e}")

        if self._enemies:
            self.logger.info(f"loaded {len(self._enemies)} enemies")

    async def stop(self):
        for task in self._spam_tasks.values():
            task.cancel()
        self._spam_tasks.clear()
        self._spam_bags.clear()
        self._reply_bags.clear()
        self._enemies.clear()
        self._cooldowns.clear()
        await super().stop()