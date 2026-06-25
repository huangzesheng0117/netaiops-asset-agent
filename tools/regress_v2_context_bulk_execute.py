#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch47 context-following bulk execution regression.

This script validates:
1. CPU question generates V2 command suggestions.
2. Context-following request without YES is recognized and does not execute.
3. Confirm all with YES executes all passed commands.
4. Bulk answer contains combined analysis.

This will execute multiple read-only commands:
- show system resources
- show processes cpu
- show processes cpu sort
- show logging last 100
"""

from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def post_chat(question, conversation_id=None, timeout=240):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    req = urllib.request.Request(
        BASE_URL + "/api/v1/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch47 Context Bulk Execute Regression ==========")

    print("\n========== 1. Generate CPU command suggestions ==========")
    first = post_chat("WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令", timeout=180)
    conversation_id = first.get("conversation_id")
    items = first.get("items") or []

    report["checks"]["first"] = {
        "status": first.get("status"),
        "planner_source": first.get("planner_source"),
        "conversation_id": conversation_id,
        "parsed": first.get("parsed"),
        "answer": first.get("answer"),
        "items": items,
    }

    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:8000])

    require(first.get("planner_source") == "v2_chat_router", "First response uses v2_chat_router", errors)
    require(bool(conversation_id), "First response has conversation_id", errors)
    require(len(items) >= 4, "First response has at least 4 command suggestions", errors)
    require(all(x.get("guard_status") == "passed" for x in items[:4]), "First four suggestions are passed", errors)

    print("\n========== 2. Context-following request without YES ==========")
    no_yes = post_chat("将你上述给出的命令在设备上执行，然后根据命令的结果给出分析", conversation_id=conversation_id, timeout=180)

    report["checks"]["no_yes"] = {
        "status": no_yes.get("status"),
        "planner_source": no_yes.get("planner_source"),
        "parsed": no_yes.get("parsed"),
        "answer": no_yes.get("answer"),
        "count": no_yes.get("count"),
        "items": no_yes.get("items"),
    }

    print(json.dumps(report["checks"]["no_yes"], ensure_ascii=False, indent=2)[:7000])

    require(no_yes.get("planner_source") == "v2_execution_confirmation", "Context-following request enters confirmation router", errors)
    require(no_yes.get("status") == "pending_confirmation", "No-YES bulk request is pending_confirmation", errors)
    require("确认执行全部命令 YES" in (no_yes.get("answer") or ""), "No-YES answer asks for bulk YES confirmation", errors)
    require(no_yes.get("count", 0) >= 4, "No-YES response returns pending passed commands", errors)

    print("\n========== 3. Confirm all commands with YES ==========")
    executed = post_chat("确认执行全部命令 YES", conversation_id=conversation_id, timeout=360)

    exec_items = executed.get("items") or []
    answer = executed.get("answer") or ""
    v2 = executed.get("v2") or {}

    report["checks"]["executed"] = {
        "status": executed.get("status"),
        "planner_source": executed.get("planner_source"),
        "parsed": executed.get("parsed"),
        "answer": answer,
        "items": exec_items,
        "v2_counts": v2.get("counts"),
    }

    print(json.dumps(report["checks"]["executed"], ensure_ascii=False, indent=2)[:12000])

    require(executed.get("planner_source") == "v2_execution_confirmation", "Bulk YES enters confirmation router", errors)
    require(executed.get("status") in ("ok", "partial"), "Bulk execution status ok/partial", errors)
    require(len(exec_items) >= 4, "Bulk execution returns at least 4 execution items", errors)
    require(all(x.get("execution_status") == "executed" for x in exec_items[:4]), "First four commands executed", errors)
    require(all(x.get("ok") is True for x in exec_items[:4]), "First four executions ok=true", errors)
    require("综合分析" in answer, "Bulk answer includes 综合分析", errors)
    require("命令执行结果摘要" in answer, "Bulk answer includes command summary", errors)
    require("建议下一步" in answer, "Bulk answer includes next steps", errors)
    require(bool(v2.get("analyses")), "v2.analyses exists", errors)

    out = "/tmp/v2_context_bulk_execute_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== Result ==========")
    print("conversation_id:", conversation_id)
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
