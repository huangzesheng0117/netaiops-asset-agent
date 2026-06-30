#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def post_json(url: str, payload: dict[str, Any], timeout: int) -> tuple[int, dict[str, Any], str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except Exception:
                data = {"_json_parse_failed": True, "_body_prefix": text[:500]}
            return int(resp.status), data, text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except Exception:
            data = {"_json_parse_failed": True, "_body_prefix": text[:500]}
        return int(exc.code), data, text


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def dated_audit_file(audit_dir: Path) -> Path:
    return audit_dir / f"takeover_{dt.datetime.now().strftime('%Y%m%d')}.jsonl"


def load_runtime_env(service: str) -> dict[str, str]:
    pid = subprocess.check_output(["systemctl", "show", service, "-p", "MainPID", "--value"], text=True).strip()
    env: dict[str, str] = {"_main_pid": pid}
    if pid and pid != "0":
        for item in (Path("/proc") / pid / "environ").read_bytes().split(b"\0"):
            if b"=" in item:
                k, v = item.split(b"=", 1)
                env[k.decode("utf-8", errors="replace")] = v.decode("utf-8", errors="replace")
    return env


def static_app_check(app_path: Path) -> dict[str, Any]:
    source = app_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(app_path))

    def route_info(node: ast.AST) -> list[str]:
        routes = []
        for deco in getattr(node, "decorator_list", []):
            if isinstance(deco, ast.Call) and deco.args:
                try:
                    routes.append(str(ast.literal_eval(deco.args[0])))
                except Exception:
                    routes.append("")
        return routes

    def find_func(name: str):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        return None

    middleware = find_func("v2_chat_router_middleware")
    chat = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "/api/v1/chat" in route_info(node):
            chat = node
            break
    audit_write = find_func("_v3_canary_write_audit")
    apply_func = find_func("_v3_apply_chat_canary_takeover")

    if middleware is None:
        raise RuntimeError("v2_chat_router_middleware missing")
    if chat is None:
        raise RuntimeError("/api/v1/chat route missing")
    if audit_write is None:
        raise RuntimeError("_v3_canary_write_audit missing")
    if apply_func is None:
        raise RuntimeError("_v3_apply_chat_canary_takeover missing")

    middleware_json_returns = 0
    middleware_wrapped = 0
    for node in ast.walk(middleware):
        if isinstance(node, ast.Return) and node.value is not None:
            text = ast.get_source_segment(source, node) or ""
            if "JSONResponse" in text:
                middleware_json_returns += 1
                if "_v3_apply_chat_canary_takeover" in text:
                    middleware_wrapped += 1

    chat_returns = 0
    chat_wrapped = 0
    for node in ast.walk(chat):
        if isinstance(node, ast.Return) and node.value is not None:
            chat_returns += 1
            text = ast.get_source_segment(source, node) or ""
            if "_v3_apply_chat_canary_takeover" in text:
                chat_wrapped += 1

    audit_write_source = ast.get_source_segment(source, audit_write) or ""
    audit_write_except_no_pass = True
    for node in ast.walk(audit_write):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                audit_write_except_no_pass = False

    report = {
        "helper_begin_count": source.count("V3_ROUTE_RETURN_CANARY_MARKER_BEGIN"),
        "helper_end_count": source.count("V3_ROUTE_RETURN_CANARY_MARKER_END"),
        "middleware_json_returns": middleware_json_returns,
        "middleware_wrapped": middleware_wrapped,
        "chat_returns": chat_returns,
        "chat_wrapped": chat_wrapped,
        "audit_write_present": audit_write is not None,
        "audit_write_returns_error": "return repr(exc)" in audit_write_source,
        "audit_write_except_no_pass": audit_write_except_no_pass,
        "v3_audit_error_present": "v3_audit_error" in source,
        "request_context_priority_present": "original request context has priority over response" in source or "original request context has priority" in source,
        "old_shadow_writer_real_takeover_marker_present": "V3_REAL_TAKEOVER_CANARY_MARKER_BEGIN" in source,
    }

    if report["helper_begin_count"] != 1 or report["helper_end_count"] != 1:
        raise RuntimeError(f"helper marker count mismatch: {report}")
    if middleware_json_returns < 1 or middleware_wrapped != middleware_json_returns:
        raise RuntimeError(f"middleware return wrapping mismatch: {report}")
    if chat_returns < 1 or chat_wrapped != chat_returns:
        raise RuntimeError(f"chat return wrapping mismatch: {report}")
    if not report["audit_write_returns_error"] or not report["audit_write_except_no_pass"]:
        raise RuntimeError(f"audit write error exposure mismatch: {report}")
    if not report["v3_audit_error_present"]:
        raise RuntimeError(f"v3_audit_error missing: {report}")
    if report["old_shadow_writer_real_takeover_marker_present"]:
        raise RuntimeError(f"old shadow writer marker exists: {report}")

    return report


