# -*- coding: utf-8 -*-
"""
V3 response generator adapter.

This module prepares V3 for real takeover by separating three concepts:
1. gate eligibility
2. response readiness
3. actual frontend response content generation

Default behavior is conservative:
- No live LLM call unless allow_live_llm=True.
- No device command, MCP, Netmiko, or Prometheus access.
- No CMDB call; cmdb_query is ready only if caller already provides items.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Dict, List, Optional

from netaiops_asset.chat_v3.takeover_gate import evaluate_takeover
from netaiops_asset.chat_v3.takeover_response import (
    build_safe_takeover_response,
    evaluate_response_readiness,
)


SAFE_RESPONSE_ACTIONS = {
    "general_chat",
    "advice_analysis",
    "analyze_existing_evidence",
    "need_clarification",
    "cmdb_query",
}

BLOCKED_ACTIONS = {
    "generate_commands",
    "execute_provided_commands",
    "execute_provided_commands_and_analyze",
    "confirm_execute_pending",
    "blocked_unsafe_commands",
}


@dataclass
class V3GeneratedResponse:
    generated: bool
    ready: bool
    reason: str
    action: str
    handler_key: str
    response_mode: str
    answer: str
    status: str
    items: List[Dict[str, Any]]
    count: int
    returned: int
    columns: List[str]
    field_labels: Dict[str, str]
    source: str
    llm_status: str
    llm_error: str
    gate: Dict[str, Any]
    readiness: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        try:
            data = asdict(value)
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
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
    if hasattr(value, "__dict__"):
        try:
            data = vars(value)
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    try:
        data = dict(value)
        if isinstance(data, dict):
            return data
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


def _mode_from(plan: Dict[str, Any], action: str) -> str:
    value = str(plan.get("response_mode") or "")
    if value:
        return value
    if action == "advice_analysis":
        return "advice"
    if action == "need_clarification":
        return "clarification"
    if action == "cmdb_query":
        return "cmdb"
    return "chat"


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_frontend_answer(plan: Any = None, decision: Any = None, context: Any = None) -> str:
    plan_dict = _as_dict(plan)
    decision_dict = _as_dict(decision)
    context_dict = _as_dict(context)

    # Deliberately exclude v3_plan.reason. It is internal intent reasoning,
    # not a frontend answer.
    return _first_text(
        plan_dict.get("answer"),
        plan_dict.get("final_answer"),
        plan_dict.get("message"),
        plan_dict.get("user_message"),
        plan_dict.get("clarification_question"),
        decision_dict.get("answer"),
        decision_dict.get("message"),
        decision_dict.get("clarification_question"),
        context_dict.get("answer"),
        context_dict.get("message"),
    )


def _extract_items(plan: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("items", "cmdb_items", "cmdb_results", "results"):
        value = plan.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        value = context.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_columns(items: List[Dict[str, Any]], plan: Dict[str, Any], context: Dict[str, Any]) -> List[str]:
    for key in ("columns", "fields"):
        value = plan.get(key)
        if isinstance(value, list):
            return [str(x) for x in value]
        value = context.get(key)
        if isinstance(value, list):
            return [str(x) for x in value]
    columns: List[str] = []
    for item in items:
        for key in item.keys():
            if key not in columns:
                columns.append(str(key))
    return columns


def _extract_field_labels(plan: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, str]:
    for key in ("field_labels", "labels"):
        value = plan.get(key)
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        value = context.get(key)
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
    return {}


def build_response_messages(
    action: str,
    question: str,
    plan: Dict[str, Any],
    decision: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    intent_reason = str(
        plan.get("reason") or decision.get("reason") or ""
    ).strip()
    context_dict = _as_dict(context)
    followup_context = _as_dict(context_dict.get("followup_context"))

    base_system = (
        "你是 NetAIOps 网络运维助手。请输出可直接展示给用户的中文回答。"
        "不要输出 JSON；不要编造查询结果；不要生成设备配置或危险命令；"
        "如果用户要求纯文本解释或建议，就直接给出解释或建议。"
    )

    if action == "analyze_existing_evidence":
        system = (
            base_system
            + " 这是基于既有会话上下文继续分析的 follow-up 请求。"
            + "只能使用提供的上下文，不得声称执行了新命令、查询了新设备或获得了新证据。"
            + "请明确区分已有事实、推断和仍缺失的信息；"
            + "如果上下文不足，必须明确说明不足，不能补造上一轮内容。"
        )
    elif action == "advice_analysis":
        system = (
            base_system
            + " 这是运维建议类问题。请给出明确结论、理由和风险提醒；"
            + "如果信息不足，要说明哪些信息不足。"
        )
    else:
        system = (
            base_system
            + " 这是通用解释类问题。请用简洁但完整的方式回答。"
        )

    user_parts = [f"用户问题：{question.strip()}"]
    if intent_reason:
        user_parts.append(f"V3 意图判断备注：{intent_reason}")

    if action == "analyze_existing_evidence":
        context_payload = json.dumps(
            followup_context,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )[:16000]
        user_parts.append(
            "可使用的既有会话上下文："
            + (context_payload or "无")
        )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]



def _parse_llm_answer(result: Dict[str, Any]) -> str:
    content = str(result.get("content") or "").strip()
    if not content:
        return ""

    # Some local models may still return JSON text. Accept {"answer": "..."}.
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            value = data.get("answer") or data.get("final_answer") or data.get("message")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception:
        pass

    return content


def _call_llm(messages: List[Dict[str, str]], llm_client: Any = None, timeout: int = 60) -> Dict[str, Any]:
    client = llm_client
    if client is None:
        from netaiops_asset.llm.client import LLMClient

        client = LLMClient()

    if not hasattr(client, "chat"):
        return {
            "status": "error",
            "error_code": "INVALID_LLM_CLIENT",
            "message": "llm_client has no chat method",
        }

    try:
        configured_max_tokens = int(getattr(client, "max_tokens", 0) or 0)
        response_max_tokens = max(1200, configured_max_tokens)
        return client.chat(
            messages,
            max_tokens=response_max_tokens,
            temperature=0,
            top_p=None,
            response_format=False,
            thinking={"type": "disabled"},
            timeout=timeout,
        )
    except TypeError:
        # Test fakes may accept only messages.
        return client.chat(messages)
    except Exception as exc:
        return {
            "status": "error",
            "error_code": "LLM_CALL_EXCEPTION",
            "message": f"{type(exc).__name__}: {exc}",
        }


def _result(
    *,
    generated: bool,
    ready: bool,
    reason: str,
    action: str,
    handler_key: str,
    response_mode: str,
    answer: str = "",
    status: str = "ok",
    items: Optional[List[Dict[str, Any]]] = None,
    columns: Optional[List[str]] = None,
    field_labels: Optional[Dict[str, str]] = None,
    source: str = "",
    llm_status: str = "",
    llm_error: str = "",
    gate: Optional[Dict[str, Any]] = None,
    readiness: Optional[Dict[str, Any]] = None,
) -> V3GeneratedResponse:
    items = items or []
    columns = columns or []
    field_labels = field_labels or {}
    return V3GeneratedResponse(
        generated=generated,
        ready=ready,
        reason=reason,
        action=action,
        handler_key=handler_key,
        response_mode=response_mode,
        answer=answer,
        status=status,
        items=items,
        count=len(items),
        returned=len(items),
        columns=columns,
        field_labels=field_labels,
        source=source,
        llm_status=llm_status,
        llm_error=llm_error,
        gate=gate or {},
        readiness=readiness or {},
    )


def generate_v3_response(
    *,
    question: str,
    conversation_id: Optional[str] = None,
    plan: Any = None,
    decision: Any = None,
    context: Any = None,
    gate: Any = None,
    allow_live_llm: bool = False,
    llm_client: Any = None,
    llm_timeout: int = 60,
) -> V3GeneratedResponse:
    plan_dict = _as_dict(plan)
    decision_dict = _as_dict(decision)
    context_dict = _as_dict(context)

    action = _action_from(plan_dict, decision_dict)
    handler_key = _handler_from(plan_dict, action)
    response_mode = _mode_from(plan_dict, action)

    gate_dict = _as_dict(gate)
    if not gate_dict:
        gate_dict = evaluate_takeover(plan=plan_dict, decision=decision_dict, enabled=True).as_dict()

    if action in BLOCKED_ACTIONS or handler_key in BLOCKED_ACTIONS:
        return _result(
            generated=False,
            ready=False,
            reason="blocked_action",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            gate=gate_dict,
        )

    if action not in SAFE_RESPONSE_ACTIONS and handler_key not in SAFE_RESPONSE_ACTIONS:
        return _result(
            generated=False,
            ready=False,
            reason="unsupported_action",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            gate=gate_dict,
        )

    if gate_dict.get("eligible") is False:
        return _result(
            generated=False,
            ready=False,
            reason=f"gate_not_eligible:{gate_dict.get('reason')}",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            gate=gate_dict,
        )

    if action == "need_clarification" or handler_key == "need_clarification":
        answer = extract_frontend_answer(plan_dict, decision_dict, context_dict)
        if not answer:
            answer = "请补充更明确的设备、对象、时间范围或你希望我执行的具体操作。"
        readiness = evaluate_response_readiness(plan={**plan_dict, "answer": answer}, decision=decision_dict, gate=gate_dict).as_dict()
        return _result(
            generated=True,
            ready=True,
            reason="clarification_fallback_generated",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            answer=answer,
            status="need_clarification",
            source="deterministic_clarification",
            gate=gate_dict,
            readiness=readiness,
        )

    if action == "cmdb_query" or handler_key == "cmdb_query":
        items = _extract_items(plan_dict, context_dict)
        columns = _extract_columns(items, plan_dict, context_dict)
        field_labels = _extract_field_labels(plan_dict, context_dict)
        if not items:
            return _result(
                generated=False,
                ready=False,
                reason="missing_cmdb_items",
                action=action,
                handler_key=handler_key,
                response_mode=response_mode,
                gate=gate_dict,
            )
        answer = extract_frontend_answer(plan_dict, decision_dict, context_dict) or f"查询到 {len(items)} 条 CMDB 记录。"
        enriched_plan = {**plan_dict, "answer": answer, "items": items}
        readiness = evaluate_response_readiness(plan=enriched_plan, decision=decision_dict, gate=gate_dict).as_dict()
        return _result(
            generated=True,
            ready=True,
            reason="cmdb_items_response_generated",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            answer=answer,
            status="ok",
            items=items,
            columns=columns,
            field_labels=field_labels,
            source="provided_cmdb_items",
            gate=gate_dict,
            readiness=readiness,
        )

    if action == "analyze_existing_evidence" or handler_key == "analyze_existing_evidence":
        followup_context = _as_dict(context_dict.get("followup_context"))
        if not bool(followup_context.get("followup_context_available")):
            return _result(
                generated=False,
                ready=False,
                reason="missing_followup_context",
                action=action,
                handler_key=handler_key,
                response_mode=response_mode,
                gate=gate_dict,
            )

    existing_answer = extract_frontend_answer(plan_dict, decision_dict, context_dict)
    if existing_answer:
        enriched_plan = {**plan_dict, "answer": existing_answer}
        readiness = evaluate_response_readiness(plan=enriched_plan, decision=decision_dict, gate=gate_dict).as_dict()
        return _result(
            generated=True,
            ready=True,
            reason="existing_frontend_answer_reused",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            answer=existing_answer,
            status="ok",
            source="existing_frontend_answer",
            gate=gate_dict,
            readiness=readiness,
        )

    if not allow_live_llm:
        return _result(
            generated=False,
            ready=False,
            reason="live_llm_disabled_and_no_existing_answer",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            gate=gate_dict,
        )

    messages = build_response_messages(
        action,
        question,
        plan_dict,
        decision_dict,
        context=context_dict,
    )
    llm_result = _call_llm(messages, llm_client=llm_client, timeout=llm_timeout)
    llm_status = str(llm_result.get("status") or "")
    answer = _parse_llm_answer(llm_result)

    if llm_status != "ok":
        return _result(
            generated=False,
            ready=False,
            reason="llm_generation_failed",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            gate=gate_dict,
            llm_status=llm_status,
            llm_error=str(llm_result.get("error_code") or llm_result.get("message") or "")[:500],
        )

    if len(answer.strip()) < 20:
        return _result(
            generated=False,
            ready=False,
            reason="llm_answer_too_short",
            action=action,
            handler_key=handler_key,
            response_mode=response_mode,
            answer=answer,
            gate=gate_dict,
            llm_status=llm_status,
        )

    enriched_plan = {**plan_dict, "answer": answer}
    readiness = evaluate_response_readiness(plan=enriched_plan, decision=decision_dict, gate=gate_dict).as_dict()

    return _result(
        generated=True,
        ready=True,
        reason="llm_answer_generated",
        action=action,
        handler_key=handler_key,
        response_mode=response_mode,
        answer=answer,
        status="ok",
        source="llm",
        llm_status=llm_status,
        gate=gate_dict,
        readiness=readiness,
    )


def build_frontend_response(
    *,
    question: str,
    conversation_id: Optional[str],
    generated: Any,
) -> Dict[str, Any]:
    generated_dict = _as_dict(generated)
    if not generated_dict.get("ready"):
        raise ValueError(f"generated response is not ready: {generated_dict.get('reason')}")

    plan = {
        "action": generated_dict.get("action"),
        "handler_key": generated_dict.get("handler_key"),
        "response_mode": generated_dict.get("response_mode"),
        "answer": generated_dict.get("answer"),
        "items": generated_dict.get("items") or [],
        "columns": generated_dict.get("columns") or [],
        "field_labels": generated_dict.get("field_labels") or {},
    }

    response = build_safe_takeover_response(
        question=question,
        conversation_id=conversation_id,
        plan=plan,
        gate=generated_dict.get("gate") or {"eligible": True, "takeover": True},
    )
    response["planner_source"] = "v3_response_generator"
    response["v3"]["response_generator"] = {
        "reason": generated_dict.get("reason"),
        "source": generated_dict.get("source"),
        "llm_status": generated_dict.get("llm_status"),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
    }
    return response


def generate_from_shadow_record(
    payload: Dict[str, Any],
    *,
    allow_live_llm: bool = False,
    llm_client: Any = None,
) -> Dict[str, Any]:
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    gate = extra.get("takeover_gate_if_enabled") if isinstance(extra.get("takeover_gate_if_enabled"), dict) else {}
    generated = generate_v3_response(
        question=str(payload.get("question") or ""),
        conversation_id=payload.get("conversation_id"),
        plan=payload.get("v3_plan") or {},
        decision=payload.get("v3_decision") or {},
        context=payload,
        gate=gate,
        allow_live_llm=allow_live_llm,
        llm_client=llm_client,
    )
    return generated.as_dict()
