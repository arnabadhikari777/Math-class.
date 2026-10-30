# -*- coding: utf-8 -*-
"""Server-side AI provider calls with automatic fallback.

Everything is driven by .env (see .env.example). Keys never reach the browser.

  <NAME>_API_KEY      - put a key to switch that AI ON; leave it empty and that AI is simply OFF.
  AI_NAME_<REAL AI>   - the name STUDENTS see for that AI, e.g.  AI_NAME_GEMINI=Anubhab Dutta
                        (left side = the real AI so you always know which is which; students
                        never see the real name). Two AIs with the SAME name become ONE button:
                        the first is tried first, the other is its backup.
                        A key without a name still works, shown as "AI <number>".
  AI_PROVIDER_ORDER   - optional: only these AIs, in this order (also the order of the buttons).

If an AI fails (timeout, network, 429, 5xx, empty answer) the next one is tried automatically.
Error text is never forwarded to students.
"""
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
QWEN_DIRECT_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

# ---------------------------------------------------------------------------
# Real providers.  kind: gemini | anthropic | openai (= OpenAI-compatible chat/completions).
# The model of every provider can be changed from .env with its <model_env> variable.
# ---------------------------------------------------------------------------
PROVIDERS = {
    "gemini": dict(kind="gemini", label="Gemini", key_env="GEMINI_API_KEY",
                   model_env="GEMINI_MODEL", model="gemini-3.5-flash-lite"),
    # --- Groq: ONE key (GROQ_API_KEY) runs gpt-oss and compound (and Qwen if QWEN_API_KEY is empty) ---
    "gpt-oss": dict(kind="openai", label="Groq \u00b7 gpt-oss", url=GROQ_URL, key_env="GROQ_API_KEY",
                    model_env="GPT_OSS_MODEL", model="openai/gpt-oss-120b"),
    # Qwen: with QWEN_API_KEY it talks to Alibaba directly; otherwise it is run on Groq (GROQ_API_KEY).
    "qwen": dict(kind="openai", label="Qwen", url=QWEN_DIRECT_URL, key_env="QWEN_API_KEY",
                 url_env="QWEN_BASE_URL", model_env="QWEN_MODEL", model="qwen-plus",
                 groq_fallback=dict(label="Groq \u00b7 Qwen", model="qwen/qwen3.8-27b")),
    "groq": dict(kind="openai", label="Groq \u00b7 compound", url=GROQ_URL, key_env="GROQ_API_KEY",
                 model_env="GROQ_COMPOUND_MODEL", model="groq/compound"),
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
    # any other OpenAI-compatible service: set CUSTOM_AI_BASE_URL + CUSTOM_AI_API_KEY + CUSTOM_AI_MODEL
    "custom": dict(kind="openai", label="Custom AI", url_env="CUSTOM_AI_BASE_URL", key_env="CUSTOM_AI_API_KEY",
                   model_env="CUSTOM_AI_MODEL", model=""),
}
REAL_LABELS = {k: v["label"] for k, v in PROVIDERS.items()}

# Names students see when .env has no AI_NAME_<PROVIDER> for these four (the old built-in behaviour).
_DEFAULT_NAMES = {"gemini": "Anubhab Dutta", "gpt-oss": "Arnab Adhikari", "qwen": "Qwen", "groq": "Compound"}
# Stable, harmless ids sent to the browser (never the real provider name).
_LEGACY_IDS = {"gemini": "anubhab", "gpt-oss": "arnab"}


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


# ------------------------------------------------------------------ what students see
def _index(provider):
    return list(PROVIDERS).index(provider) + 1


def _choice_id(provider):
    return _LEGACY_IDS.get(provider) or f"ai{_index(provider)}"


def provider_name(provider):
    """Student-facing name of one real provider: AI_NAME_<PROVIDER> > built-in default > 'AI <n>'."""
    env_key = "AI_NAME_" + provider.upper().replace("-", "_")
    name = " ".join(_env(env_key).split()) or _DEFAULT_NAMES.get(provider) or f"AI {_index(provider)}"
    return name


def _choices_table():
    """[(choice_id, student name, (real providers...)), ...]  Same name => one merged choice."""
    wanted = [p for p in _csv(_env("AI_PROVIDER_ORDER")) if p in PROVIDERS]
    ordered = wanted + [p for p in PROVIDERS if p not in wanted]
    groups = {}
    for p in ordered:
        nm = provider_name(p)
        groups.setdefault(nm.lower(), [nm, []])[1].append(p)
    return [(_choice_id(provs[0]), nm, tuple(provs)) for nm, provs in groups.values()]


