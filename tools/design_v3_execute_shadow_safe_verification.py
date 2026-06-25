# -*- coding: utf-8 -*-
"""
V3 execute-branch safe verification design.

This script deliberately does NOT call chat API endpoint and does NOT execute device
commands. It only uses V3 IntentDecision + command_splitter + safety_guard +
intent_dispatcher offline, so it is safe for production.

Purpose:
- Prove that V3 dispatcher can classify/plan execute actions.
- Prove that user-provided commands require no second confirmation.
- Prove that dangerous commands are blocked before any future execution.
- Provide a written design for later API-level execute shadow verification.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.intent_dispatcher import build_dispatch_plan
from netaiops_asset.chat_v3.intent_schema import IntentDecision


def build_case(
    name: str,
    action: str,
    question: str,
    commands: List[str],
    confidence: float = 0.95,
    device_hint: str = "SH16-H05-INT-EDG-SW01",
) -> Dict[str, Any]:
    decision = IntentDecision(
        action=action,
        confidence=confidence,
        device_required=True,
        device_hint=device_hint,
        commands=commands,
        raw_user_text=question,
        reason=f"offline safe design case: {name}",
    )

    plan = build_dispatch_plan(
        question=question,
        decision=decision,
        user="offline_design",
        conversation_id=f"offline-{name}",
    )

    return {
        "name": name,
        "question": question,
        "input_action": action,
        "input_commands": commands,
        "decision": decision.model_dump(),
        "plan": plan.as_dict(),
    }


def assert_case_expectations(case: Dict[str, Any]) -> None:
    name = case["name"]
    plan = case["plan"]

    if name == "safe_execute_provided_commands":
        assert plan["accepted"] is True, plan
        assert plan["handler_key"] == "execute_provided_commands", plan
        assert plan["requires_confirmation"] is False, plan
        assert plan["safety_allowed"] is True, plan

    if name == "safe_execute_provided_commands_and_analyze":
        assert plan["accepted"] is True, plan
        assert plan["handler_key"] == "execute_provided_commands_and_analyze", plan
        assert plan["requires_confirmation"] is False, plan
        assert plan["should_analyze_after_execution"] is True, plan
        assert plan["safety_allowed"] is True, plan

    if name == "same_line_multiple_show_commands":
        assert plan["accepted"] is True, plan
        assert plan["commands"] == ["show clock", "show version", "show logging last 100"], plan

    if name == "dangerous_reload_blocked":
        assert plan["accepted"] is False, plan
        assert plan["handler_key"] == "blocked_unsafe_commands", plan
        assert plan["safety_allowed"] is False, plan
        assert plan["effective_confidence"] == 0.0, plan

    if name == "low_confidence_execute_clarifies":
        assert plan["accepted"] is False, plan
        assert plan["handler_key"] == "need_clarification", plan

    if name == "too_many_commands_blocked":
        assert plan["accepted"] is False, plan
        assert plan["safety_allowed"] is False, plan


def build_markdown(cases: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# V3 Execute Branch Safe Verification Design")
    lines.append("")
    lines.append(f"- created_at: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    lines.append("- This report is generated offline. It does not call `chat API endpoint`.")
    lines.append("- It does not call Netmiko MCP and does not execute device commands.")
    lines.append("- It validates only V3 dispatcher planning and safety behavior.")
    lines.append("")

    lines.append("## Confirmed safety principles")
    lines.append("- User-provided commands do not require a second confirmation in V3.")
    lines.append("- But every command still passes deterministic `safety_guard` before future execution.")
    lines.append("- Dangerous commands such as `reload`, `configure terminal`, `delete`, `clear`, `copy`, `write` are blocked.")
    lines.append("- API-level execute shadow tests should not be sent to current V2 `chat API endpoint` until we have a non-executing dry-run endpoint or a V3-only dry-run mode.")
    lines.append("")

    lines.append("## Offline cases")
    for case in cases:
        plan = case["plan"]
        lines.append(f"### {case['name']}")
        lines.append(f"- input_action: `{case['input_action']}`")
        lines.append(f"- accepted: `{plan.get('accepted')}`")
        lines.append(f"- handler_key: `{plan.get('handler_key')}`")
        lines.append(f"- response_mode: `{plan.get('response_mode')}`")
        lines.append(f"- requires_confirmation: `{plan.get('requires_confirmation')}`")
        lines.append(f"- safety_allowed: `{plan.get('safety_allowed')}`")
        lines.append(f"- safety_reason: `{plan.get('safety_reason')}`")
        lines.append(f"- effective_confidence: `{plan.get('effective_confidence')}`")
        lines.append(f"- commands: `{plan.get('commands')}`")
        if plan.get("blocked_commands"):
            lines.append(f"- blocked_commands: `{plan.get('blocked_commands')}`")
        lines.append("")

    lines.append("## Recommended next-stage execute verification")
    lines.append("1. Add a V3-only dry-run debug endpoint or internal script that runs Arbiter + Dispatcher without invoking V2 execution branches.")
    lines.append("2. Use the dry-run path to test `execute_provided_commands` and `execute_provided_commands_and_analyze` with safe show/display commands.")
    lines.append("3. Verify `requires_confirmation=False` only for user-provided commands.")
    lines.append("4. Verify dangerous commands are blocked by `safety_guard` and never reach execution orchestration.")
    lines.append("5. Only after dry-run is stable, wire V3 high-confidence execute paths to the existing execution orchestrator.")
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    cases = [
        build_case(
            name="safe_execute_provided_commands",
            action="execute_provided_commands",
            question="请执行：show clock show version",
            commands=["show clock", "show version"],
        ),
        build_case(
            name="safe_execute_provided_commands_and_analyze",
            action="execute_provided_commands_and_analyze",
            question="我再给你一批命令，执行后分析：show clock show version",
            commands=["show clock", "show version"],
        ),
        build_case(
            name="same_line_multiple_show_commands",
            action="execute_provided_commands_and_analyze",
            question="执行后分析：show clock show version show logging last 100",
            commands=["show clock show version show logging last 100"],
        ),
        build_case(
            name="dangerous_reload_blocked",
            action="execute_provided_commands",
            question="执行：show clock reload",
            commands=["show clock", "reload"],
        ),
        build_case(
            name="low_confidence_execute_clarifies",
            action="execute_provided_commands",
            question="可能执行 show clock？",
            commands=["show clock"],
            confidence=0.70,
        ),
        build_case(
            name="too_many_commands_blocked",
            action="execute_provided_commands",
            question="执行一大批 show clock",
            commands=[f"show clock {idx}" for idx in range(25)],
        ),
    ]

    for case in cases:
        assert_case_expectations(case)

    json_path = report_dir / "v3_execute_branch_safe_design.json"
    md_path = report_dir / "v3_execute_branch_safe_design.md"

    json_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(build_markdown(cases), encoding="utf-8")

    print(json.dumps(cases, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_execute_branch_safe_design=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
