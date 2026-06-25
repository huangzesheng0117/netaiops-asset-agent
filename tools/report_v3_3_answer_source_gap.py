# -*- coding: utf-8 -*-
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

from netaiops_asset.chat_v3.takeover_response import extract_answer_text

DEFAULT_SHADOW_DIR = Path("/var/lib/netaiops-asset-agent/data/v3_intent_shadow")

ANSWER_FIELDS = [
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


def container(record: Dict[str, Any], name: str) -> Dict[str, Any]:
    if name == "record":
        return record
    value = record.get(name)
    return value if isinstance(value, dict) else {}


def detect_answer_source(record: Dict[str, Any]) -> str:
    for container_name, field in ANSWER_FIELDS:
        value = container(record, container_name).get(field)
        if isinstance(value, str) and value.strip():
            return f"{container_name}.{field}"
    return ""


def analyze(path: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    invalid_records = 0

    for line_no, record in iter_records(path):
        if record.get("_invalid_json"):
            invalid_records += 1
            rows.append(
                {
                    "line_no": line_no,
                    "invalid_json": True,
                    "answer_takeover_ready": False,
                    "reason": "invalid_json",
                    "raw_error": record.get("_error"),
                }
            )
            continue

        plan = record.get("v3_plan") if isinstance(record.get("v3_plan"), dict) else {}
        decision = record.get("v3_decision") if isinstance(record.get("v3_decision"), dict) else {}
        extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
        readiness = extra.get("takeover_response_readiness_if_enabled") if isinstance(extra.get("takeover_response_readiness_if_enabled"), dict) else {}
        gate = extra.get("takeover_gate_if_enabled") if isinstance(extra.get("takeover_gate_if_enabled"), dict) else {}

        action = str(plan.get("action") or decision.get("action") or readiness.get("action") or "")
        handler = str(plan.get("handler_key") or readiness.get("handler_key") or action)
        answer = extract_answer_text(plan=plan, decision=decision, context=record)
        source = detect_answer_source(record)

        answer_takeover_ready = False
        reason = "not_ready"

        if action == "need_clarification" and readiness.get("ready") and gate.get("takeover"):
            answer_takeover_ready = True
            reason = "clarification_ready"
        elif action == "cmdb_query":
            if readiness.get("ready") and readiness.get("has_cmdb_items") and gate.get("takeover"):
                answer_takeover_ready = True
                reason = "cmdb_items_ready"
            else:
                reason = "cmdb_missing_items_or_gate_blocked"
        elif action in {"general_chat", "advice_analysis"}:
            if not gate.get("takeover"):
                reason = "gate_not_takeover"
            elif not readiness.get("ready"):
                reason = f"response_not_ready:{readiness.get('reason')}"
            elif source.endswith(".reason"):
                reason = "only_reason_field_not_frontend_answer"
            elif len(answer.strip()) < 20:
                reason = "answer_too_short"
            elif answer.strip():
                answer_takeover_ready = True
                reason = "answer_ready"
            else:
                reason = "missing_answer_text"
        else:
            reason = "action_not_in_first_scope"

        rows.append(
            {
                "line_no": line_no,
                "conversation_id": record.get("conversation_id"),
                "v2_route": record.get("v2_route"),
                "action": action,
                "handler_key": handler,
                "gate_takeover": gate.get("takeover"),
                "gate_reason": gate.get("reason"),
                "response_ready": readiness.get("ready"),
                "response_reason": readiness.get("reason"),
                "answer_source": source,
                "answer_length": len(answer),
                "answer_prefix": answer[:160],
                "answer_takeover_ready": answer_takeover_ready,
                "reason": reason,
                "question_prefix": str(record.get("question") or "")[:160],
            }
        )

    by_ready = collections.Counter(str(bool(row.get("answer_takeover_ready"))) for row in rows)
    by_reason = collections.Counter(str(row.get("reason") or "") for row in rows)
    by_source = collections.Counter(str(row.get("answer_source") or "<none>") for row in rows)
    by_action = collections.Counter(str(row.get("action") or "") for row in rows)

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "shadow_file": str(path),
        "total_records": len(rows),
        "invalid_records": invalid_records,
        "by_answer_takeover_ready": dict(by_ready),
        "by_reason": dict(by_reason),
        "by_answer_source": dict(by_source),
        "by_action": dict(by_action),
        "ready_samples": [row for row in rows if row.get("answer_takeover_ready")][:50],
        "blocked_samples": [row for row in rows if not row.get("answer_takeover_ready")][:50],
        "notes": [
            "V3.3-7 showed no real takeover candidate because answer content is not yet frontend-ready.",
            "The main observed gap is that general/advice records use v3_plan.reason, which is intent reasoning, not a user-facing answer.",
            "Next implementation should add or reuse a response generation path before enabling real takeover.",
        ],
    }


def write_markdown(summary: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3.3-8 Answer Source Gap Report")
    lines.append("")
    lines.append(f"- created_at: {summary['created_at']}")
    lines.append(f"- shadow_file: `{summary['shadow_file']}`")
    lines.append(f"- total_records: `{summary['total_records']}`")
    lines.append(f"- invalid_records: `{summary['invalid_records']}`")
    lines.append("")

    for title, key in [
        ("Answer takeover ready", "by_answer_takeover_ready"),
        ("Reason", "by_reason"),
        ("Answer source", "by_answer_source"),
        ("Action", "by_action"),
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

    lines.append("## Blocked samples")
    for row in (summary.get("blocked_samples") or [])[:30]:
        lines.append(
            f"- line {row.get('line_no')}: action=`{row.get('action')}` "
            f"reason=`{row.get('reason')}` source=`{row.get('answer_source')}` "
            f"answer_len=`{row.get('answer_length')}` q=`{row.get('question_prefix')}`"
        )
    if not summary.get("blocked_samples"):
        lines.append("- none")
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

    json_path = report_dir / "v3_3_answer_source_gap.json"
    md_path = report_dir / "v3_3_answer_source_gap.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(summary, md_path)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_answer_source_gap=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
