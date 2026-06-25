#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch49 context inheritance regression.

Safety:
- Does NOT execute device CLI.
- Verifies that follow-up questions without device name can inherit current_device/current_topic.
"""

from __future__ import print_function

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime


BASE_URL = "http://127.0.0.1:18081"


def http_json(method, path, payload=None, timeout=180):
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


def chat(question, conversation_id=None):
    payload = {
        "question": question,
        "user": "baoleiji",
        "limit": 20,
        "planner_mode": "llm",
        "debug": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    return http_json("POST", "/api/v1/chat", payload=payload)


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
    }


def main():
    errors = []
    report = {
        "created_at": datetime.now().isoformat(),
        "checks": {},
        "errors": errors,
    }

    print("========== V2 Batch49 Context Inheritance Regression ==========")

    print("\n========== 1. Seed context with explicit device CPU question ==========")
    first = chat("WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令")
    conv_id = first.get("conversation_id")
    report["checks"]["first"] = compact(first)
    print(json.dumps(report["checks"]["first"], ensure_ascii=False, indent=2)[:7000])

    require(first.get("planner_source") == "v2_chat_router", "First turn uses v2_chat_router", errors)
    require(bool(conv_id), "First turn has conversation_id", errors)
    require((first.get("parsed") or {}).get("device_name") == "WG88-SW-H15-1", "First turn device_name ok", errors)
    require((first.get("parsed") or {}).get("mgmt_ip") == "10.189.250.79", "First turn mgmt_ip ok", errors)
    require((first.get("parsed") or {}).get("v2_intent") == "cpu_check", "First turn intent cpu_check", errors)

    print("\n========== 2. Follow-up route question without device ==========")
    route = chat("这个设备的路由表有多少条", conversation_id=conv_id)
    report["checks"]["route_followup"] = compact(route)
    print(json.dumps(report["checks"]["route_followup"], ensure_ascii=False, indent=2)[:8000])

    route_parsed = route.get("parsed") or {}
    route_items = route.get("items") or []

    require(route.get("planner_source") == "v2_chat_router", "Route follow-up uses v2_chat_router", errors)
    require(route_parsed.get("v2_intent") == "route_table", "Route follow-up intent route_table", errors)
    require(route_parsed.get("context_inherited") is True, "Route follow-up inherited context", errors)
    require(route_parsed.get("device_name") == "WG88-SW-H15-1", "Route follow-up device inherited", errors)
    require(route_parsed.get("mgmt_ip") == "10.189.250.79", "Route follow-up mgmt_ip inherited", errors)
    require(any("route" in str(x.get("command", "")).lower() for x in route_items), "Route follow-up returns route commands", errors)
    require("已继承上一轮 V2 上下文" in (route.get("answer") or ""), "Route answer mentions inherited context", errors)

    print("\n========== 3. Follow-up CPU question without device ==========")
    cpu2 = chat("继续查看当前设备CPU，还需要哪些命令？", conversation_id=conv_id)
    report["checks"]["cpu_followup"] = compact(cpu2)
    print(json.dumps(report["checks"]["cpu_followup"], ensure_ascii=False, indent=2)[:8000])

    cpu_parsed = cpu2.get("parsed") or {}
    cpu_items = cpu2.get("items") or []

    require(cpu2.get("planner_source") == "v2_chat_router", "CPU follow-up uses v2_chat_router", errors)
    require(cpu_parsed.get("v2_intent") == "cpu_check", "CPU follow-up intent cpu_check", errors)
    require(cpu_parsed.get("context_inherited") is True, "CPU follow-up inherited context", errors)
    require(cpu_parsed.get("device_name") == "WG88-SW-H15-1", "CPU follow-up device inherited", errors)
    require(cpu_parsed.get("mgmt_ip") == "10.189.250.79", "CPU follow-up mgmt_ip inherited", errors)
    require(cpu_items and cpu_items[0].get("command") == "show system resources", "CPU follow-up returns CPU commands", errors)

    print("\n========== 4. Topic-only follow-up without explicit device and without explicit topic ==========")
    next_cmd = chat("继续给我下一批排查命令", conversation_id=conv_id)
    report["checks"]["topic_only_followup"] = compact(next_cmd)
    print(json.dumps(report["checks"]["topic_only_followup"], ensure_ascii=False, indent=2)[:8000])

    next_parsed = next_cmd.get("parsed") or {}

    require(next_cmd.get("planner_source") == "v2_chat_router", "Topic-only follow-up uses v2_chat_router", errors)
    require(next_parsed.get("v2_intent") == "cpu_check", "Topic-only follow-up inherits cpu_check intent", errors)
    require(next_parsed.get("context_inherited") is True, "Topic-only follow-up inherited context", errors)
    require(next_parsed.get("device_name") == "WG88-SW-H15-1", "Topic-only follow-up device inherited", errors)

    print("\n========== 5. Context debug ==========")
    ctx = get_context(conv_id)
    report["checks"]["context"] = ctx
    print(json.dumps({
        "exists": ctx.get("exists"),
        "current_device": (ctx.get("context") or {}).get("current_device"),
        "current_topic": (ctx.get("context") or {}).get("current_topic"),
        "current_intent": (ctx.get("context") or {}).get("current_intent"),
        "recent_turns_count": len((ctx.get("context") or {}).get("recent_turns") or []),
        "rolling_summary": (ctx.get("context") or {}).get("rolling_summary"),
    }, ensure_ascii=False, indent=2)[:5000])

    ctx_data = ctx.get("context") or {}
    require(ctx.get("exists") is True, "Context exists", errors)
    require((ctx_data.get("current_device") or {}).get("device_name") == "WG88-SW-H15-1", "Context keeps current device", errors)
    require(len(ctx_data.get("recent_turns") or []) >= 4, "Context records multiple turns", errors)

    out = "/tmp/v2_context_inheritance_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
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
