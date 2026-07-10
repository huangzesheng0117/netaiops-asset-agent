# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from netaiops_asset.chat_v2 import llm_intent_planner
from netaiops_asset.chat_v3.response_generator import generate_v3_response
from netaiops_asset.llm.client import LLMClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def make_client(max_tokens=900, model="glm-5.2"):
    client = LLMClient()
    client.enabled = True
    client.base_url = "http://unit.test/v1"
    client.fallback_enabled = False
    client.model = model
    client.api_key = "dummy"
    client.max_tokens = max_tokens
    client.request_retries = 0
    return client


class GLM52CompatTests(unittest.TestCase):
    def test_probe_32_promotes_to_1200(self):
        self._assert_probe_budget(32, 1200)

    def test_probe_900_promotes_to_1200(self):
        self._assert_probe_budget(900, 1200)

    def test_probe_1800_preserved(self):
        self._assert_probe_budget(1800, 1800)

    def _assert_probe_budget(self, configured, expected):
        captured = []

        def fake_post(*args, **kwargs):
            captured.append(kwargs["json"])
            return FakeResponse(
                {
                    "model": "glm-5.2",
                    "choices": [
                        {
                            "message": {"content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        client = make_client(configured)
        with patch.object(requests, "post", side_effect=fake_post):
            result = client.probe()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured[-1]["max_tokens"], expected)
        self.assertEqual(
            captured[-1].get("thinking"),
            {"type": "disabled"},
        )
        self.assertNotIn("response_format", captured[-1])
        self.assertEqual(result["max_tokens_used"], expected)

    def test_success_observability(self):
        payload = {
            "model": "glm-5.2-reported",
            "choices": [
                {
                    "message": {"content": "  OK  "},
                    "finish_reason": "stop",
                }
            ],
        }
        client = make_client(1200)
        with patch.object(requests, "post", return_value=FakeResponse(payload)):
            result = client.chat(
                [{"role": "user", "content": "hello"}],
                response_format=False,
                thinking=False,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["requested_model"], "glm-5.2")
        self.assertEqual(result["reported_model"], "glm-5.2-reported")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["max_tokens_used"], 1200)
        self.assertEqual(result["content"], "OK")
        self.assertEqual(result["content_length"], 2)

    def test_length_empty_content_is_error(self):
        payload = {
            "model": "glm-5.2",
            "choices": [
                {
                    "message": {"content": ""},
                    "finish_reason": "length",
                }
            ],
        }
        client = make_client()
        with patch.object(requests, "post", return_value=FakeResponse(payload)):
            result = client.chat(
                [{"role": "user", "content": "hello"}],
                response_format=False,
                thinking=False,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "LLM_EMPTY_CONTENT")
        self.assertEqual(result["finish_reason"], "length")

    def test_reasoning_only_is_not_final_answer(self):
        payload = {
            "model": "glm-5.2",
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "internal reasoning only",
                    },
                    "finish_reason": "length",
                }
            ],
        }
        client = make_client()
        with patch.object(requests, "post", return_value=FakeResponse(payload)):
            result = client.chat(
                [{"role": "user", "content": "hello"}],
                response_format=False,
                thinking=False,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "LLM_EMPTY_CONTENT")

    def test_choices_missing_is_error(self):
        client = make_client()
        with patch.object(
            requests,
            "post",
            return_value=FakeResponse({"model": "glm-5.2", "choices": []}),
        ):
            result = client.chat(
                [{"role": "user", "content": "hello"}],
                response_format=False,
                thinking=False,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "LLM_EMPTY_CHOICES")

    def test_message_missing_is_error(self):
        client = make_client()
        with patch.object(
            requests,
            "post",
            return_value=FakeResponse(
                {
                    "model": "glm-5.2",
                    "choices": [{"finish_reason": "stop"}],
                }
            ),
        ):
            result = client.chat(
                [{"role": "user", "content": "hello"}],
                response_format=False,
                thinking=False,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "LLM_EMPTY_MESSAGE")

    def test_model_missing_is_explicit_error(self):
        client = make_client(model="")
        result = client.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "LLM_EMPTY_MODEL")

    def test_v3_generator_floor_1200(self):
        self._assert_generator_budget(900, 1200)

    def test_v3_generator_preserves_1800(self):
        self._assert_generator_budget(1800, 1800)

    def _assert_generator_budget(self, configured, expected):
        class FakeClient:
            def __init__(self):
                self.max_tokens = configured
                self.kwargs = None

            def chat(self, messages, **kwargs):
                self.kwargs = kwargs
                return {
                    "status": "ok",
                    "content": (
                        "这是一个足够长的中文回答，用于验证 GLM 5.2 "
                        "响应生成器的输出预算不会低于要求。"
                    ),
                }

        fake = FakeClient()
        generated = generate_v3_response(
            question="解释接口错包排查思路",
            plan={"action": "general_chat"},
            decision={"action": "general_chat"},
            gate={"eligible": True},
            allow_live_llm=True,
            llm_client=fake,
        )
        self.assertTrue(generated.ready)
        self.assertEqual(fake.kwargs["max_tokens"], expected)
        self.assertEqual(
            fake.kwargs["thinking"],
            {"type": "disabled"},
        )
        self.assertFalse(fake.kwargs["response_format"])

    def test_v2_planner_missing_model_does_not_guess_qwen(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text(
                "llm:\n"
                "  base_url: http://unit.test/v1\n"
                "  api_key: dummy\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"NETAIOPS_CONFIG": str(cfg)},
                clear=True,
            ):
                loaded = llm_intent_planner._load_llm_config()

        self.assertFalse(loaded.get("model"))
        self.assertGreaterEqual(int(loaded.get("max_tokens") or 0), 1200)


    def test_legacy_advice_classifier_does_not_steal_general_chat(self):
        import app as app_module

        self.assertFalse(
            app_module._batch67_is_advice_analysis_question(
                "请用三句话解释 BGP 是什么，不要生成命令。"
            )
        )
        self.assertTrue(
            app_module._batch67_is_advice_analysis_question(
                "是否建议在重启 standby 前先隔离流量？只给建议，不要命令。"
            )
        )

    def test_legacy_advice_uses_unified_llm_client(self):
        import app as app_module

        captured = {}

        def fake_chat(client, messages, **kwargs):
            captured["client"] = client
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return {
                "status": "ok",
                "content": "建议先确认主备状态，再评估流量隔离和回退条件。",
                "requested_model": "glm-5.2",
                "reported_model": "glm-5.2",
                "finish_reason": "stop",
                "max_tokens_used": 1200,
                "content_length": 24,
                "http_status": 200,
                "latency_ms": 10,
                "base_url_used": "http://unit.test/v1",
            }

        with patch.object(app_module.LLMClient, "chat", new=fake_chat):
            result = app_module._batch67_call_local_llm_for_advice(
                "是否建议先隔离流量？",
                {"current_device": "device01"},
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["content"])
        self.assertFalse(captured["kwargs"]["response_format"])
        self.assertEqual(
            captured["kwargs"]["thinking"],
            {"type": "disabled"},
        )
        self.assertGreaterEqual(captured["kwargs"]["max_tokens"], 1200)
        self.assertEqual(
            result["observability"]["requested_model"],
            "glm-5.2",
        )
        self.assertEqual(
            result["observability"]["finish_reason"],
            "stop",
        )

    def test_legacy_advice_payload_contains_explicit_thinking_disabled(self):
        import app as app_module

        captured_payloads = []
        client = make_client(1200)

        def fake_post(*args, **kwargs):
            captured_payloads.append(dict(kwargs.get("json") or {}))
            return FakeResponse(
                {
                    "model": "glm-5.2",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "建议先确认主备状态，再评估隔离范围。",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        with (
            patch.object(app_module, "LLMClient", return_value=client),
            patch.object(requests, "post", side_effect=fake_post),
        ):
            result = app_module._batch67_call_local_llm_for_advice(
                "是否建议先隔离流量？",
                {"current_device": "device01"},
            )

        self.assertTrue(result["ok"])
        self.assertTrue(captured_payloads)
        self.assertEqual(
            captured_payloads[-1].get("thinking"),
            {"type": "disabled"},
        )
        self.assertNotIn("response_format", captured_payloads[-1])
        self.assertGreaterEqual(
            int(captured_payloads[-1].get("max_tokens") or 0),
            1200,
        )

    def test_legacy_advice_empty_content_keeps_observability(self):
        import app as app_module

        def fake_chat(client, messages, **kwargs):
            return {
                "status": "error",
                "error_code": "LLM_EMPTY_CONTENT",
                "message": "LLM response content is empty",
                "requested_model": "glm-5.2",
                "reported_model": "glm-5.2",
                "finish_reason": "length",
                "max_tokens_used": 1200,
                "content_length": 0,
                "http_status": 200,
                "latency_ms": 10,
                "base_url_used": "http://unit.test/v1",
            }

        with patch.object(app_module.LLMClient, "chat", new=fake_chat):
            result = app_module._batch67_call_local_llm_for_advice(
                "是否建议先隔离流量？",
                {},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "LLM_EMPTY_CONTENT")
        self.assertEqual(
            result["observability"]["finish_reason"],
            "length",
        )
        self.assertEqual(
            result["observability"]["content_length"],
            0,
        )

    def test_runtime_files_have_no_qwen3_max_default(self):
        project = Path(__file__).resolve().parents[1]
        files = [
            project / "netaiops_asset/chat_v2/llm_intent_planner.py",
            project / "netaiops_asset/chat_v2/llm_evidence_analyzer.py",
            project / "netaiops_asset/chat_v3/response_generator.py",
            project / "netaiops_asset/llm/client.py",
        ]
        for path in files:
            self.assertNotIn(
                "qwen3-max",
                path.read_text(encoding="utf-8"),
                str(path),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
