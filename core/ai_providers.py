"""
لایه‌ی سرویس‌های AI — چند provider، چند کلید، بدون وابستگی به یک سرویس.

پشتیبانی:
  • kind="openai"  → هر سرویس سازگار با OpenAI (OpenRouter، Groq، DeepSeek،
                     Together، Ollama/LLM استودیو محلی، پروکسی شخصی ...)
  • kind="gemini"  → Google Gemini

تنظیمات از دو جا خوانده می‌شود:
  ۱) .env → AI_PROVIDERS  (JSON؛ ساده‌ترین راه برای عوض کردن سریع توکن‌ها)
  ۲) دیتابیس → feature_toggles.config_json["providers"]  (برای کاربر، رمزنگاری‌شده)

رفتار:
  • درخواست‌ها به ترتیب اولویت provider و کلیدها می‌روند.
  • خطای ۴۲۹/۵xx یا مهلت → آن کلید کول‌داون می‌شود و بعدی امتحان می‌شود.
  • اگر همه شکست بخورند، None برمی‌گردد تا لایه‌ی بالاتر «نردبان پاسخ» را اجرا کند.
"""

import asyncio
import json
import logging
import os
import re
import time

logger = logging.getLogger("ai_providers")

_httpx = None


def _http():
    """
    httpx فقط وقتی AI واقعاً استفاده شود ایمپورت می‌شود.
    اگر نصب نباشد، کل ربات نمی‌خوابد؛ فقط قابلیت AI پیام راهنما می‌دهد.
    """
    global _httpx
    if _httpx is None:
        try:
            import httpx as _m
            _httpx = _m
        except ImportError:
            logger.error("httpx نصب نیست — دستور: pip install -r requirements.txt")
            _httpx = False
    return _httpx or None

# آخرین خطای هر سرویس — برای نمایش به کاربر (وگرنه خطاها فقط در لاگ می‌ماندند
# و کاربر فقط «جوابی نیامد» را می‌دید).
LAST_ERRORS: dict[str, str] = {}
PROVIDER_SOURCE = "none"        # تنظیمات از کجا خوانده شد: config / env-json / env / none


# تاریخچه‌ی کوتاه خطاها — تا وقتی یک خطای جدید می‌آید، خطای قبلی گم نشود
ERROR_HISTORY: dict[str, list[str]] = {}


def last_errors() -> dict[str, str]:
    return dict(LAST_ERRORS)


def error_history(name: str) -> list[str]:
    return list(ERROR_HISTORY.get(name, []))


def set_last_error(name: str, msg: str):
    msg = (msg or "")[:300]
    if not msg:
        return
    LAST_ERRORS[name] = msg
    hist = ERROR_HISTORY.setdefault(name, [])
    if msg not in hist:
        hist.append(msg)
        del hist[:-3]


def clear_errors(name: str | None = None):
    if name is None:
        LAST_ERRORS.clear()
        ERROR_HISTORY.clear()
    else:
        LAST_ERRORS.pop(name, None)
        ERROR_HISTORY.pop(name, None)


_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")


def clean_value(value) -> str:
    """
    پاک‌سازی مقدارهایی که (معمولاً) از چت کپی می‌شوند:

      [https://a/v1](https://a/v1)  →  https://a/v1      (لینک مارک‌داون)
      "sk-..." / 'sk-...' / [sk-...] / sk-...،‌   →  sk-...
      https://a/v1/                 →  https://a/v1      (اسلش آخر)

    بدون این، یک کپی‌پیست ساده از چت باعث می‌شود همه‌ی درخواست‌ها بی‌صدا
    شکست بخورند و کاربر فکر کند «AI خراب است».
    """
    v = "" if value is None else str(value)
    v = v.replace("\u200c", "").replace("\u200f", "").replace("\ufeff", "")
    v = v.strip().strip("\u201c\u201d").strip()
    v = v.strip('"').strip("'").strip().rstrip(",").strip()

    m = _MD_LINK_RE.match(v)
    if m:
        v = m.group(2).strip()          # آدرس داخل پرانتز معتبرتر است
    v = v.strip("[]").strip()
    v = v.strip('"').strip("'").strip()

    if v.startswith("http"):
        v = v.rstrip("/")
    return v


