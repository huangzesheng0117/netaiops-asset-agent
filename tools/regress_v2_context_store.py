#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch48 conversation context store regression.

Safety:
- Does NOT execute device CLI.
- It generates V2 command suggestions.
- It sends context-following execution request without YES, which must not execute.
"""

from __future__ import print_function

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def http_json(method, path, payload=None, timeout=180):
    data = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def chat(question, conversation_id=None):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    return http_json("POST", "/api/v1/chat", payload=payload)


def get_context(conversation_id=None, user=None):
    params = {}
    if conversation_id:
        params["conversation_id"] = conversation_id
    if user:
        params["user"] = user

    query = urllib.parse.urlencode(params)
    path = "/api/v1/v2/context"
    if query:
        path += "?" + query

    return http_json("GET", path)


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

    print("========== V2 Batch48 Conversation Context Store Regression ==========")

    print("\n========== 1. Ask CPU question ==========")
    first = chat("WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令")
    conv_id = first.get("conversation_id")
    report["checks"]["first"] = {
        "status": first.get("status"),
        "planner_source": first.get("planner_source"),
        "conversation_id": conv_id,
        "parsed": first.get("parsed"),
        "answer": first.get("answer"),
        "count": first.get("count"),
        "items": first.get("items"),
        "prometheus_evidence": (first.get("v2") or {}).get("prometheus_evidence"),
    }

    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:8000])

    require(first.get("planner_source") == "v2_chat_router", "First turn uses v2_chat_router", errors)
    require(bool(conv_id), "First turn has conversation_id", errors)
    require((first.get("parsed") or {}).get("device_name") == "WG88-SW-H15-1", "First turn device is expected", errors)
    require((first.get("parsed") or {}).get("mgmt_ip") == "10.189.250.79", "First turn mgmt_ip is expected", errors)
    require(first.get("count", 0) >= 4, "First turn has command suggestions", errors)

    print("\n========== 2. Check context after first turn ==========")
    ctx1 = get_context(conversation_id=conv_id)
    report["checks"]["context_after_first"] = ctx1
    print(json.dumps(ctx1, ensure_ascii=False, indent=2)[:9000])

    ctx1_data = ctx1.get("context") or {}
    dev1 = ctx1_data.get("current_device") or {}

    require(ctx1.get("exists") is True, "Context exists after first turn", errors)
    require(dev1.get("device_name") == "WG88-SW-H15-1", "Context current_device device_name is expected", errors)
    require(dev1.get("mgmt_ip") == "10.189.250.79", "Context current_device mgmt_ip is expected", errors)
    require(ctx1_data.get("current_topic") == "cpu", "Context current_topic is cpu", errors)
    require(len(ctx1_data.get("last_command_suggestions") or []) >= 4, "Context has last_command_suggestions", errors)
    require(bool(ctx1_data.get("last_prometheus_evidence")), "Context has last_prometheus_evidence", errors)
    require(len(ctx1_data.get("recent_turns") or []) >= 1, "Context recent_turns has first turn", errors)
    require(bool(ctx1_data.get("rolling_summary")), "Context rolling_summary exists", errors)

    print("\n========== 3. Context-following request without YES ==========")
    second = chat("将你上述给出的命令在设备上执行，然后根据命令的结果给出分析", conversation_id=conv_id)
    report["checks"]["second"] = {
        "status": second.get("status"),
        "planner_source": second.get("planner_source"),
        "conversation_id": second.get("conversation_id"),
        "parsed": second.get("parsed"),
        "answer": second.get("answer"),
        "count": second.get("count"),
        "items": second.get("items"),
    }

    print(json.dumps(report["checks"]["second"], ensure_ascii=False, indent=2)[:8000])

    require(second.get("planner_source") == "v2_execution_confirmation", "Second turn uses confirmation router", errors)
    require(second.get("status") == "pending_confirmation", "Second turn does not execute without YES", errors)
    require("确认执行全部命令 YES" in (second.get("answer") or ""), "Second turn asks for YES", errors)

    print("\n========== 4. Check context after second turn ==========")
    ctx2 = get_context(conversation_id=conv_id)
    report["checks"]["context_after_second"] = ctx2
    print(json.dumps(ctx2, ensure_ascii=False, indent=2)[:9000])

    ctx2_data = ctx2.get("context") or {}
    recent = ctx2_data.get("recent_turns") or []

    require(ctx2.get("exists") is True, "Context exists after second turn", errors)
    require((ctx2_data.get("current_device") or {}).get("device_name") == "WG88-SW-H15-1", "Context preserves current device", errors)
    require(ctx2_data.get("current_topic") == "cpu", "Context preserves current topic", errors)
    require(len(recent) >= 2, "Context recent_turns has at least 2 turns", errors)
    require(recent[-1].get("planner_source") == "v2_execution_confirmation", "Latest turn recorded confirmation router", errors)
    require(ctx2_data.get("last_command_suggestions"), "Context still preserves last command suggestions", errors)
    require(ctx2_data.get("current_intent") == "cpu_check", "Context current_intent still preserves troubleshooting intent", errors)
    require(ctx2_data.get("last_action_intent") == "v2_execute_all_confirmation", "Context records last action intent", errors)
    require(len(ctx2_data.get("last_executions") or []) == 0, "Pending confirmation is not stored as executed command", errors)

    suggestions = ctx2_data.get("last_command_suggestions") or []
    require([x.get("index") for x in suggestions[:4]] == [1, 2, 3, 4], "Context command suggestion indexes are normalized", errors)

    out = "/tmp/v2_context_store_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== Result ==========")
    print("conversation_id:", conv_id)
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
