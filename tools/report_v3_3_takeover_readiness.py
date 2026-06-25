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

from netaiops_asset.chat_v3.takeover_gate import evaluate_shadow_record

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

    by_enabled_takeover = collections.Counter()
    by_eligible = collections.Counter()
    by_reason = collections.Counter()
    by_v2_route = collections.Counter()
    by_handler = collections.Counter()
    by_pair = collections.Counter()

    takeover_candidates: List[Dict[str, Any]] = []
    blocked_samples: List[Dict[str, Any]] = []
    invalid_samples: List[Dict[str, Any]] = []

    for line_no, payload in iter_records(path):
        total_lines += 1

        if payload.get("_invalid_json"):
            invalid_records += 1
            if len(invalid_samples) < 20:
                invalid_samples.append({"line_no": line_no, **payload})
            continue

        valid_records += 1

        # Force enabled=True here to calculate "would take over if switch is enabled".
        gate = evaluate_shadow_record(payload, enabled=True)
        gate["line_no"] = line_no

        by_enabled_takeover[str(bool(gate.get("takeover")))] += 1
        by_eligible[str(bool(gate.get("eligible")))] += 1
        by_reason[gate.get("reason") or ""] += 1
        by_v2_route[gate.get("v2_route") or ""] += 1
        by_handler[gate.get("handler_key") or gate.get("action") or ""] += 1
        by_pair[f"{gate.get('v2_route')} -> {gate.get('handler_key') or gate.get('action')}"] += 1

        sample = {
            "line_no": line_no,
            "conversation_id": gate.get("conversation_id"),
            "v2_route": gate.get("v2_route"),
            "action": gate.get("action"),
            "handler_key": gate.get("handler_key"),
            "eligible": gate.get("eligible"),
            "takeover_if_enabled": gate.get("takeover"),
            "reason": gate.get("reason"),
            "effective_confidence": gate.get("effective_confidence"),
            "is_diff": gate.get("is_diff"),
            "question_prefix": gate.get("question_prefix"),
        }

        if gate.get("takeover") and len(takeover_candidates) < 50:
            takeover_candidates.append(sample)
        elif not gate.get("eligible") and len(blocked_samples) < 50:
            blocked_samples.append(sample)

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "shadow_file": str(path),
        "total_lines": total_lines,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "takeover_enabled_runtime_default": os.environ.get("NETAIOPS_V3_TAKEOVER_ENABLED", "<unset/default_false>"),
        "by_takeover_if_enabled": dict(by_enabled_takeover),
        "by_eligible": dict(by_eligible),
        "by_reason": dict(by_reason),
        "by_v2_route": dict(by_v2_route),
        "by_handler": dict(by_handler),
        "by_pair": dict(by_pair),
        "takeover_candidates": takeover_candidates,
        "blocked_samples": blocked_samples,
        "invalid_samples": invalid_samples,
        "notes": [
            "This report forces enabled=True only for readiness analysis.",
            "Runtime takeover still defaults to disabled unless NETAIOPS_V3_TAKEOVER_ENABLED=1.",
            "Execute/generate/confirm/analyze-existing-evidence paths are intentionally blocked in V3.3-1.",
        ],
    }


def write_markdown(summary: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3.3-1 Takeover Readiness Report")
    lines.append("")
    lines.append(f"- created_at: {summary['created_at']}")
    lines.append(f"- shadow_file: `{summary['shadow_file']}`")
    lines.append(f"- total_lines: `{summary['total_lines']}`")
    lines.append(f"- valid_records: `{summary['valid_records']}`")
    lines.append(f"- invalid_records: `{summary['invalid_records']}`")
    lines.append(f"- runtime_switch: `{summary['takeover_enabled_runtime_default']}`")
    lines.append("")

    for title, key in [
        ("Takeover if enabled", "by_takeover_if_enabled"),
        ("Eligibility", "by_eligible"),
        ("Gate reasons", "by_reason"),
        ("V2 route", "by_v2_route"),
        ("V3 handler", "by_handler"),
        ("V2 -> V3 pair", "by_pair"),
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
    samples = summary.get("takeover_candidates") or []
    if not samples:
        lines.append("- none")
    for item in samples[:30]:
        lines.append(
            f"- line {item.get('line_no')}: v2=`{item.get('v2_route')}` "
            f"v3=`{item.get('handler_key')}` reason=`{item.get('reason')}` "
            f"eff_conf=`{item.get('effective_confidence')}` q=`{item.get('question_prefix')}`"
        )
    lines.append("")

    lines.append("## Blocked samples")
    samples = summary.get("blocked_samples") or []
    if not samples:
        lines.append("- none")
    for item in samples[:30]:
        lines.append(
            f"- line {item.get('line_no')}: v2=`{item.get('v2_route')}` "
            f"v3=`{item.get('handler_key')}` reason=`{item.get('reason')}` "
            f"eff_conf=`{item.get('effective_confidence')}` q=`{item.get('question_prefix')}`"
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

    json_path = report_dir / "v3_3_takeover_readiness.json"
    md_path = report_dir / "v3_3_takeover_readiness.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(summary, md_path)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_takeover_readiness=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
