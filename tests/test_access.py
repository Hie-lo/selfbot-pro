"""
تست رفتاری کنترل دسترسی (core/access.py) — بدون تلگرام و بدون دیتابیس.

سناریوها:
  • انقضای اشتراک در حین اجرا → watchdog معلق می‌کند و اعلان می‌دهد
  • تمدید → خودکار دوباره وصل می‌شود
  • مسدودسازی → قطع فوری؛ رفع مسدودی → برگشت
  • باطل شدن session در تلگرام → revoke + اعلان
  • `.روشن` / enable_plugin بدون اشتراک → رد می‌شود
  • خطای شبکه هنگام اتصال → session باطل علامت نمی‌خورد (معلق + تلاش دوباره)

اجرا:  python tests/test_access.py   یا   python -m pytest tests/
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault("DB_NAME", "x")
os.environ.setdefault("DB_USER", "x")
os.environ.setdefault("DB_PASS", "x")
os.environ.setdefault("ENCRYPTION_KEY", "pX3q1n2ZfJm6c0Yw4tY9sQk0gk8e3m3yF6Qe9m0sV1A=")

from core import access, client_manager, runtime  # noqa: E402
from core import plugin_manager  # noqa: E402
from core.crypto import encrypt  # noqa: E402
from database import db  # noqa: E402


# ═══════ Fakes ═══════

class FakeDB:
    def __init__(self):
        self.users: dict[int, dict] = {}
        self.sessions: dict[int, dict] = {}
        self.audit: list = []
        self.features: dict[int, list] = {}

    def install(self):
        async def get_user_by_db_id(uid):
            u = self.users.get(uid)
            return dict(u) if u else None

        async def get_users_by_db_ids(ids):
            return [dict(self.users[i]) for i in ids if i in self.users]

        async def get_session(uid):
            s = self.sessions.get(uid)
            return dict(s) if s else None

        async def update_session_status(uid, status, error=None):
            if uid in self.sessions:
                self.sessions[uid]["status"] = status
                self.sessions[uid]["error_message"] = error

        async def get_suspended_sessions():
            out = []
            for uid, s in self.sessions.items():
                if s["status"] == "suspended":
                    u = self.users[uid]
                    out.append({"user_id": uid, "error_message": s["error_message"], **{
                        k: u.get(k) for k in
                        ("telegram_id", "plan", "plan_expires_at", "is_banned", "is_active")
                    }})
            return out

        async def audit_log(uid, action, detail=""):
            self.audit.append((uid, action, detail))

        async def get_features(uid):
            return self.features.get(uid, [])

        for name, fn in locals().items():
            if callable(fn) and not name.startswith("_") and name != "self":
                setattr(db, name, fn)


class FakeClient:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.connected = True

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return self.authorized


NOTIFY: list = []


def setup(user_kwargs=None, status="connected"):
    fake = FakeDB()
    fake.install()
    now = datetime.now(timezone.utc)
    fake.users[7] = {
        "id": 7, "telegram_id": 7007, "plan": "premium",
        "plan_expires_at": now + timedelta(days=10),
        "is_banned": False, "is_active": True, **(user_kwargs or {}),
    }
    fake.sessions[7] = {
        "user_id": 7, "status": status, "error_message": None,
        "session_data_enc": encrypt("SESSION"),
    }

    client_manager.active_clients.clear()
    access._warned.clear()
    NOTIFY.clear()

    async def notify(tg, text, **kw):
        NOTIFY.append((tg, text))
        return True
    runtime.notify_user = notify

    state = {"reconnect": "ok"}

    async def reconnect(uid, s):
        if state["reconnect"] == "network":
            raise ConnectionError("boom")
        if state["reconnect"] == "dead":
            return None
        c = FakeClient()
        client_manager.active_clients[uid] = c
        return c
    client_manager.reconnect_client = reconnect

    async def load_plugins(uid, client):
        plugin_manager._active_plugins[uid] = {"x": object()}
    plugin_manager.load_plugins_for_user = load_plugins

    async def unload(uid):
        plugin_manager._active_plugins.pop(uid, None)
    plugin_manager.unload_all_for_user = unload

    return fake, state


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ═══════ Tests ═══════

def test_expiry_suspends_then_renewal_resumes():
    async def go():
        fake, _ = setup()
        client_manager.active_clients[7] = FakeClient()

        # اشتراک در حین اجرا تمام می‌شود
        fake.users[7]["plan_expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
        await access.run_checks()
        assert 7 not in client_manager.active_clients
        assert fake.sessions[7]["status"] == "suspended"
        assert fake.sessions[7]["error_message"] == access.R_SUB_EXPIRED
        assert any("پایان" in t for _, t in NOTIFY)

        # دور بعد: اعلان تکراری نمی‌رود
        n = len(NOTIFY)
        await access.run_checks()
        assert len(NOTIFY) == n

        # تمدید → خودکار برمی‌گردد
        fake.users[7]["plan_expires_at"] = datetime.now(timezone.utc) + timedelta(days=30)
        await access.run_checks()
        assert 7 in client_manager.active_clients
        assert fake.sessions[7]["status"] == "connected"
        assert 7 in plugin_manager._active_plugins
    run(go())


def test_ban_and_unban_via_sync():
    async def go():
        fake, _ = setup()
        client_manager.active_clients[7] = FakeClient()
        fake.users[7]["is_banned"] = True
        assert await access.sync_user(7) == "suspended"
        assert 7 not in client_manager.active_clients
        assert fake.sessions[7]["error_message"] == access.R_BANNED

        fake.users[7]["is_banned"] = False
        assert await access.sync_user(7) == "running"
        assert 7 in client_manager.active_clients
    run(go())


def test_revoked_session_detected():
    async def go():
        fake, _ = setup()
        client_manager.active_clients[7] = FakeClient(authorized=False)
        access._cycle = access.AUTH_CHECK_EVERY - 1   # این دور، بررسی عمیق
        await access.run_checks()
        assert 7 not in client_manager.active_clients
        assert fake.sessions[7]["status"] == "revoked"
        assert any("باطل" in t for _, t in NOTIFY)
    run(go())


def test_network_error_is_not_revocation():
    async def go():
        fake, state = setup(status="suspended")
        fake.sessions[7]["error_message"] = access.R_OFFLINE
        state["reconnect"] = "network"
        await access.run_checks()
        assert fake.sessions[7]["status"] == "suspended"
        assert fake.sessions[7]["error_message"] == access.R_OFFLINE

        state["reconnect"] = "ok"
        await access.run_checks()
        assert fake.sessions[7]["status"] == "connected"
        assert not NOTIFY  # برگشت از قطعی شبکه بی‌صداست
    run(go())


def test_enable_plugin_requires_subscription():
    async def go():
        fake, _ = setup({"plan": "free", "plan_expires_at": None})
        plugin_manager._active_plugins.pop(7, None)
        ok = await plugin_manager.enable_plugin(7, "anti_delete", FakeClient())
        assert ok is False
        assert "anti_delete" not in plugin_manager._active_plugins.get(7, {})
    run(go())


def test_status_labels():
    assert "متصل" in access.status_label({"status": "connected"})
    assert "معلق" in access.status_label({"status": "suspended", "error_message": "banned"})
    assert "نامعتبر" in access.status_label({"status": "revoked"})
    assert "متصل نیست" in access.status_label(None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