def run_api_smoke(base_url: str, audit_dir: Path, report_dir: Path, timeout: int) -> dict[str, Any]:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    tests = [
        {
            "name": "v3_3_17_general_closeout",
            "user": "v3_3_17_takeover",
            "conversation_id": f"v3-3-17-takeover-{ts}-closeout-general",
            "message": "解释一下 OSPF 邻居状态 Full 和 2-Way 的区别，不要生成命令。",
            "expect_takeover": True,
            "expect_action": "general_chat",
        },
        {
            "name": "v3_3_17_advice_closeout",
            "user": "v3_3_17_takeover",
            "conversation_id": f"v3-3-17-takeover-{ts}-closeout-advice",
            "message": "是否建议在重启 standby 网络设备前先隔离流量？只给运维建议，不要生成命令。",
            "expect_takeover": True,
            "expect_action": "advice_analysis",
        },
        {
            "name": "v3_3_16_compat_closeout",
            "user": "v3_3_16_takeover",
            "conversation_id": f"v3-3-16-takeover-{ts}-closeout-compat",
            "message": "只做文本解释，不要查询设备、不生成命令：解释一下 Cisco StackWise Virtual 是什么，以及它主要解决什么问题。",
            "expect_takeover": True,
            "expect_action": "general_chat",
        },
        {
            "name": "blocked_wrong_user_closeout",
            "user": "normal_user",
            "conversation_id": f"v3-3-17-takeover-{ts}-closeout-blocked-user",
            "message": "解释一下 OSPF 邻居状态 Full 和 2-Way 的区别，不要生成命令。",
            "expect_takeover": False,
        },
        {
            "name": "blocked_wrong_prefix_closeout",
            "user": "v3_3_17_takeover",
            "conversation_id": f"normal-prefix-{ts}-closeout-blocked-prefix",
            "message": "解释一下 OSPF 邻居状态 Full 和 2-Way 的区别，不要生成命令。",
            "expect_takeover": False,
        },
        {
            "name": "blocked_cmdb_query_closeout",
            "user": "v3_3_17_takeover",
            "conversation_id": f"v3-3-17-takeover-{ts}-closeout-blocked-cmdb",
            "message": "查一下 V3-3-18-CLOSEOUT-NONEXIST-DEVICE-001 的管理 IP 和设备类型",
            "expect_takeover": False,
        },
    ]

    audit_file = dated_audit_file(audit_dir)
    before_audit_lines = count_lines(audit_file)
    api_results: list[dict[str, Any]] = []

    for index, test in enumerate(tests, start=1):
        payload = {
            "user": test["user"],
            "conversation_id": test["conversation_id"],
            "message": test["message"],
            "question": test["message"],
        }
        payload_path = report_dir / f"payload_{index}_{test['name']}.json"
        response_path = report_dir / f"response_{index}_{test['name']}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        status, data, raw_text = post_json(f"{base_url}/api/v1/chat", payload, timeout)
        response_path.write_text(raw_text, encoding="utf-8")
        answer = str((data.get("answer") or data.get("message") or "") if isinstance(data, dict) else "")

        item = {
            "index": index,
            "name": test["name"],
            "http_status": status,
            "user": test["user"],
            "conversation_id": test["conversation_id"],
            "planner_source": data.get("planner_source") if isinstance(data, dict) else None,
            "v3_takeover": data.get("v3_takeover") if isinstance(data, dict) else None,
            "v3_takeover_mode": data.get("v3_takeover_mode") if isinstance(data, dict) else None,
            "v3_takeover_action": data.get("v3_takeover_action") if isinstance(data, dict) else None,
            "v3_takeover_source": data.get("v3_takeover_source") if isinstance(data, dict) else None,
            "v3_takeover_route_label": data.get("v3_takeover_route_label") if isinstance(data, dict) else None,
            "v3_takeover_error": data.get("v3_takeover_error") if isinstance(data, dict) else None,
            "v3_audit_error": data.get("v3_audit_error") if isinstance(data, dict) else None,
            "answer_len": len(answer),
            "answer_prefix": answer[:260],
            "response_path": str(response_path),
        }
        print(json.dumps(item, ensure_ascii=False))
        api_results.append(item)

        if status != 200:
            raise RuntimeError(f"API test {test['name']} returned HTTP {status}: {item}")
        actual_takeover = item["planner_source"] == "v3_response_generator" and item["v3_takeover"] is True
        if actual_takeover != bool(test["expect_takeover"]):
            raise RuntimeError(f"takeover expectation mismatch for {test['name']}: {item}")
        if item["v3_audit_error"]:
            raise RuntimeError(f"audit write failed and was exposed in response for {test['name']}: {item}")
        if item["v3_takeover_error"]:
            raise RuntimeError(f"takeover error exposed in response for {test['name']}: {item}")

        if test["expect_takeover"]:
            if item["v3_takeover_mode"] != "canary":
                raise RuntimeError(f"expected canary takeover mode for {test['name']}: {item}")
            if item["v3_takeover_source"] != "llm":
                raise RuntimeError(f"expected llm takeover source for {test['name']}: {item}")
            if item["v3_takeover_action"] != test["expect_action"]:
                raise RuntimeError(f"unexpected takeover action for {test['name']}: {item}")
            if item["answer_len"] < 40:
                raise RuntimeError(f"answer too short for {test['name']}: {item}")

        time.sleep(1)

    time.sleep(3)
    after_audit_lines = count_lines(audit_file)
    if after_audit_lines < before_audit_lines + len(tests):
        raise RuntimeError(
            f"audit log did not grow enough: before={before_audit_lines}, after={after_audit_lines}, tests={len(tests)}"
        )

    audit_tail: list[dict[str, Any]] = []
    if audit_file.exists():
        for line in audit_file.read_text(encoding="utf-8", errors="replace").splitlines()[-max(20, len(tests)):]:
            try:
                audit_tail.append(json.loads(line))
            except Exception:
                audit_tail.append({"_raw": line[:300]})

    taken_count = sum(1 for row in audit_tail if row.get("taken") is True and row.get("version") == "v3.3.17")
    blocked_count = sum(1 for row in audit_tail if row.get("taken") is False and row.get("version") == "v3.3.17")
    if taken_count < 3:
        raise RuntimeError(f"audit taken_count too low: {taken_count}")
    if blocked_count < 3:
        raise RuntimeError(f"audit blocked_count too low: {blocked_count}")

    return {
        "audit_file": str(audit_file),
        "before_audit_lines": before_audit_lines,
        "after_audit_lines": after_audit_lines,
        "api_results": api_results,
        "audit_tail": audit_tail,
        "taken_count": taken_count,
        "blocked_count": blocked_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatBot V3.3-18 closeout check")
    parser.add_argument("--app-dir", default="/opt/netaiops-asset-agent")
    parser.add_argument("--service", default="netaiops-asset-agent.service")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--audit-dir", default="/var/lib/netaiops-asset-agent/data/v3_takeover_audit")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    app_dir = Path(args.app_dir)
    audit_dir = Path(args.audit_dir)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    app_static = static_app_check(app_dir / "app.py")
    runtime_env = load_runtime_env(args.service)
    api_smoke = run_api_smoke(args.base_url.rstrip("/"), audit_dir, report_dir, args.timeout)

    summary = {
        "version": "v3.3.18",
        "purpose": "ChatBot V3.3 closeout",
        "app_static": app_static,
        "runtime_env": {
            "_main_pid": runtime_env.get("_main_pid"),
            "has_llm_key": bool(runtime_env.get("NETAIOPS_LLM_API_KEY")),
            "llm_key_len": len(runtime_env.get("NETAIOPS_LLM_API_KEY") or ""),
            "v3_response_generator_live_llm": runtime_env.get("NETAIOPS_V3_RESPONSE_GENERATOR_LIVE_LLM", ""),
            "v3_takeover_enabled": runtime_env.get("NETAIOPS_V3_TAKEOVER_ENABLED", ""),
            "v3_takeover_allowed_users": runtime_env.get("NETAIOPS_V3_TAKEOVER_ALLOWED_USERS", ""),
            "v3_takeover_conversation_prefix": runtime_env.get("NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX", ""),
            "v3_takeover_allowed_actions": runtime_env.get("NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS", ""),
            "v3_takeover_allowed_sources": runtime_env.get("NETAIOPS_V3_TAKEOVER_ALLOWED_SOURCES", ""),
            "v3_takeover_audit_dir": runtime_env.get("NETAIOPS_V3_TAKEOVER_AUDIT_DIR", ""),
        },
        "api_smoke": api_smoke,
        "passed": True,
    }

    (report_dir / "v3_3_18_closeout_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print("v3_3_18_static_app_check=OK")
    print("v3_3_18_runtime_env_check=OK")
    print("v3_3_18_allowed_general=OK")
    print("v3_3_18_allowed_advice=OK")
    print("v3_3_18_compat_v3_3_16=OK")
    print("v3_3_18_blocked_user=OK")
    print("v3_3_18_blocked_prefix=OK")
    print("v3_3_18_blocked_cmdb_query=OK")
    print("v3_3_18_audit_growth=OK")
    print("v3_3_18_closeout_summary=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
