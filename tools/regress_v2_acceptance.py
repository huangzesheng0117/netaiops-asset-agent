#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 acceptance regression.

This script validates V2 foundation capabilities after Batch33-Batch39.

Safety:
- It does NOT execute any new network device CLI command.
- It reads Netmiko MCP device inventory only.
- It reads latest existing Netmiko execution audit file only.
- It runs Prometheus read-only guarded queries.
"""

from __future__ import print_function

import json
import os
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.mcp.netmiko_client import NetmikoMcpClient
from netaiops_asset.mcp.prometheus_client import PrometheusMcpClient
from netaiops_asset.device_identity.resolver import DeviceIdentityResolver
from netaiops_asset.observability.promql_guard import PromqlGuard
from netaiops_asset.observability.prometheus_query import GuardedPrometheusQueryService
from netaiops_asset.netmiko.cli_guard import CliReadOnlyGuard
from netaiops_asset.netmiko.executor import ConfirmedNetmikoExecutor
from netaiops_asset.troubleshoot.session import TroubleSessionStore
from netaiops_asset.troubleshoot.evidence_builder import EvidenceBuilder


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def file_exists(path):
    return os.path.exists(str(path))


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Acceptance Regression ==========")

    print("\n========== 1. Required files ==========")
    required_files = [
        "docs/v2_mcp_discovery.md",
        "docs/v2_batch34_mcp_client.md",
        "docs/v2_batch35_device_identity.md",
        "docs/v2_batch36_prometheus_guard.md",
        "docs/v2_batch37_cli_guard.md",
        "docs/v2_batch38_netmiko_confirmed_execute.md",
        "docs/v2_batch39_troubleshoot_evidence.md",
        "netaiops_asset/mcp/client.py",
        "netaiops_asset/mcp/netmiko_client.py",
        "netaiops_asset/mcp/prometheus_client.py",
        "netaiops_asset/device_identity/resolver.py",
        "netaiops_asset/observability/promql_guard.py",
        "netaiops_asset/observability/prometheus_query.py",
        "netaiops_asset/netmiko/cli_guard.py",
        "netaiops_asset/netmiko/executor.py",
        "netaiops_asset/troubleshoot/session.py",
        "netaiops_asset/troubleshoot/evidence_builder.py",
        "tools/regress_v2_mcp_discovery.py",
        "tools/regress_v2_mcp_client.py",
        "tools/regress_v2_device_identity.py",
        "tools/regress_v2_prometheus_guard.py",
        "tools/regress_v2_cli_guard.py",
        "tools/regress_v2_netmiko_confirmed_execute.py",
        "tools/regress_v2_troubleshoot_evidence.py",
    ]

    missing = []
    for rel in required_files:
        path = PROJECT_ROOT / rel
        ok = file_exists(path)
        print("[{}] {}".format("OK" if ok else "MISSING", rel))
        if not ok:
            missing.append(rel)

    report["checks"]["required_files"] = {
        "missing": missing,
        "count": len(required_files),
    }
    require(not missing, "All required V2 files exist", errors)

    print("\n========== 2. Netmiko MCP client ==========")
    netmiko = NetmikoMcpClient()
    nt_tools = netmiko.list_tools()
    nt_tool_names = [x.get("name") for x in nt_tools]
    devices = netmiko.list_devices()

    print("Netmiko tools:", ", ".join(nt_tool_names))
    print("Netmiko device_count:", len(devices))

    report["checks"]["netmiko"] = {
        "tools": nt_tool_names,
        "device_count": len(devices),
    }

    require("get_network_device_list" in nt_tool_names, "Netmiko tool get_network_device_list exists", errors)
    require("send_command_and_get_output" in nt_tool_names, "Netmiko tool send_command_and_get_output exists", errors)
    require("set_config_commands_and_commit_or_save" in nt_tool_names, "Netmiko config tool discovered for blocking", errors)
    require(len(devices) > 0, "Netmiko device list is not empty", errors)

    print("\n========== 3. Prometheus MCP client ==========")
    prometheus = PrometheusMcpClient()
    pt_tools = prometheus.list_tools()
    pt_tool_names = [x.get("name") for x in pt_tools]
    health = prometheus.health_check()
    count_up = prometheus.execute_query("count(up)")
    direct_targets = prometheus.get_targets_via_direct_prometheus()

    print("Prometheus tools:", ", ".join(pt_tool_names))
    print("Prometheus health ok:", health.ok)
    print("Prometheus count(up) ok:", count_up.ok)
    print("Direct Prometheus targets ok:", direct_targets.get("ok"))

    report["checks"]["prometheus"] = {
        "tools": pt_tool_names,
        "health_ok": health.ok,
        "health": health.content_json,
        "count_up_ok": count_up.ok,
        "count_up": count_up.content_json,
        "direct_targets": {
            "ok": direct_targets.get("ok"),
            "active_targets_count": direct_targets.get("active_targets_count"),
            "error": direct_targets.get("error"),
        },
    }

    require("health_check" in pt_tool_names, "Prometheus tool health_check exists", errors)
    require("execute_query" in pt_tool_names, "Prometheus tool execute_query exists", errors)
    require("execute_range_query" in pt_tool_names, "Prometheus tool execute_range_query exists", errors)
    require("list_metrics" in pt_tool_names, "Prometheus tool list_metrics exists", errors)
    require(health.ok, "Prometheus health_check ok", errors)
    require(count_up.ok, "Prometheus count(up) ok", errors)
    require(direct_targets.get("ok"), "Direct Prometheus targets ok", errors)

    print("\n========== 4. Device identity resolver ==========")
    resolver = DeviceIdentityResolver()
    identity = resolver.resolve("10.189.250.8", probe_prometheus=True)
    print(json.dumps({
        "status": identity.get("status"),
        "hostname": identity.get("hostname"),
        "mgmt_ip": identity.get("mgmt_ip"),
        "netmiko_match": identity.get("netmiko_match"),
        "prometheus_label_candidates": identity.get("prometheus_label_candidates"),
    }, ensure_ascii=False, indent=2)[:3000])

    report["checks"]["identity"] = identity

    require(identity.get("status") in ("ok", "partial"), "Device identity status ok/partial", errors)
    require(identity.get("mgmt_ip") == "10.189.250.8", "Device identity mgmt_ip matches", errors)
    require(bool(identity.get("hostname")), "Device identity hostname exists", errors)
    require(bool(identity.get("netmiko_match")), "Device identity has Netmiko match", errors)

    ip_candidates = ((identity.get("prometheus_label_candidates") or {}).get("ip") or [])
    require("10.189.250.8" in ip_candidates, "Prometheus label candidate ip includes known IP", errors)

    print("\n========== 5. PromQL guard and guarded query ==========")
    guard = PromqlGuard()
    service = GuardedPrometheusQueryService()

    allow = guard.validate_instant_query('up{ip="10.189.250.8"}')
    reject = guard.validate_instant_query("ifHCInOctets")
    device_plan = service.plan_device_up_query(identity)
    device_up = service.execute_instant(device_plan["query"]) if device_plan.get("ok") else {"ok": False, "error": "plan failed"}
    unsafe = service.execute_instant("ifHCInOctets")

    report["checks"]["promql_guard"] = {
        "allow": allow.to_dict(),
        "reject": reject.to_dict(),
        "device_plan": device_plan,
        "device_up_ok": device_up.get("ok"),
        "unsafe_status": unsafe.get("status"),
    }

    print("allow passed:", allow.passed)
    print("reject passed:", reject.passed)
    print("device_plan:", json.dumps(device_plan, ensure_ascii=False))
    print("device_up ok:", device_up.get("ok"))
    print("unsafe status:", unsafe.get("status"))

    require(allow.passed, "PromQL guard allows up filtered by ip", errors)
    require(not reject.passed, "PromQL guard rejects bare high-cardinality metric", errors)
    require(device_plan.get("ok"), "Device up query plan generated", errors)
    require(device_up.get("ok"), "Guarded device up query ok", errors)
    require(unsafe.get("status") == "rejected", "Unsafe PromQL rejected before MCP call", errors)

    print("\n========== 6. CLI guard and confirmed executor safety ==========")
    cli_guard = CliReadOnlyGuard()
    passed = cli_guard.validate("show clock", device_type="cisco_nxos").to_dict()
    blocked = cli_guard.validate("configure terminal", device_type="cisco_nxos").to_dict()
    review = cli_guard.validate("show running-config", device_type="cisco_nxos").to_dict()

    executor = ConfirmedNetmikoExecutor()
    safe_plan = executor.build_plan("SH16-A04-ACI-2001", "show clock", device_type="cisco_nxos")
    bad_plan = executor.build_plan("SH16-A04-ACI-2001", "configure terminal", device_type="cisco_nxos")
    no_confirm = executor.execute_confirmed(
        device_name="SH16-A04-ACI-2001",
        command="show clock",
        device_type="cisco_nxos",
        confirm_execute="",
        confirmed_by="acceptance",
        timeout=5,
    )

    report["checks"]["cli_guard"] = {
        "passed": passed,
        "blocked": blocked,
        "review": review,
        "safe_plan": safe_plan,
        "bad_plan": bad_plan,
        "no_confirm": {
            "ok": no_confirm.get("ok"),
            "status": no_confirm.get("status"),
            "error": no_confirm.get("error"),
            "audit_path": no_confirm.get("audit_path"),
        },
    }

    print("passed:", json.dumps(passed, ensure_ascii=False))
    print("blocked:", json.dumps(blocked, ensure_ascii=False))
    print("review:", json.dumps(review, ensure_ascii=False))
    print("safe_plan status:", safe_plan.get("status"))
    print("bad_plan status:", bad_plan.get("status"))
    print("no_confirm status:", no_confirm.get("status"))

    require(passed.get("status") == "passed", "CLI guard passes show clock", errors)
    require(blocked.get("status") == "blocked", "CLI guard blocks configure terminal", errors)
    require(review.get("status") == "review", "CLI guard marks show running-config as review", errors)
    require(safe_plan.get("status") == "pending_confirmation", "Executor safe plan pending confirmation", errors)
    require(bad_plan.get("status") == "rejected", "Executor bad plan rejected", errors)
    require(no_confirm.get("status") == "pending_confirmation", "Executor blocks missing confirmation before MCP call", errors)

    print("\n========== 7. Trouble session and evidence builder ==========")
    store = TroubleSessionStore()
    builder = EvidenceBuilder()

    session = store.create_session(
        question="V2总体验收：汇总10.189.250.8身份、Prometheus状态和最近Netmiko审计",
        keyword="10.189.250.8",
    )
    session_id = session["session_id"]

    identity_ev = builder.build_identity_evidence("10.189.250.8")
    prom_ev = builder.build_prometheus_up_evidence(identity_ev.get("payload") or {})
    netmiko_ev = builder.build_latest_netmiko_audit_evidence()

    store.add_evidence(session_id, **identity_ev)
    store.add_evidence(session_id, **prom_ev)
    store.add_evidence(session_id, **netmiko_ev)

    summary = builder.build_summary([identity_ev, prom_ev, netmiko_ev])
    final_session = store.update_session(
        session_id,
        status="acceptance_evidence_collected",
        summary=summary,
        warnings=[],
    )

    report["checks"]["troubleshoot"] = {
        "session_id": session_id,
        "status": final_session.get("status"),
        "evidence_count": len(final_session.get("evidences") or []),
        "summary": final_session.get("summary"),
        "netmiko_evidence_status": netmiko_ev.get("status"),
    }

    print(json.dumps(report["checks"]["troubleshoot"], ensure_ascii=False, indent=2)[:3000])

    require(final_session.get("status") == "acceptance_evidence_collected", "Trouble session acceptance status ok", errors)
    require(len(final_session.get("evidences") or []) == 3, "Trouble session has 3 evidences", errors)
    require(netmiko_ev.get("status") == "ok", "Latest Netmiko audit evidence ok", errors)

    out = "/tmp/v2_acceptance_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
