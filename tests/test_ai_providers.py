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


class ProviderTests(unittest.TestCase):
    def test_defaults_match_old_behaviour(self):
        with run_env({"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"}):
            self.assertEqual([c["label"] for c in provider.student_choices()], ["Anubhab Dutta", "Arnab Adhikari"])
            self.assertEqual(provider.configured_order(), ["gemini", "gpt-oss", "qwen", "groq"])

    def test_name_comes_from_env_keyed_by_real_provider(self):
        with run_env({"GEMINI_API_KEY": "g", "AI_NAME_GEMINI": "Teacher Bot"}):
            self.assertEqual(provider.student_choices(), [{"id": "teacher", "label": "Teacher Bot"}])

    def test_new_provider_gets_its_own_choice(self):
        env = {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o", "AI_NAME_OPENAI": "Rahul Sen"}
        with run_env(env):
            self.assertEqual([c["label"] for c in provider.student_choices()], ["Anubhab Dutta", "Rahul Sen"])
            self.assertEqual(provider.alias_of("openai"), ("rahul", "Rahul Sen"))

    def test_same_name_merges_providers_into_one_choice(self):
        env = {"OPENAI_API_KEY": "o", "DEEPSEEK_API_KEY": "d", "AI_NAME_OPENAI": "Rahul Sen",
               "AI_NAME_DEEPSEEK": "rahul  sen"}
        with run_env(env):
            self.assertEqual(provider.student_choices(), [{"id": "rahul", "label": "Rahul Sen"}])

    def test_choice_without_key_is_hidden(self):
        with run_env({"GEMINI_API_KEY": "g", "AI_NAME_MISTRAL": "Rahul Sen"}):
            self.assertEqual([c["id"] for c in provider.student_choices()], ["anubhab"])

    def test_provider_without_a_name_is_never_used(self):
        with run_env({"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"}):
            self.assertNotIn("openai", provider.configured_order())

    def test_name_can_be_bengali_and_id_stays_safe(self):
        with run_env({"OPENAI_API_KEY": "o", "AI_NAME_OPENAI": "রাহুল সেন"}):
            self.assertEqual(provider.student_choices(), [{"id": "ai", "label": "রাহুল সেন"}])

    def test_gpt_oss_env_name(self):
        with run_env({"GROQ_API_KEY": "q", "AI_NAME_GPT_OSS": "Sourav", "AI_NAME_QWEN": "", "AI_NAME_GROQ": ""}):
            # empty value falls back to the built-in default for qwen/groq, gpt-oss is renamed
            labels = [c["label"] for c in provider.student_choices()]
            self.assertEqual(labels, ["Sourav", "Arnab Adhikari"])

    def test_anthropic_call_format(self):
        seen = {}

        def post(url, headers=None, json=None, timeout=None):
            seen.update(url=url, headers=headers, json=json)
            return Resp(200, {"content": [{"type": "text", "text": "hello"}]})

        env = {"ANTHROPIC_API_KEY": "a", "AI_NAME_ANTHROPIC": "Rahul Sen"}
        with run_env(env), mock.patch("backend.ai.provider.requests.post", post):
            text, used = provider.ask("sys", [{"role": "user", "text": "hi"}])
        self.assertEqual((text, used), ("hello", "anthropic"))
        self.assertEqual(seen["headers"]["x-api-key"], "a")
        self.assertEqual(seen["json"]["system"], "sys")

    def test_openai_compatible_and_fallback(self):
        calls = []

        def post(url, headers=None, json=None, timeout=None):
            calls.append(url)
            if "deepseek" in url:
                return Resp(500)
            return Resp(200, {"choices": [{"message": {"content": "ok"}}]})

        env = {"DEEPSEEK_API_KEY": "d", "MISTRAL_API_KEY": "m",
               "AI_NAME_DEEPSEEK": "Rahul Sen", "AI_NAME_MISTRAL": "Rahul Sen"}
        with run_env(env), mock.patch("backend.ai.provider.requests.post", post):
            text, used = provider.ask(None, [{"role": "user", "text": "hi"}], prefer="rahul")
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
