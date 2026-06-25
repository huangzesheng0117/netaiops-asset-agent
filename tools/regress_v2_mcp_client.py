#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch34 MCP client regression.

This script validates the newly introduced MCP client modules.

Safety:
- It does NOT execute any network device CLI command.
- It does NOT call Netmiko send_command_and_get_output.
- It does NOT call Netmiko config tool.
"""

from __future__ import print_function

import json
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.mcp.netmiko_client import NetmikoMcpClient
from netaiops_asset.mcp.prometheus_client import PrometheusMcpClient


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
        "netmiko": {},
        "prometheus": {},
        "errors": errors,
    }

    print("========== V2 Batch34 MCP Client Regression ==========")

    print("\n========== Netmiko MCP client ==========")
    netmiko = NetmikoMcpClient()

    nt_tools = netmiko.list_tools()
    nt_tool_names = [t.get("name") for t in nt_tools]
    print("Netmiko tools:", ", ".join(nt_tool_names))
    report["netmiko"]["tools"] = nt_tool_names

    require("get_network_device_list" in nt_tool_names, "Netmiko client can list get_network_device_list", errors)
    require("send_command_and_get_output" in nt_tool_names, "Netmiko client can discover send_command_and_get_output", errors)
    require("set_config_commands_and_commit_or_save" in nt_tool_names, "Netmiko config tool is discovered for explicit blocking", errors)

    devices = netmiko.list_devices()
    report["netmiko"]["device_count"] = len(devices)
    print("Netmiko device_count:", len(devices))
    require(len(devices) > 0, "Netmiko client can list devices", errors)

    sample_devices = devices[:5]
    report["netmiko"]["sample_devices"] = sample_devices
    print("Netmiko sample_devices:")
    for d in sample_devices:
        print("  - name={name} hostname={hostname} device_type={device_type}".format(
            name=d.get("name"),
            hostname=d.get("hostname"),
            device_type=d.get("device_type"),
        ))

    print("\n========== Prometheus MCP client ==========")
    prometheus = PrometheusMcpClient()

    pt_tools = prometheus.list_tools()
    pt_tool_names = [t.get("name") for t in pt_tools]
    print("Prometheus tools:", ", ".join(pt_tool_names))
    report["prometheus"]["tools"] = pt_tool_names

    require("health_check" in pt_tool_names, "Prometheus client can list health_check", errors)
    require("execute_query" in pt_tool_names, "Prometheus client can list execute_query", errors)
    require("execute_range_query" in pt_tool_names, "Prometheus client can list execute_range_query", errors)
    require("list_metrics" in pt_tool_names, "Prometheus client can list list_metrics", errors)

    health = prometheus.health_check()
    report["prometheus"]["health_check"] = {
        "ok": health.ok,
        "is_error": health.is_error,
        "error": health.error,
        "content_json": health.content_json,
    }
    require(health.ok, "Prometheus health_check via wrapper ok", errors)
    if isinstance(health.content_json, dict):
        print("Prometheus health status:", health.content_json.get("status"))
        print("Prometheus backend:", health.content_json.get("prometheus_url"))

    metrics = prometheus.list_metrics(limit=10)
    report["prometheus"]["list_metrics"] = {
        "ok": metrics.ok,
        "is_error": metrics.is_error,
        "error": metrics.error,
        "content_json": metrics.content_json,
    }
    require(metrics.ok, "Prometheus list_metrics via wrapper ok", errors)
    if isinstance(metrics.content_json, dict):
        print("Prometheus total_count:", metrics.content_json.get("total_count"))
        print("Prometheus returned_count:", metrics.content_json.get("returned_count"))

    query = prometheus.execute_query("count(up)")
    report["prometheus"]["execute_query_count_up"] = {
        "ok": query.ok,
        "is_error": query.is_error,
        "error": query.error,
        "content_json": query.content_json,
    }
    require(query.ok, "Prometheus execute_query count(up) via wrapper ok", errors)
    if isinstance(query.content_json, dict):
        print("Prometheus query resultType:", query.content_json.get("resultType"))
        print("Prometheus query result sample:", str(query.content_json.get("result"))[:300])

    targets = prometheus.get_targets_via_direct_prometheus()
    report["prometheus"]["direct_targets"] = {
        "ok": targets.get("ok"),
        "active_targets_count": targets.get("active_targets_count"),
        "dropped_targets_count": targets.get("dropped_targets_count"),
        "error": targets.get("error"),
    }
    require(targets.get("ok"), "Direct Prometheus targets query via wrapper ok", errors)
    if targets.get("ok"):
        print("Direct Prometheus active_targets_count:", targets.get("active_targets_count"))

    out = "/tmp/v2_mcp_client_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
