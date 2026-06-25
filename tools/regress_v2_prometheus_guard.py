#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch36 Prometheus guard regression.

Safety:
- This script does not execute any network device CLI command.
- It only performs guarded Prometheus read-only queries.
"""

from __future__ import print_function

import argparse
import json
import sys
import time
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.device_identity.resolver import DeviceIdentityResolver
from netaiops_asset.observability.promql_guard import PromqlGuard
from netaiops_asset.observability.prometheus_query import GuardedPrometheusQueryService


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def is_ip_literal(value):
    import ipaddress
    try:
        ipaddress.ip_address(str(value))
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Regress V2 Prometheus guard")
    parser.add_argument("--known-ip", default="10.189.250.8")
    args = parser.parse_args()

    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "known_ip": args.known_ip,
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch36 Prometheus Guard Regression ==========")

    print("\n========== 1. Resolve device identity and verify label candidates ==========")
    resolver = DeviceIdentityResolver()
    identity = resolver.resolve(args.known_ip, probe_prometheus=True)
    report["checks"]["identity"] = {
        "status": identity.get("status"),
        "hostname": identity.get("hostname"),
        "mgmt_ip": identity.get("mgmt_ip"),
        "netmiko_match": identity.get("netmiko_match"),
        "prometheus_label_candidates": identity.get("prometheus_label_candidates"),
        "prometheus_up_probe": identity.get("prometheus_up_probe"),
        "warnings": identity.get("warnings"),
    }

    print(json.dumps(report["checks"]["identity"], ensure_ascii=False, indent=2)[:3000])

    require(identity.get("status") in ("ok", "partial"), "Device identity resolution is ok/partial", errors)
    require(identity.get("mgmt_ip") == args.known_ip, "Device identity mgmt_ip matches known IP", errors)

    candidates = identity.get("prometheus_label_candidates") or {}
    ip_candidates = candidates.get("ip") or []
    require(args.known_ip in ip_candidates, "Prometheus ip candidates include known IP", errors)
    require(all(is_ip_literal(x) for x in ip_candidates), "Prometheus ip candidates contain only valid IP literals", errors)

    print("\n========== 2. PromQL guard allow/reject checks ==========")
    guard = PromqlGuard()

    allow_count = guard.validate_instant_query("count(up)")
    allow_device_up = guard.validate_instant_query('up{ip="' + args.known_ip + '"}')
    reject_empty = guard.validate_instant_query("")
    reject_high_card = guard.validate_instant_query("ifHCInOctets")
    reject_high_card_rate = guard.validate_instant_query("rate(ifHCInOctets[5m])")
    allow_high_card_labeled = guard.validate_instant_query('rate(ifHCInOctets{ip="' + args.known_ip + '"}[5m])')

    guard_checks = {
        "allow_count": allow_count.to_dict(),
        "allow_device_up": allow_device_up.to_dict(),
        "reject_empty": reject_empty.to_dict(),
        "reject_high_card": reject_high_card.to_dict(),
        "reject_high_card_rate": reject_high_card_rate.to_dict(),
        "allow_high_card_labeled": allow_high_card_labeled.to_dict(),
    }
    report["checks"]["guard_instant"] = guard_checks

    print(json.dumps(guard_checks, ensure_ascii=False, indent=2)[:4000])

    require(allow_count.passed, "Guard allows count(up)", errors)
    require(allow_device_up.passed, "Guard allows up filtered by ip", errors)
    require(not reject_empty.passed, "Guard rejects empty query", errors)
    require(not reject_high_card.passed, "Guard rejects bare high-cardinality metric", errors)
    require(not reject_high_card_rate.passed, "Guard rejects rate over bare high-cardinality metric", errors)
    require(allow_high_card_labeled.passed, "Guard allows high-cardinality metric with explicit label selector", errors)

    print("\n========== 3. Range guard checks ==========")
    now = int(time.time())
    start_ok = str(now - 600)
    end_ok = str(now)
    range_ok = guard.validate_range_query("count(up)", start_ok, end_ok, "60s")
    range_too_large = guard.validate_range_query("count(up)", str(now - 3 * 86400), end_ok, "60s")
    range_step_too_small = guard.validate_range_query("count(up)", start_ok, end_ok, "1s")

    range_checks = {
        "range_ok": range_ok.to_dict(),
        "range_too_large": range_too_large.to_dict(),
        "range_step_too_small": range_step_too_small.to_dict(),
    }
    report["checks"]["guard_range"] = range_checks

    print(json.dumps(range_checks, ensure_ascii=False, indent=2)[:4000])

    require(range_ok.passed, "Range guard allows bounded count(up)", errors)
    require(not range_too_large.passed, "Range guard rejects too large range", errors)
    require(not range_step_too_small.passed, "Range guard rejects too small step", errors)

    print("\n========== 4. Guarded Prometheus query execution ==========")
    service = GuardedPrometheusQueryService()

    plan = service.plan_device_up_query(identity)
    report["checks"]["device_up_plan"] = plan
    print("device_up_plan:", json.dumps(plan, ensure_ascii=False))

    require(plan.get("ok") and plan.get("query"), "Device up query plan generated", errors)

    count_result = service.execute_instant("count(up)")
    report["checks"]["execute_count_up"] = {
        "ok": count_result.get("ok"),
        "status": count_result.get("status"),
        "guard": count_result.get("guard"),
        "result": count_result.get("result"),
        "error": count_result.get("error"),
    }
    print("count_up_result:", json.dumps(report["checks"]["execute_count_up"], ensure_ascii=False)[:1200])
    require(count_result.get("ok"), "Guarded execute count(up) ok", errors)

    if plan.get("query"):
        up_result = service.execute_instant(plan["query"])
        report["checks"]["execute_device_up"] = {
            "ok": up_result.get("ok"),
            "status": up_result.get("status"),
            "guard": up_result.get("guard"),
            "result": up_result.get("result"),
            "error": up_result.get("error"),
        }
        print("device_up_result:", json.dumps(report["checks"]["execute_device_up"], ensure_ascii=False)[:1600])
        require(up_result.get("ok"), "Guarded execute device up query ok", errors)

    rejected = service.execute_instant("ifHCInOctets")
    report["checks"]["execute_rejected"] = {
        "ok": rejected.get("ok"),
        "status": rejected.get("status"),
        "guard": rejected.get("guard"),
        "error": rejected.get("error"),
    }
    print("rejected_result:", json.dumps(report["checks"]["execute_rejected"], ensure_ascii=False)[:1200])
    require(rejected.get("status") == "rejected", "Guarded service rejects unsafe query before MCP call", errors)

    range_result = service.execute_range("count(up)", start_ok, end_ok, "60s")
    report["checks"]["execute_range_count_up"] = {
        "ok": range_result.get("ok"),
        "status": range_result.get("status"),
        "guard": range_result.get("guard"),
        "result_type": (range_result.get("result") or {}).get("resultType") if isinstance(range_result.get("result"), dict) else None,
        "error": range_result.get("error"),
    }
    print("range_count_up_result:", json.dumps(report["checks"]["execute_range_count_up"], ensure_ascii=False)[:1200])
    require(range_result.get("ok"), "Guarded execute range count(up) ok", errors)

    out = "/tmp/v2_prometheus_guard_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
