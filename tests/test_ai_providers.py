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
            self.assertEqual(provider.student_choices(), [])

    def test_defaults_keep_old_ids_and_names(self):
        with run_env({"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"}):
            self.assertEqual(provider.student_choices(), [
                {"id": "anubhab", "label": "Anubhab Dutta"}, {"id": "arnab", "label": "Arnab Adhikari"},
                {"id": "ai3", "label": "Qwen"}, {"id": "ai4", "label": "Compound"}])
            self.assertEqual(provider.configured_order(), ["gemini", "gpt-oss", "qwen", "groq"])

    def test_name_comes_from_env_keyed_by_real_provider(self):
        with run_env({"GEMINI_API_KEY": "g", "AI_NAME_GEMINI": "Teacher Bot"}):
            self.assertEqual(provider.student_choices(), [{"id": "anubhab", "label": "Teacher Bot"}])

    def test_each_key_switches_only_its_own_ai_on(self):
        env = {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "AI_NAME_OPENAI": "Rahul Sen",
               "AI_NAME_MISTRAL": "Priya Das"}          # mistral has a name but NO key
        with run_env(env):
            self.assertEqual(labels(), ["Anubhab Dutta", "Rahul Sen"])
            self.assertEqual(provider.alias_of("openai"), ("ai5", "Rahul Sen"))

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


if __name__ == "__main__":
    unittest.main()