_URL_RE = re.compile(r"^https?://[^\s\[\]()\"']+$")


def looks_like_url(value: str) -> bool:
    return bool(_URL_RE.match(value or ""))


REQUEST_TIMEOUT = 45.0          # ثانیه
COOLDOWN_AFTER_ERROR = 90       # ثانیه — چقدر یک کلید/سرویس خطادار کنار برود
MAX_ATTEMPTS = 6                # سقف تلاش در یک فراخوانی (ضد حلقه)


class Provider:
    """یک سرویس + کلیدهایش"""

    def __init__(self, name: str, kind: str, base_url: str, model: str,
                 keys: list[str], priority: int = 0, max_tokens: int = 700,
                 temperature: float = 0.8, extra: dict | None = None):
        self.name = clean_value(name) or "provider"
        self.kind = (clean_value(kind) or "openai").lower()
        self.base_url = clean_value(base_url)
        self.model = clean_value(model)
        self.keys = [clean_value(k) for k in (keys or []) if clean_value(k)]
        self.last_error = ""
        self.priority = priority
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.extra = extra or {}
        self._cooldown_until: dict[str, float] = {}
        self._key_index = 0

    # ── کلیدها ──
    def available_keys(self) -> list[str]:
        # سرویس محلی/پروکسی بدون کلید: یک «کلید خالی» یعنی بدون هدر Authorization
        if not self.keys:
            return [""]
        now = time.monotonic()
        keys = [k for k in self.keys if self._cooldown_until.get(k, 0) <= now]
        if not keys:
            return []
        # چرخش: از کلیدی که دفعه‌ی قبل استفاده شد، بعدی شروع شود
        self._key_index = (self._key_index + 1) % len(keys)
        return keys[self._key_index:] + keys[:self._key_index]

    def cooldown(self, key: str, seconds: int = COOLDOWN_AFTER_ERROR) -> None:
        self._cooldown_until[key] = time.monotonic() + seconds
        logger.warning(f"{self.name}: key …{key[-6:]} cooling down {seconds}s")

    @property
    def blocked(self) -> bool:
        """همه‌ی کلیدها در کول‌داون هستند؟"""
        now = time.monotonic()
        return bool(self.keys) and all(self._cooldown_until.get(k, 0) > now for k in self.keys)

    def __repr__(self) -> str:
        return f"<Provider {self.name} kind={self.kind} model={self.model} keys={len(self.keys)}>"


# ─────────────── ساخت provider از تنظیمات ───────────────

def _from_dict(d: dict) -> Provider | None:
    try:
        keys = d.get("keys") or ([d["key"]] if d.get("key") else [])
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split(",")]
        if not keys and d.get("kind") != "ollama":
            return None
        return Provider(
            name=d.get("name") or d.get("kind") or "provider",
            kind=d.get("kind", "openai"),
            base_url=d.get("base_url") or _default_base(d.get("kind")),
            model=d.get("model") or "",
            keys=keys,
            priority=int(d.get("priority", 0)),
            max_tokens=int(d.get("max_tokens", 700)),
            temperature=float(d.get("temperature", 0.8)),
            extra=d.get("extra") or {},
        )
    except Exception as e:
        logger.warning(f"bad provider config {d.get('name')}: {e}")
        return None


def mask_key(key: str) -> str:
    """نمایش امن کلید: sk-or-…a1b2"""
    k = key or ""
    if not k:
        return "(بدون کلید)"
    if len(k) <= 8:
        return f"{k[:2]}…{k[-2:]}"
    return f"{k[:6]}…{k[-4:]}"


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _default_base(kind: str | None) -> str:
    return {
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "http://127.0.0.1:11434/v1",
    }.get((kind or "openai").lower(), "https://api.openai.com/v1")


