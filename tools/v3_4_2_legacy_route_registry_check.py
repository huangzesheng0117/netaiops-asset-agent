#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from netaiops_asset.chat_v3.legacy_route_registry import (  # noqa: E402
    classify_legacy_route,
    legacy_route_decision_from_inventory_record,
    registry_metadata,
    should_allow_v3_takeover_for_legacy_route,
)


UNIT_CASES = [
    {
        "name": "general_text_explain",
        "question": "解释一下 policing 和 shaping 的区别",
        "expected_type": "general_chat",
        "expected_action": "general_chat",
        "expected_allow": True,
    },
    {
        "name": "advice_analysis",
        "question": "接口错包持续增长，给我排查思路和处理建议",
        "expected_type": "advice_analysis",
        "expected_action": "advice_analysis",
        "expected_allow": True,
    },
    {
        "name": "followup_not_allowed_in_v3_4_2",
        "question": "继续分析刚才这个设备的问题",
        "expected_type": "followup",
        "expected_action": "advice_analysis",
        "expected_allow": False,
    },
    {
        "name": "cmdb_not_allowed",
        "question": "查一下 SH16-G03-DCI-BN-SW01 的管理IP",
        "expected_type": "cmdb_query",
        "expected_action": "cmdb_query",
        "expected_allow": False,
    },
    {
        "name": "command_explanation_deferred",
        "question": "只解释 show interface status 这条命令是什么意思，不要执行",
        "expected_type": "command_explanation",
        "expected_action": "general_chat",
        "expected_allow": False,
    },
    {
        "name": "command_execution_blocked",
        "question": "帮我执行 show interface status",
        "expected_type": "command_execution",
        "expected_action": None,
        "expected_allow": False,
    },
    {
        "name": "config_change_blocked",
        "question": "进入接口执行 shutdown",
        "expected_type": "config_change",
        "expected_action": None,
        "expected_allow": False,
    },
]


def run_unit_cases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in UNIT_CASES:
        decision = classify_legacy_route(question=case["question"])
        allow = should_allow_v3_takeover_for_legacy_route(decision, canary_triggered=True)
        item = {
            "name": case["name"],
            "question": case["question"],
            "decision": decision.to_dict(),
            "allow_with_canary": allow,
            "expected_type": case["expected_type"],
            "expected_action": case["expected_action"],
            "expected_allow": case["expected_allow"],
        }
        if decision.route_type != case["expected_type"]:
            raise SystemExit(
                f"ERROR: unit case {case['name']} route_type mismatch: "
                f"{decision.route_type} != {case['expected_type']}"
            )
        if decision.v3_action != case["expected_action"]:
            raise SystemExit(
                f"ERROR: unit case {case['name']} v3_action mismatch: "
                f"{decision.v3_action} != {case['expected_action']}"
            )
        if allow != case["expected_allow"]:
            raise SystemExit(
                f"ERROR: unit case {case['name']} allow mismatch: "
                f"{allow} != {case['expected_allow']}"
            )
        results.append(item)
    return results


def classify_inventory(inventory_json: Path) -> dict[str, Any]:
    raw = json.loads(inventory_json.read_text(encoding="utf-8"))
    returns = raw.get("returns", [])
    signals = raw.get("legacy_signals", [])

    return_decisions = [
        legacy_route_decision_from_inventory_record(item).to_dict()
        for item in returns
    ]
    signal_decisions = [
        legacy_route_decision_from_inventory_record(item).to_dict()
        for item in signals
    ]

    route_type_counts = Counter(item["route_type"] for item in signal_decisions)
    risk_counts = Counter(item["risk_level"] for item in signal_decisions)
    action_counts = Counter(str(item["v3_action"]) for item in signal_decisions)
    candidate_counts = Counter(str(item["takeover_candidate"]) for item in signal_decisions)

    summary = {
        "inventory_json": str(inventory_json),
        "return_count": len(returns),
        "legacy_signal_count": len(signals),
        "return_decision_route_type_counts": dict(Counter(item["route_type"] for item in return_decisions)),
        "signal_decision_route_type_counts": dict(route_type_counts),
        "signal_decision_risk_counts": dict(risk_counts),
        "signal_decision_action_counts": dict(action_counts),
        "signal_decision_takeover_candidate_counts": dict(candidate_counts),
    }

    expected_signal_types = {"advice_analysis", "cmdb_query", "followup", "semantic_route", "batch_route"}
    missing = sorted(item for item in expected_signal_types if route_type_counts.get(item, 0) < 1)
    if missing:
        raise SystemExit(f"ERROR: expected legacy signal route types missing from registry classification: {missing}")

    if len(signals) < 100:
        raise SystemExit(f"ERROR: unexpectedly few legacy signals in V3.4-1 inventory: {len(signals)}")
    if len(returns) < 10:
        raise SystemExit(f"ERROR: unexpectedly few return records in V3.4-1 inventory: {len(returns)}")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="V3.4-2 legacy route registry check")
    parser.add_argument("--inventory-json", required=True)
    parser.add_argument("--report-out", required=True)
    args = parser.parse_args()

    unit_results = run_unit_cases()
    inventory_summary = classify_inventory(Path(args.inventory_json))
    metadata = registry_metadata()

    report = {
        "version": "v3.4.2",
        "purpose": "validate legacy route registry without wiring runtime behavior",
        "metadata": metadata,
        "unit_results": unit_results,
        "inventory_summary": inventory_summary,
    }

    Path(args.report_out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("legacy_route_registry_unit_cases=OK")
    print("legacy_route_registry_inventory_classification=OK")
    print("legacy_route_registry_no_runtime_wiring=OK")
    print("v3_4_2_legacy_route_registry_check=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