def alias_of(provider_name_):
    """(choice_id, student-facing name) for a real provider, or (None, None)."""
    for cid, label, provs in _choices_table():
        if provider_name_ in provs:
            return cid, label
    return None, None


def student_choices():
    """Only the choices that have at least one working key. Never contains real names."""
    order = configured_order()
    return [{"id": cid, "label": label} for cid, label, provs in _choices_table()
            if any(p in order for p in provs)]


def real_label(provider_name_):
    """Real provider label - for the teacher's AI Chats page only."""
    return REAL_LABELS.get(provider_name_, provider_name_ or "")


# ------------------------------------------------------------------ provider config
def _route(name):
    """Resolve url / key / model for one provider from the current environment."""
    spec = PROVIDERS[name]
    key = _env(spec["key_env"])
    url = (_env(spec["url_env"]) if spec.get("url_env") else "") or spec.get("url", "")
    model = _env(spec["model_env"])
    if name == "qwen" and not key and _env("GROQ_API_KEY"):
        key, url = _env("GROQ_API_KEY"), GROQ_URL
        model = model or spec["groq_fallback"]["model"]
    elif name == "custom":
        url = _env(spec["url_env"]).rstrip("/")
    return key, url.rstrip("/") if name == "custom" else url, model or spec["model"]


def _usable(name):
    key, url, model = _route(name)
    if not key:
        return False
    if PROVIDERS[name]["kind"] == "openai" and not url:
        return False
    return bool(model)


def configured_order():
    """Providers that will actually be tried, in order (only those with a key)."""
    wanted = _csv(_env("AI_PROVIDER_ORDER")) or list(PROVIDERS)
    seen, out = set(), []
    for p in wanted:
        if p in PROVIDERS and p not in seen and _usable(p):
            seen.add(p)
            out.append(p)
    return out


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
    key, _url, model = _route(name)
    payload = {"contents": [{"role": "model" if t["role"] == "assistant" else "user",
                             "parts": [{"text": t["text"]}]} for t in turns],
               "generationConfig": {"maxOutputTokens": 1024}}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    r = requests.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key},
                      json=payload, timeout=timeout)
    if r.status_code != 200:
        raise _ProviderFailed(f"{name} http {r.status_code}")
    cand = (r.json().get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])).strip()
    if not text:
        raise _ProviderFailed(f"{name} empty")
    return text


def _ask_anthropic(name, system, turns, timeout):
    key, _url, model = _route(name)
    payload = {"model": model, "max_tokens": 1024,
               "messages": [{"role": t["role"], "content": t["text"]} for t in turns]}
    if system:
        payload["system"] = system
    r = requests.post(ANTHROPIC_URL,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
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
    """Groq, Qwen, OpenAI, DeepSeek, Mistral, OpenRouter, xAI, Together, Cerebras, Perplexity, custom."""
    key, url, model = _route(name)
    messages = [{"role": "system", "content": system}] if system else []
    messages += [{"role": t["role"], "content": t["text"]} for t in turns]
    # uniform OpenAI-compatible payload: no model-specific extras that could cause a 400
    payload = {"model": model, "messages": messages,
               PROVIDERS[name].get("tokens_param", "max_tokens"): 1024}
    if name == "custom" and not url.endswith("/chat/completions"):
        url += "/chat/completions"
    r = requests.post(url, headers={"Authorization": "Bearer " + key}, json=payload, timeout=timeout)
    if r.status_code != 200:
        raise _ProviderFailed(f"{name} http {r.status_code}")
    text = str(((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not text:
        raise _ProviderFailed(f"{name} empty")
    return text


_CALLERS = {"gemini": _ask_gemini, "anthropic": _ask_anthropic, "openai": _ask_openai_compat}


def ask(system, turns, prefer=None):
    """turns: [{"role": "user"|"assistant", "text": str}] ending with a user turn.
    system: str, or a function(provider_name) -> str (persona matches who answers).
    prefer: a student choice id - its AI(s) are tried first, the rest stay as automatic fallback.
    Returns (text, provider_name). Raises AIError."""
    order = configured_order()
    if prefer:
        group = next((provs for cid, _l, provs in _choices_table() if cid == prefer), ())
        first = [p for p in order if p in group]
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
            # class (+ status for our own error) only; never URL, headers or key
            detail = str(exc)[:60] if isinstance(exc, _ProviderFailed) else type(exc).__name__
            log.warning("AI provider %s failed: %s", name, detail)
    raise AIError("ai_unavailable", "The AI is busy or not responding. Please try again in a moment.", 503)