def load_providers(config: dict | None = None) -> list[Provider]:
    """
    اولویت: providers داخل config کاربر → AI_PROVIDERS در .env → تک‌سرویس ساده
    (AI_BASE_URL/AI_API_KEY/AI_MODEL) → خالی
    """
    global PROVIDER_SOURCE
    PROVIDER_SOURCE = "none"
    raw = None
    if config and isinstance(config.get("providers"), list) and config["providers"]:
        raw = config["providers"]
        PROVIDER_SOURCE = "config"
    elif config and isinstance(config.get("providers_json"), str):
        try:
            raw = json.loads(config["providers_json"])
            PROVIDER_SOURCE = "config"
        except Exception:
            raw = None
    if raw is None:
        env_json = os.getenv("AI_PROVIDERS", "").strip()
        if env_json:
            try:
                raw = json.loads(env_json)
                PROVIDER_SOURCE = "env-json"
            except Exception as e:
                logger.error(f"AI_PROVIDERS is not valid JSON: {e}")
                raw = None

    providers: list[Provider] = []
    if raw:
        for d in raw:
            if isinstance(d, dict):
                p = _from_dict(d)
                if p:
                    providers.append(p)

    if not providers:
        # حالت ساده: یک سرویس
        key = clean_value(os.getenv("AI_API_KEY", ""))
        base = clean_value(os.getenv("AI_BASE_URL", ""))
        model = clean_value(os.getenv("AI_MODEL", ""))
        kind = clean_value(os.getenv("AI_KIND", "openai")) or "openai"
        if model and (key or kind == "ollama"):
            providers.append(Provider(
                name=f"{kind}-default", kind=kind,
                base_url=base or _default_base(kind), model=model,
                keys=[key] if key else [],
            ))
            PROVIDER_SOURCE = "env"

    # آدرسی که معتبر نیست را همان‌جا با پیام واضح علامت می‌زنیم (نه این‌که
    # بعداً وسط درخواست بی‌صدا شکست بخورد)
    for p in providers:
        if not looks_like_url(p.base_url):
            msg = (f"آدرس سرویس معتبر نیست: {p.base_url!r} — "
                   f"باید با http/https شروع شود و کاراکتر اضافه نداشته باشد")
            logger.error(f"{p.name}: {msg}")
            set_last_error(p.name, msg)

    providers.sort(key=lambda p: p.priority)
    return providers


# ─────────────── فراخوانی ───────────────

def _openai_payload(p: Provider, messages: list[dict], max_tokens: int, temperature: float) -> dict:
    return {
        "model": p.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }


def _openai_headers(p: Provider, key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # OpenRouter با این دو هدر خوش‌رفتارتر است
    if "openrouter" in (p.base_url or ""):
        headers["HTTP-Referer"] = "https://github.com/Hie-lo/selfbot-pro"
        headers["X-Title"] = "SelfBot Pro"
    return headers


_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "analysis")


def _openai_text(data: dict) -> str:
    """متن نهایی پاسخ (فقط content — استدلال هرگز به‌عنوان پیام نمی‌رود)"""
    try:
        msg = (data.get("choices") or [{}])[0].get("message") or {}
    except Exception:
        msg = {}
    if not isinstance(msg, dict):
        return ""
    return (msg.get("content") or "").strip()


def _openai_reasoning(data: dict) -> str:
    """
    بخش استدلال (reasoning) مدل‌های فکری.

    ⚠️ مدل‌های reasoning وقتی سقف توکن تمام شود، content را خالی و فقط
    reasoning می‌فرستند → آدم فکر می‌کرد «سرویس خراب است».
    این متن هرگز مستقیم برای طرف مقابل فرستاده نمی‌شود؛ فقط سیگنال این است
    که «مدل جواب داشت ولی جا کم آورد» تا با بودجه‌ی بیشتر از نو پرسیده شود.
    """
    try:
        msg = (data.get("choices") or [{}])[0].get("message") or {}
    except Exception:
        msg = {}
    if not isinstance(msg, dict):
        return ""
    for k in _REASONING_KEYS:
        val = msg.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _openai_finish_reason(data: dict) -> str:
    try:
        return str((data.get("choices") or [{}])[0].get("finish_reason") or "")
    except Exception:
        return ""


def _api_error_text(data: dict) -> str:
    """پیام خطای داخل بدنه (اگر سرویس با ۲۰۰ آمد ولی خطا داد)"""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "")[:200]
    if isinstance(err, str):
        return err[:200]
    return ""


