#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch37 CLI guard regression.

Safety:
- This script only validates command strings.
- It does NOT execute any network device CLI command.
- It does NOT call Netmiko send_command_and_get_output.
"""

from __future__ import print_function

import json
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.netmiko.cli_guard import CliReadOnlyGuard
from netaiops_asset.mcp.netmiko_client import NetmikoMcpClient


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def validate_case(guard, case, errors, report_cases):
    result = guard.validate(
        case["command"],
        platform=case.get("platform"),
        device_type=case.get("device_type"),
    ).to_dict()

    report_cases.append({
        "case": case,
        "result": result,
    })

    print()
    print("command:", case["command"])
    print("platform:", case.get("platform") or case.get("device_type") or "generic")
    print("expected:", case["expected"])
    print("actual:", result["status"])
    print("rule:", result.get("matched_rule"))
    if result.get("reasons"):
        print("reasons:", "; ".join(result.get("reasons")))

    require(result["status"] == case["expected"], "CLI guard result matches expected status", errors)


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "cases": [],
        "wrapper_checks": {},
        "errors": errors,
    }

    print("========== V2 Batch37 CLI Guard Regression ==========")

    guard = CliReadOnlyGuard()

    cases = [
        {
            "platform": "cisco_xe",
            "command": "show version",
            "expected": "passed",
        },
        {
            "platform": "cisco_nxos",
            "command": "show interface status",
            "expected": "passed",
        },
        {
            "platform": "cisco_nxos",
            "command": "show interface Ethernet1/1 | include rate",
            "expected": "passed",
        },
        {
            "platform": "huawei",
            "command": "display interface brief",
            "expected": "passed",
        },
        {
            "platform": "h3c",
            "command": "display bgp peer",
            "expected": "passed",
        },
        {
            "platform": "fortigate",
            "command": "get system status",
            "expected": "passed",
        },
        {
            "platform": "fortigate",
            "command": "diagnose hardware deviceinfo nic port1",
            "expected": "passed",
        },
        {
            "platform": "f5",
            "command": "tmsh show ltm virtual",
            "expected": "passed",
        },
        {
            "platform": "f5",
            "command": "tmsh list ltm virtual",
            "expected": "passed",
        },
        {
            "platform": "hillstone",
            "command": "show interface",
            "expected": "passed",
        },
        {
            "platform": "cisco_xe",
            "command": "configure terminal",
            "expected": "blocked",
        },
        {
            "platform": "huawei",
            "command": "system-view",
            "expected": "blocked",
        },
        {
            "platform": "cisco_xe",
            "command": "shutdown",
            "expected": "blocked",
        },
        {
            "platform": "cisco_xe",
            "command": "no shutdown",
            "expected": "blocked",
        },
        {
            "platform": "huawei",
            "command": "save force",
            "expected": "blocked",
        },
        {
            "platform": "cisco_xe",
            "command": "reload",
            "expected": "blocked",
        },
        {
            "platform": "fortigate",
            "command": "set hostname test",
            "expected": "blocked",
        },
        {
            "platform": "f5",
            "command": "tmsh modify ltm virtual test disabled",
            "expected": "blocked",
        },
        {
            "platform": "cisco_xe",
            "command": "show running-config",
            "expected": "review",
        },
        {
            "platform": "huawei",
            "command": "display current-configuration",
            "expected": "review",
        },
        {
            "platform": "cisco_xe",
            "command": "show tech-support",
            "expected": "review",
        },
        {
            "platform": "fortigate",
            "command": "diagnose debug enable",
            "expected": "review",
        },
        {
            "platform": "cisco_xe",
            "command": "ping 10.1.1.1",
            "expected": "review",
        },
        {
            "platform": "cisco_xe",
            "command": "show version ; reload",
            "expected": "blocked",
        },
        {
            "platform": "cisco_xe",
            "command": "show version | redirect flash:test.txt",
            "expected": "blocked",
        },
    ]

    print("\n========== 1. CLI guard case validation ==========")
    for case in cases:
        validate_case(guard, case, errors, report["cases"])

    print("\n========== 2. NetmikoMcpClient wrapper validation, no CLI execution ==========")
    client = NetmikoMcpClient()

    allowed = client.validate_command("show version", device_type="cisco_xe")
    blocked = client.validate_command("configure terminal", device_type="cisco_xe")
    review = client.validate_command("show running-config", device_type="cisco_xe")

    report["wrapper_checks"] = {
        "allowed": allowed,
        "blocked": blocked,
        "review": review,
    }

    print("allowed:", json.dumps(allowed, ensure_ascii=False))
    print("blocked:", json.dumps(blocked, ensure_ascii=False))
    print("review:", json.dumps(review, ensure_ascii=False))

    require(allowed["status"] == "passed", "Netmiko wrapper validates read-only command as passed", errors)
    require(blocked["status"] == "blocked", "Netmiko wrapper blocks config command", errors)
    require(review["status"] == "review", "Netmiko wrapper marks sensitive read-only as review", errors)

    print("\n========== 3. Confirm guard blocks execution unless status=passed and confirmed=True ==========")
    try:
        client.send_command_after_guard(
            name="__DO_NOT_EXECUTE__",
            command="show version",
            guard_status="blocked",
            confirmed=True,
            timeout=5,
        )
        require(False, "send_command_after_guard should block guard_status=blocked before MCP call", errors)
    except Exception as exc:
        print("[OK] blocked before MCP call:", repr(exc))

    try:
        client.send_command_after_guard(
            name="__DO_NOT_EXECUTE__",
            command="show version",
            guard_status="passed",
            confirmed=False,
            timeout=5,
        )
        require(False, "send_command_after_guard should block missing confirmation before MCP call", errors)
    except Exception as exc:
        print("[OK] blocked before MCP call:", repr(exc))

    out = "/tmp/v2_cli_guard_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
