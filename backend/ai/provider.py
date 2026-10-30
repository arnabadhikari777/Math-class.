# -*- coding: utf-8 -*-
"""Server-side AI provider calls with automatic fallback.

Keys come ONLY from environment variables / .env (never sent to the browser).

Two layers:
  * PROVIDERS  - the real AI services (Gemini, Groq models, OpenAI, Claude, DeepSeek ...).
                 A provider without an API key in .env is simply skipped.
  * CHOICES    - what STUDENTS see: a display name + the real provider(s) behind it.
                 The name for each real provider is editable from .env (AI_NAME_<PROVIDER>), no code change.

.env controls (all optional; defaults reproduce the old behaviour exactly):
  AI_NAME_<REAL_PROVIDER>=Name students see
        e.g. AI_NAME_GEMINI=Anubhab Dutta      AI_NAME_OPENAI=Rahul Sen
        (use the real provider name; for "gpt-oss" write AI_NAME_GPT_OSS)
        Providers given the SAME name become ONE choice for students (the first one is
        preferred, the others are its fallback). A provider with no name is never used.
  AI_PROVIDER_ORDER=...   optional: global try/fallback order and order of the student buttons
Providers are tried in order; if one fails (timeout, network, 429, 5xx, empty answer)
the next is tried. Error text is never forwarded to users.
"""
import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# ---------------------------------------------------------------------------
# Registry of real providers.  kind: gemini | anthropic | openai (OpenAI-compatible)
# Every model can be changed from .env with its <model_env> variable.
# ---------------------------------------------------------------------------
PROVIDERS = {
    "gemini": dict(kind="gemini", label="Gemini", key_env="GEMINI_API_KEY",
                   model_env="GEMINI_MODEL", model="gemini-3.5-flash-lite"),
    # --- Groq (one key, several models) ---
    "gpt-oss": dict(kind="openai", label="Groq \u00b7 gpt-oss", url=GROQ_URL, key_env="GROQ_API_KEY",
                    model_env="GPT_OSS_MODEL", model="openai/gpt-oss-120b"),
    "qwen": dict(kind="openai", label="Groq \u00b7 Qwen", url=GROQ_URL, key_env="GROQ_API_KEY",
                 model_env="QWEN_MODEL", model="qwen/qwen3.8-27b",
                 extra={"max_completion_tokens": 800, "reasoning_effort": "none"}, tokens_param=None),
    "groq": dict(kind="openai", label="Groq \u00b7 compound", url=GROQ_URL, key_env="GROQ_API_KEY",
                 model_env="GROQ_COMPOUND_MODEL", model="groq/compound"),
    # --- new providers (add the key to .env to switch them on) ---
    "openai": dict(kind="openai", label="OpenAI", url="https://api.openai.com/v1/chat/completions",
                   key_env="OPENAI_API_KEY", model_env="OPENAI_MODEL", model="gpt-4o-mini",
                   tokens_param="max_completion_tokens"),
    "anthropic": dict(kind="anthropic", label="Claude (Anthropic)", key_env="ANTHROPIC_API_KEY",
                      model_env="ANTHROPIC_MODEL", model="claude-haiku-5-5"),
    "deepseek": dict(kind="openai", label="DeepSeek", url="https://api.deepseek.com/chat/completions",
                     key_env="DEEPSEEK_API_KEY", model_env="DEEPSEEK_MODEL", model="deepseek-chat"),
    "mistral": dict(kind="openai", label="Mistral", url="https://api.mistral.ai/v1/chat/completions",
                    key_env="MISTRAL_API_KEY", model_env="MISTRAL_MODEL", model="mistral-small-latest"),
    "openrouter": dict(kind="openai", label="OpenRouter", url="https://openrouter.ai/api/v1/chat/completions",
                       key_env="OPENROUTER_API_KEY", model_env="OPENROUTER_MODEL", model="openrouter/auto"),
    "xai": dict(kind="openai", label="xAI Grok", url="https://api.x.ai/v1/chat/completions",
                key_env="XAI_API_KEY", model_env="XAI_MODEL", model="grok-4"),
    "together": dict(kind="openai", label="Together AI", url="https://api.together.xyz/v1/chat/completions",
                     key_env="TOGETHER_API_KEY", model_env="TOGETHER_MODEL",
                     model="meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "cerebras": dict(kind="openai", label="Cerebras", url="https://api.cerebras.ai/v1/chat/completions",
                     key_env="CEREBRAS_API_KEY", model_env="CEREBRAS_MODEL", model="llama-3.3-70b"),
    "perplexity": dict(kind="openai", label="Perplexity", url="https://api.perplexity.ai/chat/completions",
                       key_env="PERPLEXITY_API_KEY", model_env="PERPLEXITY_MODEL", model="sonar"),
    # --- any other OpenAI-compatible service: set the three CUSTOM_AI_* variables ---
    "custom": dict(kind="openai", label="Custom AI", url_env="CUSTOM_AI_BASE_URL", key_env="CUSTOM_AI_API_KEY",
                   model_env="CUSTOM_AI_MODEL", model=""),
}
KNOWN = tuple(PROVIDERS)

