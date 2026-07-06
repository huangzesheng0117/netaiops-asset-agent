# -*- coding: utf-8 -*-
"""
V3 safe takeover response readiness and response contract adapter.

This module is offline/pure logic:
- It does not call LLM, CMDB, Netmiko MCP, Prometheus MCP, or device execution.
- It does not decide whether takeover is enabled; takeover_gate does that.
- It answers one question: if the gate says this record is takeover-eligible,
  do we already have enough V3-side content to construct a safe frontend response?

Important:
V3.3-4 intentionally separates "gate eligible" from "response ready".
This prevents early takeover of records such as cmdb_query or general_chat when
V3 has only classified the intent but has not produced the actual answer/result.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


SAFE_TAKEOVER_ACTIONS = {
    "general_chat",
    "advice_analysis",
    "analyze_existing_evidence",
    "need_clarification",
    "cmdb_query",
}


@dataclass
class TakeoverResponseReadiness:
    ready: bool
    reason: str
    action: str
    handler_key: str
    response_mode: str
    has_answer_text: bool
    has_cmdb_items: bool
    gate_takeover: Optional[bool]
    gate_eligible: Optional[bool]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            data = value.as_dict()
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            data = value.model_dump()
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    return {}


def _action_from(plan: Dict[str, Any], decision: Dict[str, Any]) -> str:
    value = plan.get("action") or decision.get("action") or ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _handler_from(plan: Dict[str, Any], action: str) -> str:
    return str(plan.get("handler_key") or action or "")


def _response_mode_from(plan: Dict[str, Any]) -> str:
    return str(plan.get("response_mode") or "")


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_answer_text(plan: Any = None, decision: Any = None, context: Any = None) -> str:
    plan_dict = _as_dict(plan)
    decision_dict = _as_dict(decision)
    context_dict = _as_dict(context)

    return _first_text(
        plan_dict.get("answer"),
        plan_dict.get("final_answer"),
        plan_dict.get("message"),
        plan_dict.get("user_message"),
        plan_dict.get("clarification_question"),
        plan_dict.get("reason"),
        decision_dict.get("answer"),
        decision_dict.get("message"),
        decision_dict.get("clarification_question"),
        context_dict.get("answer"),
        context_dict.get("message"),
    )


def _extract_cmdb_items(plan: Dict[str, Any], context: Dict[str, Any]) -> list:
    for key in ("items", "cmdb_items", "cmdb_results", "results"):
        value = plan.get(key)
        if isinstance(value, list):
            return value
        value = context.get(key)
        if isinstance(value, list):
            return value
    return []


def evaluate_response_readiness(
    *,
    plan: Any = None,
    decision: Any = None,
    gate: Any = None,
    context: Any = None,
) -> TakeoverResponseReadiness:
    plan_dict = _as_dict(plan)
    decision_dict = _as_dict(decision)
    gate_dict = _as_dict(gate)
    context_dict = _as_dict(context)

    action = _action_from(plan_dict, decision_dict)
    handler_key = _handler_from(plan_dict, action)
    response_mode = _response_mode_from(plan_dict)

    gate_takeover = gate_dict.get("takeover")
    gate_eligible = gate_dict.get("eligible")

    answer_text = extract_answer_text(plan_dict, decision_dict, context_dict)
    cmdb_items = _extract_cmdb_items(plan_dict, context_dict)

    has_answer_text = bool(answer_text)
    has_cmdb_items = bool(cmdb_items)

    ready = True
    reason = "ready"

    if action not in SAFE_TAKEOVER_ACTIONS and handler_key not in SAFE_TAKEOVER_ACTIONS:
        ready = False
        reason = "action_not_safe_for_response_takeover"
    elif gate_eligible is False:
        ready = False
        reason = "gate_not_eligible"
    elif action == "cmdb_query" or handler_key == "cmdb_query":
        if not has_cmdb_items:
            ready = False
            reason = "missing_cmdb_result_items"
    elif action == "need_clarification" or handler_key == "need_clarification":
        # Need clarification can safely use a deterministic fallback even if the
        # plan does not contain a polished question.
        ready = True
        reason = "ready_with_clarification_fallback"
    elif not has_answer_text:
        ready = False
        reason = "missing_answer_text"

    return TakeoverResponseReadiness(
        ready=ready,
        reason=reason,
        action=action,
        handler_key=handler_key,
        response_mode=response_mode,
        has_answer_text=has_answer_text,
        has_cmdb_items=has_cmdb_items,
        gate_takeover=gate_takeover if isinstance(gate_takeover, bool) else None,
        gate_eligible=gate_eligible if isinstance(gate_eligible, bool) else None,
    )


def build_safe_takeover_response(
    *,
    question: str,
    conversation_id: Optional[str],
    plan: Any = None,
    decision: Any = None,
    gate: Any = None,
    context: Any = None,
) -> Dict[str, Any]:
    plan_dict = _as_dict(plan)
    decision_dict = _as_dict(decision)
    context_dict = _as_dict(context)

    readiness = evaluate_response_readiness(
        plan=plan_dict,
        decision=decision_dict,
        gate=gate,
        context=context_dict,
    )

    if not readiness.ready:
        raise ValueError(f"takeover response is not ready: {readiness.reason}")

    answer = extract_answer_text(plan_dict, decision_dict, context_dict)
    items = []

    if readiness.action == "need_clarification" or readiness.handler_key == "need_clarification":
        if not answer:
            answer = "请补充更明确的设备、对象、时间范围或你希望我执行的具体操作。"

    if readiness.action == "cmdb_query" or readiness.handler_key == "cmdb_query":
        items = _extract_cmdb_items(plan_dict, context_dict)
        if not answer:
            answer = "已根据 V3 CMDB 查询结果返回。"

    return {
        "status": "ok" if readiness.action != "need_clarification" else "need_clarification",
        "answer": answer,
        "items": items,
        "count": len(items),
        "returned": len(items),
        "columns": [],
        "field_labels": {},
        "conversation_id": conversation_id,
        "planner_source": "v3_takeover",
        "question": question,
        "v3": {
            "takeover": True,
            "response_ready": readiness.as_dict(),
            "action": readiness.action,
            "handler_key": readiness.handler_key,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        },
    }


def evaluate_shadow_record_response_readiness(payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = payload.get("v3_plan") or {}
    decision = payload.get("v3_decision") or {}
    extra = payload.get("extra") or {}
    gate = extra.get("takeover_gate_if_enabled") or extra.get("takeover_gate_runtime") or {}

    readiness = evaluate_response_readiness(
        plan=plan,
        decision=decision,
        gate=gate,
        context=payload,
    )

    result = readiness.as_dict()
    result.update(
        {
            "conversation_id": payload.get("conversation_id"),
            "v2_route": payload.get("v2_route"),
            "is_diff": payload.get("is_diff"),
            "question_prefix": str(payload.get("question") or "")[:160],
        }
    )
    return result
