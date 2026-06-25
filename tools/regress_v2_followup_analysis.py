#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch50 follow-up analysis regression.

This script seeds context, executes passed CPU read-only commands, then asks
follow-up questions without device name.

It will execute read-only commands through the confirmed bulk flow:
- show system resources
- show processes cpu
- show processes cpu sort
- show logging last 100
"""

from __future__ import print_function

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def http_json(method, path, payload=None, timeout=360):
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


def get_context(conversation_id=None):
    query = urllib.parse.urlencode({"conversation_id": conversation_id})
    return http_json("GET", "/api/v1/v2/context?" + query)


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
    report = {
        "created_at": datetime.now().isoformat(),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch50 Follow-up Analysis Regression ==========")

    print("\n========== 1. Seed CPU context ==========")
    first = chat("WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令", timeout=180)
    conv_id = first.get("conversation_id")
    report["checks"]["first"] = compact(first)
    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:6000])

    require(first.get("planner_source") == "v2_chat_router", "First turn uses v2_chat_router", errors)
    require(bool(conv_id), "Conversation id exists", errors)

    print("\n========== 2. Bulk execute all passed commands ==========")
    executed = chat("确认执行全部命令 YES", conversation_id=conv_id, timeout=360)
    report["checks"]["executed"] = compact(executed)
    print(json.dumps(report["checks"]["executed"], ensure_ascii=False, indent=2)[:10000])

    exec_items = executed.get("items") or []

    require(executed.get("planner_source") == "v2_execution_confirmation", "Bulk execution uses confirmation router", errors)
    require(executed.get("status") in ("ok", "partial"), "Bulk execution ok/partial", errors)
    require(len(exec_items) >= 4, "Bulk execution returns at least 4 items", errors)
    require(all(x.get("ok") is True for x in exec_items[:4]), "First four commands executed ok", errors)

    print("\n========== 3. Ask follow-up conclusion question ==========")
    follow1 = chat("结合以上三点，给出更准确的结论，当前是否真的是CPU问题？", conversation_id=conv_id, timeout=180)
    report["checks"]["followup_conclusion"] = compact(follow1)
    print(json.dumps(report["checks"]["followup_conclusion"], ensure_ascii=False, indent=2)[:9000])

    answer1 = follow1.get("answer") or ""
    v2_1 = follow1.get("v2") or {}

    require(follow1.get("planner_source") == "v2_followup_analysis", "Conclusion follow-up uses v2_followup_analysis", errors)
    require(follow1.get("status") == "ok", "Conclusion follow-up status ok", errors)
    require("我会沿用上一轮 V2 会话上下文继续分析" in answer1, "Answer states context continuation", errors)
    require("综合结论" in answer1, "Answer includes 综合结论", errors)
    require("建议下一步" in answer1, "Answer includes 建议下一步", errors)
    require(v2_1.get("context_used") is True, "v2.context_used true", errors)
    require(bool(v2_1.get("facts")), "v2 facts exist", errors)
    require(bool(v2_1.get("conclusion")), "v2 conclusion exists", errors)
    require("不支持" in v2_1.get("conclusion", "") or "CPU" in v2_1.get("conclusion", ""), "Conclusion discusses CPU", errors)

    print("\n========== 4. Ask next-step follow-up ==========")
    follow2 = chat("如果CPU不高，下一步应该排查什么？还需要查Prometheus历史趋势吗？", conversation_id=conv_id, timeout=180)
    report["checks"]["followup_next_steps"] = compact(follow2)
    print(json.dumps(report["checks"]["followup_next_steps"], ensure_ascii=False, indent=2)[:9000])

    answer2 = follow2.get("answer") or ""
    v2_2 = follow2.get("v2") or {}

    require(follow2.get("planner_source") == "v2_followup_analysis", "Next-step follow-up uses v2_followup_analysis", errors)
    require(follow2.get("status") == "ok", "Next-step follow-up status ok", errors)
    require("Prometheus" in answer2, "Next-step answer mentions Prometheus", errors)
    require("历史趋势" in answer2, "Next-step answer mentions history trend", errors)
    require(bool(v2_2.get("next_steps")), "v2 next_steps exist", errors)

    print("\n========== 5. Context after follow-ups ==========")
    ctx = get_context(conv_id)
    report["checks"]["context"] = ctx
    ctx_data = ctx.get("context") or {}
    print(json.dumps({
        "exists": ctx.get("exists"),
        "current_device": ctx_data.get("current_device"),
        "current_topic": ctx_data.get("current_topic"),
        "current_intent": ctx_data.get("current_intent"),
        "last_executions_count": len(ctx_data.get("last_executions") or []),
        "has_last_bulk_analysis": bool(ctx_data.get("last_bulk_analysis")),
        "has_last_followup_analysis": bool(ctx_data.get("last_followup_analysis")),
        "recent_turns_count": len(ctx_data.get("recent_turns") or []),
        "rolling_summary": ctx_data.get("rolling_summary"),
    }, ensure_ascii=False, indent=2)[:7000])

    require(ctx.get("exists") is True, "Context exists", errors)
    require(len(ctx_data.get("last_executions") or []) >= 4, "Context records executions", errors)
    require(bool(ctx_data.get("last_bulk_analysis")), "Context has last_bulk_analysis", errors)
    require(bool(ctx_data.get("last_followup_analysis")), "Context has last_followup_analysis", errors)
    require(len(ctx_data.get("recent_turns") or []) >= 4, "Context records follow-up turns", errors)

    out = "/tmp/v2_followup_analysis_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