# Student-facing names used when .env has no AI_NAME_<PROVIDER> (= exactly what the site had before).
_DEFAULT_NAMES = {"gemini": "Anubhab Dutta", "gpt-oss": "Arnab Adhikari",
                  "qwen": "Arnab Adhikari", "groq": "Arnab Adhikari"}


class AIError(Exception):
    """code is machine readable; message is safe to show to the student."""

    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class _ProviderFailed(Exception):
    pass


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _csv(value):
    return [p.strip().lower() for p in (value or "").split(",") if p.strip()]


# ------------------------------------------------------------------ choices (student-facing)
def _name_env(provider):
    return "AI_NAME_" + provider.upper().replace("-", "_")


def provider_name(provider):
    """Student-facing name of one real provider (from .env, else the built-in default, else '')."""
    return " ".join((_env(_name_env(provider)) or _DEFAULT_NAMES.get(provider, "")).split())


def _choice_id(name, taken):
    base = re.sub(r"[^a-z0-9]+", "", (name.split() or [""])[0].lower())[:24] or "ai"
    cid, n = base, 2
    while cid in taken:
        cid, n = f"{base}{n}", n + 1
    return cid


def get_choices():
    """[(choice_id, student-facing name, (real providers...)), ...] read from .env each call.
    Providers with the same student-facing name are merged into one choice."""
    wanted = [p for p in _csv(_env("AI_PROVIDER_ORDER")) if p in PROVIDERS]
    ordered = wanted + [p for p in PROVIDERS if p not in wanted]
    groups = {}   # lower-case name -> [display name, [providers]]
    for p in ordered:
        nm = provider_name(p)
        if nm:
            groups.setdefault(nm.lower(), [nm, []])[1].append(p)
    out, taken = [], set()
    for nm, provs in groups.values():
        cid = _choice_id(nm, taken)
        taken.add(cid)
        out.append((cid, nm, tuple(provs)))
    return out


def alias_of(provider_name):
    """(choice_id, student-facing name) for a real provider, or (None, None)."""
    for cid, label, provs in get_choices():
        if provider_name in provs:
            return cid, label
    return None, None


def student_choices():
    """Choices a student may pick: only those with at least one working key. No real names."""
    order = configured_order()
    return [{"id": cid, "label": label} for cid, label, provs in get_choices()
            if any(p in order for p in provs)]


def real_label(provider_name):
    spec = PROVIDERS.get(provider_name)
    return spec["label"] if spec else (provider_name or "")


# ------------------------------------------------------------------ provider config
def _model(name):
    spec = PROVIDERS[name]
    return _env(spec["model_env"], spec["model"])


def _key_for(name):
    return _env(PROVIDERS[name]["key_env"])


def _url_for(name):
    spec = PROVIDERS[name]
    if "url_env" in spec:
        return _env(spec["url_env"]).rstrip("/")
    return spec.get("url", "")


def _usable(name):
    """Has key (and for 'custom' a URL + model)."""
    if not _key_for(name):
        return False
    if name == "custom":
        return bool(_url_for(name) and _model(name))
    return True


def configured_order():
    """Providers that will actually be tried, in order. Only providers that belong to a student
    choice are used, so an answer never comes from an AI the student cannot see a name for."""
    in_choices = []
    for _cid, _l, provs in get_choices():
        for p in provs:
            if p not in in_choices:
                in_choices.append(p)
    wanted = _csv(_env("AI_PROVIDER_ORDER")) or in_choices
    return [p for p in wanted if p in PROVIDERS and p in in_choices and _usable(p)]


