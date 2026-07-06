# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.takeover_gate import evaluate_takeover


def base_plan(**overrides):
    data = {
        "action": "general_chat",
        "handler_key": "general_chat",
        "response_mode": "chat",
        "accepted": True,
        "requires_confirmation": False,
        "safety_allowed": True,
        "confidence": 0.95,
        "effective_confidence": 0.95,
    }
    data.update(overrides)
    return data


def assert_case(name, condition, payload=None):
    if not condition:
        raise AssertionError(f"{name} failed: {payload}")
    print(f"{name}=OK")


def main() -> int:
    os.environ.pop("NETAIOPS_V3_TAKEOVER_ENABLED", None)

    gate = evaluate_takeover(plan=base_plan())
    assert_case("disabled_by_default_no_actual_takeover", gate.enabled is False and gate.eligible is True and gate.takeover is False, gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(), enabled=True)
    assert_case("enabled_general_chat_takeover", gate.takeover is True and gate.reason == "eligible", gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(action="advice_analysis", handler_key="advice_analysis", response_mode="advice"), enabled=True)
    assert_case("enabled_advice_takeover", gate.takeover is True, gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(action="cmdb_query", handler_key="cmdb_query", response_mode="cmdb"), enabled=True)
    assert_case("enabled_cmdb_takeover", gate.takeover is True, gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(action="need_clarification", handler_key="need_clarification", response_mode="clarification", accepted=False), enabled=True)
    assert_case("enabled_need_clarification_takeover", gate.takeover is True, gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(effective_confidence=0.79), enabled=True)
    assert_case("low_confidence_blocked", gate.takeover is False and gate.reason == "low_effective_confidence", gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(action="execute_provided_commands", handler_key="execute_provided_commands"), enabled=True)
    assert_case("execute_action_blocked", gate.takeover is False and gate.reason == "blocked_action", gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(action="execute_provided_commands_and_analyze", handler_key="execute_provided_commands_and_analyze"), enabled=True)
    assert_case("execute_and_analyze_blocked", gate.takeover is False and gate.reason == "blocked_action", gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(action="generate_commands", handler_key="generate_commands"), enabled=True)
    assert_case("generate_commands_blocked", gate.takeover is False and gate.reason == "blocked_action", gate.as_dict())

    gate = evaluate_takeover(
        plan=base_plan(
            action="analyze_existing_evidence",
            handler_key="analyze_existing_evidence",
            response_mode="analysis",
        ),
        enabled=True,
    )
    assert_case(
        "analyze_existing_evidence_allowed_in_v344",
        gate.takeover is True and gate.reason == "eligible",
        gate.as_dict(),
    )

    gate = evaluate_takeover(plan=base_plan(requires_confirmation=True), enabled=True)
    assert_case("requires_confirmation_blocked", gate.takeover is False and gate.reason == "requires_confirmation", gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(safety_allowed=False), enabled=True)
    assert_case("safety_false_blocked", gate.takeover is False and gate.reason == "safety_not_allowed", gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(accepted=False), enabled=True)
    assert_case("accepted_false_blocked_except_clarification", gate.takeover is False and gate.reason == "plan_not_accepted", gate.as_dict())

    gate = evaluate_takeover(plan=base_plan(), enabled=True, allowed={"advice_analysis"})
    assert_case("allowed_list_restricts_general_chat", gate.takeover is False and gate.reason == "not_in_allowed_actions", gate.as_dict())

    print("regress_v3_takeover_gate=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
