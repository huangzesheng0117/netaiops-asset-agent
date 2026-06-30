#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ReturnRecord:
    function: str
    function_type: str
    route_paths: list[str]
    lineno: int
    end_lineno: int
    return_kind: str
    wrapped_by_v3: bool
    contains_jsonresponse: bool
    contains_call_next: bool
    contains_shadow_write: bool
    route_class: str
    risk_level: str
    snippet: str
    context: str


@dataclass
class LegacySignal:
    lineno: int
    category: str
    risk_level: str
    matched_tokens: list[str]
    context: str


ROUTE_KEYWORDS: dict[str, list[str]] = {
    "general_chat": [
        "general_chat",
        "文本解释",
        "解释一下",
        "什么是",
        "是什么",
        "含义",
        "作用",
        "区别",
    ],
    "advice_analysis": [
        "advice",
        "advice_analysis",
        "建议",
        "风险",
        "是否建议",
        "如何处理",
        "怎么处理",
        "排查思路",
        "batch67_advice",
    ],
    "followup": [
        "followup",
        "follow_up",
        "继续",
        "上一个",
        "刚才",
        "这个设备",
        "上一轮",
        "history",
        "conversation",
    ],
    "cmdb_query": [
        "cmdb",
        "CMDB",
        "query_cmdb",
        "device",
        "device_name",
        "设备查询",
        "管理IP",
        "管理 IP",
        "设备类型",
    ],
    "inline_command": [
        "inline",
        "inline_command",
        "command",
        "show ",
        "display ",
        "命令",
        "执行",
        "下发",
    ],
    "semantic_route": [
        "semantic",
        "semantic_route",
        "route",
        "router",
        "planner_source",
    ],
    "batch_route": [
        "batch",
        "batch63",
        "batch67",
        "batch68",
    ],
}

RISK_RULES: list[tuple[str, list[str]]] = [
    ("high", ["执行", "下发", "删除", "修改配置", "重启", "command_execution", "send", "write", "delete"]),
    ("medium", ["cmdb", "CMDB", "管理IP", "管理 IP", "设备类型", "device", "inline", "command", "show ", "display "]),
    ("low", ["general_chat", "advice", "建议", "解释", "含义", "区别", "原理"]),
]


def classify_text(text: str) -> tuple[str, str, list[str]]:
    lowered = text.lower()
    category_hits: dict[str, list[str]] = {}
    for category, tokens in ROUTE_KEYWORDS.items():
        hits = []
        for token in tokens:
            if token.lower() in lowered or token in text:
                hits.append(token)
        if hits:
            category_hits[category] = hits

    priority = [
        "inline_command",
        "cmdb_query",
        "followup",
        "advice_analysis",
        "general_chat",
        "semantic_route",
        "batch_route",
    ]
    route_class = "unknown"
    matched: list[str] = []
    for category in priority:
        if category in category_hits:
            route_class = category
            matched = category_hits[category]
            break

    risk_level = "unknown"
    for risk, tokens in RISK_RULES:
        if any(token.lower() in lowered or token in text for token in tokens):
            risk_level = risk
            break
    if risk_level == "unknown":
        if route_class in {"general_chat", "advice_analysis"}:
            risk_level = "low"
        elif route_class in {"followup", "semantic_route", "batch_route"}:
            risk_level = "medium"
        elif route_class in {"cmdb_query", "inline_command"}:
            risk_level = "medium"

    return route_class, risk_level, matched


def get_source(source: str, node: ast.AST) -> str:
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:
        return ""


def route_info(node: ast.AST) -> list[str]:
    routes = []
    for deco in getattr(node, "decorator_list", []):
        if not isinstance(deco, ast.Call):
            continue
        func = deco.func
        method = ""
        if isinstance(func, ast.Attribute):
            method = func.attr
        elif isinstance(func, ast.Name):
            method = func.id
        if method not in {"get", "post", "api_route", "middleware"}:
            continue
        if deco.args:
            try:
                routes.append(str(ast.literal_eval(deco.args[0])))
            except Exception:
                try:
                    routes.append(ast.unparse(deco.args[0]))
                except Exception:
                    routes.append("")
        elif method == "middleware":
            routes.append("@middleware")
    return routes


