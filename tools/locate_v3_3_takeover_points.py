# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def snippet(lines: List[str], line_no: int, before: int = 6, after: int = 8) -> List[str]:
    start = max(1, line_no - before)
    end = min(len(lines), line_no + after)
    return [f"{idx:05d}: {lines[idx - 1]}" for idx in range(start, end + 1)]


def main() -> int:
    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    app_path = PROJECT / "app.py"
    text = app_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    tree = ast.parse(text, filename=str(app_path))
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "v2_chat_router_middleware":
            target = node
            break

    if target is None:
        raise SystemExit("v2_chat_router_middleware not found")

    shadow_build_lines = [
        idx for idx, line in enumerate(lines, start=1)
        if "v3_shadow_state = _v3_shadow_build(" in line
    ]

    shadow_write_lines = [
        idx for idx, line in enumerate(lines, start=1)
        if "_v3_shadow_write(v3_shadow_state" in line
    ]

    return_lines = sorted([node.lineno for node in ast.walk(target) if isinstance(node, ast.Return)])

    branch_records: List[Dict[str, Any]] = []
    for line_no in shadow_write_lines:
        block = "\n".join(snippet(lines, line_no, before=2, after=3))
        route = ""
        marker = '_v3_shadow_write(v3_shadow_state, question, user, conversation_id, "'
        raw_line = lines[line_no - 1]
        if marker in raw_line:
            route = raw_line.split(marker, 1)[1].split('"', 1)[0]
        branch_records.append(
            {
                "shadow_write_line": line_no,
                "v2_route": route,
                "snippet": block,
            }
        )

    report = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "app_path": str(app_path),
        "middleware": {
            "name": "v2_chat_router_middleware",
            "line": target.lineno,
            "end_line": target.end_lineno,
        },
        "shadow_build_lines": shadow_build_lines,
        "shadow_write_count": len(shadow_write_lines),
        "shadow_write_lines": shadow_write_lines,
        "return_count": len(return_lines),
        "return_lines": return_lines,
        "branch_records": branch_records,
        "recommended_v3_3_insertion_strategy": [
            "Do not add a new outer middleware.",
            "Keep V2 as the default response path.",
            "Use the existing v3_shadow_state generated after payload/question/user/conversation_id parsing.",
            "Add takeover evaluation after v3_shadow_state is built, but keep actual takeover switch disabled by default.",
            "Only safe branches should be eligible in early V3.3: general_chat, advice_analysis, need_clarification, cmdb_query.",
            "Do not take over execute_provided_commands, execute_provided_commands_and_analyze, confirm_execute_pending, generate_commands, or analyze_existing_evidence in V3.3-1.",
            "V3.3-2 should first add a dry-run takeover evaluation log, not immediately replace V2 JSONResponse.",
        ],
    }

    json_path = report_dir / "v3_3_takeover_points.json"
    md_path = report_dir / "v3_3_takeover_points.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    md_lines: List[str] = []
    md_lines.append("# V3.3 Takeover Insertion Points")
    md_lines.append("")
    md_lines.append(f"- created_at: {report['created_at']}")
    md_lines.append(f"- middleware: `v2_chat_router_middleware` line `{target.lineno}`-`{target.end_lineno}`")
    md_lines.append(f"- shadow_build_lines: `{shadow_build_lines}`")
    md_lines.append(f"- shadow_write_count: `{len(shadow_write_lines)}`")
    md_lines.append(f"- return_count: `{len(return_lines)}`")
    md_lines.append("")
    md_lines.append("## Branch records")
    for item in branch_records:
        md_lines.append(f"### {item['v2_route']} at line {item['shadow_write_line']}")
        md_lines.append("```text")
        md_lines.append(item["snippet"])
        md_lines.append("```")
        md_lines.append("")
    md_lines.append("## Recommended strategy")
    for item in report["recommended_v3_3_insertion_strategy"]:
        md_lines.append(f"- {item}")
    md_lines.append("")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_takeover_points=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
