# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.takeover_response import (
    build_safe_takeover_response,
    evaluate_response_readiness,
)


def assert_case(name, condition, payload=None):
    if not condition:
        raise AssertionError(f"{name} failed: {payload}")
    print(f"{name}=OK")


def base_gate(**overrides):
    data = {
        "enabled": True,
        "eligible": True,
        "takeover": True,
        "reason": "eligible",
    }
    data.update(overrides)
    return data


def main() -> int:
    plan = {
        "action": "general_chat",
        "handler_key": "general_chat",
        "response_mode": "chat",
        "answer": "这是一个通用解释。",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case("general_chat_with_answer_ready", ready.ready is True, ready.as_dict())

    response = build_safe_takeover_response(
        question="解释一下 StackWise Virtual",
        conversation_id="conv-general",
        plan=plan,
        gate=base_gate(),
    )
    assert_case("general_chat_response_contract", response["status"] == "ok" and response["planner_source"] == "v3_takeover", response)

    plan = {
        "action": "general_chat",
        "handler_key": "general_chat",
        "response_mode": "chat",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case("general_chat_without_answer_not_ready", ready.ready is False and ready.reason == "missing_answer_text", ready.as_dict())

    plan = {
        "action": "advice_analysis",
        "handler_key": "advice_analysis",
        "response_mode": "advice",
        "final_answer": "建议先隔离流量，再执行维护。",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case("advice_with_answer_ready", ready.ready is True, ready.as_dict())

    plan = {
        "action": "analyze_existing_evidence",
        "handler_key": "analyze_existing_evidence",
        "response_mode": "analysis",
        "answer": "基于上一轮已有证据继续分析。",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case(
        "followup_analysis_with_answer_ready",
        ready.ready is True,
        ready.as_dict(),
    )

    plan = {
        "action": "need_clarification",
        "handler_key": "need_clarification",
        "response_mode": "clarification",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case("need_clarification_fallback_ready", ready.ready is True and ready.reason == "ready_with_clarification_fallback", ready.as_dict())

    response = build_safe_takeover_response(
        question="这个设备怎么办？",
        conversation_id="conv-clarify",
        plan=plan,
        gate=base_gate(),
    )
    assert_case("need_clarification_response_contract", response["status"] == "need_clarification" and response["answer"], response)

    plan = {
        "action": "cmdb_query",
        "handler_key": "cmdb_query",
        "response_mode": "cmdb",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case("cmdb_without_items_not_ready", ready.ready is False and ready.reason == "missing_cmdb_result_items", ready.as_dict())

    plan = {
        "action": "cmdb_query",
        "handler_key": "cmdb_query",
        "response_mode": "cmdb",
        "items": [{"hostname": "device01", "management_ip": "192.0.2.1"}],
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case("cmdb_with_items_ready", ready.ready is True, ready.as_dict())

    response = build_safe_takeover_response(
        question="查 device01",
        conversation_id="conv-cmdb",
        plan=plan,
        gate=base_gate(),
    )
    assert_case("cmdb_response_contract", response["status"] == "ok" and response["count"] == 1, response)

    plan = {
        "action": "execute_provided_commands",
        "handler_key": "execute_provided_commands",
        "response_mode": "execute",
        "answer": "不应接管执行类。",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate())
    assert_case("execute_action_not_ready", ready.ready is False and ready.reason == "action_not_safe_for_response_takeover", ready.as_dict())

    plan = {
        "action": "general_chat",
        "handler_key": "general_chat",
        "response_mode": "chat",
        "answer": "有答案但 gate 不 eligible。",
    }
    ready = evaluate_response_readiness(plan=plan, gate=base_gate(eligible=False, takeover=False, reason="low_effective_confidence"))
    assert_case("gate_not_eligible_not_ready", ready.ready is False and ready.reason == "gate_not_eligible", ready.as_dict())

    print("regress_v3_takeover_response=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
