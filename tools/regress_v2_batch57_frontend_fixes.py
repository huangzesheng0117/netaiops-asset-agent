#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import sys
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def http_json(method, path, payload=None, timeout=360):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def chat(question, conversation_id=None, timeout=360):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return http_json("POST", "/api/v1/chat", payload=payload, timeout=timeout)


def plan(question, timeout=180):
    payload = {"question": question, "user": "baoleiji"}
    return http_json("POST", "/api/v1/v2/llm_plan", payload=payload, timeout=timeout)


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def compact(data):
    return {
        "status": data.get("status"),
        "planner_source": data.get("planner_source"),
        "conversation_id": data.get("conversation_id"),
        "parsed": data.get("parsed"),
        "answer": data.get("answer"),
        "count": data.get("count"),
        "items": data.get("items"),
        "v2": data.get("v2"),
    }


def main():
    errors = []
    report = {"created_at": datetime.now().isoformat(), "checks": {}, "errors": errors}

    print("========== Batch57 frontend fixes regression ==========")
    print("This regression WILL execute one batch of read-only interface commands.")

    q1 = "设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题"
    first = chat(q1)
    conv_id = first.get("conversation_id")
    report["checks"]["first"] = compact(first)
    print("\n========== first ==========")
    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:9000])

    require(first.get("planner_source") == "v2_chat_router", "first uses v2_chat_router", errors)
    require((first.get("parsed") or {}).get("v2_intent") == "interface_error_check", "first intent interface_error_check", errors)
    require((first.get("parsed") or {}).get("interface_name") == "Ethernet1/46", "first interface Ethernet1/46", errors)
    require(bool(conv_id), "conversation_id exists", errors)

    print("\n========== one-step execute ==========")
    exe = chat("执行这批命令 YES", conversation_id=conv_id, timeout=360)
    report["checks"]["execute_once"] = compact(exe)
    print(json.dumps(report["checks"]["execute_once"], ensure_ascii=False, indent=2)[:12000])

    answer = exe.get("answer") or ""
    require(exe.get("planner_source") == "v2_execution_confirmation", "one-step execute uses confirmation router", errors)
    require(exe.get("status") in ("ok", "partial"), "one-step execute ok/partial", errors)
    require("执行统计：total=5" in answer, "execution answer total=5", errors)
    require("接口错包" in answer or "接口错误" in answer, "execution answer is interface-specific", errors)
    require("system resources" not in answer, "execution answer no system resources CPU residue", errors)
    require("CPU 总体使用率" not in answer, "execution answer no CPU residue", errors)

    print("\n========== follow-up cause analysis ==========")
    follow = chat("根据命令的执行结果，分析一下接口错包增长的原因", conversation_id=conv_id, timeout=180)
    report["checks"]["followup_reason"] = compact(follow)
    print(json.dumps(report["checks"]["followup_reason"], ensure_ascii=False, indent=2)[:12000])

    f_answer = follow.get("answer") or ""
    require(follow.get("planner_source") == "v2_followup_analysis", "follow-up uses v2_followup_analysis", errors)
    require(follow.get("status") == "ok", "follow-up status ok", errors)
    require("不会重新生成命令" in f_answer or "不会重新执行设备命令" in f_answer, "follow-up states no new commands/execution", errors)
    require("接口错包" in f_answer or "接口错误" in f_answer, "follow-up is interface-error specific", errors)
    require((follow.get("items") or []) == [], "follow-up returns no command items", errors)
    require("system resources" not in f_answer, "follow-up no system resources CPU residue", errors)
    require("CPU 总体使用率" not in f_answer, "follow-up no CPU residue", errors)

    print("\n========== llm auth diagnostic ==========")
    p = plan(q1)
    report["checks"]["llm_plan_diag"] = p
    plan_data = p.get("plan") or {}
    safe = {
        "source": plan_data.get("source"),
        "llm_status": plan_data.get("llm_status"),
        "llm_error": str(plan_data.get("llm_error") or "")[:300],
        "llm_config": plan_data.get("llm_config"),
        "action": plan_data.get("action"),
        "category": plan_data.get("category"),
        "entities": plan_data.get("entities"),
    }
    print(json.dumps(safe, ensure_ascii=False, indent=2)[:5000])

    out = "/tmp/v2_batch57_frontend_fixes_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== Result ==========")
    print("conversation_id:", conv_id)
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
