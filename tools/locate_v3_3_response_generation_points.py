# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

SCAN_FILES = [
    PROJECT / "app.py",
]
for subdir in [
    PROJECT / "netaiops_asset" / "chat_v2",
    PROJECT / "netaiops_asset" / "chat_v3",
    PROJECT / "netaiops_asset" / "llm",
    PROJECT / "netaiops_asset" / "cmdb",
]:
    if subdir.exists():
        SCAN_FILES.extend(sorted(subdir.rglob("*.py")))

KEYWORDS = [
    "answer",
    "final_answer",
    "JSONResponse",
    "planner_source",
    "advice",
    "general_chat",
    "need_clarification",
    "cmdb_query",
    "llm",
    "chat_completion",
    "completion",
    "qwen",
    "generate",
    "reason",
    "message",
]


def safe_read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def snippet(lines: List[str], line_no: int, before: int = 3, after: int = 6) -> str:
    start = max(1, line_no - before)
    end = min(len(lines), line_no + after)
    return "\n".join(f"{idx:05d}: {lines[idx - 1]}" for idx in range(start, end + 1))


def function_records(path: Path, text: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=str(path))
    except Exception as exc:
        return [{"path": str(path), "parse_error": repr(exc)}]

    lines = text.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        name = getattr(node, "name", "")
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)

        if start is None:
            continue

        block = "\n".join(lines[start - 1 : end]) if end else lines[start - 1]
        lower = block.lower()
        hit_keywords = [kw for kw in KEYWORDS if kw.lower() in lower or kw.lower() in name.lower()]

        if not hit_keywords:
            continue

        category = "generic"
        if any(kw in lower for kw in ["jsonresponse", "response", "answer", "final_answer", "message"]):
            category = "response_shape"
        if any(kw in lower for kw in ["llm", "qwen", "completion"]):
            category = "llm_generation"
        if any(kw in lower for kw in ["cmdb", "networkserver"]):
            category = "cmdb_result"
        if any(kw in lower for kw in ["advice", "general_chat", "need_clarification"]):
            category = "chat_answer"

        records.append(
            {
                "path": str(path.relative_to(PROJECT)),
                "type": type(node).__name__,
                "name": name,
                "line": start,
                "end_line": end,
                "category": category,
                "keywords": hit_keywords[:20],
                "snippet": snippet(lines, start, before=2, after=8),
            }
        )

    return records


def keyword_hits(path: Path, text: str) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    hits: List[Dict[str, Any]] = []
    patterns = [
        r'"answer"',
        r"'answer'",
        r'"final_answer"',
        r"'final_answer'",
        r'"planner_source"',
        r"'planner_source'",
        r'JSONResponse\(',
        r'def .*llm',
        r'class .*LLM',
        r'chat_completion',
        r'completion',
        r'qwen',
    ]

    for idx, line in enumerate(lines, start=1):
        for pattern in patterns:
            if re.search(pattern, line, flags=re.IGNORECASE):
                hits.append(
                    {
                        "path": str(path.relative_to(PROJECT)),
                        "line": idx,
                        "pattern": pattern,
                        "line_text": line.strip()[:240],
                    }
                )
                break

    return hits


def analyze() -> Dict[str, Any]:
    files = []
    funcs = []
    hits = []

    for path in SCAN_FILES:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = safe_read(path)
        except Exception as exc:
            files.append({"path": str(path), "read_error": repr(exc)})
            continue

        rel = str(path.relative_to(PROJECT))
        files.append({"path": rel, "bytes": len(text.encode("utf-8", errors="replace"))})
        funcs.extend(function_records(path, text))
        hits.extend(keyword_hits(path, text))

    categories = {}
    for record in funcs:
        categories.setdefault(record.get("category", "unknown"), 0)
        categories[record.get("category", "unknown")] += 1

    candidate_reuse_points = []
    for record in funcs:
        if record.get("category") in {"llm_generation", "response_shape", "chat_answer", "cmdb_result"}:
            candidate_reuse_points.append(record)

    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "project": str(PROJECT),
        "scanned_file_count": len(files),
        "files": files,
        "function_category_counts": categories,
        "candidate_reuse_points": candidate_reuse_points[:120],
        "keyword_hits": hits[:200],
        "recommended_next_steps": [
            "Do not enable real takeover yet because V3 shadow records currently lack frontend-ready answer text.",
            "Locate the existing V2 LLM answer generation path and wrap it behind a V3 response generator interface.",
            "For cmdb_query, reuse existing CMDB query logic to populate V3-side result items before takeover.",
            "For need_clarification, deterministic fallback can be implemented first, but only if gate confidence is intentionally allowed.",
            "After response generator exists, rerun shadow audit until pre_takeover_candidate is no longer zero.",
        ],
    }


def write_markdown(report: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3.3-8 Response Generation Reuse Point Locator")
    lines.append("")
    lines.append(f"- created_at: {report['created_at']}")
    lines.append(f"- scanned_file_count: `{report['scanned_file_count']}`")
    lines.append(f"- function_category_counts: `{report['function_category_counts']}`")
    lines.append("")

    lines.append("## Candidate reuse points")
    for item in report.get("candidate_reuse_points", [])[:80]:
        lines.append(
            f"- `{item.get('path')}` line `{item.get('line')}` "
            f"{item.get('type')} `{item.get('name')}` category=`{item.get('category')}` "
            f"keywords=`{item.get('keywords')}`"
        )
    if not report.get("candidate_reuse_points"):
        lines.append("- none")
    lines.append("")

    lines.append("## Keyword hits")
    for item in report.get("keyword_hits", [])[:80]:
        lines.append(
            f"- `{item.get('path')}` line `{item.get('line')}` pattern=`{item.get('pattern')}`: "
            f"`{item.get('line_text')}`"
        )
    if not report.get("keyword_hits"):
        lines.append("- none")
    lines.append("")

    lines.append("## Recommended next steps")
    for item in report.get("recommended_next_steps", []):
        lines.append(f"- {item}")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    report = analyze()

    json_path = report_dir / "v3_3_response_generation_points.json"
    md_path = report_dir / "v3_3_response_generation_points.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(report, md_path)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_response_generation_points=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
