#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch45 Prometheus CPU evidence in chat router regression.

Safety:
- No device CLI execution.
- Only /api/v1/chat CPU question and Prometheus read-only query.
"""

from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def post_chat(question):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }

    req = urllib.request.Request(
        BASE_URL + "/api/v1/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
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

    print("========== V2 Batch45 Prometheus CPU Evidence Chat Regression ==========")

    data = post_chat("SH8-G03-DCI-BN-SW01当前CPU利用率是多少？我该通过哪些命令排查？")
    parsed = data.get("parsed") or {}
    answer = data.get("answer") or ""
    v2 = data.get("v2") or {}
    evidence = v2.get("prometheus_evidence")

    report["checks"]["chat"] = {
        "status": data.get("status"),
        "planner_source": data.get("planner_source"),
        "parsed": parsed,
        "answer": answer,
        "count": data.get("count"),
        "items": data.get("items"),
        "prometheus_evidence": evidence,
    }

    print(json.dumps(report["checks"]["chat"], ensure_ascii=False, indent=2)[:10000])

    require(data.get("planner_source") == "v2_chat_router", "chat uses v2_chat_router", errors)
    require(parsed.get("v2_intent") == "cpu_check", "intent is cpu_check", errors)
    require(parsed.get("mgmt_ip") == "10.192.251.101", "mgmt_ip is expected", errors)
    require(bool(data.get("items")), "command suggestions exist", errors)
    require("Prometheus 当前 CPU 证据" in answer, "answer includes Prometheus CPU evidence section", errors)
    require(isinstance(evidence, dict), "v2.prometheus_evidence exists", errors)
    require(evidence.get("status") in ("ok", "no_data", "skipped", "failed"), "prometheus evidence has valid status", errors)

    if evidence.get("status") == "ok":
        require(bool((evidence.get("matched") or {}).get("query")), "matched CPU query exists", errors)
        require((evidence.get("matched") or {}).get("has_data") is True, "matched CPU query has data", errors)
    else:
        require("未从内置 CPU 指标候选中查询到当前 CPU 数据" in answer or evidence.get("status") in ("skipped", "failed"), "no_data status is explained", errors)

    out = "/tmp/v2_prometheus_cpu_chat_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
