"""
ثبت کارهایی که «خود سلف‌بات» انجام می‌دهد (حذف/ویرایش پیام)

مشکل (مثبت کاذب): پلاگین‌ها پیام دستور را پاک می‌کنند (`.راهنما`، `.استیکر`،
...) یا پیام خود را پشت‌سرهم ویرایش می‌کنند (انیمیشن `.قلب`، `.پنل`، تاس).
تلگرام برای این‌ها هم UpdateDeleteMessages / UpdateEditMessage می‌فرستد و
هیچ فرقی با حذف/ویرایش واقعی ندارد → ضدحذف «پیامِ خودت حذف شد: .راهنما» و
ضدویرایش ده‌ها گزارش برای انیمیشن قلب می‌فرستاد.

راه‌حل دقیق (نه حدسی): متدهای delete_messages و edit_message همان کلاینت
پوشانده می‌شوند. Message.delete()/edit() و event.delete()/edit() هم در
Telethon دقیقاً همین متدها را صدا می‌زنند، پس هر حذف/ویرایشی که از کد ما
شروع شود — از هر پلاگینی — قبل از ارسال درخواست ثبت می‌شود. حذف/ویرایشی
که کاربر یا طرف مقابل در اپ تلگرام انجام دهد ثبت نمی‌شود و مثل قبل گزارش
می‌شود.
"""

from __future__ import annotations

import time

TTL = 900          # ثانیه — آپدیت تلگرام معمولاً ظرف چند ثانیه می‌رسد
_MAX = 5000


class _Registry:
    __slots__ = ("deleted", "edited")

    def __init__(self):
        self.deleted: dict[int, float] = {}
        self.edited: dict[int, float] = {}

    @staticmethod
    def _add(store: dict, ids):
        now = time.time()
        for i in ids:
            if isinstance(i, int):
                store[i] = now
        if len(store) > _MAX:
            cutoff = now - TTL
            for k in [k for k, t in store.items() if t < cutoff]:
                del store[k]
            while len(store) > _MAX:
                store.pop(next(iter(store)))

    @staticmethod
    def _has(store: dict, msg_id: int) -> bool:
        t = store.get(msg_id)
        return t is not None and time.time() - t < TTL


def _ids(message_ids) -> list:
    if message_ids is None:
        return []
    if not isinstance(message_ids, (list, tuple, set)):
        message_ids = [message_ids]
    out = []
    for m in message_ids:
        mid = getattr(m, "id", m)
        if isinstance(mid, int):
            out.append(mid)
    return out


def install(client) -> None:
    """پوشاندن متدهای حذف/ویرایش همین کلاینت (یک بار)"""
    if getattr(client, "_sb_self", None) is not None:
        return
    reg = _Registry()
    client._sb_self = reg

    orig_delete = client.delete_messages
    orig_edit = client.edit_message

    async def delete_messages(entity, message_ids, *args, **kwargs):
        reg._add(reg.deleted, _ids(message_ids))
        return await orig_delete(entity, message_ids, *args, **kwargs)

    async def edit_message(entity, message=None, *args, **kwargs):
        # امضاهای Telethon: edit_message(entity, message_id, text) یا edit_message(message, text)
        target = message if message is not None else entity
        reg._add(reg.edited, _ids(target) or _ids(entity))
        return await orig_edit(entity, message, *args, **kwargs)

    client.delete_messages = delete_messages
    client.edit_message = edit_message


def deleted_by_self(client, msg_id: int) -> bool:
    reg = getattr(client, "_sb_self", None)
    return bool(reg and reg._has(reg.deleted, msg_id))


def edited_by_self(client, msg_id: int) -> bool:
    reg = getattr(client, "_sb_self", None)
    return bool(reg and reg._has(reg.edited, msg_id))


def mark_deleted(client, ids) -> None:
    """برای کدی که مستقیم DeleteMessagesRequest می‌فرستد"""
    reg = getattr(client, "_sb_self", None)
    if reg:
        reg._add(reg.deleted, _ids(ids))


def mark_edited(client, ids) -> None:
    reg = getattr(client, "_sb_self", None)
    if reg:
        reg._add(reg.edited, _ids(ids))
