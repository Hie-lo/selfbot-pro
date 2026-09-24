"""
تست ظرفیت و پایداری (بدون تلگرام و بدون دیتابیس):

  • governor: صف FIFO، لغو امن در صف بدون «گم شدن» ظرفیت، لغو task در حال انتظار
  • job_slot: نمایش «در صف» + جایگاه، و آزادسازی موقت هنگام FloodWait طولانی
  • admission: سقف تعداد، حداقل RAM آزاد، و تأخیر حلقه فقط برای ورود جدید
  • instance lock: نمونه‌ی دوم اجرا نمی‌شود؛ آزاد شدن قفل → نمونه‌ی بعدی می‌گیرد
  • روشن شدن موازی: هرگز از سقف ظرفیت رد نمی‌شود (رزرو ایمن)

اجرا:  python tests/test_capacity.py   یا   python -m pytest tests/
"""

import asyncio
import os
import sys

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

from core import governor, metrics, instance_lock  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ═══════ governor ═══════

def test_pool_fifo_and_cancel_no_leak():
    async def go():
        p = governor.Pool("t", 1)
        order = []

        async def worker(name, hold=0.05):
            async with p.slot():
                order.append(name)
                await asyncio.sleep(hold)

        await asyncio.gather(*(worker(i) for i in range(5)))
        assert order == [0, 1, 2, 3, 4]
        assert p.active == 0 and p.waiting == 0

        # لغو در صف با cancel_check
        await p.acquire()
        flag = {"cancel": False}
        t = asyncio.create_task(p.acquire(cancel_check=lambda: flag["cancel"], poll=0.05))
        await asyncio.sleep(0.02)
        assert p.waiting == 1
        flag["cancel"] = True
        assert await t is False
        assert p.waiting == 0
        p.release()
        assert p.active == 0, "ظرفیت نباید گم شود"

        # لغو task منتظر (CancelledError)
        await p.acquire()
        t = asyncio.create_task(p.acquire(poll=0.05))
        await asyncio.sleep(0.02)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        p.release()
        assert p.active == 0 and p.waiting == 0
        assert p.try_acquire()
        p.release()
    run(go())


def test_job_slot_queue_and_flood_yield():
    async def go():
        governor.POOLS["forward"] = governor.Pool("forward", 1)

        class Job:
            cancel_requested = False
            queued = False
            queue_pos = 0

        j1, j2 = Job(), Job()
        seen = []

        async def progress(job):
            seen.append((job is j2, job.queued, job.queue_pos))

        entered = asyncio.Event()
        release = asyncio.Event()

        async def run1():
            async with governor.job_slot("forward", j1, progress):
                entered.set()
                await release.wait()

        async def run2():
            async with governor.job_slot("forward", j2, progress):
                seen.append(("j2-running",))

        t1 = asyncio.create_task(run1())
        await entered.wait()
        t2 = asyncio.create_task(run2())
        await asyncio.sleep(0.05)
        assert j2.queued and j2.queue_pos == 1
        assert (True, True, 1) in seen

        # FloodWait طولانی روی j1 → ظرفیت به j2 می‌رسد
        governor.YIELD_ON_FLOOD_SECONDS = 60
        assert governor.yield_for_flood(j1, 300)
        await asyncio.sleep(0.05)
        assert ("j2-running",) in seen and not j2.queued
        await t2
        await governor.reclaim_after_flood(j1)     # j1 دوباره ظرفیت می‌گیرد
        assert governor.POOLS["forward"].active == 1
        release.set()
        await t1
        assert governor.POOLS["forward"].active == 0
    run(go())


def test_job_cancelled_while_queued():
    async def go():
        governor.POOLS["forward"] = governor.Pool("forward", 1)
        await governor.POOLS["forward"].acquire()

        class Job:
            cancel_requested = False

        j = Job()
        ran = []

        async def body():
            async with governor.job_slot("forward", j, None):
                ran.append(True)

        t = asyncio.create_task(body())
        await asyncio.sleep(0.05)
        j.cancel_requested = True
        await asyncio.wait_for(t, 5)
        assert ran == [True]                 # بدنه اجرا شد تا ذخیره‌ی نهایی انجام شود
        governor.POOLS["forward"].release()
        assert governor.POOLS["forward"].active == 0
    run(go())


# ═══════ admission ═══════

def test_admission():
    old = (metrics.MAX_CLIENTS, metrics.MIN_FREE_MEM_MB, metrics.LAG_LIMIT_MS)
    try:
        metrics.MAX_CLIENTS, metrics.MIN_FREE_MEM_MB, metrics.LAG_LIMIT_MS = 3, 0, 500
        assert metrics.admission(2) == (True, None)
        assert metrics.admission(3) == (False, metrics.R_CAP_COUNT)

        metrics.MIN_FREE_MEM_MB = 10 ** 9          # غیرممکن
        assert metrics.admission(0, new_login=True) == (False, metrics.R_CAP_MEMORY)
        # اکانت‌های موجود با حدس حافظه رد نمی‌شوند (فقط سقف‌های صریح)
        assert metrics.admission(0, new_login=False) == (True, None)
        metrics.MIN_FREE_MEM_MB = 0
        old_rss = metrics.MAX_RSS_MB
        metrics.MAX_RSS_MB = 1                     # سقف صریح → برای همه
        assert metrics.admission(0) == (False, metrics.R_CAP_MEMORY)
        metrics.MAX_RSS_MB = old_rss

        metrics._lag_ewma_ms = 2000.0
        assert metrics.admission(0, new_login=True) == (False, metrics.R_CAP_LAG)
        assert metrics.admission(0, new_login=False) == (True, None)  # ری‌استارت رد نشود
        metrics._lag_ewma_ms = 0.0
        assert "3" in metrics.admission_message(metrics.R_CAP_COUNT)
    finally:
        metrics.MAX_CLIENTS, metrics.MIN_FREE_MEM_MB, metrics.LAG_LIMIT_MS = old


