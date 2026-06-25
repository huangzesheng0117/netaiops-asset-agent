# -*- coding: utf-8 -*-
"""
Regression tests for V3 intent_dispatcher and shadow_logger.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.intent_dispatcher import build_dispatch_plan
from netaiops_asset.chat_v3.intent_schema import IntentDecision
from netaiops_asset.chat_v3.shadow_logger import infer_diff, write_shadow_record


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def assert_equal(left: Any, right: Any, message: str) -> None:
    if left != right:
        raise AssertionError(f"{message}: left={left!r}, right={right!r}")


def make_decision(action: str, confidence: float = 0.95, **kwargs: Any) -> IntentDecision:
    return IntentDecision(
        action=action,
        confidence=confidence,
        raw_user_text=kwargs.pop("raw_user_text", ""),
        reason=kwargs.pop("reason", "test reason"),
        **kwargs,
    )


def test_execute_and_analyze_plan() -> None:
    decision = make_decision(
        "execute_provided_commands_and_analyze",
        commands=["show clock show version show logging last 100"],
        device_required=True,
        device_hint="SH16-H05-INT-EDG-SW01",
    )
    plan = build_dispatch_plan(
        question="执行后分析：show clock show version show logging last 100",
        decision=decision,
    )

    assert_true(plan.accepted, plan.as_dict())
    assert_equal(plan.handler_key, "execute_provided_commands_and_analyze", "handler mismatch")
    assert_equal(plan.response_mode, "execute_and_analyze", "response mode mismatch")
    assert_equal(plan.commands, ["show clock", "show version", "show logging last 100"], "commands mismatch")
    assert_true(plan.safety_allowed, plan.as_dict())
    assert_true(plan.should_execute_commands, plan.as_dict())
    assert_true(plan.should_analyze_after_execution, plan.as_dict())
    assert_true(not plan.requires_confirmation, plan.as_dict())


def test_execute_missing_commands_fallback_to_question() -> None:
    decision = make_decision(
        "execute_provided_commands",
        commands=[],
        raw_user_text="执行：show clock show version",
    )
    plan = build_dispatch_plan(
        question="执行：show clock show version",
        decision=decision,
    )

    assert_true(plan.accepted, plan.as_dict())
    assert_equal(plan.commands, ["show clock", "show version"], "fallback extraction mismatch")


def test_dangerous_command_blocked() -> None:
    decision = make_decision(
        "execute_provided_commands",
        commands=["show clock", "reload"],
    )
    plan = build_dispatch_plan(
        question="执行：show clock reload",
        decision=decision,
    )

    assert_true(not plan.accepted, plan.as_dict())
    assert_equal(plan.handler_key, "blocked_unsafe_commands", "handler mismatch")
    assert_true(not plan.safety_allowed, plan.as_dict())
    assert_true(plan.blocked_commands, plan.as_dict())
    assert_equal(plan.effective_confidence, 0.0, "unsafe plan confidence must be zero")


def test_advice_plan() -> None:
    decision = make_decision(
        "advice_analysis",
        confidence=0.91,
        reason="用户只需要方案建议",
    )
    plan = build_dispatch_plan(
        question="是否建议先隔离流量？只给建议，不要命令",
        decision=decision,
    )

    assert_true(plan.accepted, plan.as_dict())
    assert_equal(plan.handler_key, "advice_analysis", "handler mismatch")
    assert_equal(plan.response_mode, "advice", "response mode mismatch")
    assert_true(not plan.should_execute_commands, plan.as_dict())


def test_low_confidence_command_execution_clarifies() -> None:
    decision = make_decision(
        "execute_provided_commands",
        confidence=0.70,
        commands=["show clock"],
    )
    plan = build_dispatch_plan(
        question="执行 show clock",
        decision=decision,
    )

    assert_true(not plan.accepted, plan.as_dict())
    assert_equal(plan.handler_key, "need_clarification", "low confidence execute should clarify")


def test_need_clarification_plan() -> None:
    decision = make_decision(
        "need_clarification",
        confidence=0.20,
        clarification_question="请补充设备名。",
    )
    plan = build_dispatch_plan(
        question="这个设备怎么办？",
        decision=decision,
    )

    assert_true(not plan.accepted, plan.as_dict())
    assert_equal(plan.handler_key, "need_clarification", "handler mismatch")
    assert_true("请补充设备名" in plan.reason, plan.as_dict())


def test_generate_commands_plan() -> None:
    decision = make_decision(
        "generate_commands",
        confidence=0.92,
        device_required=True,
        device_hint="SH16-H05-INT-EDG-SW01",
    )
    plan = build_dispatch_plan(
        question="给我查看日志的命令",
        decision=decision,
    )

    assert_true(plan.accepted, plan.as_dict())
    assert_equal(plan.handler_key, "generate_commands", "handler mismatch")
    assert_true(plan.should_generate_commands, plan.as_dict())


def test_cmdb_plan() -> None:
    decision = make_decision(
        "cmdb_query",
        confidence=0.90,
        device_hint="SH16-H05-INT-EDG-SW01",
    )
    plan = build_dispatch_plan(
        question="查一下这个设备的管理IP",
        decision=decision,
    )

    assert_true(plan.accepted, plan.as_dict())
    assert_equal(plan.handler_key, "cmdb_query", "handler mismatch")
    assert_equal(plan.response_mode, "cmdb", "response mode mismatch")


def test_shadow_logger_writes_jsonl() -> None:
    decision = make_decision(
        "advice_analysis",
        confidence=0.93,
    )
    plan = build_dispatch_plan(
        question="是否建议先隔离流量？",
        decision=decision,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        path = write_shadow_record(
            question="是否建议先隔离流量？",
            conversation_id="conv-test",
            user="tester",
            v2_route="v2_advice_analysis",
            v2_summary={"status": "ok"},
            v3_decision=decision,
            v3_plan=plan,
            shadow_dir=tmpdir,
        )

        assert_true(path.exists(), "shadow log file not created")
        lines = path.read_text(encoding="utf-8").splitlines()
        assert_equal(len(lines), 1, "shadow log line count mismatch")
        payload = json.loads(lines[0])
        assert_equal(payload["conversation_id"], "conv-test", "conversation_id mismatch")
        assert_equal(payload["v3_plan"]["handler_key"], "advice_analysis", "handler mismatch")
        assert_true(not payload["is_diff"], payload)


def test_shadow_diff_inference() -> None:
    assert_true(not infer_diff("v2_advice_analysis", {"handler_key": "advice_analysis"}), "same route should not diff")
    assert_true(infer_diff("v2_followup_analysis", {"handler_key": "advice_analysis"}), "different route should diff")


def main() -> None:
    tests = [
        test_execute_and_analyze_plan,
        test_execute_missing_commands_fallback_to_question,
        test_dangerous_command_blocked,
        test_advice_plan,
        test_low_confidence_command_execution_clarifies,
        test_need_clarification_plan,
        test_generate_commands_plan,
        test_cmdb_plan,
        test_shadow_logger_writes_jsonl,
        test_shadow_diff_inference,
    ]

    for test in tests:
        test()
        print(test.__name__ + "=OK")

    print("regress_v3_dispatcher_shadow=OK")


if __name__ == "__main__":
    main()
