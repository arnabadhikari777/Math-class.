# -*- coding: utf-8 -*-
"""Server-side AI provider calls with automatic fallback.

Keys come ONLY from environment variables / .env (never sent to the browser).
Providers are tried in AI_PROVIDER_ORDER; a provider without a key is skipped.
If one fails (timeout, network, 429, 5xx, empty answer) the next is tried.
Error text is never forwarded to users.

Student-facing labels can be set in .env:
  AI_LABEL_GEMINI=Anubhab Dutta
  AI_LABEL_GPT_OSS=Arnab Adhikari
  AI_LABEL_QWEN=Qwen
  AI_LABEL_GROQ=Compound
"""
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Real provider id -> student-facing choice id (stable) + default label
# Each configured provider appears as its own choice so multi-AI works fully.
_PROVIDER_META = (
    # (provider_name, choice_id, default_label, env_label_key)
    ("gemini", "anubhab", "Anubhab Dutta", "AI_LABEL_GEMINI"),
    ("gpt-oss", "arnab", "Arnab Adhikari", "AI_LABEL_GPT_OSS"),
    ("qwen", "qwen", "Qwen", "AI_LABEL_QWEN"),
    ("groq", "compound", "Compound", "AI_LABEL_GROQ"),
)

REAL_LABELS = {
    "gemini": "Gemini",
    "gpt-oss": "Groq · gpt-oss",
    "qwen": "Groq · Qwen",
    "groq": "Groq · compound",
}


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _label_for(provider_name, default_label, env_key):
    custom = _env(env_key)
    return custom if custom else default_label


def _choices_table():
    """Build (choice_id, label, (provider,)) for every known provider."""
    out = []
    for prov, cid, default, env_key in _PROVIDER_META:
        out.append((cid, _label_for(prov, default, env_key), (prov,)))
    return tuple(out)


# Kept for any code that still imports CHOICES (tests / admin)
CHOICES = _choices_table()


def alias_of(provider_name):
    """(choice_id, student-facing name) for a real provider, or (None, None)."""
    for cid, label, provs in _choices_table():
        if provider_name in provs:
            return cid, label
    return None, None


def student_choices():
    """One choice per configured provider that has a working API key."""
    order = configured_order()
    result = []
    for cid, label, provs in _choices_table():
        if any(p in order for p in provs):
            result.append({"id": cid, "label": label})
    return result


def real_label(provider_name):
    return REAL_LABELS.get(provider_name, provider_name or "")


class AIError(Exception):
    """code is machine readable; message is safe to show to the student."""

    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class _ProviderFailed(Exception):
    pass


def _models():
    return {
        "gemini": _env("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        "gpt-oss": _env("GPT_OSS_MODEL", "openai/gpt-oss-120b"),
        "qwen": _env("QWEN_MODEL", "qwen/qwen3.8-27b"),
        "groq": _env("GROQ_COMPOUND_MODEL", "groq/compound"),
    }


def _key_for(name):
    return _env("GEMINI_API_KEY") if name == "gemini" else _env("GROQ_API_KEY")


def configured_order():
    """Providers that will actually be tried, in order."""
    known = ("gemini", "gpt-oss", "qwen", "groq")
    wanted = [p.strip().lower() for p in _env("AI_PROVIDER_ORDER", "gemini,gpt-oss,qwen,groq").split(",")]
    return [p for p in wanted if p in known and _key_for(p)]


def is_enabled():
    if _env("AI_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    return bool(configured_order())


def _timeout():
    try:
        return max(5, min(60, int(_env("AI_TIMEOUT_SECONDS", "20"))))
    except ValueError:
        return 20


def _ask_gemini(system, turns, timeout):
    payload = {
        "contents": [
            {
                "role": "model" if t["role"] == "assistant" else "user",
                "parts": [{"text": t["text"]}],
            }
            for t in turns
        ],
        "generationConfig": {"maxOutputTokens": 1024},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    r = requests.post(
        GEMINI_URL.format(model=_models()["gemini"]),
        headers={"x-goog-api-key": _key_for("gemini")},
        json=payload,
        timeout=timeout,
    )
    if r.status_code != 200:
        raise _ProviderFailed(f"gemini http {r.status_code}")
    data = r.json()
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(
        p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])
    ).strip()
    if not text:
        raise _ProviderFailed("gemini empty")
    return text


def _ask_groq(name, system, turns, timeout):
    messages = [{"role": "system", "content": system}] if system else []
    messages += [{"role": t["role"], "content": t["text"]} for t in turns]
    # Uniform OpenAI-compatible payload — no model-specific params that can 400
    payload = {
        "model": _models()[name],
        "messages": messages,
        "max_tokens": 1024,
    }
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": "Bearer " + _key_for(name)},
        json=payload,
        timeout=timeout,
    )
    if r.status_code != 200:
        raise _ProviderFailed(f"groq/{name} http {r.status_code}")
    text = str(
        ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ).strip()
    if not text:
        raise _ProviderFailed(f"groq/{name} empty")
    return text


def ask(system, turns, prefer=None):
    """turns: [{"role": "user"|"assistant", "text": str}] ending with a user turn.
    system: str, or a function(provider_name) -> str.
    prefer: a student choice id — that provider is tried first, then the rest as fallback.
    Returns (text, provider_name). Raises AIError."""
    order = configured_order()
    if prefer:
        # Map choice id -> its provider(s), put those first
        first_group = next(
            (provs for cid, _l, provs in _choices_table() if cid == prefer), ()
        )
        first = [p for p in order if p in first_group]
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
            text = (
                _ask_gemini(sys_text, turns, t)
                if name == "gemini"
                else _ask_groq(name, sys_text, turns, t)
            )
            return text, name
        except (_ProviderFailed, requests.RequestException, ValueError) as exc:
            detail = (
                str(exc)[:60]
                if isinstance(exc, _ProviderFailed)
                else type(exc).__name__
            )
            log.warning("AI provider %s failed: %s", name, detail)
    raise AIError(
        "ai_unavailable",
        "The AI is busy or not responding. Please try again in a moment.",
        503,
    )
