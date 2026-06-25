#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch42 Netmiko API regression.

This script validates:
- /api/v1/netmiko/safety_policy
- /api/v1/netmiko/validate_commands
- /api/v1/netmiko/execute_confirmed without confirmation blocks before MCP
- /api/v1/netmiko/execute_confirmed with confirm_execute=YES executes one low-risk read-only command

Default real execution:
- device_name: SH8-G03-DCI-BN-SW01
- device_type: cisco_nxos
- command: show clock
"""

from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def http_json(method, path, payload=None, timeout=80):
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

    device_name = "SH8-G03-DCI-BN-SW01"
    device_type = "cisco_nxos"
    safe_command = "show clock"
    bad_command = "configure terminal"

    print("========== V2 Batch42 Netmiko API Regression ==========")

    print("\n========== 1. safety_policy ==========")
    policy = http_json("GET", "/api/v1/netmiko/safety_policy")
    report["checks"]["policy"] = policy
    print(json.dumps(policy, ensure_ascii=False, indent=2))
    require(policy.get("status") == "ok", "safety_policy status ok", errors)
    require(policy.get("auto_execute") is False, "auto_execute is false", errors)

    print("\n========== 2. validate_commands ==========")
    validate_payload = {
        "user": "baoleiji",
        "device_name": device_name,
        "device_type": device_type,
        "commands": [
            safe_command,
            bad_command,
            "show running-config",
        ],
    }
    validation = http_json("POST", "/api/v1/netmiko/validate_commands", validate_payload)
    report["checks"]["validation"] = validation
    print(json.dumps(validation, ensure_ascii=False, indent=2)[:5000])

    require(validation.get("status") == "ok", "validate_commands status ok", errors)
    require(validation.get("passed_count") == 1, "validate_commands passed_count=1", errors)
    require(validation.get("blocked_count") == 1, "validate_commands blocked_count=1", errors)
    require(validation.get("review_count") == 1, "validate_commands review_count=1", errors)

    print("\n========== 3. execute_confirmed without confirmation ==========")
    no_confirm_payload = {
        "user": "baoleiji",
        "confirmed_by": "baoleiji",
        "device_name": device_name,
        "device_type": device_type,
        "command": safe_command,
        "confirm_execute": "",
        "timeout": 60,
    }
    no_confirm = http_json("POST", "/api/v1/netmiko/execute_confirmed", no_confirm_payload)
    report["checks"]["no_confirm"] = {
        "ok": no_confirm.get("ok"),
        "status": no_confirm.get("status"),
        "error": no_confirm.get("error"),
        "audit_path": no_confirm.get("audit_path"),
        "audit_error": no_confirm.get("audit_error"),
        "request_id": no_confirm.get("request_id"),
    }
    print(json.dumps(report["checks"]["no_confirm"], ensure_ascii=False, indent=2))

    require(no_confirm.get("ok") is False, "no confirmation ok=false", errors)
    require(no_confirm.get("status") == "pending_confirmation", "no confirmation status pending_confirmation", errors)

    print("\n========== 4. execute_confirmed dangerous command ==========")
    bad_payload = {
        "user": "baoleiji",
        "confirmed_by": "baoleiji",
        "device_name": device_name,
        "device_type": device_type,
        "command": bad_command,
        "confirm_execute": "YES",
        "timeout": 60,
    }
    bad_result = http_json("POST", "/api/v1/netmiko/execute_confirmed", bad_payload)
    report["checks"]["bad_result"] = {
        "ok": bad_result.get("ok"),
        "status": bad_result.get("status"),
        "error": bad_result.get("error"),
        "audit_path": bad_result.get("audit_path"),
        "audit_error": bad_result.get("audit_error"),
        "request_id": bad_result.get("request_id"),
        "guard": (bad_result.get("plan") or {}).get("guard"),
    }
    print(json.dumps(report["checks"]["bad_result"], ensure_ascii=False, indent=2)[:3000])

    require(bad_result.get("ok") is False, "dangerous command ok=false", errors)
    require(bad_result.get("status") == "rejected", "dangerous command rejected", errors)
    require(((bad_result.get("plan") or {}).get("guard") or {}).get("status") == "blocked", "dangerous command guard blocked", errors)

    print("\n========== 5. execute_confirmed with YES, real read-only command ==========")
    confirmed_payload = {
        "user": "baoleiji",
        "confirmed_by": "baoleiji",
        "device_name": device_name,
        "device_type": device_type,
        "command": safe_command,
        "confirm_execute": "YES",
        "timeout": 60,
    }
    executed = http_json("POST", "/api/v1/netmiko/execute_confirmed", confirmed_payload, timeout=120)
    report["checks"]["executed"] = {
        "ok": executed.get("ok"),
        "status": executed.get("status"),
        "error": executed.get("error"),
        "audit_path": executed.get("audit_path"),
        "audit_error": executed.get("audit_error"),
        "request_id": executed.get("request_id"),
        "output_preview": executed.get("output_preview"),
        "plan": executed.get("plan"),
    }
    print(json.dumps(report["checks"]["executed"], ensure_ascii=False, indent=2)[:5000])

    require(executed.get("ok") is True, "confirmed execution ok=true", errors)
    require(executed.get("status") == "executed", "confirmed execution status executed", errors)
    require(bool(executed.get("audit_path")), "confirmed execution has audit_path", errors)
    require(bool(executed.get("output_preview") or executed.get("output") is not None), "confirmed execution has output", errors)

    out = "/tmp/v2_netmiko_api_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