def _gemini_payload(p: Provider, messages: list[dict], max_tokens: int, temperature: float) -> dict:
    contents = []
    system_parts = []
    for m in messages:
        role = m.get("role")
        text = m.get("content") or ""
        if role == "system":
            system_parts.append(text)
            continue
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": text}],
        })
    payload = {
        "contents": contents or [{"role": "user", "parts": [{"text": "سلام"}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    return payload


def _gemini_text(data: dict) -> str:
    try:
        cands = data.get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        return ""


async def _call_provider(p: Provider, key: str, messages: list[dict],
                         max_tokens: int, temperature: float) -> str | None:
    if p.kind == "gemini":
        url = f"{p.base_url}/models/{p.model}:generateContent"
        if key:
            url += f"?key={key}"
        payload = _gemini_payload(p, messages, max_tokens, temperature)
        headers = {"Content-Type": "application/json"}
    else:
        url = f"{p.base_url}/chat/completions"
        payload = _openai_payload(p, messages, max_tokens, temperature)
        headers = _openai_headers(p, key)

    hx = _http()
    if hx is None:
        return None
    async with hx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code in (429, 500, 502, 503, 504):
        msg = f"HTTP {resp.status_code} — "
        msg += ("سقف مصرف/تعداد درخواست (Rate limit)" if resp.status_code == 429
                else "خطای موقت سرویس")
        p.last_error = msg
        set_last_error(p.name, msg)
        p.cooldown(key, COOLDOWN_AFTER_ERROR if resp.status_code != 429 else 60)
        return None
    if resp.status_code >= 400:
        body = resp.text[:200].replace("\n", " ")
        p.last_error = f"HTTP {resp.status_code}: {body}"
        set_last_error(p.name, p.last_error)
        logger.error(f"{p.name} HTTP {resp.status_code}: {body}")
        # کلید اشتباه/مدل اشتباه → کول‌داون طولانی‌تر
        if resp.status_code in (401, 403, 404):
            p.cooldown(key, 600)
        return None

    try:
        data = resp.json()
    except Exception as e:
        msg = f"پاسخ نامعتبر از سرویس (JSON نبود): {str(e)[:80]}"
        p.last_error = msg
        set_last_error(p.name, msg)
        return None

    api_err = _api_error_text(data) if isinstance(data, dict) else ""
    if api_err:
        msg = f"خطای سرویس: {api_err}"
        p.last_error = msg
        set_last_error(p.name, msg)
        logger.error(f"{p.name}: {msg}")
        return None

    text = _gemini_text(data) if p.kind == "gemini" else _openai_text(data)
    if not text:
        finish = _openai_finish_reason(data) if p.kind != "gemini" else ""
        reasoning = _openai_reasoning(data) if p.kind != "gemini" else ""
        budget_short = finish in ("length", "max_tokens")
        if (budget_short or reasoning) and max_tokens < 3000:
            # مدل جواب داشت ولی جا (یا متن نهایی) کم آمد → یک‌بار از نو
            # می‌پرسیم، این بار با بودجه‌ی بیشتر و تأکید بر «فقط جواب نهایی».
            bigger = min(max(max_tokens * 4, 1000), 3000)
            logger.warning(
                f"{p.name}: پاسخ خالی (finish={finish or '؟'}، "
                f"reasoning={'دارد' if reasoning else 'ندارد'}) → تلاش با {bigger} توکن")
            nudge = list(messages) + [{
                "role": "system",
                "content": ("فقط متن نهایی پیام را بنویس — کوتاه، بدون توضیح و "
                            "بدون توضیح مراحل فکر کردن."),
            }]
            try:
                retry_text = await _call_provider(p, key, nudge, bigger, temperature)
                if retry_text:
                    return retry_text
            except Exception as e:                       # noqa: BLE001
                logger.error(f"{p.name}: retry after empty reply failed: {e}")
        msg = ("مدل فقط استدلال داد و متن نهایی خالی بود (سقف توکن کم است)"
               if reasoning and not finish
               else (f"پاسخ خالی (finish_reason={finish})" if finish
                     else "سرویس جواب خالی داد (متن پاسخ خالی بود)"))
        p.last_error = msg
        set_last_error(p.name, msg)
        return None
    p.last_error = ""
    clear_errors(p.name)
    return text


RETRY_ATTEMPTS = 2        # چند دور کامل روی همه‌ی سرویس‌ها تلاش شود
RETRY_DELAY = 3.0         # فاصله‌ی بین دورها (ثانیه)


async def _try_once(providers: list[Provider], messages: list[dict], *,
                    max_tokens: int | None, temperature: float | None,
                    model: str | None) -> tuple[str | None, str | None]:
    """یک دور چرخش روی همه‌ی سرویس‌ها"""
    attempts = 0
    for p in providers:
        if model:
            p.model = model
        for key in (p.available_keys() or [""]):
            if attempts >= MAX_ATTEMPTS:
                break
            attempts += 1
            try:
                text = await _call_provider(
                    p, key, messages,
                    max_tokens or p.max_tokens,
                    p.temperature if temperature is None else temperature,
                )
                if text:
                    logger.info(f"AI reply via {p.name} ({p.model})")
                    return text, p.name
            except Exception as e:
                hx = _http()
                if hx and isinstance(e, (hx.TimeoutException, hx.TransportError)):
                    msg = f"خطای شبکه/Timeout ({type(e).__name__})"
                    logger.warning(f"{p.name}: {msg}")
                    p.cooldown(key, 45)
                else:
                    msg = f"{type(e).__name__}: {e}"
                    logger.error(f"{p.name}: {msg}")
                p.last_error = msg
                set_last_error(p.name, msg)
        if attempts >= MAX_ATTEMPTS:
            break
    return None, None


async def chat(messages: list[dict], *, providers: list[Provider] | None = None,
               config: dict | None = None, max_tokens: int | None = None,
               temperature: float | None = None, model: str | None = None,
               attempts: int = RETRY_ATTEMPTS,
               delay: float = RETRY_DELAY) -> tuple[str | None, str | None]:
    """
    فراخوانی مدل با چرخش خودکار + چند دور تلاش با فاصله.

    کاربر: «۲ بار تلاش کن با فاصله مثلاً ۳ ثانیه که جواب حتماً ارسال شود.»
    دور اول: سرویس‌های سالم. اگر همه شکست خوردند → چند ثانیه صبر → دور دوم
    روی همه (کول‌داون‌ها نادیده گرفته می‌شوند، چون ممکن است موقتی باشند).

    خروجی: (متن پاسخ، نام provider) — اگر همه شکست بخورند (None, None)
    """
    if providers is None:
        providers = load_providers(config)

    rounds = max(1, int(attempts or 1))
    usable = [p for p in providers if not p.blocked]
    if not usable:
        usable = list(providers)        # همه در کول‌داون: در دور دوم امتحان می‌کنیم

    for rnd in range(rounds):
        pool = usable if rnd == 0 else list(providers)
        if rnd:
            await asyncio.sleep(max(0.0, float(delay or 0)))
            logger.info(f"AI retry round {rnd + 1}/{rounds} after {delay}s")
            for p in pool:
                # کول‌داون موقتی (429/5xx) را فقط برای همین دور صفر کن؛
                # خطای قطعی (کلید/مدل غلط) دست‌نخورده می‌ماند تا اسپم نشود
                now = time.monotonic()
                cools = getattr(p, "_cooldown_until", None)
                if isinstance(cools, dict):
                    for k, until in list(cools.items()):
                        if until - now <= COOLDOWN_AFTER_ERROR + 1:
                            cools.pop(k, None)
        if not pool:
            continue
        text, name = await _try_once(
            pool, messages, max_tokens=max_tokens,
            temperature=temperature, model=model,
        )
        if text:
            return text, name

    logger.error("no AI provider answered after retries")
    return None, None


def status_lines(providers: list[Provider]) -> list[str]:
    """خطوط وضعیت برای نمایش در پیوی ربات"""
    if not providers:
        return ["❌ هیچ سرویس AI تنظیم نشده — <code>AI_PROVIDERS</code> را در .env پر کن."]
    out = []
    for p in providers:
        state = "🔴 در کول‌داون" if p.blocked else "🟢 آماده"
        out.append(
            f"{state} <b>{p.name}</b> — <code>{p.model or '؟'}</code> "
            f"({len(p.keys)} کلید، اولویت {p.priority})"
        )
        err = p.last_error or LAST_ERRORS.get(p.name)
        if err:
            out.append(f"   ↳ ⚠️ {_esc(err)}")
        if p.keys:
            out.append(f"   ↳ کلید: <code>{mask_key(p.keys[0])}</code>")
    return out
