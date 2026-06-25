# -*- coding: utf-8 -*-
"""
Regression tests for V3 Intent Arbiter.

Default tests use fake LLM clients and do not call production LLM.
Set RUN_V3_LIVE_LLM_TEST=1 to run one live LLM smoke test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.intent_arbiter import IntentArbiter, parse_json_from_text
from netaiops_asset.chat_v3.intent_schema import IntentAction


class FakeLLMClient:
    def __init__(self, content: str, status: str = "ok", http_status: int = 200) -> None:
        self.content = content
        self.status = status
        self.http_status = http_status
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages: List[Dict[str, str]], **overrides: Any) -> Dict[str, Any]:
        self.calls.append({"messages": messages, "overrides": overrides})
        if self.status != "ok":
            return {
                "status": self.status,
                "http_status": self.http_status,
                "error_code": "FAKE_ERROR",
                "message": "fake llm error",
            }
        return {
            "status": "ok",
            "http_status": self.http_status,
            "latency_ms": 12,
            "model": "fake-qwen",
            "content": self.content,
            "base_url_used": "fake://llm",
        }


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def test_parse_json_from_plain_json() -> None:
    data = parse_json_from_text('{"action":"general_chat","confidence":0.9}')
    assert_true(isinstance(data, dict), "plain json should parse")
    assert_true(data["action"] == "general_chat", "action mismatch")


def test_parse_json_from_markdown_fence() -> None:
    data = parse_json_from_text('```json\n{"action":"advice_analysis","confidence":0.88}\n```')
    assert_true(isinstance(data, dict), "fenced json should parse")
    assert_true(data["action"] == "advice_analysis", "action mismatch")


def test_execute_provided_commands_and_analyze() -> None:
    content = json.dumps({
        "schema_version": "v3_intent_arbiter_1",
        "action": "execute_provided_commands_and_analyze",
        "confidence": 0.96,
        "device_required": True,
        "device_hint": "SH16-H05-INT-EDG-SW01",
        "commands_provided": True,
        "commands": ["show clock", "show version"],
        "need_existing_evidence": False,
        "should_generate_commands": False,
        "should_execute_commands": True,
        "should_analyze_after_execution": True,
        "requires_confirmation": False,
        "clarification_question": "",
        "reason": "用户提供命令并要求执行后分析",
    }, ensure_ascii=False)

    decision = IntentArbiter(FakeLLMClient(content)).decide(
        "请执行 show clock show version 并分析",
        context={},
        user="baoleiji",
        conversation_id="conv-test",
    )

    assert_true(decision.action == IntentAction.execute_provided_commands_and_analyze, "wrong action")
    assert_true(decision.commands == ["show clock", "show version"], "commands mismatch")
    assert_true(decision.requires_confirmation is False, "provided commands must not require confirmation")
    assert_true(decision.should_execute_commands is True, "should_execute_commands should be true")
    assert_true(decision.should_analyze_after_execution is True, "should analyze after execution")
    assert_true(decision.metadata["llm_confidence"] == 0.96, "llm_confidence mismatch")
    assert_true(decision.metadata["effective_confidence"] == 0.96, "effective_confidence mismatch")


def test_action_alias_normalization() -> None:
    content = json.dumps({
        "action": "execute_and_analyze",
        "confidence": 0.91,
        "commands": ["show logging last 100"],
        "reason": "alias test",
    }, ensure_ascii=False)

    decision = IntentArbiter(FakeLLMClient(content)).decide("执行 show logging last 100 并分析")
    assert_true(decision.action == IntentAction.execute_provided_commands_and_analyze, "alias not normalized")


def test_invalid_json_fallback() -> None:
    decision = IntentArbiter(FakeLLMClient("not json")).decide("随便问一句")
    assert_true(decision.action == IntentAction.need_clarification, "invalid json should clarify")
    assert_true(decision.metadata["effective_confidence"] == 0.0, "invalid json confidence should be 0")


def test_llm_error_fallback() -> None:
    decision = IntentArbiter(FakeLLMClient("", status="error", http_status=500)).decide("随便问一句")
    assert_true(decision.action == IntentAction.need_clarification, "llm error should clarify")
    assert_true(decision.metadata["effective_confidence"] == 0.0, "llm error confidence should be 0")


def test_execute_without_commands_downgrades_effective_confidence() -> None:
    content = json.dumps({
        "action": "execute_provided_commands",
        "confidence": 0.97,
        "commands": [],
        "reason": "missing commands",
    }, ensure_ascii=False)

    decision = IntentArbiter(FakeLLMClient(content)).decide("执行一下")
    assert_true(decision.action == IntentAction.execute_provided_commands, "wrong action")
    assert_true(decision.metadata["effective_confidence"] < 0.80, "empty commands should downgrade effective confidence")
    assert_true(
        "execute_action_has_empty_commands" in decision.metadata["confidence_adjust_reason"],
        "missing downgrade reason",
    )


def test_live_llm_smoke_if_enabled() -> None:
    if os.getenv("RUN_V3_LIVE_LLM_TEST") != "1":
        print("live_llm_smoke=SKIPPED")
        return

    decision = IntentArbiter().decide(
        "是否建议在重启 standby 前先隔离流量？只给建议，不要命令。",
        context={},
        user="baoleiji",
        conversation_id="live-smoke",
    )
    print("live_llm_action=", decision.action)
    print("live_llm_confidence=", decision.confidence)
    print("live_effective_confidence=", decision.metadata.get("effective_confidence"))
    assert_true(decision.action in {
        IntentAction.advice_analysis,
        IntentAction.need_clarification,
    }, "unexpected live llm action")


def main() -> None:
    tests = [
        test_parse_json_from_plain_json,
        test_parse_json_from_markdown_fence,
        test_execute_provided_commands_and_analyze,
        test_action_alias_normalization,
        test_invalid_json_fallback,
        test_llm_error_fallback,
        test_execute_without_commands_downgrades_effective_confidence,
        test_live_llm_smoke_if_enabled,
    ]

    for test in tests:
        test()
        print(test.__name__ + "=OK")

    print("regress_v3_intent_arbiter=OK")


if __name__ == "__main__":
    main()
