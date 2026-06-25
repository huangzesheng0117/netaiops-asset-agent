# -*- coding: utf-8 -*-
"""
V3.3-9 precise reuse locator.

Read-only behavior:
- Does not modify app.py.
- Does not restart service.
- Does not call chat API.
- Does not call LLM, CMDB, MCP, or any device execution path.

Purpose:
Narrow V3.3-8's broad 120 candidates into practical reuse targets:
1) frontend response builders / JSON contract producers
2) LLM answer generation functions / clients
3) CMDB query functions producing items/columns/count
4) V2 middleware branch return points and their nearby calls
"""

from __future__ import annotations

import ast
import collections
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

SCAN_ROOTS = [
    PROJECT / "app.py",
    PROJECT / "netaiops_asset",
    PROJECT / "tools",
]

EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".git",
    "backup",
    "venv",
    ".venv",
    "node_modules",
}

FIRST_SCOPE_ACTIONS = {
    "general_chat",
    "advice_analysis",
    "need_clarification",
    "cmdb_query",
}

RISK_KEYWORDS = {
    "netmiko",
    "execute",
    "command",
    "mcp",
    "device",
    "ssh",
    "reload",
    "reboot",
    "write",
    "delete",
    "shutdown",
}

LLM_KEYWORDS = {
    "llm",
    "qwen",
    "openai",
    "completion",
    "chat_completion",
    "chat/completions",
    "model",
    "temperature",
    "messages",
    "prompt",
}

RESPONSE_KEYWORDS = {
    "answer",
    "final_answer",
    "message",
    "status",
    "planner_source",
    "conversation_id",
    "JSONResponse",
    "items",
    "columns",
    "count",
    "returned",
    "field_labels",
}

CMDB_KEYWORDS = {
    "cmdb",
    "networkserver",
    "networkServer",
    "management_ip",
    "hostname",
    "device_type",
    "items",
    "columns",
    "field_labels",
}

ACTION_KEYWORDS = {
    "general_chat",
    "advice_analysis",
    "need_clarification",
    "cmdb_query",
    "batch67",
    "advice",
    "clarification",
}


def iter_py_files() -> Iterable[Path]:
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        if root.is_dir():
            for path in root.rglob("*.py"):
                if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                    continue
                yield path


def safe_read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT))
    except Exception:
        return str(path)


def snippet(lines: List[str], start_line: int, before: int = 4, after: int = 10) -> str:
    start = max(1, start_line - before)
    end = min(len(lines), start_line + after)
    return "\n".join(f"{idx:05d}: {lines[idx - 1]}" for idx in range(start, end + 1))


def source_segment(lines: List[str], start: int, end: Optional[int], max_lines: int = 120) -> str:
    if end is None:
        end = start
    end = min(end, start + max_lines - 1)
    return "\n".join(f"{idx:05d}: {lines[idx - 1]}" for idx in range(start, end + 1))


def names_in_node(node: ast.AST) -> List[str]:
    names: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.append(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            names.append(sub.value)
    return names


def called_names(node: ast.AST) -> List[str]:
    values: List[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name):
            values.append(func.id)
        elif isinstance(func, ast.Attribute):
            parts = [func.attr]
            value = func.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            values.append(".".join(reversed(parts)))
    return values


def literal_keys(node: ast.AST) -> List[str]:
    keys: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for key in sub.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.append(key.value)
    return keys


def return_dict_keys(node: ast.AST) -> List[str]:
    keys: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return):
            keys.extend(literal_keys(sub))
        elif isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name) and sub.func.id == "JSONResponse":
                keys.extend(literal_keys(sub))
            elif isinstance(sub.func, ast.Attribute) and sub.func.attr == "JSONResponse":
                keys.extend(literal_keys(sub))
    return sorted(set(keys))


