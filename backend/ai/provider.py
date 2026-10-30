# -*- coding: utf-8 -*-
"""Server-side AI provider calls with automatic fallback.

Keys come ONLY from environment variables / .env (never sent to the browser).
Providers are tried in AI_PROVIDER_ORDER (default "gemini,gpt-oss"); a provider
without a key is skipped. If one fails (timeout, network, 429, 5xx, empty
answer) the next is tried. Error text is never forwarded to users.
"""
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class AIError(Exception):
    """code is machine readable; message is safe to show to the student."""

    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class _ProviderFailed(Exception):
    pass


def _env(name, default=""):
    return os.environ.get(name, default).strip()


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
    wanted = [p.strip().lower() for p in _env("AI_PROVIDER_ORDER", "gemini,gpt-oss").split(",")]
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
    payload = {"contents": [{"role": "model" if t["role"] == "assistant" else "user",
                             "parts": [{"text": t["text"]}]} for t in turns],
               "generationConfig": {"maxOutputTokens": 1024}}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    r = requests.post(GEMINI_URL.format(model=_models()["gemini"]),
                      headers={"x-goog-api-key": _key_for("gemini")},
                      json=payload, timeout=timeout)
    if r.status_code != 200:
        raise _ProviderFailed(f"gemini http {r.status_code}")
    data = r.json()
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])).strip()
    if not text:
        raise _ProviderFailed("gemini empty")
    return text


def _ask_groq(name, system, turns, timeout):
    messages = [{"role": "system", "content": system}] if system else []
    messages += [{"role": t["role"], "content": t["text"]} for t in turns]
    payload = {"model": _models()[name], "messages": messages}
    if name == "qwen":
        payload["max_completion_tokens"] = 800
        payload["reasoning_effort"] = "none"
    else:
        payload["max_tokens"] = 1024
    r = requests.post(GROQ_URL, headers={"Authorization": "Bearer " + _key_for(name)},
                      json=payload, timeout=timeout)
    if r.status_code != 200:
        raise _ProviderFailed(f"groq http {r.status_code}")
    text = str(((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not text:
        raise _ProviderFailed("groq empty")
    return text


def ask(system, turns):
    """turns: [{"role": "user"|"assistant", "text": str}] ending with a user turn.
    Returns (text, provider_name). Raises AIError."""
    order = configured_order()
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
            text = _ask_gemini(system, turns, t) if name == "gemini" else _ask_groq(name, system, turns, t)
            return text, name
        except (_ProviderFailed, requests.RequestException, ValueError) as exc:
            # class (+ status for our own _ProviderFailed) only; never URL, headers or key
            detail = str(exc)[:60] if isinstance(exc, _ProviderFailed) else type(exc).__name__
            log.warning("AI provider %s failed: %s", name, detail)
    raise AIError("ai_unavailable", "The AI is busy or not responding. Please try again in a moment.", 503)
