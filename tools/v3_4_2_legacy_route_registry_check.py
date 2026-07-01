#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from netaiops_asset.chat_v3.legacy_route_registry import (  # noqa: E402
    DEFAULT_LEGACY_ROUTE_REGISTRY,
    LegacyRouteDescriptor,
    descriptor_from_dict,
    legacy_route_to_v3_action,
    list_legacy_route_metadata,
    registry_metadata,
    resolve_legacy_route,
    resolve_legacy_route_dict,
)


PROHIBITED_SOURCE_PATTERNS = [
    "CATEGORY_TOKENS",
    "ROUTE_KEYWORDS",
    "TYPE_PRIORITY",
    "_token_hits",
    "matched_tokens",
    "classify_legacy_route",
]


def check_registry_source(registry_file: Path) -> dict[str, Any]:
    source = registry_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(registry_file))
    findings: list[str] = []

    for pattern in PROHIBITED_SOURCE_PATTERNS:
        if pattern in source:
            findings.append(pattern)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("classify_"):
                findings.append(f"function:{node.name}")
            arg_names = [arg.arg for arg in node.args.args]
            for prohibited_arg in ("question", "context", "snippet"):
                if prohibited_arg in arg_names:
                    findings.append(f"function_arg:{node.name}.{prohibited_arg}")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"CATEGORY_TOKENS", "ROUTE_KEYWORDS"}:
                    findings.append(f"assign:{target.id}")
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in {"CATEGORY_TOKENS", "ROUTE_KEYWORDS"}:
                findings.append(f"annassign:{node.target.id}")

    descriptor_fields = {item.name for item in fields(LegacyRouteDescriptor)}
    forbidden_descriptor_fields = descriptor_fields.intersection({"question", "context", "snippet"})
    if forbidden_descriptor_fields:
        findings.append(f"descriptor_fields:{sorted(forbidden_descriptor_fields)}")

    if findings:
        raise SystemExit(
            "ERROR: registry still contains prohibited local intent-classifier constructs: "
            + json.dumps(sorted(set(findings)), ensure_ascii=False)
        )

    return {
        "registry_file": str(registry_file),
        "prohibited_patterns_absent": True,
        "descriptor_fields": sorted(descriptor_fields),
    }


def run_metadata_unit_cases() -> list[dict[str, Any]]:
    cases = [
        {
            "name": "registered_advice_branch",
            "descriptor": LegacyRouteDescriptor(
                legacy_branch_id="v2_advice_analysis_return",
                explicit_legacy_route_type="advice_analysis",
                source_function="v2_chat_router_middleware",
                return_path="JSONResponse",
                known_legacy_behavior="existing pure advice analysis path",
                migration_stage="v3.4-3",
            ),
            "expected_action": "advice_analysis",
        },
        {
            "name": "registered_general_branch",
            "descriptor": LegacyRouteDescriptor(
                legacy_branch_id="v2_general_chat_return",
                explicit_legacy_route_type="general_chat",
                source_function="chat",
                return_path="route_return",
                known_legacy_behavior="existing general response path",
                migration_stage="v3.4-3",
            ),
            "expected_action": "general_chat",
        },
        {
            "name": "registered_followup_branch_metadata_only",
            "descriptor": LegacyRouteDescriptor(
                legacy_branch_id="v2_followup_return",
                explicit_legacy_route_type="followup",
                source_function="v2_chat_router_middleware",
                return_path="JSONResponse",
                known_legacy_behavior="existing context continuation path",
                migration_stage="v3.4-4",
            ),
            "expected_action": "analyze_existing_evidence",
        },
        {
            "name": "custom_cmdb_descriptor",
            "descriptor": LegacyRouteDescriptor(
                legacy_branch_id="custom_cmdb_descriptor",
                explicit_legacy_route_type="cmdb_query",
                source_function="chat",
                return_path="route_return",
                known_legacy_behavior="explicit metadata descriptor",
                migration_stage="deferred",
            ),
            "expected_action": "cmdb_query",
        },
    ]

    results: list[dict[str, Any]] = []
    for case in cases:
        resolution = resolve_legacy_route(case["descriptor"])
        if resolution.metadata.mapped_v3_action != case["expected_action"]:
            raise SystemExit(
                f"ERROR: mapped action mismatch for {case['name']}: "
                f"{resolution.metadata.mapped_v3_action!r} != {case['expected_action']!r}"
            )
        if resolution.metadata.runtime_takeover_allowed:
            raise SystemExit(f"ERROR: V3.4-2-fix registry must not allow runtime takeover: {case['name']}")
        if not resolution.arbiter_required:
            raise SystemExit(f"ERROR: registry resolution must keep arbiter_required=True: {case['name']}")
        results.append({"name": case["name"], "resolution": resolution.to_dict()})

    return results


