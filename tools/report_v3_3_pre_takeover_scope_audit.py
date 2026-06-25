# -*- coding: utf-8 -*-
"""
V3.3 pre-takeover scope audit.

This tool is read-only:
- It does not modify app.py.
- It does not restart service.
- It does not call chat API endpoint.
- It does not execute device commands.

Purpose:
Evaluate whether shadow records are not only gate-eligible and response-ready,
but also suitable for real frontend takeover based on answer/content quality.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.takeover_response import (
    evaluate_shadow_record_response_readiness,
    extract_answer_text,
)

DEFAULT_SHADOW_DIR = Path("/var/lib/netaiops-asset-agent/data/v3_intent_shadow")


ANSWER_FIELD_ORDER = [
    ("v3_plan", "answer"),
    ("v3_plan", "final_answer"),
    ("v3_plan", "message"),
    ("v3_plan", "user_message"),
    ("v3_plan", "clarification_question"),
    ("v3_plan", "reason"),
    ("v3_decision", "answer"),
    ("v3_decision", "message"),
    ("v3_decision", "clarification_question"),
    ("record", "answer"),
    ("record", "message"),
]


PLACEHOLDER_KEYWORDS = {
    "offline safe design case",
    "fallback",
    "unknown",
    "test",
    "smoke",
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
                yield line_no, {"_invalid_json": True, "_error": repr(exc), "_raw": line[:500]}
                continue
            yield line_no, payload


def _get_container(record: Dict[str, Any], container_name: str) -> Dict[str, Any]:
    if container_name == "record":
        return record
    value = record.get(container_name)
    return value if isinstance(value, dict) else {}


def detect_answer_source(record: Dict[str, Any]) -> str:
    for container_name, field in ANSWER_FIELD_ORDER:
        container = _get_container(record, container_name)
        value = container.get(field)
        if isinstance(value, str) and value.strip():
            return f"{container_name}.{field}"
    return ""


def content_quality(action: str, answer_text: str, source: str, readiness: Dict[str, Any]) -> Tuple[bool, str]:
    answer_text = answer_text or ""
    answer_len = len(answer_text.strip())
    lowered = answer_text.lower()

    if action == "need_clarification":
        if readiness.get("ready"):
            return True, "clarification_fallback_allowed"
        return False, "clarification_not_ready"

    if action == "cmdb_query":
        if not readiness.get("has_cmdb_items"):
            return False, "cmdb_missing_items"
        return True, "cmdb_items_available"

    if action in {"general_chat", "advice_analysis"}:
        if not answer_text.strip():
            return False, "missing_answer_text"
        if source.endswith(".reason"):
            return False, "answer_source_is_reason_not_frontend_answer"
        if answer_len < 20:
            return False, "answer_text_too_short"
        if any(keyword in lowered for keyword in PLACEHOLDER_KEYWORDS):
            return False, "answer_text_looks_placeholder"
        return True, "answer_text_quality_ok"

    return False, "action_not_in_first_takeover_scope"


def analyze_record(line_no: int, record: Dict[str, Any]) -> Dict[str, Any]:
    if record.get("_invalid_json"):
        return {
            "line_no": line_no,
            "invalid_json": True,
            "pre_takeover_candidate": False,
            "pre_takeover_reason": "invalid_json",
            "raw_error": record.get("_error"),
        }

    plan = record.get("v3_plan") if isinstance(record.get("v3_plan"), dict) else {}
    decision = record.get("v3_decision") if isinstance(record.get("v3_decision"), dict) else {}
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}

    gate = extra.get("takeover_gate_if_enabled") if isinstance(extra.get("takeover_gate_if_enabled"), dict) else {}
    response_ready = (
        extra.get("takeover_response_readiness_if_enabled")
        if isinstance(extra.get("takeover_response_readiness_if_enabled"), dict)
        else evaluate_shadow_record_response_readiness(record)
    )

    action = str(plan.get("action") or decision.get("action") or response_ready.get("action") or "")
    handler = str(plan.get("handler_key") or response_ready.get("handler_key") or action or "")
    answer = extract_answer_text(plan=plan, decision=decision, context=record)
    answer_source = detect_answer_source(record)

    gate_takeover = gate.get("takeover")
    gate_eligible = gate.get("eligible")
    response_is_ready = response_ready.get("ready")

    quality_ok, quality_reason = content_quality(action, answer, answer_source, response_ready)

    pre_candidate = bool(gate_takeover is True and response_is_ready is True and quality_ok)
    if not gate:
        reason = "missing_gate_if_enabled"
    elif gate_takeover is not True:
        reason = f"gate_not_takeover:{gate.get('reason')}"
    elif response_is_ready is not True:
        reason = f"response_not_ready:{response_ready.get('reason')}"
    elif not quality_ok:
        reason = f"content_quality_blocked:{quality_reason}"
    else:
        reason = "candidate"

    return {
        "line_no": line_no,
        "conversation_id": record.get("conversation_id"),
        "v2_route": record.get("v2_route"),
        "action": action,
        "handler_key": handler,
        "gate_takeover_if_enabled": gate_takeover,
        "gate_eligible_if_enabled": gate_eligible,
        "gate_reason": gate.get("reason"),
        "response_ready": response_is_ready,
        "response_reason": response_ready.get("reason"),
        "answer_source": answer_source,
        "answer_length": len(answer),
        "answer_prefix": answer[:160],
        "content_quality_ok": quality_ok,
        "content_quality_reason": quality_reason,
        "pre_takeover_candidate": pre_candidate,
        "pre_takeover_reason": reason,
        "question_prefix": str(record.get("question") or "")[:160],
    }


def analyze(path: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    invalid_records = 0

    for line_no, record in iter_records(path):
        row = analyze_record(line_no, record)
        rows.append(row)
        if row.get("invalid_json"):
            invalid_records += 1

    by_candidate = collections.Counter(str(bool(row.get("pre_takeover_candidate"))) for row in rows)
    by_reason = collections.Counter(str(row.get("pre_takeover_reason") or "") for row in rows)
    by_action = collections.Counter(str(row.get("action") or "") for row in rows)
    by_content_quality = collections.Counter(str(row.get("content_quality_reason") or "") for row in rows)
    by_answer_source = collections.Counter(str(row.get("answer_source") or "<none>") for row in rows)

    candidates = [row for row in rows if row.get("pre_takeover_candidate")]
    blocked = [row for row in rows if not row.get("pre_takeover_candidate")]

    recommended_first_scope: List[str] = []
    if any(row.get("action") == "need_clarification" and row.get("pre_takeover_candidate") for row in rows):
        recommended_first_scope.append("need_clarification")
    if any(row.get("action") == "general_chat" and row.get("pre_takeover_candidate") for row in rows):
        recommended_first_scope.append("general_chat")
    if any(row.get("action") == "advice_analysis" and row.get("pre_takeover_candidate") for row in rows):
        recommended_first_scope.append("advice_analysis")
    if any(row.get("action") == "cmdb_query" and row.get("pre_takeover_candidate") for row in rows):
        recommended_first_scope.append("cmdb_query")

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "shadow_file": str(path),
        "total_records": len(rows),
        "invalid_records": invalid_records,
        "by_pre_takeover_candidate": dict(by_candidate),
        "by_pre_takeover_reason": dict(by_reason),
        "by_action": dict(by_action),
        "by_content_quality_reason": dict(by_content_quality),
        "by_answer_source": dict(by_answer_source),
        "candidate_samples": candidates[:50],
        "blocked_samples": blocked[:50],
        "recommended_first_scope": recommended_first_scope,
        "notes": [
            "This audit is intentionally stricter than response readiness.",
            "Records whose answer source is only v3_plan.reason are blocked from first real takeover.",
            "cmdb_query requires actual V3-side CMDB items before real takeover.",
            "A first real takeover batch should only implement a switch-disabled code path until this report is reviewed.",
        ],
    }


def write_markdown(summary: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3.3-7 Pre-Takeover Scope Audit")
    lines.append("")
    lines.append(f"- created_at: {summary['created_at']}")
    lines.append(f"- shadow_file: `{summary['shadow_file']}`")
    lines.append(f"- total_records: `{summary['total_records']}`")
    lines.append(f"- invalid_records: `{summary['invalid_records']}`")
    lines.append(f"- recommended_first_scope: `{summary['recommended_first_scope']}`")
    lines.append("")

    for title, key in [
        ("Pre takeover candidate", "by_pre_takeover_candidate"),
        ("Pre takeover reason", "by_pre_takeover_reason"),
        ("Action", "by_action"),
        ("Content quality reason", "by_content_quality_reason"),
        ("Answer source", "by_answer_source"),
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

    lines.append("## Candidate samples")
    candidates = summary.get("candidate_samples") or []
    if not candidates:
        lines.append("- none")
    for row in candidates[:30]:
        lines.append(
            f"- line {row.get('line_no')}: action=`{row.get('action')}` "
            f"source=`{row.get('answer_source')}` reason=`{row.get('pre_takeover_reason')}` "
            f"q=`{row.get('question_prefix')}`"
        )
    lines.append("")

    lines.append("## Blocked samples")
    blocked = summary.get("blocked_samples") or []
    if not blocked:
        lines.append("- none")
    for row in blocked[:30]:
        lines.append(
            f"- line {row.get('line_no')}: action=`{row.get('action')}` "
            f"reason=`{row.get('pre_takeover_reason')}` "
            f"answer_source=`{row.get('answer_source')}` "
            f"answer_len=`{row.get('answer_length')}` "
            f"q=`{row.get('question_prefix')}`"
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
        shadow_file = DEFAULT_SHADOW_DIR / sorted(p.name for p in DEFAULT_SHADOW_DIR.glob("shadow_*.jsonl"))[-1]

    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    summary = analyze(shadow_file)

    json_path = report_dir / "v3_3_pre_takeover_scope_audit.json"
    md_path = report_dir / "v3_3_pre_takeover_scope_audit.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(summary, md_path)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_pre_takeover_scope_audit=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
