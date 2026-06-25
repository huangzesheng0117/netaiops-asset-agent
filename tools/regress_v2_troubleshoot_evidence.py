#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch39 troubleshooting session and evidence builder regression.

Safety:
- This script does NOT execute any new device CLI command.
- It reads existing Netmiko execution audit files only.
- It performs guarded Prometheus read-only query.
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

from netaiops_asset.troubleshoot.session import TroubleSessionStore, session_path
from netaiops_asset.troubleshoot.evidence_builder import EvidenceBuilder


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def main():
    parser = argparse.ArgumentParser(description="Regress V2 troubleshooting evidence builder")
    parser.add_argument("--keyword", default="10.189.250.8")
    parser.add_argument("--question", default="请分析这台设备当前是否有监控up状态，并汇总最近一次Netmiko只读取证结果")
    args = parser.parse_args()

    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "args": vars(args),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch39 Trouble Session and Evidence Regression ==========")
    print("keyword:", args.keyword)
    print("question:", args.question)

    store = TroubleSessionStore()
    builder = EvidenceBuilder()

    print("\n========== 1. Create troubleshoot session ==========")
    session = store.create_session(question=args.question, keyword=args.keyword)
    session_id = session["session_id"]
    path = session_path(session_id)
    report["checks"]["session_created"] = {
        "session_id": session_id,
        "path": path,
    }
    print(json.dumps(report["checks"]["session_created"], ensure_ascii=False, indent=2))

    require(bool(session_id), "Trouble session created", errors)

    print("\n========== 2. Build device identity evidence ==========")
    identity_ev = builder.build_identity_evidence(args.keyword)
    store.add_evidence(session_id, **identity_ev)
    report["checks"]["identity_evidence"] = {
        "status": identity_ev.get("status"),
        "summary": identity_ev.get("summary"),
        "hostname": (identity_ev.get("payload") or {}).get("hostname"),
        "mgmt_ip": (identity_ev.get("payload") or {}).get("mgmt_ip"),
    }
    print(json.dumps(report["checks"]["identity_evidence"], ensure_ascii=False, indent=2))

    require(identity_ev.get("status") == "ok", "Identity evidence status is ok", errors)

    print("\n========== 3. Build Prometheus up evidence ==========")
    identity_payload = identity_ev.get("payload") or {}
    prom_ev = builder.build_prometheus_up_evidence(identity_payload)
    store.add_evidence(session_id, **prom_ev)
    report["checks"]["prometheus_evidence"] = {
        "status": prom_ev.get("status"),
        "summary": prom_ev.get("summary"),
        "series_count": (prom_ev.get("payload") or {}).get("series_count"),
    }
    print(json.dumps(report["checks"]["prometheus_evidence"], ensure_ascii=False, indent=2))

    require(prom_ev.get("status") == "ok", "Prometheus evidence status is ok", errors)

    print("\n========== 4. Build latest Netmiko audit evidence, no new CLI execution ==========")
    netmiko_ev = builder.build_latest_netmiko_audit_evidence()
    store.add_evidence(session_id, **netmiko_ev)
    report["checks"]["netmiko_evidence"] = {
        "status": netmiko_ev.get("status"),
        "summary": netmiko_ev.get("summary"),
        "audit_file": (netmiko_ev.get("payload") or {}).get("_audit_file"),
        "output_preview": (netmiko_ev.get("payload") or {}).get("output_preview"),
    }
    print(json.dumps(report["checks"]["netmiko_evidence"], ensure_ascii=False, indent=2))

    require(netmiko_ev.get("status") == "ok", "Netmiko audit evidence status is ok", errors)

    print("\n========== 5. Build and save session summary ==========")
    evidences = [identity_ev, prom_ev, netmiko_ev]
    summary = builder.build_summary(evidences)
    final_session = store.update_session(
        session_id,
        status="evidence_collected",
        summary=summary,
        warnings=[],
    )

    report["checks"]["final_session"] = {
        "session_id": final_session.get("session_id"),
        "status": final_session.get("status"),
        "evidence_count": len(final_session.get("evidences") or []),
        "summary": final_session.get("summary"),
        "path": path,
    }

    print(json.dumps(report["checks"]["final_session"], ensure_ascii=False, indent=2)[:4000])

    require(final_session.get("status") == "evidence_collected", "Trouble session status is evidence_collected", errors)
    require(len(final_session.get("evidences") or []) == 3, "Trouble session has 3 evidence records", errors)
    require(bool(final_session.get("summary")), "Trouble session has summary", errors)

    print("\n========== 6. List recent sessions ==========")
    recent = store.list_sessions(limit=5)
    report["checks"]["recent_sessions"] = recent
    print(json.dumps(recent, ensure_ascii=False, indent=2)[:3000])
    require(any(item.get("session_id") == session_id for item in recent), "New session appears in recent session list", errors)

    out = "/tmp/v2_troubleshoot_evidence_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== Result ==========")
    print("session_file:", path)
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
