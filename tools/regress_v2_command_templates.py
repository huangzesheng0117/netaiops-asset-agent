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


def chat(question):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }
    return http_json("POST", "/api/v1/chat", payload=payload)


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def commands(data):
    return [str(x.get("command") or "") for x in (data.get("items") or [])]


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch55 Command Templates Regression ==========")
    print("Safety: this regression does NOT execute device CLI.")

    cases = [
        {
            "name": "interface_error",
            "question": "设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题",
            "intent": "interface_error_check",
            "min_count": 4,
            "must_have": [
                "show interface Ethernet1/46",
                "show interface Ethernet1/46 counters errors",
                "show interface Ethernet1/46 counters detailed",
                "show interface Ethernet1/46 transceiver details",
            ],
            "must_not_only": "show version",
        },
        {
            "name": "cpu",
            "question": "WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令",
            "intent": "cpu_check",
            "min_count": 4,
            "must_have": [
                "show system resources",
                "show processes cpu",
                "show processes cpu sort",
                "show logging last 100",
            ],
        },
        {
            "name": "route_table",
            "question": "SH8-G03-DCI-BN-SW01的路由表有多少条",
            "intent": "route_table",
            "min_count": 3,
            "must_have": [
                "show ip route summary",
                "show ipv6 route summary",
            ],
        },
        {
            "name": "optical_power",
            "question": "设备WG88-SW-H16-1的eth1/46光功率异常，给我命令看看",
            "intent": "optical_power_check",
            "min_count": 2,
            "must_have": [
                "show interface Ethernet1/46 transceiver details",
            ],
        },
        {
            "name": "interface_down",
            "question": "设备WG88-SW-H16-1的eth1/46端口down，给我命令看看",
            "intent": "interface_check",
            "min_count": 3,
            "must_have": [
                "show interface Ethernet1/46",
                "show logging last 100",
            ],
        },
        {
            "name": "bgp",
            "question": "WG88-SW-H16-1的BGP邻居异常，给我第一批排查命令",
            "intent": "bgp_check",
            "min_count": 3,
            "must_have": [
                "show bgp ipv4 unicast summary",
                "show logging last 100",
            ],
        },
    ]

    for case in cases:
        print("\n========== case: {} ==========".format(case["name"]))
        data = chat(case["question"])
        report["checks"][case["name"]] = {
            "status": data.get("status"),
            "planner_source": data.get("planner_source"),
            "parsed": data.get("parsed"),
            "answer": data.get("answer"),
            "count": data.get("count"),
            "items": data.get("items"),
        }

        print(json.dumps(report["checks"][case["name"]], ensure_ascii=False, indent=2)[:10000])

        parsed = data.get("parsed") or {}
        cmd_list = commands(data)

        require(data.get("planner_source") == "v2_chat_router", "{} uses v2_chat_router".format(case["name"]), errors)
        require(parsed.get("v2_intent") == case["intent"], "{} v2_intent expected".format(case["name"]), errors)
        require(len(cmd_list) >= case["min_count"], "{} command count expected".format(case["name"]), errors)
        require(all(str(x.get("guard_status")) == "passed" for x in (data.get("items") or [])), "{} all commands guard passed".format(case["name"]), errors)

        for expected in case.get("must_have", []):
            require(expected in cmd_list, "{} contains command {}".format(case["name"], expected), errors)

        if case.get("must_not_only"):
            require(cmd_list != [case["must_not_only"]], "{} no longer only returns {}".format(case["name"], case["must_not_only"]), errors)

    out = "/tmp/v2_command_templates_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
