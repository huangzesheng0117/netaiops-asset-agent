#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch44 execution analysis regression.

This script validates:
1. chat router generates CPU check suggestions.
2. conversation confirmation executes show system resources.
3. answer includes structured analysis, key facts and next steps.
4. v2.analysis is returned in response.

This will execute one read-only command:
- device: SH8-G03-DCI-BN-SW01
- command: show system resources
"""

from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def post_chat(question, conversation_id=None):
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

    with urllib.request.urlopen(req, timeout=120) as resp:
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

    print("========== V2 Batch44 Execution Analysis Regression ==========")

    first = post_chat("SH8-G03-DCI-BN-SW01目前CPU利用率，我该通过哪些命令去排查？")
    conversation_id = first.get("conversation_id")
    items = first.get("items") or []

    report["checks"]["first"] = {
        "status": first.get("status"),
        "planner_source": first.get("planner_source"),
        "conversation_id": conversation_id,
        "parsed": first.get("parsed"),
        "items": items,
    }

    print("\n========== First response ==========")
    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:5000])

    require(first.get("planner_source") == "v2_chat_router", "First response uses v2_chat_router", errors)
    require(bool(conversation_id), "First response has conversation_id", errors)
    require(items and items[0].get("command") == "show system resources", "First command is show system resources", errors)

    executed = post_chat("确认执行第1条命令 YES", conversation_id=conversation_id)
    answer = executed.get("answer") or ""
    exec_items = executed.get("items") or []
    analysis = (executed.get("v2") or {}).get("analysis") or {}

    report["checks"]["executed"] = {
        "status": executed.get("status"),
        "planner_source": executed.get("planner_source"),
        "answer": answer,
        "items": exec_items,
        "analysis": analysis,
    }

    print("\n========== Executed response ==========")
    print(json.dumps(report["checks"]["executed"], ensure_ascii=False, indent=2)[:9000])

    first_exec = exec_items[0] if exec_items else {}

    require(executed.get("planner_source") == "v2_execution_confirmation", "Executed response uses confirmation router", errors)
    require(executed.get("status") == "ok", "Executed response status ok", errors)
    require(first_exec.get("execution_status") == "executed", "Command executed", errors)
    require(bool(first_exec.get("audit_path")), "Execution has audit_path", errors)

    require("初步分析" in answer, "Answer includes 初步分析", errors)
    require("关键证据" in answer, "Answer includes 关键证据", errors)
    require("建议下一步" in answer, "Answer includes 建议下一步", errors)
    require(bool(analysis), "v2.analysis exists", errors)
    require(analysis.get("analysis_type") == "nxos_system_resources", "analysis_type is nxos_system_resources", errors)
    require(bool(analysis.get("facts")), "analysis has facts", errors)
    require(bool(analysis.get("next_steps")), "analysis has next_steps", errors)

    out = "/tmp/v2_execution_analysis_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