def run_rejection_cases() -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    natural_language_payloads = [
        {"question": "执行 shutdown", "legacy_branch_id": "bad_1", "legacy_route_type": "general_chat"},
        {"context": "继续分析刚才这个设备", "legacy_branch_id": "bad_2", "legacy_route_type": "followup"},
        {"snippet": "管理IP", "legacy_branch_id": "bad_3", "legacy_route_type": "cmdb_query"},
        {"user_input": "show interface status", "legacy_branch_id": "bad_4", "legacy_route_type": "command_execution"},
    ]
    for item in natural_language_payloads:
        try:
            descriptor_from_dict(item)
        except ValueError as exc:
            rejected.append({"payload_keys": sorted(item), "rejected": True, "error": str(exc)})
        else:
            raise SystemExit(
                "ERROR: descriptor_from_dict accepted natural-language payload keys: "
                + json.dumps(item, ensure_ascii=False)
            )

    invalid_payloads = [
        {
            "legacy_branch_id": "invalid_route",
            "legacy_route_type": "not_a_route",
            "source_function": "chat",
            "return_path": "route_return",
        },
        {
            "legacy_branch_id": "",
            "legacy_route_type": "general_chat",
            "source_function": "chat",
            "return_path": "route_return",
        },
    ]
    for item in invalid_payloads:
        try:
            descriptor_from_dict(item)
        except ValueError as exc:
            rejected.append({"payload_keys": sorted(item), "rejected": True, "error": str(exc)})
        else:
            raise SystemExit(
                "ERROR: descriptor_from_dict accepted invalid explicit descriptor: "
                + json.dumps(item, ensure_ascii=False)
            )

    return rejected


def run_dict_resolution_case() -> dict[str, Any]:
    resolution = resolve_legacy_route_dict(
        {
            "legacy_branch_id": "dict_advice_descriptor",
            "legacy_route_type": "advice_analysis",
            "source_function": "v2_chat_router_middleware",
            "return_path": "JSONResponse",
            "known_legacy_behavior": "explicit descriptor from code branch",
            "migration_stage": "v3.4-3",
        }
    )
    if resolution.metadata.mapped_v3_action != "advice_analysis":
        raise SystemExit("ERROR: dict descriptor did not map explicit advice_analysis route correctly")
    return resolution.to_dict()


def read_inventory_readonly_summary(inventory_json: Path) -> dict[str, Any]:
    raw = json.loads(inventory_json.read_text(encoding="utf-8"))
    returns = raw.get("returns", [])
    signals = raw.get("legacy_signals", [])
    if len(returns) < 10:
        raise SystemExit(f"ERROR: unexpectedly few inventory return records: {len(returns)}")
    if len(signals) < 100:
        raise SystemExit(f"ERROR: unexpectedly few inventory legacy signals: {len(signals)}")
    return {
        "inventory_json": str(inventory_json),
        "return_count": len(returns),
        "legacy_signal_count": len(signals),
        "mode": "read_only_summary_only_no_registry_classification",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V3.4-2-fix metadata-only registry check")
    parser.add_argument("--registry-file", required=True)
    parser.add_argument("--inventory-json", required=True)
    parser.add_argument("--report-out", required=True)
    args = parser.parse_args()

    report = {
        "version": "v3.4.2-fix",
        "purpose": "verify metadata-only legacy route registry",
        "source_check": check_registry_source(Path(args.registry_file)),
        "metadata": registry_metadata(),
        "registered_branch_count": len(DEFAULT_LEGACY_ROUTE_REGISTRY),
        "registered_routes": [item.to_dict() for item in list_legacy_route_metadata()],
        "metadata_unit_cases": run_metadata_unit_cases(),
        "rejection_cases": run_rejection_cases(),
        "dict_resolution_case": run_dict_resolution_case(),
        "inventory_readonly_summary": read_inventory_readonly_summary(Path(args.inventory_json)),
        "action_map_smoke": {
            "general_chat": legacy_route_to_v3_action("general_chat"),
            "advice_analysis": legacy_route_to_v3_action("advice_analysis"),
            "cmdb_query": legacy_route_to_v3_action("cmdb_query"),
            "config_change": legacy_route_to_v3_action("config_change"),
        },
    }

    Path(args.report_out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("legacy_route_registry_metadata_only=OK")
    print("legacy_route_registry_no_category_tokens=OK")
    print("legacy_route_registry_rejects_natural_language_fields=OK")
    print("legacy_route_registry_inventory_readonly=OK")
    print("legacy_route_registry_no_runtime_wiring=OK")
    print("v3_4_2_fix_registry_check=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
