"""
دفترچه‌ی نام‌ها (Peer Directory) — یکی برای هر کلاینت

قبلاً برای **هر** پیام پی‌وی، ضدحذف و ضدویرایش هرکدام جدا get_entity و
get_me صدا می‌زدند (تا ~۴ درخواست API برای هر پیام). get_entity حتی وقتی
entity در حافظه‌ی Telethon هست یک درخواست users.getUsers می‌فرستد.

اینجا:
1. هر User/Chat که همراه خود آپدیت می‌آید «رایگان» یاد گرفته می‌شود.
2. اگر کسی ناشناخته بود، فقط **یک** درخواست برای او زده می‌شود (نه برای هر
   پیام) و نتیجه ۲۴ ساعت معتبر است؛ درخواست‌های همزمان برای یک نفر یکی
   می‌شوند؛ شکست‌ها ۱ ساعت کش منفی دارند.
3. FloodWait روی درخواست نام → تا پایان آن هیچ درخواست نامی زده نمی‌شود
   (به‌جای اینکه هر پیام دوباره به دیوار بخورد).
4. حجم ثابت: حداکثر PEER_CACHE_SIZE نفر (قدیمی‌ترها بیرون می‌روند).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

from telethon.errors import FloodWaitError
from telethon.tl import types

from core import metrics

logger = logging.getLogger("peers")

PEER_CACHE_SIZE = 5000
FRESH_SECONDS = 24 * 3600
NEGATIVE_SECONDS = 3600
ME_REFRESH_SECONDS = 6 * 3600


class PeerInfo:
    __slots__ = ("id", "first", "last", "username", "title", "is_user", "ts")

    def __init__(self, id, first="", last="", username=None, title="", is_user=True):
        self.id = id
        self.first = first or ""
        self.last = last or ""
        self.username = username or None
        self.title = title or ""
        self.is_user = is_user
        self.ts = time.time()

    @property
    def full_name(self) -> str:
        """first + last (بدون عنوان) — رفتار قبلیِ پلاگین‌ها"""
        return " ".join(p for p in (self.first, self.last) if p).strip()

    @property
    def display(self) -> str:
        """عنوان (گروه/کانال) یا نام کامل؛ در نبود هر دو، آیدی"""
        return (self.title.strip() or self.full_name or str(self.id))

    @classmethod
    def from_entity(cls, ent) -> "PeerInfo | None":
        if ent is None:
            return None
        if isinstance(ent, types.User):
            return cls(ent.id, ent.first_name, ent.last_name,
                       getattr(ent, "username", None), "", True)
        if isinstance(ent, (types.Chat, types.Channel, types.ChatForbidden,
                            types.ChannelForbidden)):
            from telethon import utils
            try:
                pid = utils.get_peer_id(ent)
            except Exception:
                pid = ent.id
            return cls(pid, "", "", getattr(ent, "username", None),
                       getattr(ent, "title", "") or "", False)
        return None


class PeerDirectory:
    def __init__(self, client, size: int = PEER_CACHE_SIZE):
        self.client = client
        self.size = size
        self._peers: OrderedDict[int, PeerInfo] = OrderedDict()
        self._negative: dict[int, float] = {}
        self._inflight: dict[int, asyncio.Future] = {}
        self._flood_until = 0.0
        self._me: PeerInfo | None = None
        self._me_ts = 0.0

    # ── یادگیری رایگان ──

    def learn(self, ent) -> None:
        info = PeerInfo.from_entity(ent)
        if info is None:
            return
        self._put(info)

    def learn_from_message(self, msg) -> None:
        # Telethon در _finish_init این‌ها را از entities همان آپدیت پر می‌کند
        for attr in ("_sender", "_chat"):
            ent = getattr(msg, attr, None)
            if ent is not None:
                self.learn(ent)

    def _put(self, info: PeerInfo) -> None:
        self._peers[info.id] = info
        self._peers.move_to_end(info.id)
        self._negative.pop(info.id, None)
        while len(self._peers) > self.size:
            self._peers.popitem(last=False)

    # ── خواندن ──

    def peek(self, peer_id: int) -> PeerInfo | None:
        """فقط از حافظه، بدون هیچ درخواستی"""
        return self._peers.get(peer_id)

    def is_fresh(self, peer_id: int) -> bool:
        p = self._peers.get(peer_id)
        return bool(p and time.time() - p.ts < FRESH_SECONDS)

    async def get(self, peer_id: int | None) -> PeerInfo | None:
        """از حافظه؛ در صورت نبود/کهنگی حداکثر یک درخواست (با dedupe)"""
        if not peer_id:
            return None
        cached = self._peers.get(peer_id)
        now = time.time()
        if cached and now - cached.ts < FRESH_SECONDS:
            metrics.inc("peer_hit")
            return cached
        if now < self._flood_until or now - self._negative.get(peer_id, 0) < NEGATIVE_SECONDS:
            return cached                     # کهنه بهتر از هیچ

        fut = self._inflight.get(peer_id)
        if fut is not None:
            try:
                return await asyncio.shield(fut)
            except Exception:
                return cached

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._inflight[peer_id] = fut
        result = cached
        try:
            metrics.inc("peer_rpc")
            ent = await self.client.get_entity(peer_id)
            info = PeerInfo.from_entity(ent)
            if info is not None:
                info.id = peer_id if info.is_user else info.id
                self._put(info)
                result = info
        except FloodWaitError as e:
            metrics.inc("floodwait")
            self._flood_until = time.time() + int(getattr(e, "seconds", 60) or 60)
            logger.warning(f"peer lookup flood-wait {e.seconds}s — pausing lookups")
        except Exception as e:
            self._negative[peer_id] = time.time()
            logger.debug(f"peer lookup {peer_id} failed: {e}")
        finally:
            self._inflight.pop(peer_id, None)
            if not fut.done():
                fut.set_result(result)
        return result

    def warm(self, peer_id: int | None) -> None:
        """در پس‌زمینه یاد بگیر (هندلر پیام منتظر نمی‌ماند)"""
        if not peer_id or self.is_fresh(peer_id) or peer_id in self._inflight:
            return
        now = time.time()
        if now < self._flood_until or now - self._negative.get(peer_id, 0) < NEGATIVE_SECONDS:
            return
        task = asyncio.create_task(self.get(peer_id))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def me(self) -> PeerInfo | None:
        now = time.time()
        if self._me and now - self._me_ts < ME_REFRESH_SECONDS:
            return self._me
        try:
            ent = await self.client.get_me()
            info = PeerInfo.from_entity(ent)
            if info:
                self._me, self._me_ts = info, now
                self._put(info)
        except FloodWaitError:
            metrics.inc("floodwait")
        except Exception as e:
            logger.debug(f"get_me failed: {e}")
        return self._me

    def __len__(self):
        return len(self._peers)


def for_client(client) -> PeerDirectory:
    """دفترچه‌ی همان کلاینت (با عمر خود کلاینت ساخته/دور ریخته می‌شود)"""
    d = getattr(client, "_sb_peers", None)
    if d is None:
        d = PeerDirectory(client)
        client._sb_peers = d
    return d
