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


def chat(question, conversation_id=None, timeout=180):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return http_json("POST", "/api/v1/chat", payload=payload, timeout=timeout)


def plan(question, conversation_id=None, timeout=180):
    payload = {
        "question": question,
        "user": "baoleiji",
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return http_json("POST", "/api/v1/v2/llm_plan", payload=payload, timeout=timeout)


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

    print("========== V2 Batch53 LLM-first Planner Regression ==========")
    print("Safety: this regression does NOT execute device CLI.")

    q1 = "设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题"
    print("\n========== 1. Direct planner: interface error ==========")
    p1 = plan(q1)
    report["checks"]["plan_interface_error"] = p1
    print(json.dumps(p1, ensure_ascii=False, indent=2)[:9000])

    plan1 = p1.get("plan") or {}
    require(p1.get("status") == "ok", "planner endpoint status ok", errors)
    require(plan1.get("action") == "suggest_commands", "interface error action=suggest_commands", errors)
    require(plan1.get("category") == "interface_error", "interface error category=interface_error", errors)
    require(plan1.get("v2_intent") == "interface_error_check", "interface error v2_intent=interface_error_check", errors)
    require((plan1.get("entities") or {}).get("device_name") == "WG88-SW-H16-1", "interface error device extracted", errors)
    require((plan1.get("entities") or {}).get("interface") in ("Ethernet1/46", "eth1/46", "Eth1/46"), "interface extracted", errors)

    print("\n========== 2. Chat route: interface error should NOT fall back to V1 ==========")
    c1 = chat(q1)
    report["checks"]["chat_interface_error"] = {
        "status": c1.get("status"),
        "planner_source": c1.get("planner_source"),
        "parsed": c1.get("parsed"),
        "answer": c1.get("answer"),
        "count": c1.get("count"),
        "items": c1.get("items"),
    }
    print(json.dumps(report["checks"]["chat_interface_error"], ensure_ascii=False, indent=2)[:9000])

    parsed1 = c1.get("parsed") or {}
    llm_plan1 = parsed1.get("llm_intent_plan") or {}

    require(c1.get("planner_source") == "v2_chat_router", "interface error chat uses v2_chat_router", errors)
    require(c1.get("planner_source") != "llm_tool_planner", "interface error does not fallback to V1 LLM tool planner", errors)
    require(parsed1.get("v2_intent") == "interface_error_check", "interface error parsed v2_intent", errors)
    require(parsed1.get("device_name") == "WG88-SW-H16-1", "interface error parsed device_name", errors)
    require(parsed1.get("mgmt_ip") == "10.189.250.80", "interface error parsed mgmt_ip", errors)
    require(parsed1.get("interface_name") in ("Ethernet1/46", "eth1/46", "Eth1/46"), "interface error parsed interface_name", errors)
    require(llm_plan1.get("category") == "interface_error", "interface error parsed llm category", errors)
    require("LLM-first 识别结果" in (c1.get("answer") or ""), "answer includes LLM-first diagnostic", errors)

    print("\n========== 3. Direct planner: CPU ==========")
    q2 = "WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令"
    p2 = plan(q2)
    report["checks"]["plan_cpu"] = p2
    print(json.dumps(p2, ensure_ascii=False, indent=2)[:7000])
    plan2 = p2.get("plan") or {}
    require(plan2.get("category") == "cpu", "cpu category=cpu", errors)
    require(plan2.get("v2_intent") == "cpu_check", "cpu v2_intent=cpu_check", errors)
    require((plan2.get("entities") or {}).get("device_name") == "WG88-SW-H15-1", "cpu device extracted", errors)

    print("\n========== 4. Direct planner: route table ==========")
    q3 = "SH8-G03-DCI-BN-SW01的路由表有多少条"
    p3 = plan(q3)
    report["checks"]["plan_route"] = p3
    print(json.dumps(p3, ensure_ascii=False, indent=2)[:7000])
    plan3 = p3.get("plan") or {}
    require(plan3.get("category") == "route_table", "route category=route_table", errors)
    require(plan3.get("v2_intent") == "route_table", "route v2_intent=route_table", errors)

    print("\n========== 5. Direct planner: follow-up summary ==========")
    q4 = "总结一下目前这个设备CPU排查到的结论"
    p4 = plan(q4)
    report["checks"]["plan_followup"] = p4
    print(json.dumps(p4, ensure_ascii=False, indent=2)[:7000])
    plan4 = p4.get("plan") or {}
    require(plan4.get("action") == "followup_analysis", "followup action=followup_analysis", errors)

    out = "/tmp/v2_llm_first_planner_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