def test_counter_rate():
    metrics.inc("x_test", 5)
    metrics.inc("x_test")
    assert metrics.total("x_test") == 6 and metrics.rate("x_test") == 6


# ═══════ instance lock ═══════

class _FakePG:
    holder = None

    class Conn:
        def __init__(self):
            self.closed = False

        async def fetchval(self, q, *a, **k):
            if "pg_try_advisory_lock" in q:
                if _FakePG.holder in (None, self):
                    _FakePG.holder = self
                    return True
                return False
            return 1

        async def execute(self, q, *a):
            if "unlock" in q and _FakePG.holder is self:
                _FakePG.holder = None

        async def close(self):
            self.closed = True
            if _FakePG.holder is self:
                _FakePG.holder = None

        def is_closed(self):
            return self.closed

        def terminate(self):
            self.closed = True

    @staticmethod
    async def connect(*a, **k):
        return _FakePG.Conn()


def test_instance_lock():
    async def go():
        real = instance_lock.asyncpg.connect
        instance_lock.asyncpg.connect = _FakePG.connect
        try:
            await instance_lock.acquire(wait=0)
            assert instance_lock.held()
            first_conn = instance_lock._conn
            first_task = instance_lock._task

            # نمونه‌ی دوم (شبیه‌سازی: وضعیت ماژول جدا)
            instance_lock._conn, instance_lock._task = None, None
            try:
                await instance_lock.acquire(wait=0)
                raise AssertionError("نمونه‌ی دوم نباید قفل بگیرد")
            except instance_lock.AnotherInstanceRunning:
                pass

            # نمونه‌ی اول آزاد می‌کند → حالا می‌شود گرفت
            instance_lock._conn, instance_lock._task = first_conn, first_task
            await instance_lock.release()
            assert _FakePG.holder is None
            await instance_lock.acquire(wait=0)
            assert instance_lock.held()
            await instance_lock.release()
        finally:
            instance_lock.asyncpg.connect = real
    run(go())


# ═══════ روشن شدن موازی ═══════

def test_parallel_startup_respects_capacity():
    async def go():
        from core import engine, client_manager, access, pv_cache
        from database import db
        from core.crypto import encrypt
        from datetime import datetime, timedelta, timezone

        exp = datetime.now(timezone.utc) + timedelta(days=5)
        sessions = [
            {"user_id": i, "telegram_id": 5000 + i, "plan": "1m", "plan_expires_at": exp,
             "is_banned": False, "is_active": True, "session_data_enc": encrypt("s")}
            for i in range(1, 9)
        ]
        statuses = {}
        peak = {"n": 0}

        async def get_all_active_sessions():
            return sessions

        async def update_session_status(uid, st, err=None):
            statuses[uid] = (st, err)

        async def audit_log(*a, **k):
            pass

        async def get_suspended_sessions():
            return []

        async def reconnect(user_db_id, session_string):
            await asyncio.sleep(0.05)
            client_manager.active_clients[user_db_id] = object()
            peak["n"] = max(peak["n"], len(client_manager.active_clients))
            return client_manager.active_clients[user_db_id]

        async def load_plugins(uid, client):
            pass

        saved = (db.get_all_active_sessions, db.update_session_status, db.audit_log,
                 db.get_suspended_sessions, engine.reconnect_client,
                 engine.load_plugins_for_user, metrics.MAX_CLIENTS, metrics.MIN_FREE_MEM_MB,
                 access.start_watchdog)
        db.get_all_active_sessions = get_all_active_sessions
        db.update_session_status = update_session_status
        db.audit_log = audit_log
        db.get_suspended_sessions = get_suspended_sessions
        engine.reconnect_client = reconnect
        engine.load_plugins_for_user = load_plugins
        metrics.MAX_CLIENTS, metrics.MIN_FREE_MEM_MB = 5, 0
        access.start_watchdog = lambda: None
        client_manager.active_clients.clear()
        try:
            import core.forwarder as fw
            real_resume = fw.resume_pending_jobs

            async def no_resume(**k):
                pass
            fw.resume_pending_jobs = no_resume
            await engine.startup()
            fw.resume_pending_jobs = real_resume
            assert len(client_manager.active_clients) == 5, len(client_manager.active_clients)
            assert peak["n"] <= 5
            deferred = [u for u, (st, err) in statuses.items() if err == access.R_CAPACITY]
            assert len(deferred) == 3, statuses
        finally:
            (db.get_all_active_sessions, db.update_session_status, db.audit_log,
             db.get_suspended_sessions, engine.reconnect_client,
             engine.load_plugins_for_user, metrics.MAX_CLIENTS, metrics.MIN_FREE_MEM_MB,
             access.start_watchdog) = saved
            client_manager.active_clients.clear()
    run(go())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
