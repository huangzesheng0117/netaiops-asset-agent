#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch43 chat-style confirmed execution regression.

This script validates:
1. /api/v1/chat generates V2 command suggestions.
2. Pending commands are saved.
3. "确认执行第1条命令" without YES does not execute.
4. "确认执行第1条命令 YES" executes the selected read-only command.

Default real execution:
- question: SH8-G03-DCI-BN-SW01目前CPU利用率，我该通过哪些命令去排查？
- selected command index: 1
- expected command: show system resources
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

    print("========== V2 Batch43 Chat Confirm Execute Regression ==========")

    print("\n========== 1. Ask CPU troubleshooting question ==========")
    first = post_chat("SH8-G03-DCI-BN-SW01目前CPU利用率，我该通过哪些命令去排查？")
    report["checks"]["first"] = {
        "status": first.get("status"),
        "planner_source": first.get("planner_source"),
        "conversation_id": first.get("conversation_id"),
        "parsed": first.get("parsed"),
        "answer": first.get("answer"),
        "items": first.get("items"),
    }

    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:7000])

    conversation_id = first.get("conversation_id")
    first_items = first.get("items") or []

    require(first.get("planner_source") == "v2_chat_router", "First question handled by v2_chat_router", errors)
    require(bool(conversation_id), "First response has conversation_id", errors)
    require(len(first_items) > 0, "First response has command suggestions", errors)
    require(first_items[0].get("command") == "show system resources", "First command is show system resources", errors)
    require(first_items[0].get("guard_status") == "passed", "First command guard_status is passed", errors)

    print("\n========== 2. Confirm without YES, should not execute ==========")
    no_yes = post_chat("确认执行第1条命令", conversation_id=conversation_id)
    report["checks"]["no_yes"] = {
        "status": no_yes.get("status"),
        "planner_source": no_yes.get("planner_source"),
        "answer": no_yes.get("answer"),
        "items": no_yes.get("items"),
    }

    print(json.dumps(report["checks"]["no_yes"], ensure_ascii=False, indent=2)[:4000])

    require(no_yes.get("planner_source") == "v2_execution_confirmation", "No-YES handled by confirmation router", errors)
    require(no_yes.get("status") == "pending_confirmation", "No-YES response is pending_confirmation", errors)

    print("\n========== 3. Confirm with YES, real read-only command execution ==========")
    executed = post_chat("确认执行第1条命令 YES", conversation_id=conversation_id)
    report["checks"]["executed"] = {
        "status": executed.get("status"),
        "planner_source": executed.get("planner_source"),
        "answer": executed.get("answer"),
        "items": executed.get("items"),
        "parsed": executed.get("parsed"),
    }

    print(json.dumps(report["checks"]["executed"], ensure_ascii=False, indent=2)[:7000])

    exec_items = executed.get("items") or []
    first_exec = exec_items[0] if exec_items else {}

    require(executed.get("planner_source") == "v2_execution_confirmation", "YES confirmation handled by confirmation router", errors)
    require(executed.get("status") == "ok", "YES confirmation status ok", errors)
    require(first_exec.get("command") == "show system resources", "Executed command is show system resources", errors)
    require(first_exec.get("execution_status") == "executed", "Execution status is executed", errors)
    require(first_exec.get("ok") is True, "Execution ok=true", errors)
    require(bool(first_exec.get("audit_path")), "Execution has audit_path", errors)
    require(bool(first_exec.get("output_preview")), "Execution has output_preview", errors)

    out = "/tmp/v2_chat_confirm_execute_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
