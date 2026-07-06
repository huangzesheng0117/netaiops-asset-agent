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

temp_root = Path(tempfile.mkdtemp(prefix="v3442_fix1_direct_"))
audit_dir = temp_root / "audit"
context_dir = temp_root / "context"
audit_dir.mkdir(parents=True, exist_ok=True)
context_dir.mkdir(parents=True, exist_ok=True)

os.environ["NETAIOPS_V3_TAKEOVER_ENABLED"] = "1"
os.environ["NETAIOPS_V3_TAKEOVER_ALLOWED_USERS"] = "v3_3_16_takeover"
os.environ["NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX"] = (
    "v3-3-16-takeover-"
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
        "netaiops_asset_agent_v3442_fix1_direct",
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
    build_followup_context,
    delete_followup_context,
)


def fake_call_llm(messages, llm_client=None, timeout=60):
    content = json.dumps(messages, ensure_ascii=False)
    if "V2第一轮返回内容" not in content:
        return {
            "status": "error",
            "error_code": "FIX1_CONTEXT_NOT_IN_PROMPT",
            "message": content[:1000],
        }
    return {
        "status": "ok",
        "content": (
            "基于上一轮已有内容继续分析：应先确认已有证据边界，"
            "本轮没有重新查询或执行命令。"
        ),
    }


response_generator._call_llm = fake_call_llm
app = import_app(APP_FILE)
wrapper = getattr(app, "_v3_apply_chat_canary_takeover")

user = "v3_3_16_takeover"
conversation_id = "v3-3-16-takeover-v3442-fix1-direct"
delete_followup_context(conversation_id)

non_taken_plan = {
    "action": "query_cmdb",
    "handler_key": "query_cmdb",
    "response_mode": "data",
    "accepted": True,
    "requires_confirmation": False,
    "safety_allowed": True,
    "confidence": 0.99,
    "effective_confidence": 0.99,
}
non_taken_response = {
    "status": "ok",
    "answer": "V2第一轮返回内容：未查询到匹配设备。",
    "message": "V2第一轮返回内容：未查询到匹配设备。",
    "planner_source": "v2_cmdb_query",
    "conversation_id": "legacy-effective-first",
}
non_taken_context = {
    "question": "查询一个不存在的CMDB设备。",
    "user": user,
    "conversation_id": conversation_id,
    "v3_shadow_state": {
        "enabled": True,
        "plan": non_taken_plan,
        "decision": dict(non_taken_plan),
        "error": "",
    },
}

first_result = wrapper(
    response=non_taken_response,
    local_context=non_taken_context,
    route_label="middleware_jsonresponse_line_707",
)
context_after_first = build_followup_context(
    conversation_id=conversation_id,
    user=user,
)
repeat_result = wrapper(
    response=non_taken_response,
    local_context=non_taken_context,
    route_label="middleware_jsonresponse_line_707",
)
context_after_repeat = build_followup_context(
    conversation_id=conversation_id,
    user=user,
)

followup_plan = {
    "action": "analyze_existing_evidence",
    "handler_key": "analyze_existing_evidence",
    "response_mode": "analysis",
    "accepted": True,
    "requires_confirmation": False,
    "safety_allowed": True,
    "confidence": 0.97,
    "effective_confidence": 0.97,
}
followup_response = {
    "status": "need_clarification",
    "answer": "V2 fallback should be replaced.",
    "message": "V2 fallback should be replaced.",
    "planner_source": "v2_followup_analysis",
    "conversation_id": "legacy-effective-followup",
}
followup_context = {
    "question": "只基于上一轮结果继续分析，不重新查询。",
    "user": user,
    "conversation_id": conversation_id,
    "v3_shadow_state": {
        "enabled": True,
        "plan": followup_plan,
        "decision": dict(followup_plan),
        "error": "",
    },
}
taken_result = wrapper(
    response=followup_response,
    local_context=followup_context,
    route_label="middleware_jsonresponse_line_778",
)
context_after_taken = build_followup_context(
    conversation_id=conversation_id,
    user=user,
)
taken_repeat_result = wrapper(
    response=followup_response,
    local_context=followup_context,
    route_label="middleware_jsonresponse_line_778",
)
context_after_taken_repeat = build_followup_context(
    conversation_id=conversation_id,
    user=user,
)

