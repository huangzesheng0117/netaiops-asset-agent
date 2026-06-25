# -*- coding: utf-8 -*-
"""
V3 shadow JSONL stats reporter.

Usage:
  python tools/report_v3_shadow_stats.py
  python tools/report_v3_shadow_stats.py /var/lib/netaiops-asset-agent/data/v3_intent_shadow/shadow_YYYYMMDD.jsonl

This script is read-only for shadow logs.
"""

from __future__ import annotations

import collections
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


DEFAULT_SHADOW_DIR = Path("/var/lib/netaiops-asset-agent/data/v3_intent_shadow")


def latest_shadow_file() -> Path:
    files = sorted(DEFAULT_SHADOW_DIR.glob("shadow_*.jsonl"))
    if not files:
        raise SystemExit(f"No shadow_*.jsonl files found in {DEFAULT_SHADOW_DIR}")
    return files[-1]


def iter_records(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                payload["_line_no"] = line_no
                yield payload
            except Exception as exc:
                yield {
                    "_line_no": line_no,
                    "_invalid_json": True,
                    "_error": repr(exc),
                    "_raw": line[:500],
                }


def summarize(path: Path) -> Dict[str, Any]:
    records = list(iter_records(path))

    by_v2_route = collections.Counter()
    by_v3_action = collections.Counter()
    by_v3_handler = collections.Counter()
    by_shadow_error = collections.Counter()
    by_diff = collections.Counter()
    invalid = 0

    samples: List[Dict[str, Any]] = []

    for item in records:
        if item.get("_invalid_json"):
            invalid += 1
            continue

        v2_route = item.get("v2_route") or ""
        plan = item.get("v3_plan") or {}
        decision = item.get("v3_decision") or {}
        extra = item.get("extra") or {}

        by_v2_route[v2_route] += 1
        by_v3_action[plan.get("action") or decision.get("action") or ""] += 1
        by_v3_handler[plan.get("handler_key") or ""] += 1
        by_shadow_error[extra.get("shadow_error") or ""] += 1
        by_diff[str(bool(item.get("is_diff")))] += 1

        if len(samples) < 20:
            samples.append(
                {
                    "line_no": item.get("_line_no"),
                    "created_at": item.get("created_at"),
                    "conversation_id": item.get("conversation_id"),
                    "v2_route": v2_route,
                    "v3_action": plan.get("action") or decision.get("action"),
                    "v3_handler_key": plan.get("handler_key"),
                    "confidence": plan.get("confidence") or decision.get("confidence"),
                    "effective_confidence": plan.get("effective_confidence"),
                    "is_diff": item.get("is_diff"),
                    "shadow_error": extra.get("shadow_error") or "",
                    "question_prefix": str(item.get("question") or "")[:120],
                }
            )

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "path": str(path),
        "total_lines": len(records),
        "valid_records": len(records) - invalid,
        "invalid_records": invalid,
        "by_v2_route": dict(by_v2_route),
        "by_v3_action": dict(by_v3_action),
        "by_v3_handler": dict(by_v3_handler),
        "by_shadow_error": dict(by_shadow_error),
        "by_is_diff": dict(by_diff),
        "samples": samples,
    }


def main() -> int:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = latest_shadow_file()

    if not path.exists():
        raise SystemExit(f"Shadow log not found: {path}")

    summary = summarize(path)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