def get_context(lines: list[str], lineno: int, radius: int = 5) -> str:
    start = max(1, lineno - radius)
    end = min(len(lines), lineno + radius)
    numbered = []
    for idx in range(start, end + 1):
        numbered.append(f"{idx}: {lines[idx - 1]}")
    return "\n".join(numbered)


def function_type(node: ast.AST, routes: list[str]) -> str:
    name = getattr(node, "name", "")
    if "/api/v1/chat" in routes:
        return "chat_route"
    if name == "v2_chat_router_middleware":
        return "chat_middleware"
    if routes:
        return "api_or_middleware_route"
    if name.startswith("_v3"):
        return "v3_helper"
    if "route" in name.lower() or "chat" in name.lower():
        return "chat_related_function"
    return "function"


def collect_functions(tree: ast.AST) -> list[ast.AST]:
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(node)
    return sorted(nodes, key=lambda item: item.lineno)


def find_parent_function(functions: list[ast.AST], node: ast.AST) -> ast.AST | None:
    lineno = int(getattr(node, "lineno", 0))
    for func in functions:
        start = int(getattr(func, "lineno", 0))
        end = int(getattr(func, "end_lineno", start))
        if start <= lineno <= end:
            return func
    return None


def collect_returns(source: str, lines: list[str], tree: ast.AST) -> list[ReturnRecord]:
    functions = collect_functions(tree)
    records: list[ReturnRecord] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        func = find_parent_function(functions, node)
        if func is None:
            continue
        fname = getattr(func, "name", "<unknown>")
        routes = route_info(func)
        ftype = function_type(func, routes)
        full = get_source(source, node)
        value = get_source(source, node.value) if node.value is not None else ""
        route_class, risk_level, _matched = classify_text(get_context(lines, node.lineno, radius=6) + "\n" + full)
        return_kind = "empty"
        if node.value is not None:
            if isinstance(node.value, ast.Call):
                try:
                    return_kind = ast.unparse(node.value.func)
                except Exception:
                    return_kind = "call"
            elif isinstance(node.value, ast.Name):
                return_kind = f"name:{node.value.id}"
            elif isinstance(node.value, ast.Dict):
                return_kind = "dict"
            else:
                return_kind = type(node.value).__name__

        record = ReturnRecord(
            function=fname,
            function_type=ftype,
            route_paths=routes,
            lineno=int(node.lineno),
            end_lineno=int(getattr(node, "end_lineno", node.lineno)),
            return_kind=return_kind,
            wrapped_by_v3="_v3_apply_chat_canary_takeover" in full,
            contains_jsonresponse="JSONResponse" in full,
            contains_call_next="call_next" in full,
            contains_shadow_write="_v3_shadow_write" in get_context(lines, node.lineno, radius=8),
            route_class=route_class,
            risk_level=risk_level,
            snippet=" ".join(full.strip().split())[:300],
            context=get_context(lines, node.lineno, radius=4),
        )
        records.append(record)
    return sorted(records, key=lambda item: item.lineno)


def collect_legacy_signals(lines: list[str]) -> list[LegacySignal]:
    signals: list[LegacySignal] = []
    for idx, line in enumerate(lines, start=1):
        context = get_context(lines, idx, radius=2)
        category, risk, matched = classify_text(context)
        if category == "unknown":
            continue
        if any(marker in context for marker in [
            "semantic",
            "followup",
            "advice",
            "inline",
            "batch",
            "cmdb",
            "CMDB",
            "keyword",
            "planner_source",
            "JSONResponse",
            "try_handle",
            "route",
        ]):
            signals.append(
                LegacySignal(
                    lineno=idx,
                    category=category,
                    risk_level=risk,
                    matched_tokens=matched,
                    context=context,
                )
            )
    dedup: dict[tuple[int, str], LegacySignal] = {}
    for item in signals:
        dedup[(item.lineno, item.category)] = item
    return [dedup[key] for key in sorted(dedup)]


