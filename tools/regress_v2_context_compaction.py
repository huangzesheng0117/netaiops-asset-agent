#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 Batch51 context compaction regression.

Safety:
- Does NOT call /api/v1/chat.
- Does NOT execute device CLI.
- Uses synthetic V2 responses to validate rolling summary and context limits.
"""

from __future__ import print_function

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.chat_v2.context import (
    MAX_RECENT_TURNS,
    MAX_SUMMARY_CHARS,
    context_file_path,
    load_v2_context,
    save_v2_context_from_response,
)


def require(condition, message, errors):
    if condition:
        print("[OK]", message)
    else:
        print("[FAIL]", message)
        errors.append(message)


def cleanup(conversation_id, user):
    for path in [
        context_file_path(conversation_id=conversation_id),
        context_file_path(user=user),
    ]:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def make_router_response(turn_id):
    return {
        "status": "ok",
        "planner_source": "v2_chat_router",
        "parsed": {
            "intent": "v2_troubleshoot",
            "v2_intent": "cpu_check",
            "keyword": "WG88-SW-H15-1",
            "hostname": "WG88-SW-H15-1",
            "mgmt_ip": "10.189.250.79",
            "device_name": "WG88-SW-H15-1",
            "device_type": "cisco_nxos",
            "reason": "v2_chat_router",
        },
        "answer": "第 {} 轮：生成 CPU 排查建议命令。".format(turn_id),
        "count": 4,
        "returned": 4,
        "items": [
            {
                "device_name": "WG88-SW-H15-1",
                "mgmt_ip": "10.189.250.79",
                "device_type": "cisco_nxos",
                "command": "show system resources",
                "purpose": "查看系统 CPU/内存整体资源使用率",
                "guard_status": "passed",
                "risk_level": "readonly",
                "confirm_required": "是",
            },
            {
                "device_name": "WG88-SW-H15-1",
                "mgmt_ip": "10.189.250.79",
                "device_type": "cisco_nxos",
                "command": "show processes cpu",
                "purpose": "查看 CPU 使用情况",
                "guard_status": "passed",
                "risk_level": "readonly",
                "confirm_required": "是",
            },
            {
                "device_name": "WG88-SW-H15-1",
                "mgmt_ip": "10.189.250.79",
                "device_type": "cisco_nxos",
                "command": "show processes cpu sort",
                "purpose": "按 CPU 使用率排序查看进程",
                "guard_status": "passed",
                "risk_level": "readonly",
                "confirm_required": "是",
            },
            {
                "device_name": "WG88-SW-H15-1",
                "mgmt_ip": "10.189.250.79",
                "device_type": "cisco_nxos",
                "command": "show logging last 100",
                "purpose": "查看最近日志",
                "guard_status": "passed",
                "risk_level": "readonly",
                "confirm_required": "是",
            },
        ],
        "v2": {
            "identity": {
                "status": "ok",
                "hostname": "WG88-SW-H15-1",
                "mgmt_ip": "10.189.250.79",
                "netmiko_match": {
                    "name": "WG88-SW-H15-1",
                    "device_type": "cisco_nxos",
                },
            },
            "prometheus_evidence": {
                "status": "ok",
                "metric_type": "cpu",
                "matched": {
                    "query": "avg(cpmCPUTotal1minRev{ip=\"10.189.250.79\"})",
                    "sample_value": str(3 + (turn_id % 3)),
                    "has_data": True,
                },
                "summary": "Prometheus 当前 CPU 查询命中，value={}".format(3 + (turn_id % 3)),
            },
        },
    }


def make_execution_response():
    return {
        "status": "ok",
        "planner_source": "v2_execution_confirmation",
        "parsed": {
            "intent": "v2_execute_all_confirmation",
            "bulk": True,
            "executed_count": 4,
            "ok_count": 4,
            "failed_count": 0,
        },
        "answer": "批量执行成功，CPU 不高，内存正常。",
        "count": 4,
        "returned": 4,
        "items": [
            {
                "index": 1,
                "device_name": "WG88-SW-H15-1",
                "device_type": "cisco_nxos",
                "command": "show system resources",
                "execution_status": "executed",
                "ok": True,
                "audit_path": "/tmp/fake_audit_1.json",
                "analysis_status": "ok",
                "analysis_summary": "整体 CPU 使用率较低，当前命令输出不支持设备整体 CPU 高负载。",
                "output_preview": "X" * 5000,
            },
            {
                "index": 2,
                "device_name": "WG88-SW-H15-1",
                "device_type": "cisco_nxos",
                "command": "show processes cpu",
                "execution_status": "executed",
                "ok": True,
                "audit_path": "/tmp/fake_audit_2.json",
                "analysis_status": "unknown",
                "analysis_summary": "当前命令输出已返回，但尚未做精细字段解析。",
                "output_preview": "Y" * 5000,
            },
        ],
        "v2": {
            "counts": {
                "passed": 4,
                "executed": 4,
                "ok": 4,
                "failed": 0,
            },
            "analyses": [
                {
                    "index": 1,
                    "command": "show system resources",
                    "analysis": {
                        "analysis_type": "nxos_system_resources",
                        "status": "ok",
                        "summary": "整体 CPU 使用率较低，当前命令输出不支持设备整体 CPU 高负载。",
                        "metrics": {
                            "cpu_total": {
                                "used": 2.0,
                                "idle": 98.0,
                            },
                            "memory": {
                                "used_pct": 24.1,
                            },
                        },
                        "facts": [
                            "整体 CPU used≈2.0%。",
                            "内存 used≈24.1%。",
                        ],
                        "next_steps": [
                            "查询 Prometheus 最近 30～60 分钟 CPU 趋势。",
                            "结合日志查看异常时间点是否存在协议震荡。",
                        ],
                    },
                }
            ],
        },
    }


def make_followup_response(i):
    return {
        "status": "ok",
        "planner_source": "v2_followup_analysis",
        "parsed": {
            "intent": "v2_followup_analysis",
            "device_name": "WG88-SW-H15-1",
            "mgmt_ip": "10.189.250.79",
            "device_type": "cisco_nxos",
            "current_topic": "cpu",
            "current_intent": "cpu_check",
        },
        "answer": "第 {} 轮追问分析：CPU 当前不高，建议继续看历史趋势和日志。".format(i),
        "count": 0,
        "returned": 0,
        "items": [],
        "v2": {
            "context_used": True,
            "facts": [
                "Prometheus 当前 CPU 值偏低。",
                "CLI 当前 CPU 使用率偏低。",
            ],
            "conclusion": "第 {} 轮结论：当前证据不支持设备整体 CPU 高负载。".format(i),
            "next_steps": [
                "继续查询 Prometheus 历史趋势。",
                "继续结合日志判断是否有协议震荡。",
            ],
        },
    }


def main():
    errors = []
    conversation_id = "batch51-" + str(uuid.uuid4())
    user = "batch51-regress"

    cleanup(conversation_id, user)

    print("========== V2 Batch51 Context Compaction Regression ==========")
    print("conversation_id:", conversation_id)

    save_v2_context_from_response(
        conversation_id=conversation_id,
        user=user,
        question="WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令",
        response=make_router_response(1),
    )

    save_v2_context_from_response(
        conversation_id=conversation_id,
        user=user,
        question="确认执行全部命令 YES",
        response=make_execution_response(),
    )

    for i in range(1, 41):
        save_v2_context_from_response(
            conversation_id=conversation_id,
            user=user,
            question="第 {} 轮：结合以上结果继续分析，下一步查什么？".format(i),
            response=make_followup_response(i),
        )

    context = load_v2_context(conversation_id=conversation_id, user=user) or {}

    preview = {
        "context_id": context.get("context_id"),
        "conversation_id": context.get("conversation_id"),
        "current_device": context.get("current_device"),
        "current_topic": context.get("current_topic"),
        "current_intent": context.get("current_intent"),
        "active_focus": context.get("active_focus"),
        "recent_turns_count": len(context.get("recent_turns") or []),
        "last_command_suggestions_count": len(context.get("last_command_suggestions") or []),
        "last_executions_count": len(context.get("last_executions") or []),
        "open_questions_count": len(context.get("open_questions") or []),
        "resolved_findings_count": len(context.get("resolved_findings") or []),
        "rolling_summary_chars": len(context.get("rolling_summary") or ""),
        "context_stats": context.get("context_stats"),
        "rolling_summary": context.get("rolling_summary"),
    }

    print(json.dumps(preview, ensure_ascii=False, indent=2)[:10000])

    require((context.get("current_device") or {}).get("device_name") == "WG88-SW-H15-1", "current_device preserved", errors)
    require(context.get("current_topic") == "cpu", "current_topic preserved", errors)
    require(context.get("current_intent") == "cpu_check", "current_intent preserved", errors)
    require(bool(context.get("active_focus")), "active_focus exists", errors)
    require(len(context.get("recent_turns") or []) == MAX_RECENT_TURNS, "recent_turns is compacted to MAX_RECENT_TURNS", errors)
    require(len(context.get("rolling_summary") or "") <= MAX_SUMMARY_CHARS, "rolling_summary within limit", errors)
    require(len(context.get("last_command_suggestions") or []) >= 4, "last_command_suggestions preserved", errors)
    require(len(context.get("last_executions") or []) >= 2, "last_executions preserved", errors)
    require(bool(context.get("open_questions")), "open_questions generated", errors)
    require(bool(context.get("resolved_findings")), "resolved_findings generated", errors)
    require(bool(context.get("context_stats")), "context_stats exists", errors)
    require((context.get("context_stats") or {}).get("recent_turns_count") == MAX_RECENT_TURNS, "context_stats recent_turns_count correct", errors)
    require("当前设备" in (context.get("rolling_summary") or ""), "rolling_summary includes current device", errors)
    require("待继续确认" in (context.get("rolling_summary") or ""), "rolling_summary includes open questions", errors)
    require("已确认发现" in (context.get("rolling_summary") or ""), "rolling_summary includes resolved findings", errors)

    out = "/tmp/v2_context_compaction_regress_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "created_at": datetime.now().isoformat(),
            "conversation_id": conversation_id,
            "user": user,
            "preview": preview,
            "errors": errors,
        }, f, ensure_ascii=False, indent=2)

    print("\n========== Result ==========")
    print("context_file:", context_file_path(conversation_id=conversation_id))
    print("report:", out)

    if errors:
        print("status: FAILED")
        return 1

    print("status: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
