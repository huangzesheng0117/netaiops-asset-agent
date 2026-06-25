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

from netaiops_asset.chat_v3.response_generator import generate_from_shadow_record

DEFAULT_SHADOW_DIR = Path("/var/lib/netaiops-asset-agent/data/v3_intent_shadow")


class FakeLLM:
    def chat(self, messages, **kwargs):
        return {
            "status": "ok",
            "content": "这是离线假 LLM 生成的前端回答，用于评估 V3 response generator 的接管准备度，不代表真实线上回答。",
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
                    "offline_ready": False,
                    "fake_llm_ready": False,
                    "offline_reason": "invalid_json",
                    "fake_llm_reason": "invalid_json",
                }
            )
            continue

        offline = generate_from_shadow_record(record, allow_live_llm=False)
        fake = generate_from_shadow_record(record, allow_live_llm=True, llm_client=FakeLLM())

        plan = record.get("v3_plan") if isinstance(record.get("v3_plan"), dict) else {}
        decision = record.get("v3_decision") if isinstance(record.get("v3_decision"), dict) else {}

        rows.append(
            {
                "line_no": line_no,
                "conversation_id": record.get("conversation_id"),
                "v2_route": record.get("v2_route"),
                "action": plan.get("action") or decision.get("action"),
                "handler_key": plan.get("handler_key"),
                "offline_ready": offline.get("ready"),
                "offline_generated": offline.get("generated"),
                "offline_reason": offline.get("reason"),
                "offline_source": offline.get("source"),
                "fake_llm_ready": fake.get("ready"),
                "fake_llm_generated": fake.get("generated"),
                "fake_llm_reason": fake.get("reason"),
                "fake_llm_source": fake.get("source"),
                "question_prefix": str(record.get("question") or "")[:160],
            }
        )

    by_offline_ready = collections.Counter(str(bool(row.get("offline_ready"))) for row in rows)
    by_offline_reason = collections.Counter(str(row.get("offline_reason") or "") for row in rows)
    by_fake_ready = collections.Counter(str(bool(row.get("fake_llm_ready"))) for row in rows)
    by_fake_reason = collections.Counter(str(row.get("fake_llm_reason") or "") for row in rows)
    by_action = collections.Counter(str(row.get("action") or "") for row in rows)

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "shadow_file": str(path),
        "total_records": len(rows),
        "invalid_records": invalid_records,
        "by_action": dict(by_action),
        "by_offline_ready": dict(by_offline_ready),
        "by_offline_reason": dict(by_offline_reason),
        "by_fake_llm_ready": dict(by_fake_ready),
        "by_fake_llm_reason": dict(by_fake_reason),
        "samples": rows[:80],
        "notes": [
            "offline_ready uses allow_live_llm=False and never calls LLM.",
            "fake_llm_ready uses a fake in-process LLM and proves generator wiring, not real answer quality.",
            "V3.3-11 should attach this generator to shadow dry-run only, controlled by live LLM env switch.",
        ],
    }


def write_markdown(summary: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3.3-10 Offline Response Generator Report")
    lines.append("")
    lines.append(f"- created_at: {summary['created_at']}")
    lines.append(f"- shadow_file: `{summary['shadow_file']}`")
    lines.append(f"- total_records: `{summary['total_records']}`")
    lines.append(f"- invalid_records: `{summary['invalid_records']}`")
    lines.append("")

    for title, key in [
        ("Action", "by_action"),
        ("Offline ready", "by_offline_ready"),
        ("Offline reason", "by_offline_reason"),
        ("Fake LLM ready", "by_fake_llm_ready"),
        ("Fake LLM reason", "by_fake_llm_reason"),
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

    lines.append("## Samples")
    for row in summary.get("samples") or []:
        lines.append(
            f"- line {row.get('line_no')}: action=`{row.get('action')}` "
            f"offline_ready=`{row.get('offline_ready')}` reason=`{row.get('offline_reason')}` "
            f"fake_ready=`{row.get('fake_llm_ready')}` fake_reason=`{row.get('fake_llm_reason')}` "
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
        shadow_file = latest_shadow_file()

    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    summary = analyze(shadow_file)

    json_path = report_dir / "v3_3_response_generator_offline.json"
    md_path = report_dir / "v3_3_response_generator_offline.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(summary, md_path)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_response_generator_offline=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