def summarize(records: list[ReturnRecord], signals: list[LegacySignal]) -> dict[str, Any]:
    def count_by(items, attr):
        out: dict[str, int] = {}
        for item in items:
            key = getattr(item, attr)
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    chat_related_returns = [
        r for r in records
        if r.function_type in {"chat_route", "chat_middleware"} or "/api/v1/chat" in r.route_paths or r.function == "v2_chat_router_middleware"
    ]
    middleware_returns = [r for r in records if r.function == "v2_chat_router_middleware"]
    chat_route_returns = [r for r in records if r.function_type == "chat_route"]

    return {
        "return_count_total": len(records),
        "chat_related_return_count": len(chat_related_returns),
        "middleware_return_count": len(middleware_returns),
        "chat_route_return_count": len(chat_route_returns),
        "middleware_jsonresponse_return_count": sum(1 for r in middleware_returns if r.contains_jsonresponse),
        "middleware_jsonresponse_wrapped_count": sum(1 for r in middleware_returns if r.contains_jsonresponse and r.wrapped_by_v3),
        "chat_route_wrapped_count": sum(1 for r in chat_route_returns if r.wrapped_by_v3),
        "route_class_counts": count_by(records, "route_class"),
        "risk_level_counts": count_by(records, "risk_level"),
        "legacy_signal_count": len(signals),
        "legacy_signal_category_counts": count_by(signals, "category"),
        "legacy_signal_risk_counts": count_by(signals, "risk_level"),
    }


