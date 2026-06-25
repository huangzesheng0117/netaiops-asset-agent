#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def http_json(method, path, payload=None, timeout=180):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def dispatch(question, conversation_id=None):
    payload = {
        "question": question,
        "user": "baoleiji",
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return http_json("POST", "/api/v1/v2/dispatch_plan", payload=payload)


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

    print("========== V2 Batch54 Plan Dispatcher Regression ==========")
    print("Safety: this regression does NOT execute device CLI.")

    cases = [
        {
            "name": "interface_error",
            "question": "设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题",
            "route": "v2_chat_router",
            "action": "suggest_commands",
            "category": "interface_error",
            "v2_intent": "interface_error_check",
            "device": "WG88-SW-H16-1",
            "interface": "Ethernet1/46",
        },
        {
            "name": "cpu",
            "question": "WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令",
            "route": "v2_chat_router",
            "action": "suggest_commands",
            "category": "cpu",
            "v2_intent": "cpu_check",
            "device": "WG88-SW-H15-1",
        },
        {
            "name": "route_table",
            "question": "SH8-G03-DCI-BN-SW01的路由表有多少条",
            "route": "v2_chat_router",
            "action": "suggest_commands",
            "category": "route_table",
            "v2_intent": "route_table",
            "device": "SH8-G03-DCI-BN-SW01",
        },
        {
            "name": "followup",
            "question": "总结一下目前这个设备CPU排查到的结论",
            "route": "v2_followup_analysis",
            "action": "followup_analysis",
            "category": "cpu",
            "v2_intent": "cpu_check",
        },
        {
            "name": "execute_all",
            "question": "确认执行全部命令 YES",
            "route": "v2_execution_confirmation",
            "action": "execute_all_pending",
        },
        {
            "name": "cmdb",
            "question": "WG88-SW-H16-1的序列号是什么",
            "route": "v1_cmdb",
            "action": "cmdb_query",
            "category": "cmdb",
        },
    ]

    for case in cases:
        print("\n========== dispatch: {} ==========".format(case["name"]))
        data = dispatch(case["question"])
        report["checks"]["dispatch_" + case["name"]] = data

        print(json.dumps(data, ensure_ascii=False, indent=2)[:9000])

        d = data.get("dispatch") or {}
        ents = d.get("entities") or {}

        require(data.get("status") == "ok", "{} endpoint status ok".format(case["name"]), errors)
        require(d.get("route") == case["route"], "{} route expected".format(case["name"]), errors)
        require(d.get("action") == case["action"], "{} action expected".format(case["name"]), errors)

        if case.get("category"):
            require(d.get("category") == case["category"], "{} category expected".format(case["name"]), errors)

        if case.get("v2_intent"):
            require(d.get("v2_intent") == case["v2_intent"], "{} v2_intent expected".format(case["name"]), errors)

        if case.get("device"):
            require(ents.get("device_name") == case["device"], "{} device expected".format(case["name"]), errors)

        if case.get("interface"):
            require(ents.get("interface") == case["interface"], "{} interface expected".format(case["name"]), errors)

    print("\n========== chat path still works for interface_error ==========")
    q = "设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题"
    chat_data = chat(q)
    report["checks"]["chat_interface_error"] = {
        "status": chat_data.get("status"),
        "planner_source": chat_data.get("planner_source"),
        "parsed": chat_data.get("parsed"),
        "answer": chat_data.get("answer"),
        "count": chat_data.get("count"),
        "items": chat_data.get("items"),
    }
    print(json.dumps(report["checks"]["chat_interface_error"], ensure_ascii=False, indent=2)[:9000])

    parsed = chat_data.get("parsed") or {}
    dispatch_plan = parsed.get("dispatch_plan") or {}

    require(chat_data.get("planner_source") == "v2_chat_router", "chat interface_error uses v2_chat_router", errors)
    require(parsed.get("v2_intent") == "interface_error_check", "chat interface_error v2_intent expected", errors)
    require(parsed.get("interface_name") == "Ethernet1/46", "chat interface_error interface expected", errors)
    require(dispatch_plan.get("route") == "v2_chat_router", "chat parsed dispatch route expected", errors)
    require("Plan Dispatcher" in (chat_data.get("answer") or ""), "answer includes Plan Dispatcher diagnostic", errors)

    out = "/tmp/v2_plan_dispatcher_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== Result ==========")
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
