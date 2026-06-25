#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch41 chat router regression.

Safety:
- It does NOT execute any network device CLI command.
- It only checks V2 chat routing and command suggestion generation.
"""

from __future__ import print_function

import json
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.chat_v2.router import try_handle_v2_chat


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
        "cases": [],
        "errors": errors,
    }

    cases = [
        {
            "question": "SH8-G03-DCI-BN-SW01的路由表有多少条",
            "expected_intent": "route_table",
            "expected_command_keyword": "route",
            "expected_keyword": "SH8-G03-DCI-BN-SW01",
            "expected_mgmt_ip": "10.192.251.101",
            "expected_device_name": "SH8-G03-DCI-BN-SW01",
        },
        {
            "question": "SH8-G03-DCI-BN-SW01目前CPU利用率，我该通过哪些命令去排查？",
            "expected_intent": "cpu_check",
            "expected_command_keyword": "cpu",
            "expected_keyword": "SH8-G03-DCI-BN-SW01",
            "expected_mgmt_ip": "10.192.251.101",
            "expected_device_name": "SH8-G03-DCI-BN-SW01",
        },
    ]

    print("========== V2 Batch41 Chat Router Regression ==========")

    for case in cases:
        print()
        print("question:", case["question"])
        data = try_handle_v2_chat(case["question"], user="regress")
        report["cases"].append({
            "case": case,
            "response": data,
        })

        print(json.dumps({
            "status": data.get("status") if data else None,
            "planner_source": data.get("planner_source") if data else None,
            "parsed": data.get("parsed") if data else None,
            "answer": data.get("answer") if data else None,
            "count": data.get("count") if data else None,
            "items": data.get("items") if data else None,
        }, ensure_ascii=False, indent=2)[:5000])

        require(data is not None, "Question is handled by V2 router", errors)
        if not data:
            continue

        parsed = data.get("parsed") or {}
        items = data.get("items") or []

        require(data.get("planner_source") == "v2_chat_router", "planner_source is v2_chat_router", errors)
        require(parsed.get("intent") == "v2_troubleshoot", "intent is v2_troubleshoot", errors)
        require(parsed.get("v2_intent") == case["expected_intent"], "v2_intent matches expected", errors)
        require(parsed.get("keyword") == case["expected_keyword"], "full device keyword extracted correctly", errors)
        require(parsed.get("device_name") == case["expected_device_name"], "device_name resolved to expected device", errors)
        require(parsed.get("mgmt_ip") == case["expected_mgmt_ip"], "mgmt_ip resolved to expected IP", errors)
        require(len(items) > 0, "command suggestions generated", errors)
        require(any(x.get("guard_status") == "passed" for x in items), "at least one command passed CLI guard", errors)
        require(
            any(case["expected_command_keyword"].lower() in str(x.get("command", "")).lower() for x in items),
            "expected command keyword appears in suggestions",
            errors,
        )

    out = "/tmp/v2_chat_router_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print()
    print("========== Result ==========")
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