error_id = "v3-3-16-takeover-v3442-fix1-error"
delete_followup_context(error_id)
error_result = wrapper(
    response={
        "status": "error",
        "answer": "This error response must not be persisted.",
        "planner_source": "v2_error",
    },
    local_context={
        "question": "触发错误响应。",
        "user": user,
        "conversation_id": error_id,
        "v3_shadow_state": {
            "enabled": True,
            "plan": non_taken_plan,
            "decision": dict(non_taken_plan),
            "error": "",
        },
    },
    route_label="chat_return_line_1875",
)
error_context = build_followup_context(
    conversation_id=error_id,
    user=user,
)

audit_rows = load_audit_rows()
report = {
    "conversation_id": conversation_id,
    "first_result": first_result,
    "context_after_first": context_after_first,
    "repeat_result": repeat_result,
    "context_after_repeat": context_after_repeat,
    "taken_result": taken_result,
    "context_after_taken": context_after_taken,
    "taken_repeat_result": taken_repeat_result,
    "context_after_taken_repeat": context_after_taken_repeat,
    "error_result": error_result,
    "error_context": error_context,
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
if first_result.get("v3_takeover") is True:
    failures.append("first_non_taken_unexpected_takeover")
if first_result.get("v3_followup_context_recorded") is not True:
    failures.append("first_non_taken_not_recorded")
if first_result.get("v3_followup_context_record_source") != (
    "v2_or_non_taken_return"
):
    failures.append("first_non_taken_wrong_record_source")
if context_after_first.get("available") is not True:
    failures.append("first_context_unavailable")
if int(context_after_first.get("turn_count") or 0) != 1:
    failures.append("first_context_turn_count_not_one")

if repeat_result.get("v3_followup_context_store_deduplicated") is not True:
    failures.append("repeated_non_taken_not_deduplicated")
if int(context_after_repeat.get("turn_count") or 0) != 1:
    failures.append("repeated_non_taken_created_duplicate")

if taken_result.get("v3_takeover") is not True:
    failures.append("followup_not_taken")
if taken_result.get("v3_takeover_action") != "analyze_existing_evidence":
    failures.append("followup_wrong_action")
if taken_result.get("v3_takeover_source") != "llm":
    failures.append("followup_wrong_source")
if taken_result.get("v3_followup_context_record_source") != "v3_taken_return":
    failures.append("followup_wrong_record_source")
if int(context_after_taken.get("turn_count") or 0) != 2:
    failures.append("taken_context_turn_count_not_two")

if taken_repeat_result.get(
    "v3_followup_context_store_deduplicated"
) is not True:
    failures.append("repeated_taken_not_deduplicated")
if int(context_after_taken_repeat.get("turn_count") or 0) != 2:
    failures.append("repeated_taken_created_duplicate")

if error_result.get("v3_followup_context_recorded") is True:
    failures.append("error_response_was_recorded")
if error_context.get("available") is True:
    failures.append("error_response_created_context")

non_taken_audit = [
    row
    for row in audit_rows
    if row.get("original_conversation_id") == conversation_id
    and row.get("version") == "v3.4.4-2-fix1"
    and row.get("taken") is False
    and row.get("context_recorded") is True
    and row.get("context_record_source") == "v2_or_non_taken_return"
]
if not non_taken_audit:
    failures.append("non_taken_audit_missing")

taken_audit = [
    row
    for row in audit_rows
    if row.get("original_conversation_id") == conversation_id
    and row.get("version") == "v3.4.4-2-fix1"
    and row.get("taken") is True
    and row.get("context_recorded") is True
    and row.get("context_record_source") == "v3_taken_return"
]
if not taken_audit:
    failures.append("taken_audit_missing")

error_audit = [
    row
    for row in audit_rows
    if row.get("original_conversation_id") == error_id
    and row.get("version") == "v3.4.4-2-fix1"
    and row.get("context_recorded") is False
    and row.get("context_record_skip_reason") == "error_status"
]
if not error_audit:
    failures.append("error_skip_audit_missing")

delete_followup_context(conversation_id)
delete_followup_context(error_id)

if failures:
    raise SystemExit(
        "ERROR: V3.4-4-2 fix1 direct regression failed: "
        + json.dumps(failures, ensure_ascii=False)
    )

print("v3_4_4_2_fix1_non_taken_return_recorded=OK")
print("v3_4_4_2_fix1_non_taken_deduplicated=OK")
print("v3_4_4_2_fix1_taken_return_recorded_once=OK")
print("v3_4_4_2_fix1_error_response_skipped=OK")
print("v3_4_4_2_fix1_audit_fields=OK")
print("result=OK")
