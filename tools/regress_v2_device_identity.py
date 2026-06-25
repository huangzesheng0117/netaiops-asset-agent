#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch35 device identity resolver regression.

Safety:
- This script reads CMDB device detail.
- This script reads Netmiko MCP device list.
- This script executes a Prometheus read-only instant query up{ip="..."}.
- This script does NOT execute any network device CLI command.
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

from netaiops_asset.device_identity.resolver import DeviceIdentityResolver
from netaiops_asset.mcp.netmiko_client import NetmikoMcpClient


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def compact_resolution(data):
    return {
        "status": data.get("status"),
        "keyword": data.get("keyword"),
        "keyword_type": data.get("keyword_type"),
        "cmdb_count": data.get("cmdb_count"),
        "hostname": data.get("hostname"),
        "mgmt_ip": data.get("mgmt_ip"),
        "netmiko_match": data.get("netmiko_match"),
        "netmiko_match_reason": data.get("netmiko_match_reason"),
        "prometheus_label_candidates": data.get("prometheus_label_candidates"),
        "prometheus_up_probe": data.get("prometheus_up_probe"),
        "warnings": data.get("warnings"),
    }


def main():
    parser = argparse.ArgumentParser(description="Regress V2 device identity resolver")
    parser.add_argument("--known-ip", default="10.189.250.8")
    parser.add_argument("--probe-prometheus", action="store_true", default=True)
    args = parser.parse_args()

    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "known_ip": args.known_ip,
        "resolutions": [],
        "errors": errors,
    }

    print("========== V2 Batch35 Device Identity Regression ==========")

    resolver = DeviceIdentityResolver()

    print("\n========== 1. Resolve known CMDB IP ==========")
    known = resolver.resolve(args.known_ip, probe_prometheus=args.probe_prometheus)
    report["resolutions"].append(compact_resolution(known))

    print(json.dumps(compact_resolution(known), ensure_ascii=False, indent=2)[:3000])

    require(known.get("status") in ("ok", "partial"), "Known IP resolution status is ok/partial", errors)
    require(bool(known.get("selected_cmdb")), "Known IP has selected CMDB item", errors)
    require(bool(known.get("mgmt_ip")), "Known IP has mgmt_ip", errors)
    require(bool(known.get("hostname")), "Known IP has hostname", errors)
    require(isinstance(known.get("prometheus_label_candidates"), dict), "Known IP has Prometheus label candidates", errors)

    print("\n========== 2. Pick one Netmiko sample and resolve by name/IP ==========")
    netmiko = NetmikoMcpClient()
    devices = netmiko.list_devices()
    require(len(devices) > 0, "Netmiko device list is not empty", errors)

    sample = devices[0] if devices else {}
    sample_name = sample.get("name")
    sample_ip = sample.get("hostname")

    print("sample_name:", sample_name)
    print("sample_ip:", sample_ip)
    report["netmiko_sample"] = sample

    if sample_name:
        by_name = resolver.resolve(sample_name, probe_prometheus=False)
        report["resolutions"].append(compact_resolution(by_name))
        print("\n-- resolve sample by Netmiko name --")
        print(json.dumps(compact_resolution(by_name), ensure_ascii=False, indent=2)[:2500])
        require(bool(by_name.get("netmiko_match")), "Resolve sample name can match Netmiko device", errors)

    if sample_ip:
        by_ip = resolver.resolve(sample_ip, probe_prometheus=False)
        report["resolutions"].append(compact_resolution(by_ip))
        print("\n-- resolve sample by Netmiko hostname/IP --")
        print(json.dumps(compact_resolution(by_ip), ensure_ascii=False, indent=2)[:2500])
        require(bool(by_ip.get("netmiko_match")), "Resolve sample IP can match Netmiko device", errors)

    print("\n========== 3. Not-found input should not crash ==========")
    not_found_keyword = "__NETAIOPS_DEVICE_NOT_FOUND_TEST__"
    not_found = resolver.resolve(not_found_keyword, probe_prometheus=False)
    report["resolutions"].append(compact_resolution(not_found))
    print(json.dumps(compact_resolution(not_found), ensure_ascii=False, indent=2)[:2000])
    require(not_found.get("status") in ("not_found", "partial", "ok"), "Not-found input returns structured result", errors)

    out = "/tmp/v2_device_identity_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
