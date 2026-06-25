#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime

BASE_URL = "http://127.0.0.1:18081"


def chat(question, conversation_id=None, timeout=180):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    req = urllib.request.Request(
        BASE_URL + "/api/v1/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
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

    print("========== Batch52 follow-up summary fix regression ==========")
    print("This regression does NOT execute device CLI.")

    first = chat("WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令")
    conv_id = first.get("conversation_id")

    report["checks"]["first"] = {
        "status": first.get("status"),
        "planner_source": first.get("planner_source"),
        "conversation_id": conv_id,
        "parsed": first.get("parsed"),
        "answer": first.get("answer"),
    }

    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:5000])

    require(first.get("planner_source") == "v2_chat_router", "seed turn uses v2_chat_router", errors)
    require(bool(conv_id), "conversation_id exists", errors)

    summary = chat("总结一下目前这个设备CPU排查到的结论", conversation_id=conv_id)

    report["checks"]["summary"] = {
        "status": summary.get("status"),
        "planner_source": summary.get("planner_source"),
        "conversation_id": summary.get("conversation_id"),
        "parsed": summary.get("parsed"),
        "answer": summary.get("answer"),
        "v2": summary.get("v2"),
    }

    print(json.dumps(report["checks"]["summary"], ensure_ascii=False, indent=2)[:7000])

    answer = summary.get("answer") or ""

    require(summary.get("planner_source") == "v2_followup_analysis", "summary follow-up uses v2_followup_analysis", errors)
    require(summary.get("status") == "ok", "summary follow-up status ok", errors)
    require("综合结论" in answer, "summary answer contains 综合结论", errors)
    require("当前设备" in answer, "summary answer contains 当前设备", errors)
    require(summary.get("planner_source") != "v2_chat_router", "summary does not regenerate commands", errors)
    require(summary.get("planner_source") != "llm_tool_planner", "summary does not fallback to V1", errors)

    out = "/tmp/v2_followup_summary_fix_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
