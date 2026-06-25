# -*- coding: utf-8 -*-
"""
V3 shadow diff analysis reporter.

This script is read-only for shadow JSONL logs.
It summarizes V2 route vs V3 action/handler differences, confidence buckets,
shadow errors, and representative samples.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


DEFAULT_SHADOW_DIR = Path("/var/lib/netaiops-asset-agent/data/v3_intent_shadow")


ROUTE_TO_V3_HANDLER_ALIAS = {
    "v2_advice_analysis": "advice_analysis",
    "v2_inline_command_execute": "execute_provided_commands",
    "v2_inline_command_execute_error": "execute_provided_commands",
    "v2_semantic_execution_confirmation": "confirm_execute_pending",
    "v2_semantic_followup_analysis": "analyze_existing_evidence",
    "v2_execution_request_confirmation": "confirm_execute_pending",
    "v2_followup_analysis": "analyze_existing_evidence",
    "v2_execution_confirmation": "confirm_execute_pending",
    "v2_chat_router": "generate_commands",
    "call_next_to_legacy_chat": "legacy_chat_or_fallback",
}


def latest_shadow_file() -> Path:
    files = sorted(DEFAULT_SHADOW_DIR.glob("shadow_*.jsonl"))
    if not files:
        raise SystemExit(f"No shadow_*.jsonl files found in {DEFAULT_SHADOW_DIR}")
    return files[-1]


def iter_records(path: Path) -> Iterable[Tuple[int, Dict[str, Any]]]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except Exception as exc:
                yield line_no, {
                    "_invalid_json": True,
                    "_error": repr(exc),
                    "_raw": line[:500],
                }
                continue

            yield line_no, payload


def confidence_bucket(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return "unknown"

    if number >= 0.90:
        return "0.90-1.00"
    if number >= 0.80:
        return "0.80-0.89"
    if number >= 0.60:
        return "0.60-0.79"
    if number >= 0.50:
        return "0.50-0.59"
    if number >= 0.00:
        return "0.00-0.49"
    return "unknown"


def compact_record(line_no: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = payload.get("v3_plan") or {}
    decision = payload.get("v3_decision") or {}
    extra = payload.get("extra") or {}

    return {
        "line_no": line_no,
        "created_at": payload.get("created_at"),
        "conversation_id": payload.get("conversation_id"),
        "v2_route": payload.get("v2_route"),
        "v3_action": plan.get("action") or decision.get("action"),
        "v3_handler_key": plan.get("handler_key"),
        "confidence": plan.get("confidence") or decision.get("confidence"),
        "effective_confidence": plan.get("effective_confidence"),
        "is_diff": payload.get("is_diff"),
        "shadow_error": extra.get("shadow_error") or "",
        "question_prefix": str(payload.get("question") or "")[:160],
    }


def analyze(path: Path) -> Dict[str, Any]:
    by_v2_route = collections.Counter()
    by_v3_action = collections.Counter()
    by_v3_handler = collections.Counter()
    by_pair = collections.Counter()
    by_shadow_error = collections.Counter()
    by_is_diff = collections.Counter()
    by_confidence_bucket = collections.Counter()
    by_effective_confidence_bucket = collections.Counter()
    by_expected_handler_match = collections.Counter()

    total_lines = 0
    valid_records = 0
    invalid_records = 0

    diff_samples: List[Dict[str, Any]] = []
    route_samples: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    error_samples: List[Dict[str, Any]] = []
    low_confidence_samples: List[Dict[str, Any]] = []

    for line_no, payload in iter_records(path):
        total_lines += 1

        if payload.get("_invalid_json"):
            invalid_records += 1
            if len(error_samples) < 20:
                error_samples.append(
                    {
                        "line_no": line_no,
                        "invalid_json": True,
                        "error": payload.get("_error"),
                        "raw": payload.get("_raw"),
                    }
                )
            continue

        valid_records += 1
        plan = payload.get("v3_plan") or {}
        decision = payload.get("v3_decision") or {}
        extra = payload.get("extra") or {}
        compact = compact_record(line_no, payload)

        v2_route = str(payload.get("v2_route") or "")
        v3_action = str(plan.get("action") or decision.get("action") or "")
        v3_handler = str(plan.get("handler_key") or "")
        shadow_error = str(extra.get("shadow_error") or "")
        is_diff = bool(payload.get("is_diff"))

        by_v2_route[v2_route] += 1
        by_v3_action[v3_action] += 1
        by_v3_handler[v3_handler] += 1
        by_pair[f"{v2_route} -> {v3_handler or v3_action}"] += 1
        by_shadow_error[shadow_error] += 1
        by_is_diff[str(is_diff)] += 1

        confidence = plan.get("confidence") if plan.get("confidence") is not None else decision.get("confidence")
        effective = plan.get("effective_confidence")
        by_confidence_bucket[confidence_bucket(confidence)] += 1
        by_effective_confidence_bucket[confidence_bucket(effective)] += 1

        expected = ROUTE_TO_V3_HANDLER_ALIAS.get(v2_route, "")
        if expected:
            match = expected == (v3_handler or v3_action)
            by_expected_handler_match[str(match)] += 1
        else:
            by_expected_handler_match["no_expected_mapping"] += 1

        if is_diff and len(diff_samples) < 50:
            diff_samples.append(compact)

        if len(route_samples[v2_route]) < 5:
            route_samples[v2_route].append(compact)

        if shadow_error and len(error_samples) < 20:
            error_samples.append(compact)

        try:
            effective_float = float(effective)
        except Exception:
            effective_float = 0.0

        if effective_float < 0.80 and len(low_confidence_samples) < 30:
            low_confidence_samples.append(compact)

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "path": str(path),
        "total_lines": total_lines,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "by_v2_route": dict(by_v2_route),
        "by_v3_action": dict(by_v3_action),
        "by_v3_handler": dict(by_v3_handler),
        "by_pair": dict(by_pair),
        "by_shadow_error": dict(by_shadow_error),
        "by_is_diff": dict(by_is_diff),
        "by_confidence_bucket": dict(by_confidence_bucket),
        "by_effective_confidence_bucket": dict(by_effective_confidence_bucket),
        "by_expected_handler_match": dict(by_expected_handler_match),
        "diff_samples": diff_samples,
        "route_samples": dict(route_samples),
        "error_samples": error_samples,
        "low_confidence_samples": low_confidence_samples,
        "notes": [
            "is_diff=True is expected in shadow mode and does not mean user-visible failure.",
            "call_next_to_legacy_chat may map to several V3 actions because legacy chat handles multiple fallback cases.",
            "execute-related API tests are intentionally excluded here to avoid triggering real device command execution through V2.",
        ],
    }


def write_markdown(summary: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3 Shadow Diff Analysis")
    lines.append("")
    lines.append(f"- created_at: {summary['created_at']}")
    lines.append(f"- shadow_file: `{summary['path']}`")
    lines.append(f"- total_lines: {summary['total_lines']}")
    lines.append(f"- valid_records: {summary['valid_records']}")
    lines.append(f"- invalid_records: {summary['invalid_records']}")
    lines.append("")

    for title, key in [
        ("V2 route distribution", "by_v2_route"),
        ("V3 action distribution", "by_v3_action"),
        ("V3 handler distribution", "by_v3_handler"),
        ("V2 -> V3 pair distribution", "by_pair"),
        ("Shadow error distribution", "by_shadow_error"),
        ("is_diff distribution", "by_is_diff"),
        ("confidence buckets", "by_confidence_bucket"),
        ("effective confidence buckets", "by_effective_confidence_bucket"),
        ("expected handler match", "by_expected_handler_match"),
    ]:
        lines.append(f"## {title}")
        data = summary.get(key) or {}
        if not data:
            lines.append("- none")
        else:
            for name, count in sorted(data.items(), key=lambda x: str(x[0])):
                display = name if name != "" else "<empty>"
                lines.append(f"- `{display}`: {count}")
        lines.append("")

    lines.append("## Diff samples")
    samples = summary.get("diff_samples") or []
    if not samples:
        lines.append("- none")
    for item in samples[:30]:
        lines.append(
            f"- line {item.get('line_no')}: v2=`{item.get('v2_route')}` "
            f"v3=`{item.get('v3_handler_key') or item.get('v3_action')}` "
            f"eff_conf=`{item.get('effective_confidence')}` "
            f"q=`{item.get('question_prefix')}`"
        )
    lines.append("")

    lines.append("## Notes")
    for note in summary.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) > 1:
        shadow_file = Path(sys.argv[1])
    else:
        shadow_file = latest_shadow_file()

    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    summary = analyze(shadow_file)

    json_path = report_dir / "v3_shadow_diff_analysis.json"
    md_path = report_dir / "v3_shadow_diff_analysis.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(summary, md_path)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_shadow_diff_analysis=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