def score_function(name: str, block_text: str, calls: List[str], keys: List[str]) -> Dict[str, Any]:
    lower = block_text.lower()
    name_lower = name.lower()
    calls_lower = " ".join(calls).lower()
    keys_lower = " ".join(keys).lower()

    llm_hits = sorted({kw for kw in LLM_KEYWORDS if kw.lower() in lower or kw.lower() in name_lower or kw.lower() in calls_lower})
    response_hits = sorted({kw for kw in RESPONSE_KEYWORDS if kw.lower() in lower or kw.lower() in name_lower or kw.lower() in keys_lower})
    cmdb_hits = sorted({kw for kw in CMDB_KEYWORDS if kw.lower() in lower or kw.lower() in name_lower or kw.lower() in keys_lower})
    action_hits = sorted({kw for kw in ACTION_KEYWORDS if kw.lower() in lower or kw.lower() in name_lower})
    risk_hits = sorted({kw for kw in RISK_KEYWORDS if kw.lower() in lower or kw.lower() in name_lower})

    score = 0
    score += 8 * len(response_hits)
    score += 10 * len(llm_hits)
    score += 10 * len(cmdb_hits)
    score += 8 * len(action_hits)
    score += 6 if "jsonresponse" in lower else 0
    score += 5 if "return" in lower and ("answer" in lower or "items" in lower) else 0
    score -= 6 * len(risk_hits)

    categories: List[str] = []
    if llm_hits:
        categories.append("llm_generation")
    if response_hits or "jsonresponse" in lower:
        categories.append("response_contract")
    if cmdb_hits:
        categories.append("cmdb_query")
    if action_hits:
        categories.append("first_scope_action")
    if risk_hits:
        categories.append("risk_related")

    return {
        "score": score,
        "categories": categories or ["generic"],
        "llm_hits": llm_hits,
        "response_hits": response_hits,
        "cmdb_hits": cmdb_hits,
        "action_hits": action_hits,
        "risk_hits": risk_hits,
    }


def parse_functions(path: Path, text: str) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    records: List[Dict[str, Any]] = []

    try:
        tree = ast.parse(text, filename=str(path))
    except Exception as exc:
        return [{"path": rel(path), "parse_error": repr(exc)}]

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        name = getattr(node, "name", "")
        if start is None:
            continue

        block = "\n".join(lines[start - 1 : end]) if end else lines[start - 1]
        calls = sorted(set(called_names(node)))
        keys = return_dict_keys(node)
        scoring = score_function(name, block, calls, keys)

        if scoring["score"] <= 0 and not scoring["categories"]:
            continue

        records.append(
            {
                "path": rel(path),
                "node_type": type(node).__name__,
                "name": name,
                "line": start,
                "end_line": end,
                "score": scoring["score"],
                "categories": scoring["categories"],
                "return_or_json_keys": keys,
                "calls": calls[:80],
                "llm_hits": scoring["llm_hits"],
                "response_hits": scoring["response_hits"],
                "cmdb_hits": scoring["cmdb_hits"],
                "action_hits": scoring["action_hits"],
                "risk_hits": scoring["risk_hits"],
                "snippet": source_segment(lines, start, end, max_lines=60),
            }
        )

    return records


