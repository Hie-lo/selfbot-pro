"""
هارنس تست پاسخ هوشمند: فقط لایه‌ی شبکه‌ی Telethon جعل می‌شود؛ پلاگین‌ها،
دیسپچر، فیلترها، دیتابیس و plugin_manager کد واقعی هستند.

اجرا (نیاز به دیتابیس Postgres که در .env تنظیم شده):
    PYTHONPATH=. python tests/test_ai_flow.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "42")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault("DB_NAME", "sbt")
os.environ.setdefault("DB_USER", "sb")
os.environ.setdefault("DB_PASS", "sb")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")

from telethon import TelegramClient, events          # noqa: E402
from telethon.sessions import StringSession           # noqa: E402
from telethon.tl import types, functions              # noqa: E402

ME = 1001
PEER = 200
GROUP = -1001234567890
NOW = lambda: datetime.now(timezone.utc)              # noqa: E731

SENT = []      # (kind, chat_id, text)
PV = []        # اعلان‌های ربات کنترلی
STORE = {}
_mid = [5000]

meuser = types.User(id=ME, access_hash=1, first_name="Me", is_self=True)
peer = types.User(id=PEER, access_hash=99, first_name="Ali", username="ali")
group = types.Chat(id=abs(GROUP), title="گروه تست", participants_count=3, date=NOW(),
                   photo=types.ChatPhotoEmpty(), version=1)
_ENTS = {ME: meuser, PEER: peer, abs(GROUP): group}


def _next_id():
    _mid[0] += 1
    return _mid[0]


def _mk_msg(text, out=True, sender_id=ME, reply_to=None, chat=PEER, mid=None):
    mid = mid or _next_id()
    m = types.Message(
        id=mid,
        peer_id=types.PeerChat(abs(chat)) if chat == GROUP else types.PeerUser(chat),
        from_id=types.PeerUser(sender_id),
        message=text, date=NOW(), out=out,
        reply_to=types.MessageReplyHeader(reply_to_msg_id=reply_to) if reply_to else None,
    )
    STORE[mid] = m
    return m


def _chat_of(entity):
    if isinstance(entity, int):
        return entity
    if isinstance(entity, types.InputPeerUser):
        return entity.user_id
    if isinstance(entity, types.InputPeerChat):
        return -entity.chat_id
    return getattr(entity, "id", entity)


# ── جعل لایه‌ی شبکه ──
async def _connect(self): self._sb_conn = True
def _is_connected(self): return getattr(self, "_sb_conn", False)
async def _disconnect(self): self._sb_conn = False
async def _auth(self): return True


async def _get_me(self, input_peer=False):
    self._mb_entity_cache.self_id = ME
    return types.InputUserSelf() if input_peer else meuser


async def _get_entity(self, entity):
    if isinstance(entity, int):
        if entity in _ENTS:
            return _ENTS[entity]
        if entity < 0 and abs(entity) in _ENTS:
            return _ENTS[abs(entity)]
        raise ValueError(f"cannot resolve {entity}")
    if isinstance(entity, str):
        if entity == "me":
            return meuser
        if entity.lstrip("@").lower() == "ali":
            return peer
        raise ValueError(f"cannot resolve {entity}")
    return entity


async def _get_input_entity(self, peer):
    if isinstance(peer, (types.InputPeerSelf, types.InputPeerUser)):
        return peer
    if isinstance(peer, int):
        return types.InputPeerSelf() if peer == ME else types.InputPeerUser(user_id=peer, access_hash=99)
    if isinstance(peer, str):
        return types.InputPeerSelf() if peer == "me" else types.InputPeerUser(user_id=PEER, access_hash=99)
    return peer


async def _send_message(self, entity, message=None, reply_to=None, **kw):
    text = message if isinstance(message, str) else getattr(message, "message", str(message))
    chat = "me" if entity == "me" else _chat_of(entity)
    m = _mk_msg(text, out=True, sender_id=ME, reply_to=getattr(reply_to, "id", reply_to))
    m._client = self
    SENT.append(("send", chat, text, getattr(reply_to, "id", reply_to)))
    return m


async def _edit_message(self, entity, message, text=None, **kw):
    txt = text if isinstance(text, str) else getattr(text, "message", str(text))
    SENT.append(("edit", _chat_of(entity), txt))
    return message


async def _delete_messages(self, entity, ids=None, **kw):
    SENT.append(("delete", _chat_of(entity), None))
    return []


async def _get_messages(self, entity, ids=None, limit=None, **kw):
    if isinstance(ids, types.InputMessageReplyTo):
        src = STORE.get(ids.id)
        return STORE.get(src.reply_to.reply_to_msg_id) if (src and src.reply_to) else None
    if ids is None:
        return [m for m in STORE.values()]
    if isinstance(ids, (list, tuple)):
        return [STORE.get(i) for i in ids]
    return STORE.get(ids)


async def _get_sender(self):
    """کاربر/چت فرستنده — در تست‌ها از جدول موجودیت‌ها می‌آید"""
    sid = getattr(self, "sender_id", None)
    if sid is None:
        return None
    ent = _ENTS.get(sid if sid > 0 else abs(sid))
    if ent is None:
        return None
    return ent


async def _get_chat(self):
    cid = getattr(self, "chat_id", None)
    if cid is None:
        return None
    return _ENTS.get(cid if cid > 0 else abs(cid))


async def _get_input_chat(self):
    peer = getattr(self, "_chat_peer", None) or getattr(self, "peer_id", None)
    uid = getattr(peer, "user_id", None)
    cid = getattr(peer, "chat_id", None)
    if cid:
        return types.InputPeerChat(chat_id=cid)
    if uid is None:
        c = getattr(self, "chat_id", None)
        uid = c if isinstance(c, int) and c > 0 else None
    if uid is None:
        return None
    return types.InputPeerSelf() if uid == ME else types.InputPeerUser(user_id=uid, access_hash=99)


async def _call(self, request, ordered=False, flood_sleep_threshold=None):
    reqs = request if isinstance(request, list) else [request]
    r = reqs[0]
    if isinstance(r, functions.users.GetUsersRequest):
        return [meuser if isinstance(i, types.InputUserSelf) else peer for i in r.id]
    if isinstance(r, functions.messages.GetPeerDialogsRequest):
        return types.messages.PeerDialogs(
            dialogs=[], messages=[], chats=[], users=[], state=types.updates.State(0, 0, 0, 0, 0))
    return []


class _Action:
    """جای client.action(chat, 'typing')"""
    def __init__(self, chat_id, kind):
        self.chat_id = chat_id
        self.kind = kind

    async def __aenter__(self):
        SENT.append(("action", self.chat_id, self.kind))
        return self

    async def __aexit__(self, *exc):
        return False


class FakeBot:
    def __init__(self):
        self.fail = False
        self.last_kwargs = None

    async def send_message(self, chat_id=None, text=None, **kw):
        if self.fail:
            raise RuntimeError("bot blocked by user")
        self.last_kwargs = kw
        PV.append((chat_id, text))
        return None


def install():
    from telethon.tl.custom.chatgetter import ChatGetter
    ChatGetter.get_input_chat = _get_input_chat
    ChatGetter.get_sender = _get_sender
    ChatGetter.get_chat = _get_chat
    TelegramClient.connect = _connect
    TelegramClient.is_connected = _is_connected
    TelegramClient.disconnect = _disconnect
    TelegramClient.is_user_authorized = _auth
    TelegramClient.get_me = _get_me
    TelegramClient.get_entity = _get_entity
    TelegramClient.get_input_entity = _get_input_entity
    TelegramClient.send_message = _send_message
    TelegramClient.edit_message = _edit_message
    TelegramClient.delete_messages = _delete_messages
    TelegramClient.get_messages = _get_messages
    TelegramClient.__call__ = _call
    TelegramClient.action = lambda self, chat, kind="typing", **kw: _Action(chat, kind)

    from core import runtime
    runtime.bot = FakeBot()
    return runtime.bot


def make_client():
    c = TelegramClient(StringSession(""), api_id=1, api_hash="x")
    c._sb_conn = True
    c._mb_entity_cache.self_id = ME
    return c


async def fire(client, text, out=True, reply_to=None, sender_id=None, chat=PEER, mid=None):
    sid = sender_id if sender_id is not None else (ME if out else PEER)
    m = _mk_msg(text, out=out, sender_id=sid, reply_to=reply_to, chat=chat, mid=mid)
    m._client = client
    u = types.UpdateNewMessage(message=m, pts=1, pts_count=1)
    u._entities = {}
    await client._dispatch_update(u)
    await asyncio.sleep(0)
    return m


async def setup_user():
    """کاربر تازه با اشتراک فعال؛ خروجی: (user_db_id, client)"""
    from database import db
    from core import plugin_manager as pm
    await db.init_db()
    await db.get_pool().execute("DELETE FROM users WHERE telegram_id=$1", ME)
    u = await db.create_user(ME, "Owner")
    await db.get_pool().execute(
        "UPDATE users SET plan='pro', plan_expires_at=$2 WHERE telegram_id=$1",
        ME, NOW().replace(year=NOW().year + 1))
    client = make_client()
    await pm.load_plugins_for_user(u["id"], client)
    return u["id"], client


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def errors(self):
        return [f"{r.name}: {r.getMessage()}" for r in self.records if r.levelno >= logging.ERROR]
