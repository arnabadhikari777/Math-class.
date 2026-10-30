# -*- coding: utf-8 -*-
"""Unit tests for the env-driven provider/choice layer (no network, no database)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.ai import provider  # noqa: E402


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body or {}

    def json(self):
        return self._b


def run_env(env):
    return mock.patch.dict(os.environ, env, clear=True)


def labels():
    return [c["label"] for c in provider.student_choices()]


class ProviderTests(unittest.TestCase):
    def test_no_keys_means_nothing_works(self):
        with run_env({"AI_NAME_OPENAI": "Rahul Sen"}):
            self.assertFalse(provider.is_enabled())

    def test_defaults_keep_old_ids_and_names(self):
        with run_env({"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"}):
            self.assertEqual(provider.student_choices(), [
                {"id": "anubhab", "label": "Anubhab Dutta"}, {"id": "arnab", "label": "Arnab Adhikari"},
                {"id": "ai3", "label": "Qwen"}, {"id": "ai4", "label": "Compound"}])
            self.assertEqual(provider.configured_order(), ["gemini", "gpt-oss", "qwen", "groq"])

    def test_name_comes_from_env_keyed_by_real_provider(self):
        with run_env({"GEMINI_API_KEY": "g", "AI_NAME_GEMINI": "Teacher Bot"}):
            self.assertEqual(provider.student_choices(), [{"id": "anubhab", "label": "Teacher Bot"}])

    def test_every_saved_name_is_shown_even_without_a_key(self):
        env = {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "AI_NAME_OPENAI": "Rahul Sen",
               "AI_NAME_MISTRAL": "Priya Das"}          # mistral has a name but NO key: still shown
        with run_env(env):
            self.assertEqual(labels(), ["Anubhab Dutta", "Rahul Sen", "Priya Das"])
            self.assertEqual(provider.alias_of("openai"), ("ai5", "Rahul Sen"))
            self.assertEqual(provider.configured_order(), ["gemini", "openai"])   # but only keyed ones answer

    def test_defaults_without_key_or_saved_name_stay_hidden(self):
        with run_env({"GEMINI_API_KEY": "g"}):
            self.assertEqual(labels(), ["Anubhab Dutta"])

    def test_choosing_a_keyless_ai_falls_back_to_a_working_one(self):
        def post(url, headers=None, json=None, timeout=None):
            return Resp(200, {"candidates": [{"content": {"parts": [{"text": "from gemini"}]}}]})
        with run_env({"GEMINI_API_KEY": "g", "AI_NAME_MISTRAL": "Priya Das"}), \
                mock.patch("backend.ai.provider.requests.post", post):
            text, used = provider.ask(None, [{"role": "user", "text": "hi"}], prefer="ai8")
        self.assertEqual(used, "gemini")

    def test_same_name_merges_providers_into_one_choice(self):
        env = {"OPENAI_API_KEY": "o", "DEEPSEEK_API_KEY": "d", "AI_NAME_OPENAI": "Rahul Sen",
               "AI_NAME_DEEPSEEK": "rahul  sen"}
        with run_env(env):
            self.assertEqual(provider.student_choices(), [{"id": "ai5", "label": "Rahul Sen"}])

    def test_key_without_a_name_still_works_with_a_neutral_name(self):
        with run_env({"ANTHROPIC_API_KEY": "a", "AI_NAME_ANTHROPIC": ""}):
            self.assertEqual(provider.student_choices(), [{"id": "ai6", "label": "AI 6"}])

    def test_real_provider_name_never_in_student_choices(self):
        env = {"OPENAI_API_KEY": "o", "DEEPSEEK_API_KEY": "d", "XAI_API_KEY": "x", "GEMINI_API_KEY": "g"}
        with run_env(env):
            blob = repr(provider.student_choices()).lower()
            for real in ("openai", "deepseek", "xai", "gemini", "groq", "mistral"):
                self.assertNotIn(real, blob)

    def test_provider_order_restricts_and_orders(self):
        with run_env({"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "AI_PROVIDER_ORDER": "openai,gemini"}):
            self.assertEqual(provider.configured_order(), ["openai", "gemini"])
            self.assertEqual(labels(), ["AI 5", "Anubhab Dutta"])

    def test_name_can_be_bengali(self):
        with run_env({"OPENAI_API_KEY": "o", "AI_NAME_OPENAI": "রাহুল সেন"}):
            self.assertEqual(labels(), ["রাহুল সেন"])

    def test_qwen_direct_key_vs_groq_fallback(self):
        seen = []

        def post(url, headers=None, json=None, timeout=None):
            seen.append((url, headers["Authorization"], json["model"]))
            return Resp(200, {"choices": [{"message": {"content": "ok"}}]})

        turns = [{"role": "user", "text": "hi"}]
        with run_env({"QWEN_API_KEY": "direct"}), mock.patch("backend.ai.provider.requests.post", post):
            provider.ask(None, turns)
        with run_env({"GROQ_API_KEY": "gq", "AI_PROVIDER_ORDER": "qwen"}), \
                mock.patch("backend.ai.provider.requests.post", post):
            provider.ask(None, turns)
        self.assertIn("dashscope", seen[0][0])
        self.assertEqual((seen[0][1], seen[0][2]), ("Bearer direct", "qwen-plus"))
        self.assertIn("groq.com", seen[1][0])
        self.assertEqual(seen[1][1], "Bearer gq")

    def test_anthropic_call_format(self):
        seen = {}

        def post(url, headers=None, json=None, timeout=None):
            seen.update(url=url, headers=headers, json=json)
            return Resp(200, {"content": [{"type": "text", "text": "hello"}]})

        with run_env({"ANTHROPIC_API_KEY": "a", "AI_NAME_ANTHROPIC": "Rahul Sen"}), \
                mock.patch("backend.ai.provider.requests.post", post):
            text, used = provider.ask("sys", [{"role": "user", "text": "hi"}])
        self.assertEqual((text, used), ("hello", "anthropic"))
        self.assertEqual(seen["headers"]["x-api-key"], "a")
        self.assertEqual(seen["json"]["system"], "sys")

    def test_openai_compatible_and_fallback_inside_one_choice(self):
        calls = []

        def post(url, headers=None, json=None, timeout=None):
            calls.append(url)
            if "deepseek" in url:
                return Resp(500)
            return Resp(200, {"choices": [{"message": {"content": "ok"}}]})

        env = {"DEEPSEEK_API_KEY": "d", "MISTRAL_API_KEY": "m",
               "AI_NAME_DEEPSEEK": "Rahul Sen", "AI_NAME_MISTRAL": "Rahul Sen"}
        with run_env(env), mock.patch("backend.ai.provider.requests.post", post):
            text, used = provider.ask(None, [{"role": "user", "text": "hi"}], prefer="ai8")
        self.assertEqual(used, "mistral")
        self.assertEqual(len(calls), 2)

    def test_custom_provider_needs_url_key_model(self):
        base = {"CUSTOM_AI_API_KEY": "k", "AI_NAME_CUSTOM": "Rahul Sen"}
        with run_env(base):
            self.assertFalse(provider.is_enabled())
        with run_env(dict(base, CUSTOM_AI_BASE_URL="https://x.example/v1", CUSTOM_AI_MODEL="m")):
            self.assertTrue(provider.is_enabled())

    def test_error_reason_is_shown_to_teacher_and_keys_are_redacted(self):
        def post(url, headers=None, json=None, timeout=None):
            return Resp(401, {"error": {"message": "Incorrect API key provided: sk-abc123456789012345678901234xyz"}})
        with run_env({"DEEPSEEK_API_KEY": "sk-abc123456789012345678901234xyz", "AI_NAME_DEEPSEEK": "Rahul"}), \
                mock.patch("backend.ai.provider.requests.post", post):
            rows = provider.diagnose()
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("http 401", rows[0]["detail"])
        self.assertIn("API key is wrong", rows[0]["detail"])
        self.assertNotIn("sk-abc", repr(rows))

    def test_diagnose_reports_ok_no_key_and_blocked_network(self):
        import requests

        def post(url, headers=None, json=None, timeout=None):
            if "deepseek" in url:
                raise requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden")
            return Resp(200, {"choices": [{"message": {"content": "OK"}}]})
        env = {"OPENAI_API_KEY": "o", "DEEPSEEK_API_KEY": "d", "AI_NAME_OPENAI": "A", "AI_NAME_DEEPSEEK": "B",
               "AI_NAME_MISTRAL": "C"}
        with run_env(env), mock.patch("backend.ai.provider.requests.post", post):
            rows = {r["provider"]: r for r in provider.diagnose()}
        self.assertEqual(rows["OpenAI"]["status"], "ok")
        self.assertEqual(rows["DeepSeek"]["status"], "failed")
        self.assertIn("allowlist", rows["DeepSeek"]["detail"])
        self.assertEqual(rows["Mistral"]["status"], "no_key")

    def test_qwen_base_url_override_gets_endpoint_appended(self):
        seen = []

        def post(url, headers=None, json=None, timeout=None):
            seen.append(url)
            return Resp(200, {"choices": [{"message": {"content": "ok"}}]})
        env = {"QWEN_API_KEY": "k", "QWEN_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/"}
        with run_env(env), mock.patch("backend.ai.provider.requests.post", post):
            provider.ask(None, [{"role": "user", "text": "hi"}])
        self.assertEqual(seen[0], "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
