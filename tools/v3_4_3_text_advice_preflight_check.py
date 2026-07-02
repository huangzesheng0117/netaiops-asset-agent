#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

START = "# V3_ROUTE_RETURN_CANARY_MARKER_BEGIN"
END = "# V3_ROUTE_RETURN_CANARY_MARKER_END"
REQUIRED_PATTERNS = [
    "V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_R6_MARKER_BEGIN",
    "_v3_canary_norm_action",
    "_v3_canary_normalize_plan_decision",
    "_v3_canary_inspect_shadow_state",
    "_v3_canary_rejected_candidate_summary",
    "arbiter_state_rejected_candidates",
    "arbiter_state_rejected_count",
    "_v3_canary_extract_existing_shadow_state",
    "_v3_canary_build_shadow_state",
    "_v3_shadow_build",
    "_v3_canary_extract_plan_decision",
    "_v3_canary_arbiter_action",
    "llm_intent_arbiter",
    "rebuilt_in_takeover_wrapper",
    "v3_4_3_preflight_arbiter_rebuild_text_advice_convergence",
]
FORBIDDEN_PATTERNS = [
    "_v3_canary_low_risk_action",
    "_v3_canary_contains_any",
    "positive_danger_tokens",
    "query_tokens",
    "advice_tokens",
    "general_tokens",
    "explicit_advice_constraints",
    '"answer": response.get("answer")',
    '"message": response.get("message") or response.get("answer")',
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--report-out", required=True)
    args = parser.parse_args()

    app_path = Path(args.app)
    text = app_path.read_text(encoding="utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise SystemExit("ERROR: app.py must contain exactly one V3 route-return canary marker block")

    start = text.index(START)
    end = text.index(END, start) + len(END)
    block = text[start:end]
    ast.parse(text, filename=str(app_path))

    missing = [item for item in REQUIRED_PATTERNS if item not in block]
    forbidden = [item for item in FORBIDDEN_PATTERNS if item in block]
    report = {
        "app": str(app_path),
        "required_missing": missing,
        "forbidden_present": forbidden,
        "block_len": len(block),
        "uses_enum_action_normalization": "_v3_canary_norm_action" in block,
        "normalizes_plan_decision_before_gate": "_v3_canary_normalize_plan_decision" in block,
        "validates_existing_shadow_state_action": "_v3_canary_inspect_shadow_state" in block,
        "records_rejected_shadow_candidates": (
            "arbiter_state_rejected_candidates" in block
            and "arbiter_state_rejected_count" in block
        ),
        "uses_shadow_rebuild": "_v3_canary_build_shadow_state" in block,
        "uses_arbiter_action": "_v3_canary_arbiter_action" in block,
        "stage_allowed_actions": "general_chat" in block and "advice_analysis" in block,
        "does_not_define_low_risk_action": "_v3_canary_low_risk_action" not in block,
        "does_not_define_contains_any": "_v3_canary_contains_any" not in block,
        "does_not_inject_v2_answer_into_generator_context": '"answer": response.get("answer")' not in block,
        "does_not_inject_v2_message_into_generator_context": '"message": response.get("message") or response.get("answer")' not in block,
    }
    Path(args.report_out).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if missing:
        raise SystemExit("ERROR: missing required V3.4-3 r6 patterns")
    if forbidden:
        raise SystemExit("ERROR: forbidden local text classifier patterns still present")
    if not report["uses_enum_action_normalization"]:
        raise SystemExit("ERROR: V3.4-3 r6 block must normalize IntentAction enum values")
    if not report["normalizes_plan_decision_before_gate"]:
        raise SystemExit("ERROR: V3.4-3 r6 block must normalize plan/decision before gate/generator")
    if not report["validates_existing_shadow_state_action"]:
        raise SystemExit("ERROR: V3.4-3 r6 block must validate an existing state has an Arbiter action")
    if not report["records_rejected_shadow_candidates"]:
        raise SystemExit("ERROR: V3.4-3 r6 block must audit rejected shadow-state candidates")
    if not report["uses_shadow_rebuild"]:
        raise SystemExit("ERROR: V3.4-3 r6 block must rebuild Arbiter state when local shadow state is missing")
    if not report["uses_arbiter_action"]:
        raise SystemExit("ERROR: V3.4-3 r6 block must use Arbiter-derived action")
    print("v3_4_3_static_app_check=OK")
    print("v3_4_3_no_local_text_classifier=OK")
    print("v3_4_3_enum_action_normalization=OK")
    print("v3_4_3_plan_decision_normalization=OK")
    print("v3_4_3_existing_shadow_state_action_validation=OK")
    print("v3_4_3_rejected_shadow_candidate_audit=OK")
    print("v3_4_3_arbiter_rebuild_enabled=OK")
    print("v3_4_3_arbiter_driven_takeover=OK")
    print("v3_4_3_no_v2_answer_in_generator_context=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
