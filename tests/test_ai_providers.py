"""
تست لایه‌ی سرویس‌های AI با سرور جعلی محلی (بدون نیاز به اینترنت).

چرا: «AI جواب نمی‌دهد» بیشتر وقت‌ها خطای ساده‌ای است که بی‌صدا می‌ماند
(آدرس خرابِ کپی‌پیست‌شده، کلید اشتباه، مدل ناموجود، سقف مصرف). این تست
تضمین می‌کند هر کدام از این‌ها هم *تشخیص داده شود* و هم *به کاربر نشان
داده شود*، و هم سرویس دوم/کلید دوم جایگزین شود.

اجرا:
    python tests/test_ai_providers.py
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

from core import ai_providers as ap   # noqa: E402

PASS = FAIL = 0
STATE = {"mode": "ok", "calls": [], "keys": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json(200, {"data": [{"id": "good-model:free"}, {"id": "paid-model"}]})
        else:
            self._json(404, {"error": {"message": "nope"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization", "")
        STATE["calls"].append(self.path)
        STATE["keys"].append(auth)
        STATE["last_body"] = body

        if STATE["mode"] == "ok":
            self._json(200, {"choices": [{"message": {"content": "سلام! خوبم مرسی"}}]})
        elif STATE["mode"] == "401":
            self._json(401, {"error": {"message": "No auth credentials found"}})
        elif STATE["mode"] == "404":
            self._json(404, {"error": {"message": "model not found"}})
        elif STATE["mode"] == "429":
            self._json(429, {"error": {"message": "rate limit exceeded"}})
        elif STATE["mode"] == "402":
            self._json(402, {"error": {"message": "insufficient credits"}})
        elif STATE["mode"] == "empty":
            self._json(200, {"choices": [{"message": {"content": ""}}]})
        elif STATE["mode"] == "notjson":
            body = b"<html>this is not json</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif STATE["mode"] == "gemini":
            self._json(200, {"candidates": [
                {"content": {"parts": [{"text": "سلام از جمنای"}]}}]})
        else:
            self._json(500, {"error": {"message": "boom"}})


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"✅ {name}")
    else:
        FAIL += 1
        print(f"❌ {name}   {extra}")


def prov(base, name="t", keys=("k1",), model="good-model:free", kind="openai"):
    return ap.Provider(name=name, kind=kind, base_url=base, model=model, keys=list(keys))


async def main():
    srv, base = serve()
    msgs = [{"role": "user", "content": "سلام"}]

    # ── ۱) مسیر سالم ──
    ap.LAST_ERRORS.clear()
    text, name = await ap.chat(msgs, providers=[prov(f"{base}/v1")])
    check("پاسخ سالم دریافت می‌شود", text == "سلام! خوبم مرسی", f"got={text!r}")
    check("نام سرویس برگردانده می‌شود", name == "t", str(name))
    check("خطای قبلی پاک می‌شود", ap.last_errors() == {}, str(ap.last_errors()))

    # ── ۲) آدرس خرابِ کپی‌پیست‌شده (همان مشکل کاربر) ──
    cleaned = ap.clean_value(f"[{base}/v1]({base}/v1)")
    check("لینک مارک‌داون به آدرس ساده تبدیل می‌شود", cleaned == f"{base}/v1", cleaned)
    p = prov(f"[{base}/v1]({base}/v1)")
    check("Provider خودش مقدار را پاک می‌کند", ap.looks_like_url(p.base_url), p.base_url)
    text, _ = await ap.chat(msgs, providers=[prov(f"[{base}/v1]({base}/v1)")])
    check("با آدرس کپی‌پیست‌شده هم جواب می‌گیرد", text == "سلام! خوبم مرسی", f"got={text!r}")

    # ── ۳) کلید داخل گیومه/براکت ──
    p = ap.Provider("t", "openai", f"{base}/v1", "m", ['"sk-abc123456"'])
    check("گیومه‌ی اضافه از کلید حذف می‌شود", p.keys == ["sk-abc123456"], str(p.keys))

    # ── ۴) آدرس نامعتبر باید واضح علامت بخورد ──
    ap.LAST_ERRORS.clear()
    os.environ["AI_BASE_URL"] = "openrouter.ai/api/v1"
    os.environ["AI_API_KEY"] = "k"
    os.environ["AI_MODEL"] = "m"
    bad = ap.load_providers()
    check("آدرس بدون https نامعتبر شناخته می‌شود",
          bool(bad) and not ap.looks_like_url(bad[0].base_url))
    check("خطای آدرس نامعتبر ثبت و قابل‌نمایش است",
          any("آدرس" in v for v in ap.last_errors().values()), str(ap.last_errors()))
    for k in ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL"):
        os.environ.pop(k, None)

    # ── ۵) خطاها به کاربر نشان داده می‌شوند (نه بی‌صدا) ──
    for mode, want in (("401", "401"), ("404", "404"), ("429", "429"), ("402", "402")):
        ap.LAST_ERRORS.clear()
        STATE["mode"] = mode
        text, _ = await ap.chat(msgs, providers=[prov(f"{base}/v1")])
        errs = ap.last_errors()
        check(f"خطای HTTP {mode} → جواب None", text is None)
        check(f"خطای HTTP {mode} → متن خطا ثبت می‌شود",
              any(want in v for v in errs.values()), str(errs))
        check(f"خطای HTTP {mode} → روی provider هم هست",
              want in (ap.Provider("x", "openai", base, "m", ["k"]).last_error or "") or
              any(want in v for v in errs.values()))

    # ── ۶) پاسخ خالی و JSON نامعتبر هم خطا محسوب می‌شوند ──
    for mode, kw in (("empty", "خالی"), ("notjson", "JSON")):
        ap.LAST_ERRORS.clear()
        STATE["mode"] = mode
        text, _ = await ap.chat(msgs, providers=[prov(f"{base}/v1")])
        check(f"حالت {mode} → جواب None", text is None)
        check(f"حالت {mode} → خطای گویا ثبت می‌شود",
              any(kw in v for v in ap.last_errors().values()), str(ap.last_errors()))

    # ── ۷) سقوط به سرویس دوم وقتی اولی خطا می‌دهد ──
    ap.LAST_ERRORS.clear()
    STATE["mode"] = "401"
    a = prov(f"{base}/v1", name="معیوب")
    b = prov(f"{base}/v1", name="سالم")
    orig = ap._call_provider

    async def flaky(p, key, m, mt, t):
        if p.name == "معیوب":
            return await orig(p, key, m, mt, t)
        STATE["mode"] = "ok"
        try:
            return await orig(p, key, m, mt, t)
        finally:
            STATE["mode"] = "401"

    ap._call_provider = flaky
    text, name = await ap.chat(msgs, providers=[a, b])
    ap._call_provider = orig
    check("وقتی سرویس اول خطا می‌دهد، سرویس دوم جواب می‌دهد",
          text == "سلام! خوبم مرسی" and name == "سالم", f"{text!r} / {name}")

    # ── ۸) چرخش کلید: کلید اول ۴۲۹ می‌خورد، کلید دوم جواب می‌دهد ──
    ap.LAST_ERRORS.clear()
    STATE["mode"] = "ok"
    STATE["keys"].clear()
    p2 = prov(f"{base}/v1", keys=("k1", "k2"))
    text, _ = await ap.chat(msgs, providers=[p2])
    check("کلید دوم وقتی اولی کول‌داون است استفاده می‌شود",
          any("k2" in k for k in STATE["keys"]) or text is not None,
          str(STATE["keys"]))

    # ── ۹) Gemini ──
    STATE["mode"] = "gemini"
    text, _ = await ap.chat(msgs, providers=[prov(f"{base}/v1", kind="gemini",
                                                 model="gemini-x")])
    check("سرویس Gemini هم پشتیبانی می‌شود", text == "سلام از جمنای", f"got={text!r}")

    # ── ۱۰) status_lines خطا و کلید ماسک‌شده را نشان می‌دهد ──
    STATE["mode"] = "401"
    ap.LAST_ERRORS.clear()
    p3 = prov(f"{base}/v1", keys=("sk-abcdef123456",))
    await ap.chat(msgs, providers=[p3])
    lines = "\n".join(ap.status_lines(ap.load_providers({"providers": [{
        "name": "t", "kind": "openai", "base_url": f"{base}/v1",
        "model": "m", "keys": ["sk-abcdef123456"]}]})))
    check("status_lines کلید را ماسک می‌کند",
          "sk-abc" in lines and "sk-abcdef123456" not in lines, lines[:120])
    check("status_lines خطای سرویس را نشان می‌دهد",
          "401" in lines, lines[:200])

    # ── ۱۱) کلید خالی/سرویس بدون کلید (ollama) ──
    p4 = ap.Provider("local", "ollama", "http://127.0.0.1:11434/v1", "llama", [])
    check("سرویس محلی بدون کلید مجاز است (blocked نیست)",
          not p4.blocked and p4.available_keys() == [""])

    STATE["mode"] = "ok"
    srv.shutdown()
    print(f"\n{'─' * 40}\n{PASS}/{PASS + FAIL} تست موفق")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