def is_enabled():
    if _env("AI_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    return bool(configured_order())


def _timeout():
    try:
        return max(5, min(60, int(_env("AI_TIMEOUT_SECONDS", "20"))))
    except ValueError:
        return 20


# ------------------------------------------------------------------ API calls
def _ask_gemini(name, system, turns, timeout):
    payload = {"contents": [{"role": "model" if t["role"] == "assistant" else "user",
                             "parts": [{"text": t["text"]}]} for t in turns],
               "generationConfig": {"maxOutputTokens": 1024}}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    r = requests.post(GEMINI_URL.format(model=_model(name)),
                      headers={"x-goog-api-key": _key_for(name)},
                      json=payload, timeout=timeout)
    if r.status_code != 200:
        raise _ProviderFailed(f"{name} http {r.status_code}")
    data = r.json()
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])).strip()
    if not text:
        raise _ProviderFailed(f"{name} empty")
    return text


def _ask_anthropic(name, system, turns, timeout):
    payload = {"model": _model(name), "max_tokens": 1024,
               "messages": [{"role": t["role"], "content": t["text"]} for t in turns]}
    if system:
        payload["system"] = system
    r = requests.post(ANTHROPIC_URL,
                      headers={"x-api-key": _key_for(name), "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=payload, timeout=timeout)
    if r.status_code != 200:
        raise _ProviderFailed(f"{name} http {r.status_code}")
    blocks = r.json().get("content") or []
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        raise _ProviderFailed(f"{name} empty")
    return text


def _ask_openai_compat(name, system, turns, timeout):
    """Works for Groq, OpenAI, DeepSeek, Mistral, OpenRouter, xAI, Together, Cerebras, Perplexity, custom."""
    spec = PROVIDERS[name]
    messages = [{"role": "system", "content": system}] if system else []
    messages += [{"role": t["role"], "content": t["text"]} for t in turns]
    payload = {"model": _model(name), "messages": messages}
    tokens_param = spec.get("tokens_param", "max_tokens")
    if tokens_param:
        payload[tokens_param] = 1024
    payload.update(spec.get("extra") or {})
    url = _url_for(name)
    if name == "custom" and not url.endswith("/chat/completions"):
        url += "/chat/completions"
    r = requests.post(url, headers={"Authorization": "Bearer " + _key_for(name)},
                      json=payload, timeout=timeout)
    if r.status_code != 200:
        raise _ProviderFailed(f"{name} http {r.status_code}")
    text = str(((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not text:
        raise _ProviderFailed(f"{name} empty")
    return text


_CALLERS = {"gemini": _ask_gemini, "anthropic": _ask_anthropic, "openai": _ask_openai_compat}


def ask(system, turns, prefer=None):
    """turns: [{"role": "user"|"assistant", "text": str}] ending with a user turn.
    system: str, or a function(provider_name) -> str (so the persona can match who answers).
    prefer: a CHOICES id to try first; the others stay as automatic fallback.
    Returns (text, provider_name). Raises AIError."""
    order = configured_order()
    if prefer:
        first_group = next((provs for cid, _l, provs in get_choices() if cid == prefer), ())
        first = [p for p in first_group if p in order]
        order = first + [p for p in order if p not in first]
    if _env("AI_ENABLED", "1").lower() in ("0", "false", "no") or not order:
        raise AIError("ai_not_configured", "AI assistant is not available right now.", 503)
    timeout = _timeout()
    deadline = time.monotonic() + timeout * 2
    for name in order:
        remaining = deadline - time.monotonic()
        if remaining < 3:
            break
        t = min(timeout, remaining)
        try:
            sys_text = system(name) if callable(system) else system
            text = _CALLERS[PROVIDERS[name]["kind"]](name, sys_text, turns, t)
            return text, name
        except (_ProviderFailed, requests.RequestException, ValueError) as exc:
            # class (+ status for our own _ProviderFailed) only; never URL, headers or key
            detail = str(exc)[:60] if isinstance(exc, _ProviderFailed) else type(exc).__name__
            log.warning("AI provider %s failed: %s", name, detail)
    raise AIError("ai_unavailable", "The AI is busy or not responding. Please try again in a moment.", 503)