def markdown_table(rows: list[list[Any]], headers: list[str]) -> str:
    def cell(value: Any) -> str:
        text = str(value).replace("\n", "<br>").replace("|", "\\|")
        return text
    out = []
    out.append("| " + " | ".join(cell(h) for h in headers) + " |")
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        out.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(out)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    returns = report["returns"]
    signals = report["legacy_signals"]

    chat_returns = [
        r for r in returns
        if r["function_type"] in {"chat_route", "chat_middleware"}
        or r["function"] == "v2_chat_router_middleware"
        or "/api/v1/chat" in r.get("route_paths", [])
    ]
    high_value_signals = [
        s for s in signals
        if s["category"] in {"inline_command", "cmdb_query", "followup", "advice_analysis", "semantic_route", "general_chat"}
    ][:80]

    md = []
    md.append("# ChatBot V3.4-1 Legacy Route Inventory")
    md.append("")
    md.append("## 目标")
    md.append("")
    md.append("V3.4-1 只做旧路由盘点和真实入口地图，不修改线上行为，不重启服务。")
    md.append("")
    md.append("## 总览")
    md.append("")
    md.append(markdown_table(
        [
            ["return_count_total", summary["return_count_total"]],
            ["chat_related_return_count", summary["chat_related_return_count"]],
            ["middleware_return_count", summary["middleware_return_count"]],
            ["chat_route_return_count", summary["chat_route_return_count"]],
            ["middleware_jsonresponse_return_count", summary["middleware_jsonresponse_return_count"]],
            ["middleware_jsonresponse_wrapped_count", summary["middleware_jsonresponse_wrapped_count"]],
            ["chat_route_wrapped_count", summary["chat_route_wrapped_count"]],
            ["legacy_signal_count", summary["legacy_signal_count"]],
        ],
        ["Metric", "Value"],
    ))
    md.append("")
    md.append("## Route Class Counts")
    md.append("")
    md.append(markdown_table([[k, v] for k, v in summary["route_class_counts"].items()], ["Route Class", "Count"]))
    md.append("")
    md.append("## Risk Level Counts")
    md.append("")
    md.append(markdown_table([[k, v] for k, v in summary["risk_level_counts"].items()], ["Risk", "Count"]))
    md.append("")
    md.append("## /api/v1/chat 相关 return 地图")
    md.append("")
    md.append(markdown_table(
        [
            [
                r["lineno"],
                r["function"],
                r["function_type"],
                ",".join(r["route_paths"]),
                r["return_kind"],
                r["contains_jsonresponse"],
                r["wrapped_by_v3"],
                r["route_class"],
                r["risk_level"],
                r["snippet"],
            ]
            for r in chat_returns
        ],
        ["Line", "Function", "Type", "Routes", "Return", "JSONResponse", "V3 Wrapped", "Class", "Risk", "Snippet"],
    ))
    md.append("")
    md.append("## 旧路由信号样本")
    md.append("")
    md.append(markdown_table(
        [
            [
                s["lineno"],
                s["category"],
                s["risk_level"],
                ",".join(s["matched_tokens"]),
                s["context"],
            ]
            for s in high_value_signals
        ],
        ["Line", "Category", "Risk", "Matched", "Context"],
    ))
    md.append("")
    md.append("## V3.4 后续建议")
    md.append("")
    md.append("1. V3.4-2 建立 Legacy Route Registry，先登记旧路由类型，不改行为。")
    md.append("2. V3.4-3 优先收敛 general_chat / advice_analysis。")
    md.append("3. V3.4-4 再处理 follow-up / 多轮上下文。")
    md.append("4. V3.4-5 单独处理 inline 抢路由，但不进入 V3.5 command splitter。")
    md.append("5. V3.4-6 再删除或禁用重复旧分支。")
    md.append("")
    md.append("## 边界")
    md.append("")
    md.append("- 本批不修改 app.py。")
    md.append("- 本批不重启服务。")
    md.append("- 本批不扩大 V3 takeover 范围。")
    md.append("- 本批只新增 inventory 工具和文档。")
    md.append("")
    path.write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="V3.4-1 legacy route inventory")
    parser.add_argument("--app", default="app.py")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--doc-out", required=True)
    args = parser.parse_args()

    app_path = Path(args.app)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    source = app_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(app_path))

    records = collect_returns(source, lines, tree)
    signals = collect_legacy_signals(lines)
    summary = summarize(records, signals)

    report = {
        "version": "v3.4.1",
        "purpose": "legacy route inventory",
        "app_path": str(app_path),
        "summary": summary,
        "returns": [asdict(item) for item in records],
        "legacy_signals": [asdict(item) for item in signals],
    }

    (report_dir / "v3_4_1_legacy_route_inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_markdown(Path(args.doc_out), report)

    # Hard expectations based on V3.3 closeout structure.
    if summary["middleware_return_count"] < 1:
        raise SystemExit("ERROR: no v2_chat_router_middleware returns found")
    if summary["chat_route_return_count"] < 1:
        raise SystemExit("ERROR: no /api/v1/chat route returns found")
    if summary["middleware_jsonresponse_return_count"] < 1:
        raise SystemExit("ERROR: no middleware JSONResponse returns found")
    if summary["middleware_jsonresponse_wrapped_count"] != summary["middleware_jsonresponse_return_count"]:
        raise SystemExit(
            "ERROR: not all middleware JSONResponse returns are V3 wrapped: "
            + json.dumps(summary, ensure_ascii=False)
        )
    if summary["chat_route_wrapped_count"] != summary["chat_route_return_count"]:
        raise SystemExit(
            "ERROR: not all chat route returns are V3 wrapped: "
            + json.dumps(summary, ensure_ascii=False)
        )
    if summary["legacy_signal_count"] < 10:
        raise SystemExit("ERROR: unexpectedly few legacy route signals found")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print("v3_4_1_route_inventory=OK")
    print("middleware_return_map=OK")
    print("legacy_route_classification=OK")
    print("no_behavior_change_inventory_only=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
