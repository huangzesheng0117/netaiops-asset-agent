#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def function_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"{path}: expected one function {name}, found {len(matches)}"
        )
    node = matches[0]
    return "\n".join(
        text.splitlines()[node.lineno - 1 : node.end_lineno]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--report-out", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    app = root / "app.py"
    bridge = root / "netaiops_asset/chat_v3/followup_context.py"
    arbiter = root / "netaiops_asset/chat_v3/intent_arbiter.py"
    generator = root / "netaiops_asset/chat_v3/response_generator.py"
    gate = root / "netaiops_asset/chat_v3/takeover_gate.py"
    readiness = root / "netaiops_asset/chat_v3/takeover_response.py"
    registry = root / "netaiops_asset/chat_v3/legacy_route_registry.py"
    doc = root / "docs/v3_4_4_followup_convergence.md"

    paths = [
        app,
        bridge,
        arbiter,
        generator,
        gate,
        readiness,
        registry,
        doc,
    ]
    missing_files = [str(path) for path in paths if not path.exists()]
    if missing_files:
        raise SystemExit(
            "ERROR: missing files: "
            + json.dumps(missing_files, ensure_ascii=False)
        )

    app_text = app.read_text(encoding="utf-8")
    bridge_text = bridge.read_text(encoding="utf-8")
    arbiter_text = arbiter.read_text(encoding="utf-8")
    generator_text = generator.read_text(encoding="utf-8")
    gate_text = gate.read_text(encoding="utf-8")
    readiness_text = readiness.read_text(encoding="utf-8")
    registry_text = registry.read_text(encoding="utf-8")
    doc_text = doc.read_text(encoding="utf-8")

    shadow_build = function_source(app, "_v3_shadow_build")
    wrapper = function_source(
        app,
        "_v3_apply_chat_canary_takeover",
    )
    context_summary = function_source(
        arbiter,
        "build_context_summary",
    )
    response_messages = function_source(
        generator,
        "build_response_messages",
    )
    response_generate = function_source(
        generator,
        "generate_v3_response",
    )

    forbidden_primary_route_patterns = [
        "FOLLOWUP_HINTS",
        "FOLLOWUP_KEYWORDS",
        "ROUTE_KEYWORDS",
        "CATEGORY_TOKENS",
        "classify_followup",
        "is_followup_question",
        "_v3_canary_contains_any",
        "re.search(",
        "re.match(",
        "re.fullmatch(",
    ]
    prohibited_scope = (
        shadow_build
        + "\n"
        + wrapper
        + "\n"
        + bridge_text
    )
    forbidden_present = [
        item
        for item in forbidden_primary_route_patterns
        if item in prohibited_scope
    ]

    checks = {
        "v3442_marker_present": (
            "V3_4_4_2_FOLLOWUP_CONTEXT_ARBITER_CONVERGENCE_MARKER_BEGIN"
            in app_text
            and
            "V3_4_4_2_FOLLOWUP_CONTEXT_ARBITER_CONVERGENCE_MARKER_END"
            in app_text
        ),
        "old_r6_marker_absent": (
            "V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_R6_MARKER"
            not in app_text
        ),
        "shadow_build_loads_context_bridge": (
            "build_followup_context" in shadow_build
            and "arbiter_context" in shadow_build
            and "decide_intent(" in shadow_build
            and "build_dispatch_plan(" in shadow_build
        ),
        "wrapper_allows_followup_action": (
            '"analyze_existing_evidence"' in wrapper
            and "stage_allowed_actions" in wrapper
        ),
        "wrapper_requires_context_for_followup": (
            'action == "analyze_existing_evidence"' in wrapper
            and "followup_context_unavailable" in wrapper
        ),
        "wrapper_records_original_and_effective_ids": (
            "original_conversation_id" in wrapper
            and "effective_conversation_id" in wrapper
        ),
        "wrapper_records_followup_audit_fields": all(
            item in wrapper
            for item in (
                "followup_context_source",
                "followup_context_available",
                "followup_context_turn_count",
                "followup_context_topic",
                "followup_context_has_execution_evidence",
            )
        ),
        "wrapper_persists_v3_turn": (
            "record_v3_turn" in wrapper
            and "v3_followup_context_store_error" in wrapper
        ),
        "bridge_does_not_decide_intent": (
            "decide_intent" not in bridge_text
            and "IntentArbiter" not in bridge_text
        ),
        "bridge_does_not_execute": all(
            item not in bridge_text
            for item in (
                "netmiko",
                "execute_command",
                "subprocess",
                "CMDBAdapter",
            )
        ),
        "arbiter_summary_contains_recent_turns": (
            "recent_turns" in context_summary
            and "followup_context_available" in context_summary
            and "rolling_summary" in context_summary
        ),
        "generator_allows_followup_action": (
            '"analyze_existing_evidence"' in generator_text
            and "missing_followup_context" in response_generate
        ),
        "generator_prompt_uses_structured_context": (
            "followup_context" in response_messages
            and "只能使用提供的上下文" in response_messages
        ),
        "gate_allows_followup_action_and_analysis_mode": (
            '"analyze_existing_evidence"' in gate_text
            and '"analysis"' in gate_text
        ),
        "readiness_allows_followup_action": (
            '"analyze_existing_evidence"' in readiness_text
        ),
        "registry_followup_runtime_takeover_enabled": (
            '"v2_followup_return": LegacyRouteMetadata(' in registry_text
            and "runtime_takeover_allowed=True" in registry_text[
                registry_text.index(
                    '"v2_followup_return": LegacyRouteMetadata('
                ) :
                registry_text.index(
                    '"v2_cmdb_query_return": LegacyRouteMetadata('
                )
            ]
        ),
        "doc_preserves_arbiter_boundary": (
            "LLM Intent Arbiter" in doc_text
            and "V2 follow-up" in doc_text
            and "fallback" in doc_text
        ),
        "no_local_followup_classifier_in_v3_scope": (
            not forbidden_present
        ),
    }

    failures = [
        key
        for key, value in checks.items()
        if value is not True
    ]
    report = {
        "root": str(root),
        "checks": checks,
        "forbidden_present": forbidden_present,
        "failures": failures,
    }
    Path(args.report_out).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    if failures:
        raise SystemExit(
            "ERROR: V3.4-4-2 static check failed: "
            + ", ".join(failures)
        )

    print("v3_4_4_2_context_bridge_static=OK")
    print("v3_4_4_2_arbiter_boundary_static=OK")
    print("v3_4_4_2_followup_gate_static=OK")
    print("v3_4_4_2_followup_generator_static=OK")
    print("v3_4_4_2_registry_static=OK")
    print("result=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
