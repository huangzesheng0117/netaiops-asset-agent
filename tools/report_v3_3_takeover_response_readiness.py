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

from netaiops_asset.chat_v3.takeover_response import evaluate_shadow_record_response_readiness

DEFAULT_SHADOW_DIR = Path("/var/lib/netaiops-asset-agent/data/v3_intent_shadow")


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


def analyze(path: Path) -> Dict[str, Any]:
    total_lines = 0
    valid_records = 0
    invalid_records = 0

    by_ready = collections.Counter()
    by_reason = collections.Counter()
    by_action = collections.Counter()
    by_handler = collections.Counter()
    by_v2_route = collections.Counter()

    ready_samples: List[Dict[str, Any]] = []
    not_ready_samples: List[Dict[str, Any]] = []
    invalid_samples: List[Dict[str, Any]] = []

    for line_no, payload in iter_records(path):
        total_lines += 1

        if payload.get("_invalid_json"):
            invalid_records += 1
            if len(invalid_samples) < 20:
                invalid_samples.append({"line_no": line_no, **payload})
            continue

        valid_records += 1
        result = evaluate_shadow_record_response_readiness(payload)
        result["line_no"] = line_no

        by_ready[str(bool(result.get("ready")))] += 1
        by_reason[result.get("reason") or ""] += 1
        by_action[result.get("action") or ""] += 1
        by_handler[result.get("handler_key") or ""] += 1
        by_v2_route[result.get("v2_route") or ""] += 1

        sample = {
            "line_no": line_no,
            "conversation_id": result.get("conversation_id"),
            "v2_route": result.get("v2_route"),
            "action": result.get("action"),
            "handler_key": result.get("handler_key"),
            "ready": result.get("ready"),
            "reason": result.get("reason"),
            "gate_takeover": result.get("gate_takeover"),
            "gate_eligible": result.get("gate_eligible"),
            "has_answer_text": result.get("has_answer_text"),
            "has_cmdb_items": result.get("has_cmdb_items"),
            "question_prefix": result.get("question_prefix"),
        }

        if result.get("ready") and len(ready_samples) < 50:
            ready_samples.append(sample)
        elif not result.get("ready") and len(not_ready_samples) < 50:
            not_ready_samples.append(sample)

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "shadow_file": str(path),
        "total_lines": total_lines,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "by_response_ready": dict(by_ready),
        "by_reason": dict(by_reason),
        "by_action": dict(by_action),
        "by_handler": dict(by_handler),
        "by_v2_route": dict(by_v2_route),
        "ready_samples": ready_samples,
        "not_ready_samples": not_ready_samples,
        "invalid_samples": invalid_samples,
        "notes": [
            "Gate eligible does not mean response ready.",
            "V3.3 should not take over a request unless both takeover gate and response readiness pass.",
            "cmdb_query requires V3-side CMDB result items before actual takeover.",
            "general_chat/advice_analysis require answer text before actual takeover.",
            "need_clarification can use deterministic fallback and may be the first safe real takeover candidate.",
        ],
    }


def write_markdown(summary: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3.3-4 Takeover Response Readiness")
    lines.append("")
    lines.append(f"- created_at: {summary['created_at']}")
    lines.append(f"- shadow_file: `{summary['shadow_file']}`")
    lines.append(f"- total_lines: `{summary['total_lines']}`")
    lines.append(f"- valid_records: `{summary['valid_records']}`")
    lines.append(f"- invalid_records: `{summary['invalid_records']}`")
    lines.append("")

    for title, key in [
        ("Response ready", "by_response_ready"),
        ("Reason", "by_reason"),
        ("Action", "by_action"),
        ("Handler", "by_handler"),
        ("V2 route", "by_v2_route"),
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

    lines.append("## Ready samples")
    for item in (summary.get("ready_samples") or [])[:30]:
        lines.append(
            f"- line {item.get('line_no')}: action=`{item.get('action')}` "
            f"reason=`{item.get('reason')}` q=`{item.get('question_prefix')}`"
        )
    if not summary.get("ready_samples"):
        lines.append("- none")
    lines.append("")

    lines.append("## Not ready samples")
    for item in (summary.get("not_ready_samples") or [])[:30]:
        lines.append(
            f"- line {item.get('line_no')}: action=`{item.get('action')}` "
            f"reason=`{item.get('reason')}` "
            f"gate_takeover=`{item.get('gate_takeover')}` q=`{item.get('question_prefix')}`"
        )
    if not summary.get("not_ready_samples"):
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

    json_path = report_dir / "v3_3_takeover_response_readiness.json"
    md_path = report_dir / "v3_3_takeover_response_readiness.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(summary, md_path)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_takeover_response_readiness=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
