#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch38 Netmiko confirmed execution regression.

This script validates:
1. Read-only command can build a pending confirmation plan.
2. Dangerous command is rejected before MCP call.
3. Missing confirmation blocks execution before MCP call.
4. With --confirm-execute YES, one low-risk read-only command is executed.

Default execution target:
- device: SH16-A04-ACI-2001
- device_type: cisco_nxos
- command: show clock
"""

from __future__ import print_function

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.netmiko.executor import ConfirmedNetmikoExecutor
from netaiops_asset.mcp.netmiko_client import NetmikoMcpClient


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def find_device(device_name):
    client = NetmikoMcpClient()
    devices = client.list_devices()
    for dev in devices:
        if dev.get("name") == device_name:
            return dev
    return None


def main():
    parser = argparse.ArgumentParser(description="Regress V2 Netmiko confirmed execution flow")
    parser.add_argument("--device", default="SH16-A04-ACI-2001")
    parser.add_argument("--device-type", default="cisco_nxos")
    parser.add_argument("--command", default="show clock")
    parser.add_argument("--confirm-execute", default="")
    parser.add_argument("--confirmed-by", default="baoleiji")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "args": vars(args),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch38 Netmiko Confirmed Execution Regression ==========")
    print("device:", args.device)
    print("device_type:", args.device_type)
    print("command:", args.command)
    print("confirm_execute:", args.confirm_execute)
    print("confirmed_by:", args.confirmed_by)

    executor = ConfirmedNetmikoExecutor()

    print("\n========== 1. Confirm device exists in Netmiko MCP inventory ==========")
    device = find_device(args.device)
    report["checks"]["device"] = device
    print("device:", json.dumps(device, ensure_ascii=False))
    require(device is not None, "Target device exists in Netmiko MCP inventory", errors)

    print("\n========== 2. Build plan for safe command ==========")
    safe_plan = executor.build_plan(
        device_name=args.device,
        command=args.command,
        device_type=args.device_type,
    )
    report["checks"]["safe_plan"] = safe_plan
    print(json.dumps(safe_plan, ensure_ascii=False, indent=2)[:3000])

    require(safe_plan.get("status") == "pending_confirmation", "Safe command enters pending_confirmation", errors)
    require((safe_plan.get("guard") or {}).get("status") == "passed", "Safe command guard status is passed", errors)

    print("\n========== 3. Build plan for dangerous command, should reject before MCP ==========")
    bad_plan = executor.build_plan(
        device_name=args.device,
        command="configure terminal",
        device_type=args.device_type,
    )
    report["checks"]["bad_plan"] = bad_plan
    print(json.dumps(bad_plan, ensure_ascii=False, indent=2)[:3000])

    require(bad_plan.get("status") == "rejected", "Dangerous command is rejected", errors)
    require((bad_plan.get("guard") or {}).get("status") == "blocked", "Dangerous command guard status is blocked", errors)

    print("\n========== 4. Missing confirmation should block before MCP ==========")
    no_confirm = executor.execute_confirmed(
        device_name=args.device,
        command=args.command,
        device_type=args.device_type,
        confirm_execute="",
        confirmed_by=args.confirmed_by,
        timeout=args.timeout,
    )
    report["checks"]["no_confirm"] = {
        "ok": no_confirm.get("ok"),
        "status": no_confirm.get("status"),
        "error": no_confirm.get("error"),
        "audit_path": no_confirm.get("audit_path"),
    }
    print(json.dumps(report["checks"]["no_confirm"], ensure_ascii=False, indent=2))

    require(no_confirm.get("status") == "pending_confirmation", "Missing confirmation blocks execution", errors)
    require(no_confirm.get("ok") is False, "Missing confirmation result ok=false", errors)

    print("\n========== 5. Confirmed execution ==========")
    if args.confirm_execute != "YES":
        print("[SKIP] Actual Netmiko execution skipped because --confirm-execute YES was not provided")
        report["checks"]["confirmed_execution"] = {
            "skipped": True,
            "reason": "confirm_execute is not YES",
        }
        require(False, "Batch38 requires --confirm-execute YES for final confirmed execution regression", errors)
    else:
        executed = executor.execute_confirmed(
            device_name=args.device,
            command=args.command,
            device_type=args.device_type,
            confirm_execute=args.confirm_execute,
            confirmed_by=args.confirmed_by,
            timeout=args.timeout,
        )
        report["checks"]["confirmed_execution"] = {
            "ok": executed.get("ok"),
            "status": executed.get("status"),
            "error": executed.get("error"),
            "audit_path": executed.get("audit_path"),
            "output_preview": executed.get("output_preview"),
        }

        print(json.dumps(report["checks"]["confirmed_execution"], ensure_ascii=False, indent=2)[:5000])

        require(executed.get("ok") is True, "Confirmed read-only command executed successfully", errors)
        require(executed.get("status") == "executed", "Confirmed execution status is executed", errors)
        require(bool(executed.get("audit_path")), "Confirmed execution has audit file", errors)
        require(bool(executed.get("output_preview") or executed.get("output") is not None), "Confirmed execution has output", errors)

    out = "/tmp/v2_netmiko_confirmed_execute_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
