#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

APP_FILE = Path(sys.argv[1]).resolve()
REPORT_OUT = Path(sys.argv[2]).resolve()
APP_DIR = APP_FILE.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

temp_root = Path(tempfile.mkdtemp(prefix="v3442_direct_"))
audit_dir = temp_root / "audit"
context_dir = temp_root / "context"
audit_dir.mkdir(parents=True, exist_ok=True)
context_dir.mkdir(parents=True, exist_ok=True)

os.environ["NETAIOPS_V3_TAKEOVER_ENABLED"] = "1"
os.environ["NETAIOPS_V3_TAKEOVER_ALLOWED_USERS"] = (
    "v3_3_16_takeover,v3_3_17_takeover"
)
os.environ["NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX"] = (
    "v3-3-16-takeover-,v3-3-17-takeover-"
)
os.environ["NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS"] = (
    "general_chat,advice_analysis,analyze_existing_evidence"
)
os.environ["NETAIOPS_V3_TAKEOVER_ALLOWED_SOURCES"] = "llm"
os.environ["NETAIOPS_V3_RESPONSE_GENERATOR_LIVE_LLM"] = "1"
os.environ["NETAIOPS_V3_TAKEOVER_AUDIT_DIR"] = str(audit_dir)
os.environ["NETAIOPS_V3_FOLLOWUP_CONTEXT_DIR"] = str(context_dir)


def import_app(path: Path):
    spec = importlib.util.spec_from_file_location(
        "netaiops_asset_agent_v3442_direct",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_audit_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("*.jsonl")):
        for line_number, line in enumerate(
            path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if isinstance(record, dict):
                record["_file"] = str(path)
                record["_line"] = line_number
                rows.append(record)
    return rows


from netaiops_asset.chat_v3 import response_generator
from netaiops_asset.chat_v3.followup_context import (
    delete_followup_context,
    record_v3_turn,
)


def fake_call_llm(messages, llm_client=None, timeout=60):
    content = json.dumps(messages, ensure_ascii=False)
    if "上一轮维护风险" not in content:
        return {
            "status": "error",
            "error_code": "DIRECT_CONTEXT_NOT_IN_PROMPT",
            "message": content[:1000],
        }
    return {
        "status": "ok",
        "content": (
            "基于上一轮已有内容，最主要的风险是维护前没有确认主备状态和"
            "实际流量路径。本轮没有执行任何新命令，也没有获取新证据。"
        ),
    }


response_generator._call_llm = fake_call_llm
app = import_app(APP_FILE)
wrapper = getattr(app, "_v3_apply_chat_canary_takeover")

user = "v3_3_16_takeover"
route_labels = [
    "middleware_jsonresponse_line_707",
    "middleware_jsonresponse_line_778",
    "chat_return_line_1875",
]
cases: list[dict[str, Any]] = []
conversation_ids: list[str] = []

for index, route_label in enumerate(route_labels, start=1):
    conversation_id = (
        f"v3-3-16-takeover-v3442-direct-{index}"
    )
    conversation_ids.append(conversation_id)
    delete_followup_context(conversation_id)
    record_v3_turn(
        conversation_id=conversation_id,
        user=user,
        question="上一轮维护风险是什么？",
        response={
            "status": "ok",
            "answer": "上一轮维护风险：未确认主备状态和流量路径。",
            "planner_source": "v3_response_generator",
        },
        action="advice_analysis",
        route_label="chat_return_line_1875",
    )

    plan = {
        "action": "analyze_existing_evidence",
        "handler_key": "analyze_existing_evidence",
        "response_mode": "analysis",
        "accepted": True,
        "requires_confirmation": False,
        "safety_allowed": True,
        "confidence": 0.96,
        "effective_confidence": 0.96,
        "reason": "LLM Intent Arbiter selected follow-up analysis",
    }
    decision = dict(plan)

    response = {
        "status": "need_clarification",
        "answer": "V2 fallback should be replaced by V3.",
        "message": "V2 fallback should be replaced by V3.",
        "planner_source": "v2_followup_analysis",
        "conversation_id": f"legacy-effective-{index}",
        "items": [],
        "columns": [],
        "field_labels": {},
        "count": 0,
        "returned": 0,
    }
    local_context = {
        "question": "只基于上一轮已有内容继续分析最主要的风险。",
        "user": user,
        "conversation_id": conversation_id,
        "payload": {
            "question": "只基于上一轮已有内容继续分析最主要的风险。",
            "user": user,
            "conversation_id": conversation_id,
        },
        "v3_shadow_state": {
            "enabled": True,
            "plan": plan,
            "decision": decision,
            "error": "",
        },
    }

    result = wrapper(
        response=response,
        local_context=local_context,
        route_label=route_label,
    )
    case = {
        "route_label": route_label,
        "conversation_id": conversation_id,
        "result": result,
    }
    cases.append(case)

missing_id = "v3-3-16-takeover-v3442-direct-missing"
delete_followup_context(missing_id)
missing_plan = {
    "action": "analyze_existing_evidence",
    "handler_key": "analyze_existing_evidence",
    "response_mode": "analysis",
    "accepted": True,
    "requires_confirmation": False,
    "safety_allowed": True,
    "confidence": 0.96,
    "effective_confidence": 0.96,
}
missing_response = {
    "status": "need_clarification",
    "answer": "V2 fallback for missing context.",
    "planner_source": "v2_followup_analysis",
    "conversation_id": "legacy-missing",
}
missing_result = wrapper(
    response=missing_response,
    local_context={
        "question": "继续分析上一轮结果。",
        "user": user,
        "conversation_id": missing_id,
        "v3_shadow_state": {
            "enabled": True,
            "plan": missing_plan,
            "decision": dict(missing_plan),
            "error": "",
        },
    },
    route_label="middleware_jsonresponse_line_707",
)

audit_rows = load_audit_rows()
report = {
    "cases": cases,
    "missing_context_case": {
        "conversation_id": missing_id,
        "result": missing_result,
    },
    "audit_rows": audit_rows,
}
REPORT_OUT.write_text(
    json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    ),
    encoding="utf-8",
)
print(
    json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    )
)

