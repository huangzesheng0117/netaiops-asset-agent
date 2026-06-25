#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch56 multi-type conversation regression.

Purpose:
- Validate multiple troubleshooting types after LLM-first Planner + Dispatcher + Command Templates.
- Validate V2 does not fall back to V1 for typical troubleshooting questions.
- Validate command templates for CPU / interface error / BGP / route table / optical power / interface down.
- Validate no-YES confirmation does not execute device CLI.

Safety:
- This regression does NOT execute device CLI.
- It only generates command suggestions.
- It sends no-YES confirmation text and expects pending_confirmation.
"""

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


def dispatch(question, conversation_id=None, timeout=180):
    payload = {
        "question": question,
        "user": "baoleiji",
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    return http_json("POST", "/api/v1/v2/dispatch_plan", payload=payload, timeout=timeout)


def get_context(conversation_id, timeout=120):
    path = "/api/v1/v2/context?conversation_id={}".format(conversation_id)
    return http_json("GET", path, timeout=timeout)


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def commands(data):
    return [str(x.get("command") or "") for x in (data.get("items") or [])]


def compact(data):
    return {
        "status": data.get("status"),
        "planner_source": data.get("planner_source"),
        "conversation_id": data.get("conversation_id"),
        "parsed": data.get("parsed"),
        "answer": data.get("answer"),
        "count": data.get("count"),
        "items": data.get("items"),
    }


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch56 Multi-Type Conversation Regression ==========")
    print("Safety: this regression does NOT execute device CLI.")

    cases = [
        {
            "name": "cpu",
            "question": "WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令",
            "expect_intent": "cpu_check",
            "expect_device": "WG88-SW-H15-1",
            "expect_mgmt_ip": "10.189.250.79",
            "min_count": 4,
            "must_have": [
                "show system resources",
                "show processes cpu",
                "show processes cpu sort",
                "show logging last 100",
            ],
        },
        {
            "name": "interface_error",
            "question": "设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题",
            "expect_intent": "interface_error_check",
            "expect_device": "WG88-SW-H16-1",
            "expect_mgmt_ip": "10.189.250.80",
            "expect_interface": "Ethernet1/46",
            "min_count": 5,
            "must_have": [
                "show interface Ethernet1/46",
                "show interface Ethernet1/46 counters errors",
                "show interface Ethernet1/46 counters detailed",
                "show interface Ethernet1/46 transceiver details",
                "show logging last 100",
            ],
            "check_no_yes": True,
        },
        {
            "name": "bgp",
            "question": "WG88-SW-H16-1的BGP邻居异常，给我第一批排查命令",
            "expect_intent": "bgp_check",
            "expect_device": "WG88-SW-H16-1",
            "expect_mgmt_ip": "10.189.250.80",
            "min_count": 4,
            "must_have": [
                "show bgp ipv4 unicast summary",
                "show bgp ipv6 unicast summary",
                "show ip bgp summary",
                "show logging last 100",
            ],
        },
        {
            "name": "route_table",
            "question": "SH8-G03-DCI-BN-SW01的路由表有多少条",
            "expect_intent": "route_table",
            "expect_device": "SH8-G03-DCI-BN-SW01",
            "expect_mgmt_ip": "10.192.251.101",
            "min_count": 3,
            "must_have": [
                "show ip route summary",
                "show ipv6 route summary",
                "show ip route | count",
            ],
        },
        {
            "name": "optical_power",
            "question": "设备WG88-SW-H16-1的eth1/46光功率异常，给我命令看看",
            "expect_intent": "optical_power_check",
            "expect_device": "WG88-SW-H16-1",
            "expect_mgmt_ip": "10.189.250.80",
            "expect_interface": "Ethernet1/46",
            "min_count": 3,
            "must_have": [
                "show interface Ethernet1/46",
                "show interface Ethernet1/46 transceiver details",
                "show logging last 100",
            ],
        },
        {
            "name": "interface_down",
            "question": "设备WG88-SW-H16-1的eth1/46端口down，给我命令看看",
            "expect_intent": "interface_check",
            "expect_device": "WG88-SW-H16-1",
            "expect_mgmt_ip": "10.189.250.80",
            "expect_interface": "Ethernet1/46",
            "min_count": 4,
            "must_have": [
                "show interface Ethernet1/46",
                "show interface Ethernet1/46 status",
                "show interface Ethernet1/46 transceiver details",
                "show logging last 100",
            ],
        },
    ]

    for case in cases:
        print("\n========== case: {} ==========".format(case["name"]))
        print("QUESTION:", case["question"])

        dp = dispatch(case["question"])
        report["checks"]["dispatch_" + case["name"]] = dp
        print("-- dispatch --")
        print(json.dumps(dp, ensure_ascii=False, indent=2)[:7000])

        d = dp.get("dispatch") or {}
        require(dp.get("status") == "ok", "{} dispatch endpoint ok".format(case["name"]), errors)
        require(d.get("route") == "v2_chat_router", "{} dispatch route v2_chat_router".format(case["name"]), errors)
        require(d.get("action") == "suggest_commands", "{} dispatch action suggest_commands".format(case["name"]), errors)
        require(d.get("v2_intent") == case["expect_intent"], "{} dispatch v2_intent expected".format(case["name"]), errors)

        data = chat(case["question"])
        conv_id = data.get("conversation_id")
        parsed = data.get("parsed") or {}
        cmd_list = commands(data)

        report["checks"]["chat_" + case["name"]] = compact(data)

        print("-- chat --")
        print(json.dumps(report["checks"]["chat_" + case["name"]], ensure_ascii=False, indent=2)[:10000])

        require(data.get("status") == "ok", "{} chat status ok".format(case["name"]), errors)
        require(data.get("planner_source") == "v2_chat_router", "{} chat uses v2_chat_router".format(case["name"]), errors)
        require(data.get("planner_source") != "llm_tool_planner", "{} does not fall back to V1 llm_tool_planner".format(case["name"]), errors)
        require(parsed.get("v2_intent") == case["expect_intent"], "{} chat v2_intent expected".format(case["name"]), errors)
        require(parsed.get("device_name") == case["expect_device"], "{} device_name expected".format(case["name"]), errors)
        require(parsed.get("mgmt_ip") == case["expect_mgmt_ip"], "{} mgmt_ip expected".format(case["name"]), errors)

        if case.get("expect_interface"):
            require(parsed.get("interface_name") == case["expect_interface"], "{} interface expected".format(case["name"]), errors)

        require(len(cmd_list) >= case["min_count"], "{} command count expected".format(case["name"]), errors)
        require(all(str(x.get("guard_status")) == "passed" for x in (data.get("items") or [])), "{} all commands guard passed".format(case["name"]), errors)
        require(all(str(x.get("confirm_required")) == "是" for x in (data.get("items") or [])), "{} all commands require confirmation".format(case["name"]), errors)

        for expected in case.get("must_have", []):
            require(expected in cmd_list, "{} contains {}".format(case["name"], expected), errors)

        answer = data.get("answer") or ""
        require("不会自动登录设备执行" in answer, "{} answer states no auto execution".format(case["name"]), errors)
        require("后续需要进入确认执行流程" in answer, "{} answer states confirmation flow".format(case["name"]), errors)

        if case.get("check_no_yes"):
            print("-- no-YES confirmation check --")
            no_yes = chat("将你上述给出的命令在设备上执行，然后根据命令的结果给出分析", conversation_id=conv_id)
            report["checks"]["no_yes_" + case["name"]] = compact(no_yes)
            print(json.dumps(report["checks"]["no_yes_" + case["name"]], ensure_ascii=False, indent=2)[:7000])

            require(no_yes.get("planner_source") == "v2_execution_confirmation", "{} no-YES enters confirmation router".format(case["name"]), errors)
            require(no_yes.get("status") == "pending_confirmation", "{} no-YES status pending_confirmation".format(case["name"]), errors)
            require("确认执行全部命令 YES" in (no_yes.get("answer") or ""), "{} no-YES asks for YES".format(case["name"]), errors)

    print("\n========== CMDB boundary check ==========")
    cmdb_question = "WG88-SW-H16-1的序列号是什么"
    cmdb_dispatch = dispatch(cmdb_question)
    report["checks"]["dispatch_cmdb_boundary"] = cmdb_dispatch
    print(json.dumps(cmdb_dispatch, ensure_ascii=False, indent=2)[:7000])

    cmdb_d = cmdb_dispatch.get("dispatch") or {}
    require(cmdb_d.get("route") == "v1_cmdb", "asset-only query dispatches to v1_cmdb", errors)
    require(cmdb_d.get("action") == "cmdb_query", "asset-only query action cmdb_query", errors)

    out = "/tmp/v2_multi_type_conversation_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
