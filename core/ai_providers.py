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

REQUEST_TIMEOUT = 45.0          # ثانیه
COOLDOWN_AFTER_ERROR = 90       # ثانیه — چقدر یک کلید/سرویس خطادار کنار برود
MAX_ATTEMPTS = 6                # سقف تلاش در یک فراخوانی (ضد حلقه)


class Provider:
    """یک سرویس + کلیدهایش"""

    def __init__(self, name: str, kind: str, base_url: str, model: str,
                 keys: list[str], priority: int = 0, max_tokens: int = 400,
                 temperature: float = 0.8, extra: dict | None = None):
        self.name = name
        self.kind = (kind or "openai").lower()
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.keys = [k for k in (keys or []) if k]
        self.priority = priority
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.extra = extra or {}
        self._cooldown_until: dict[str, float] = {}
        self._key_index = 0

    # ── کلیدها ──
    def available_keys(self) -> list[str]:
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
        if not keys and d.get("kind") != "ollama":
            return None
        return Provider(
            name=d.get("name") or d.get("kind") or "provider",
            kind=d.get("kind", "openai"),
            base_url=d.get("base_url") or _default_base(d.get("kind")),
            model=d.get("model") or "",
            keys=keys,
            priority=int(d.get("priority", 0)),
            max_tokens=int(d.get("max_tokens", 400)),
            temperature=float(d.get("temperature", 0.8)),
            extra=d.get("extra") or {},
        )
    except Exception as e:
        logger.warning(f"bad provider config {d.get('name')}: {e}")
        return None


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
    raw = None
    if config and isinstance(config.get("providers"), list) and config["providers"]:
        raw = config["providers"]
    elif config and isinstance(config.get("providers_json"), str):
        try:
            raw = json.loads(config["providers_json"])
        except Exception:
            raw = None
    if raw is None:
        env_json = os.getenv("AI_PROVIDERS", "").strip()
        if env_json:
            try:
                raw = json.loads(env_json)
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
        key = os.getenv("AI_API_KEY", "").strip()
        base = os.getenv("AI_BASE_URL", "").strip()
        model = os.getenv("AI_MODEL", "").strip()
        kind = os.getenv("AI_KIND", "openai").strip() or "openai"
        if model and (key or kind == "ollama"):
            providers.append(Provider(
                name=f"{kind}-default", kind=kind,
                base_url=base or _default_base(kind), model=model,
                keys=[key] if key else [],
            ))

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


def _openai_text(data: dict) -> str:
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
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
        p.cooldown(key, COOLDOWN_AFTER_ERROR if resp.status_code != 429 else 60)
        return None
    if resp.status_code >= 400:
        body = resp.text[:200].replace("\n", " ")
        logger.error(f"{p.name} HTTP {resp.status_code}: {body}")
        # کلید اشتباه/مدل اشتباه → کول‌داون طولانی‌تر
        if resp.status_code in (401, 403, 404):
            p.cooldown(key, 600)
        return None

    data = resp.json()
    text = _gemini_text(data) if p.kind == "gemini" else _openai_text(data)
    return text or None


async def chat(messages: list[dict], *, providers: list[Provider] | None = None,
               config: dict | None = None, max_tokens: int | None = None,
               temperature: float | None = None, model: str | None = None) -> tuple[str | None, str | None]:
    """
    فراخوانی مدل با چرخش خودکار.
    خروجی: (متن پاسخ، نام provider) — اگر همه شکست بخورند (None, None)
    """
    if providers is None:
        providers = load_providers(config)

    usable = [p for p in providers if not p.blocked]
    if not usable:
        logger.error("no AI provider available (all cooling down / no keys)")
        return None, None

    attempts = 0
    for p in usable:
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
                    logger.warning(f"{p.name}: network error ({type(e).__name__})")
                    p.cooldown(key, 45)
                else:
                    logger.error(f"{p.name}: {type(e).__name__}: {e}")
        if attempts >= MAX_ATTEMPTS:
            break

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
    return out