failures = []
for case in cases:
    result = case["result"]
    if not isinstance(result, dict):
        failures.append(case)
        continue
    if result.get("v3_takeover") is not True:
        failures.append(case)
    elif result.get("v3_takeover_action") != (
        "analyze_existing_evidence"
    ):
        failures.append(case)
    elif result.get("v3_takeover_source") != "llm":
        failures.append(case)
    elif result.get("v3_followup_context_available") is not True:
        failures.append(case)
    elif int(result.get("v3_followup_context_turn_count") or 0) < 1:
        failures.append(case)
    elif result.get("v3_original_conversation_id") != case[
        "conversation_id"
    ]:
        failures.append(case)
    elif result.get("v3_takeover_reason") != (
        "v3_4_4_2_followup_context_arbiter_convergence"
    ):
        failures.append(case)

if missing_result.get("v3_takeover") is True:
    failures.append(
        {
            "case": "missing_context_should_fallback",
            "result": missing_result,
        }
    )

for conversation_id in conversation_ids:
    matching = [
        row
        for row in audit_rows
        if row.get("original_conversation_id") == conversation_id
        and row.get("version") == "v3.4.4-2-fix1"
        and row.get("action") == "analyze_existing_evidence"
        and row.get("taken") is True
        and row.get("reason") == "taken"
        and row.get("generator_source") == "llm"
        and row.get("followup_context_available") is True
        and int(row.get("followup_context_turn_count") or 0) >= 1
    ]
    if not matching:
        failures.append(
            {
                "case": "missing_taken_audit",
                "conversation_id": conversation_id,
            }
        )

missing_audit = [
    row
    for row in audit_rows
    if row.get("original_conversation_id") == missing_id
    and row.get("version") == "v3.4.4-2-fix1"
    and row.get("reason") == "followup_context_unavailable"
    and row.get("taken") is False
]
if not missing_audit:
    failures.append(
        {
            "case": "missing_context_audit_not_found",
            "conversation_id": missing_id,
        }
    )

for conversation_id in conversation_ids + [missing_id]:
    delete_followup_context(conversation_id)

if failures:
    raise SystemExit(
        "ERROR: V3.4-4-2 direct wrapper regression failed: "
        + json.dumps(failures, ensure_ascii=False, default=str)
    )

print("v3_4_4_2_direct_route_707=OK")
print("v3_4_4_2_direct_route_778=OK")
print("v3_4_4_2_direct_route_1875=OK")
print("v3_4_4_2_direct_context_available=OK")
print("v3_4_4_2_direct_missing_context_fallback=OK")
print("v3_4_4_2_direct_audit=OK")
print("result=OK")
