#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Web smoke regression before frontend manual test.

Safety:
- This script does NOT execute device CLI.
- It verifies chat routing and no-YES confirmation only.
"""

from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def http_json(method, path, payload=None, timeout=120):
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

    return http_json("POST", "/api/v1/chat", payload=payload, timeout=120)


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def compact_chat(data):
    return {
        "status": data.get("status"),
        "planner_source": data.get("planner_source"),
        "conversation_id": data.get("conversation_id"),
        "parsed": data.get("parsed"),
        "answer": data.get("answer"),
        "count": data.get("count"),
        "returned": data.get("returned"),
        "items": data.get("items"),
        "v2_prometheus_evidence": (data.get("v2") or {}).get("prometheus_evidence"),
    }


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Web Smoke Regression ==========")

    print("\n========== 1. Health ==========")
    health = http_json("GET", "/health")
    report["checks"]["health"] = health
    print(json.dumps(health, ensure_ascii=False, indent=2))
    require(health.get("status") == "ok", "health status ok", errors)

    print("\n========== 2. V1 CMDB query should still work ==========")
    v1 = chat("SH8-G03-DCI-BN-SW01")
    report["checks"]["v1_cmdb"] = compact_chat(v1)
    print(json.dumps(report["checks"]["v1_cmdb"], ensure_ascii=False, indent=2)[:5000])

    require(v1.get("status") == "ok", "V1 CMDB query status ok", errors)
    require(v1.get("planner_source") != "v2_chat_router", "Plain device query does not enter V2 router", errors)
    require(v1.get("count", 0) >= 1, "V1 CMDB query returns records", errors)

    print("\n========== 3. V2 route table question ==========")
    route = chat("SH8-G03-DCI-BN-SW01的路由表有多少条")
    report["checks"]["v2_route"] = compact_chat(route)
    print(json.dumps(report["checks"]["v2_route"], ensure_ascii=False, indent=2)[:8000])

    route_parsed = route.get("parsed") or {}
    route_items = route.get("items") or []

    require(route.get("planner_source") == "v2_chat_router", "Route question enters V2 router", errors)
    require(route_parsed.get("v2_intent") == "route_table", "Route intent is route_table", errors)
    require(route_parsed.get("device_name") == "SH8-G03-DCI-BN-SW01", "Route device_name is expected", errors)
    require(route_parsed.get("mgmt_ip") == "10.192.251.101", "Route mgmt_ip is expected", errors)
    require(len(route_items) >= 1, "Route command suggestions returned", errors)
    require(any("route" in str(x.get("command", "")).lower() for x in route_items), "Route command suggestions include route", errors)
    require(all(x.get("guard_status") in ("passed", "review", "blocked") for x in route_items), "Route commands have guard status", errors)

    print("\n========== 4. V2 CPU question with Prometheus evidence ==========")
    cpu = chat("SH8-G03-DCI-BN-SW01当前CPU利用率是多少？我该通过哪些命令排查？")
    report["checks"]["v2_cpu"] = compact_chat(cpu)
    print(json.dumps(report["checks"]["v2_cpu"], ensure_ascii=False, indent=2)[:10000])

    cpu_parsed = cpu.get("parsed") or {}
    cpu_items = cpu.get("items") or []
    cpu_evidence = (cpu.get("v2") or {}).get("prometheus_evidence") or {}
    cpu_answer = cpu.get("answer") or ""

    require(cpu.get("planner_source") == "v2_chat_router", "CPU question enters V2 router", errors)
    require(cpu_parsed.get("v2_intent") == "cpu_check", "CPU intent is cpu_check", errors)
    require(cpu_parsed.get("device_name") == "SH8-G03-DCI-BN-SW01", "CPU device_name is expected", errors)
    require(cpu_parsed.get("mgmt_ip") == "10.192.251.101", "CPU mgmt_ip is expected", errors)
    require(len(cpu_items) >= 1, "CPU command suggestions returned", errors)
    require(cpu_items[0].get("command") == "show system resources", "CPU first command is show system resources", errors)
    require("Prometheus 当前 CPU 证据" in cpu_answer, "CPU answer includes Prometheus evidence section", errors)
    require(isinstance(cpu_evidence, dict), "CPU v2.prometheus_evidence exists", errors)
    require(cpu_evidence.get("status") in ("ok", "no_data", "skipped", "failed"), "CPU prometheus evidence status is valid", errors)

    print("\n========== 5. Confirmation without YES should not execute ==========")
    conv_id = cpu.get("conversation_id")
    no_yes = chat("确认执行第1条命令", conversation_id=conv_id)
    report["checks"]["no_yes"] = compact_chat(no_yes)
    print(json.dumps(report["checks"]["no_yes"], ensure_ascii=False, indent=2)[:5000])

    require(no_yes.get("planner_source") == "v2_execution_confirmation", "No-YES confirmation enters confirmation router", errors)
    require(no_yes.get("status") == "pending_confirmation", "No-YES confirmation status pending_confirmation", errors)

    print("\n========== 6. Netmiko safety policy ==========")
    policy = http_json("GET", "/api/v1/netmiko/safety_policy")
    report["checks"]["policy"] = policy
    print(json.dumps(policy, ensure_ascii=False, indent=2))
    require(policy.get("status") == "ok", "Netmiko safety policy status ok", errors)
    require(policy.get("auto_execute") is False, "Netmiko auto_execute is false", errors)

    print("\n========== 7. Netmiko validate_commands ==========")
    validation = http_json(
        "POST",
        "/api/v1/netmiko/validate_commands",
        payload={
            "user": "baoleiji",
            "device_name": "SH8-G03-DCI-BN-SW01",
            "device_type": "cisco_nxos",
            "commands": [
                "show clock",
                "configure terminal",
                "show running-config",
            ],
        },
    )
    report["checks"]["validate_commands"] = validation
    print(json.dumps(validation, ensure_ascii=False, indent=2)[:5000])

    require(validation.get("status") == "ok", "validate_commands status ok", errors)
    require(validation.get("passed_count") == 1, "validate_commands passed_count=1", errors)
    require(validation.get("blocked_count") == 1, "validate_commands blocked_count=1", errors)
    require(validation.get("review_count") == 1, "validate_commands review_count=1", errors)

    out = "/tmp/v2_web_smoke_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