def locate_fastapi_routes(path: Path, text: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    lines = text.splitlines()

    try:
        tree = ast.parse(text, filename=str(path))
    except Exception:
        return records

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        decorators = []
        for deco in node.decorator_list:
            decorators.append(ast.unparse(deco) if hasattr(ast, "unparse") else "")

        joined = "\n".join(decorators)
        if "api/v1/chat" not in joined and "middleware" not in joined:
            continue

        records.append(
            {
                "path": rel(path),
                "name": node.name,
                "line": node.lineno,
                "end_line": node.end_lineno,
                "decorators": decorators,
                "calls": sorted(set(called_names(node)))[:120],
                "return_keys": return_dict_keys(node),
                "snippet": source_segment(lines, node.lineno, node.end_lineno, max_lines=120),
            }
        )

    return records


def locate_v2_shadow_branches(path: Path, text: str) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    records: List[Dict[str, Any]] = []
    marker = '_v3_shadow_write(v3_shadow_state, question, user, conversation_id, "'

    for idx, line in enumerate(lines, start=1):
        if marker not in line:
            continue
        route = line.split(marker, 1)[1].split('"', 1)[0]
        records.append(
            {
                "v2_route": route,
                "line": idx,
                "snippet": snippet(lines, idx, before=8, after=8),
            }
        )

    return records


def locate_imports(path: Path, text: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=str(path))
    except Exception:
        return records

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if any(token in name.lower() for token in ["llm", "cmdb", "chat_v2", "openai", "requests"]):
                    records.append({"path": rel(path), "line": node.lineno, "import": name, "asname": alias.asname})
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            joined_names = ",".join(alias.name for alias in node.names)
            haystack = f"{module} {joined_names}".lower()
            if any(token in haystack for token in ["llm", "cmdb", "chat_v2", "openai", "requests"]):
                records.append(
                    {
                        "path": rel(path),
                        "line": node.lineno,
                        "from": module,
                        "import": joined_names,
                    }
                )
    return records


def rank_candidates(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets = {
        "llm_generation": [],
        "response_contract": [],
        "cmdb_query": [],
        "first_scope_action": [],
        "low_risk_reuse": [],
        "risk_related": [],
    }

    for record in records:
        categories = set(record.get("categories") or [])
        for bucket in buckets:
            if bucket in categories:
                buckets[bucket].append(record)
        if (
            record.get("score", 0) >= 15
            and "risk_related" not in categories
            and categories.intersection({"llm_generation", "response_contract", "cmdb_query", "first_scope_action"})
        ):
            buckets["low_risk_reuse"].append(record)

    for bucket in buckets:
        buckets[bucket] = sorted(
            buckets[bucket],
            key=lambda x: (x.get("score", 0), -(x.get("line") or 0)),
            reverse=True,
        )[:40]

    return buckets


def summarize_recommendation(buckets: Dict[str, List[Dict[str, Any]]], routes: List[Dict[str, Any]]) -> Dict[str, Any]:
    top_llm = buckets.get("llm_generation", [])[:8]
    top_response = buckets.get("response_contract", [])[:8]
    top_cmdb = buckets.get("cmdb_query", [])[:8]
    low_risk = buckets.get("low_risk_reuse", [])[:12]

    status = "OK_TO_PROCEED_WITH_RESPONSE_GENERATOR_DESIGN"
    blockers: List[str] = []

    if not top_llm:
        blockers.append("No clear LLM generation candidate found.")
    if not top_response:
        blockers.append("No clear response contract candidate found.")
    if not top_cmdb:
        blockers.append("No clear CMDB query candidate found.")
    if len(routes) < 10:
        blockers.append("Expected 10 V2 shadow branch records were not found.")

    if blockers:
        status = "NEED_MANUAL_REVIEW"

    return {
        "status": status,
        "blockers": blockers,
        "recommended_next_batch": (
            "V3.3-10 create offline V3 response_generator adapter using located low-risk candidates"
            if status == "OK_TO_PROCEED_WITH_RESPONSE_GENERATOR_DESIGN"
            else "Review locator report before generating V3.3-10"
        ),
        "top_llm_candidates": [
            {k: item.get(k) for k in ["path", "name", "line", "score", "categories", "calls", "llm_hits", "risk_hits"]}
            for item in top_llm
        ],
        "top_response_candidates": [
            {k: item.get(k) for k in ["path", "name", "line", "score", "categories", "return_or_json_keys", "response_hits", "risk_hits"]}
            for item in top_response
        ],
        "top_cmdb_candidates": [
            {k: item.get(k) for k in ["path", "name", "line", "score", "categories", "return_or_json_keys", "cmdb_hits", "risk_hits"]}
            for item in top_cmdb
        ],
        "top_low_risk_reuse_candidates": [
            {k: item.get(k) for k in ["path", "name", "line", "score", "categories", "return_or_json_keys", "calls", "risk_hits"]}
            for item in low_risk
        ],
    }


def write_markdown(report: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("# V3.3-9 Precise Reuse Locator")
    lines.append("")
    lines.append(f"- created_at: {report['created_at']}")
    lines.append(f"- scanned_py_files: `{report['scanned_py_files']}`")
    lines.append(f"- v2_shadow_branch_count: `{len(report.get('v2_shadow_branches') or [])}`")
    lines.append(f"- route_count: `{len(report.get('fastapi_routes') or [])}`")
    lines.append(f"- recommendation_status: `{report['recommendation']['status']}`")
    lines.append("")

    blockers = report["recommendation"].get("blockers") or []
    lines.append("## Recommendation")
    lines.append(f"- next_batch: {report['recommendation'].get('recommended_next_batch')}")
    if blockers:
        for blocker in blockers:
            lines.append(f"- blocker: {blocker}")
    else:
        lines.append("- blocker: none")
    lines.append("")

    lines.append("## V2 shadow branches")
    for item in report.get("v2_shadow_branches", []):
        lines.append(f"### `{item.get('v2_route')}` line `{item.get('line')}`")
        lines.append("```text")
        lines.append(item.get("snippet") or "")
        lines.append("```")
        lines.append("")

    for title, key in [
        ("Top LLM candidates", "top_llm_candidates"),
        ("Top response candidates", "top_response_candidates"),
        ("Top CMDB candidates", "top_cmdb_candidates"),
        ("Top low-risk reuse candidates", "top_low_risk_reuse_candidates"),
    ]:
        lines.append(f"## {title}")
        for item in report["recommendation"].get(key, [])[:20]:
            lines.append(
                f"- `{item.get('path')}` line `{item.get('line')}` "
                f"`{item.get('name')}` score=`{item.get('score')}` "
                f"categories=`{item.get('categories')}` risk=`{item.get('risk_hits')}`"
            )
        if not report["recommendation"].get(key):
            lines.append("- none")
        lines.append("")

    lines.append("## Detailed top function snippets")
    detailed = report.get("detailed_top_candidates") or []
    for item in detailed[:30]:
        lines.append(f"### `{item.get('path')}` line `{item.get('line')}` `{item.get('name')}`")
        lines.append(f"- score: `{item.get('score')}`")
        lines.append(f"- categories: `{item.get('categories')}`")
        lines.append(f"- return_or_json_keys: `{item.get('return_or_json_keys')}`")
        lines.append("```text")
        lines.append(item.get("snippet") or "")
        lines.append("```")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(set(iter_py_files()))
    all_functions: List[Dict[str, Any]] = []
    routes: List[Dict[str, Any]] = []
    shadow_branches: List[Dict[str, Any]] = []
    imports: List[Dict[str, Any]] = []
    file_errors: List[Dict[str, Any]] = []

    for path in files:
        try:
            text = safe_read(path)
        except Exception as exc:
            file_errors.append({"path": rel(path), "error": repr(exc)})
            continue

        all_functions.extend(parse_functions(path, text))
        imports.extend(locate_imports(path, text))

        if path.name == "app.py":
            routes.extend(locate_fastapi_routes(path, text))
            shadow_branches.extend(locate_v2_shadow_branches(path, text))

    clean_functions = [item for item in all_functions if not item.get("parse_error")]
    parse_errors = [item for item in all_functions if item.get("parse_error")]

    buckets = rank_candidates(clean_functions)
    recommendation = summarize_recommendation(buckets, shadow_branches)

    detailed_top_candidates = sorted(
        {
            (item.get("path"), item.get("name"), item.get("line")): item
            for bucket in ["llm_generation", "response_contract", "cmdb_query", "low_risk_reuse"]
            for item in buckets.get(bucket, [])[:12]
        }.values(),
        key=lambda x: x.get("score", 0),
        reverse=True,
    )[:40]

    report = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "project": str(PROJECT),
        "scanned_py_files": len(files),
        "file_errors": file_errors,
        "parse_errors": parse_errors[:20],
        "fastapi_routes": routes,
        "v2_shadow_branches": shadow_branches,
        "import_hits": imports[:200],
        "function_category_counts": {
            bucket: len(items) for bucket, items in buckets.items()
        },
        "recommendation": recommendation,
        "detailed_top_candidates": detailed_top_candidates,
    }

    json_path = report_dir / "v3_3_precise_reuse_points.json"
    md_path = report_dir / "v3_3_precise_reuse_points.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(report, md_path)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    print("v3_3_precise_reuse_points=OK")

    if recommendation["status"] == "NEED_MANUAL_REVIEW":
        print("NEED_MANUAL_REVIEW=1")
    else:
        print("NEED_MANUAL_REVIEW=0")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
