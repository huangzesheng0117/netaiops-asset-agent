# -*- coding: utf-8 -*-
"""
V3 LLM Intent Arbiter.

Role:
- The LLM decides user's business intent and returns strict JSON.
- This module validates and normalizes that JSON into IntentDecision.
- This module does not execute commands, query CMDB, call Netmiko MCP, or modify conversation history.

Important boundary:
- LLM confidence is the original value produced by the local LLM.
- effective_confidence is derived by chatbot backend after schema and consistency checks.
- Safety, command splitting and actual execution are handled by later V3 modules.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from netaiops_asset.chat_v3.intent_schema import (
    CONFIDENCE_ACCEPT_THRESHOLD,
    CONFIDENCE_CLARIFY_THRESHOLD,
    INTENT_SCHEMA_VERSION,
    IntentAction,
    IntentDecision,
    build_need_clarification,
)

try:
    from netaiops_asset.llm.client import LLMClient
except Exception:  # pragma: no cover
    LLMClient = None  # type: ignore


ARBITER_VERSION = "v3_intent_arbiter_engine_1"


ACTION_ALIASES = {
    "generate": "generate_commands",
    "suggest_commands": "generate_commands",
    "command_generation": "generate_commands",
    "execute": "execute_provided_commands",
    "run_commands": "execute_provided_commands",
    "execute_commands": "execute_provided_commands",
    "execute_and_analyze": "execute_provided_commands_and_analyze",
    "execute_then_analyze": "execute_provided_commands_and_analyze",
    "execute_provided_and_analyze": "execute_provided_commands_and_analyze",
    "confirm_execute": "confirm_execute_pending",
    "confirm": "confirm_execute_pending",
    "analyze": "analyze_existing_evidence",
    "followup_analysis": "analyze_existing_evidence",
    "continue_analysis": "analyze_existing_evidence",
    "advice": "advice_analysis",
    "risk_analysis": "advice_analysis",
    "plan_advice": "advice_analysis",
    "asset_query": "cmdb_query",
    "query_cmdb": "cmdb_query",
    "chat": "general_chat",
    "general": "general_chat",
    "clarify": "need_clarification",
    "clarification": "need_clarification",
}


def decide_intent(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
    conversation_id: Optional[str] = None,
    llm_client: Any = None,
) -> IntentDecision:
    """Convenience function used by future dispatcher or shadow mode."""
    return IntentArbiter(llm_client=llm_client).decide(
        question=question,
        context=context,
        user=user,
        conversation_id=conversation_id,
    )


class IntentArbiter:
    """LLM-first intent arbiter for Chat V3."""

    def __init__(self, llm_client: Any = None) -> None:
        if llm_client is not None:
            self.llm_client = llm_client
        elif LLMClient is not None:
            self.llm_client = LLMClient()
        else:
            self.llm_client = None

    def decide(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        user: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> IntentDecision:
        start = time.time()
        request_id = str(uuid.uuid4())
        raw_question = str(question or "").strip()

        if not raw_question:
            decision = build_need_clarification("", "empty_user_question")
            _attach_backend_metadata(
                decision,
                request_id=request_id,
                conversation_id=conversation_id,
                user=user,
                started_at=start,
                llm_result=None,
                raw_content="",
                effective_confidence=0.0,
                confidence_adjust_reason="empty_user_question",
            )
            return decision

        if self.llm_client is None:
            decision = build_need_clarification(raw_question, "llm_client_unavailable")
            _attach_backend_metadata(
                decision,
                request_id=request_id,
                conversation_id=conversation_id,
                user=user,
                started_at=start,
                llm_result=None,
                raw_content="",
                effective_confidence=0.0,
                confidence_adjust_reason="llm_client_unavailable",
            )
            return decision

        messages = build_intent_messages(raw_question, context=context, user=user)

        llm_result = self._call_llm(messages)
        if llm_result.get("status") != "ok":
            decision = build_need_clarification(raw_question, "llm_call_failed")
            decision.metadata["llm_error"] = _safe_llm_error(llm_result)
            _attach_backend_metadata(
                decision,
                request_id=request_id,
                conversation_id=conversation_id,
                user=user,
                started_at=start,
                llm_result=llm_result,
                raw_content="",
                effective_confidence=0.0,
                confidence_adjust_reason="llm_call_failed",
            )
            return decision

        raw_content = str(llm_result.get("content") or "")
        payload = parse_json_from_text(raw_content)
        if not isinstance(payload, dict):
            decision = build_need_clarification(raw_question, "llm_invalid_json")
            decision.metadata["raw_content_preview"] = raw_content[:2000]
            _attach_backend_metadata(
                decision,
                request_id=request_id,
                conversation_id=conversation_id,
                user=user,
                started_at=start,
                llm_result=llm_result,
                raw_content=raw_content,
                effective_confidence=0.0,
                confidence_adjust_reason="llm_invalid_json",
            )
            return decision

        payload = normalize_payload(payload)
        payload.setdefault("raw_user_text", raw_question)
        payload.setdefault("context_summary", build_context_summary(context))

        try:
            decision = IntentDecision(**payload)
        except Exception as exc:
            fallback = build_need_clarification(raw_question, "intent_schema_validation_failed")
            fallback.metadata["schema_error"] = repr(exc)
            fallback.metadata["payload_preview"] = _safe_json_preview(payload)
            _attach_backend_metadata(
                fallback,
                request_id=request_id,
                conversation_id=conversation_id,
                user=user,
                started_at=start,
                llm_result=llm_result,
                raw_content=raw_content,
                effective_confidence=0.0,
                confidence_adjust_reason="intent_schema_validation_failed",
            )
            return fallback

        effective_confidence, adjust_reason = calculate_effective_confidence(decision)
        _attach_backend_metadata(
            decision,
            request_id=request_id,
            conversation_id=conversation_id,
            user=user,
            started_at=start,
            llm_result=llm_result,
            raw_content=raw_content,
            effective_confidence=effective_confidence,
            confidence_adjust_reason=adjust_reason,
        )
        return decision

    def _call_llm(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Call existing LLM client with JSON mode first, then fallback without JSON mode."""
        try:
            first = self.llm_client.chat(
                messages,
                max_tokens=1400,
                temperature=0,
                top_p=None,
                response_format={"type": "json_object"},
                thinking={"type": "disabled"},
            )
        except TypeError:
            first = self.llm_client.chat(messages)

        if isinstance(first, dict) and first.get("status") == "ok":
            return first

        http_status = None
        if isinstance(first, dict):
            http_status = first.get("http_status")

        if http_status in (400, 404, 422):
            try:
                second = self.llm_client.chat(
                    messages,
                    max_tokens=1400,
                    temperature=0,
                    top_p=None,
                    response_format=False,
                    thinking=False,
                )
            except TypeError:
                second = self.llm_client.chat(messages)

            if isinstance(second, dict):
                second["json_mode_fallback_used"] = True
                second["first_error"] = _safe_llm_error(first)
                return second

        return first if isinstance(first, dict) else {
            "status": "error",
            "error_code": "LLM_CLIENT_RETURNED_NON_DICT",
            "message": str(first)[:500],
        }


def build_context_summary(context: Optional[Dict[str, Any]]) -> str:
    if not isinstance(context, dict):
        return ""

    compact = {
        "current_device": context.get("current_device"),
        "current_topic": context.get("current_topic"),
        "current_intent": context.get("current_intent"),
        "active_focus": context.get("active_focus"),
        "pending_commands_count": len(context.get("pending_commands") or []),
        "last_command_suggestions_count": len(context.get("last_command_suggestions") or []),
        "last_executions_count": len(context.get("last_executions") or []),
        "has_last_audit_path": bool(context.get("last_audit_path") or context.get("audit_path")),
        "rolling_summary": context.get("rolling_summary"),
    }

    return json.dumps(compact, ensure_ascii=False, default=str)[:6000]


def build_intent_messages(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
) -> List[Dict[str, str]]:
    context_summary = build_context_summary(context)

    action_list = ", ".join(action.value for action in IntentAction)

    system = f"""你是 NetAIOps ChatBot V3 的 LLM Intent Arbiter。
你的任务不是回答用户，而是判断用户输入应该触发哪个后端 action，并只输出严格 JSON。

核心架构原则：
1. 用户想干什么，由你判断。
2. 后端只根据你输出的 JSON 分发。
3. 本地规则只负责安全拦截、命令切分、格式修复、低置信度兜底。
4. 你不能决定绕过安全校验；即使 action 是执行命令，后端也会再做 safety_guard。
5. 用户主动提供命令时，不需要二次确认；因为用户提供命令本身代表人工确认过。
6. 你输出的 confidence 是你对 action 判断的置信度，不是安全分数。

只能从以下 action 中选择一个：
{action_list}

action 含义：
- generate_commands：生成排障命令建议，不执行。
- execute_provided_commands：用户直接提供命令，要求执行，不要求分析。
- execute_provided_commands_and_analyze：用户直接提供命令，要求执行后基于新输出分析。
- confirm_execute_pending：用户确认执行上一轮 pending commands。
- analyze_existing_evidence：用户没有提供新命令，只要求基于已有执行结果继续分析。
- advice_analysis：纯方案建议、风险分析、优缺点比较，不生成命令、不执行命令。
- cmdb_query：只查资产/CMDB 信息，不走排障执行链路。
- general_chat：普通解释、知识问答、闲聊、非设备操作类问题。
- need_clarification：缺少关键设备、目标、上下文或意图，需要澄清。

必须输出 JSON，不能输出 Markdown，不能输出解释文字。
JSON schema:
{{
  "schema_version": "{INTENT_SCHEMA_VERSION}",
  "action": "generate_commands | execute_provided_commands | execute_provided_commands_and_analyze | confirm_execute_pending | analyze_existing_evidence | advice_analysis | cmdb_query | general_chat | need_clarification",
  "confidence": 0.0,
  "device_required": false,
  "device_hint": "",
  "commands_provided": false,
  "commands": [],
  "need_existing_evidence": false,
  "should_generate_commands": false,
  "should_execute_commands": false,
  "should_analyze_after_execution": false,
  "requires_confirmation": false,
  "clarification_question": "",
  "reason": ""
}}

判断规则：
1. 用户问“给我命令/怎么排查/查看日志命令/第一批排查命令”，通常是 generate_commands。
2. 用户贴出 show/display/ping/traceroute 等命令并说执行，通常是 execute_provided_commands。
3. 用户贴出命令并说“执行后分析/根据结果分析/进一步分析”，必须是 execute_provided_commands_and_analyze。
4. 用户只说“继续分析刚才结果/结合以上输出/根据刚才执行结果”，且没有新命令，才是 analyze_existing_evidence。
5. 用户问“是否建议/风险如何/哪种方案更稳/只给建议不要命令”，必须是 advice_analysis。
6. 用户查管理 IP、型号、机房、设备类型、资产字段，是 cmdb_query。
7. 用户问概念解释，如 StackWise Virtual 是什么，是 general_chat。
8. 关键信息不足且无法从上下文继承时，是 need_clarification。
9. 如果同一句话同时包含新命令和分析诉求，优先 execute_provided_commands_and_analyze，不要误判为 analyze_existing_evidence。
10. 如果用户主动提供命令，requires_confirmation 必须为 false。
"""

    examples = """示例 1：
用户：给我查看一下设备 SH16-H05-INT-EDG-SW01 日志的命令
输出：{"schema_version":"v3_intent_arbiter_1","action":"generate_commands","confidence":0.94,"device_required":true,"device_hint":"SH16-H05-INT-EDG-SW01","commands_provided":false,"commands":[],"need_existing_evidence":false,"should_generate_commands":true,"should_execute_commands":false,"should_analyze_after_execution":false,"requires_confirmation":false,"clarification_question":"","reason":"用户要求生成查看日志的排障命令，不要求执行"}

示例 2：
用户：请执行以下命令并分析：show clock show version show logging last 100
输出：{"schema_version":"v3_intent_arbiter_1","action":"execute_provided_commands_and_analyze","confidence":0.96,"device_required":true,"device_hint":"","commands_provided":true,"commands":["show clock","show version","show logging last 100"],"need_existing_evidence":false,"should_generate_commands":false,"should_execute_commands":true,"should_analyze_after_execution":true,"requires_confirmation":false,"clarification_question":"","reason":"用户提供新命令并要求执行后分析"}

示例 3：
用户：继续分析刚才的执行结果
输出：{"schema_version":"v3_intent_arbiter_1","action":"analyze_existing_evidence","confidence":0.90,"device_required":false,"device_hint":"","commands_provided":false,"commands":[],"need_existing_evidence":true,"should_generate_commands":false,"should_execute_commands":false,"should_analyze_after_execution":false,"requires_confirmation":false,"clarification_question":"","reason":"用户没有提供新命令，只要求基于已有证据继续分析"}

示例 4：
用户：是否建议在重启 standby 前先隔离流量？只给建议，不要命令。
输出：{"schema_version":"v3_intent_arbiter_1","action":"advice_analysis","confidence":0.95,"device_required":false,"device_hint":"","commands_provided":false,"commands":[],"need_existing_evidence":false,"should_generate_commands":false,"should_execute_commands":false,"should_analyze_after_execution":false,"requires_confirmation":false,"clarification_question":"","reason":"用户明确要求方案建议和风险分析，不要命令"}

示例 5：
用户：查一下 SH16-H05-INT-EDG-SW01 的管理 IP 和设备类型
输出：{"schema_version":"v3_intent_arbiter_1","action":"cmdb_query","confidence":0.94,"device_required":true,"device_hint":"SH16-H05-INT-EDG-SW01","commands_provided":false,"commands":[],"need_existing_evidence":false,"should_generate_commands":false,"should_execute_commands":false,"should_analyze_after_execution":false,"requires_confirmation":false,"clarification_question":"","reason":"用户只查询资产字段，不要求排障或执行命令"}"""

    user_msg = "用户={}\n上下文摘要={}\n当前输入={}".format(
        user or "",
        context_summary or "无",
        question,
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": examples},
        {"role": "user", "content": user_msg},
    ]


def parse_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return data if isinstance(data, dict) else None
        except Exception:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    return None


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload or {})

    action = str(data.get("action") or data.get("intent") or "").strip()
    action = ACTION_ALIASES.get(action.lower(), action)
    data["action"] = action or IntentAction.need_clarification.value

    try:
        data["confidence"] = float(data.get("confidence", 0.0))
    except Exception:
        data["confidence"] = 0.0

    if data["confidence"] < 0:
        data["confidence"] = 0.0
    if data["confidence"] > 1:
        data["confidence"] = 1.0

    commands = data.get("commands")
    if commands is None:
        data["commands"] = []
    elif isinstance(commands, str):
        data["commands"] = [line.strip() for line in commands.splitlines() if line.strip()]
    elif isinstance(commands, list):
        data["commands"] = [str(item).strip() for item in commands if str(item).strip()]
    else:
        data["commands"] = []

    data["schema_version"] = str(data.get("schema_version") or INTENT_SCHEMA_VERSION)

    if "requires_confirmation" not in data and data["action"] in {
        IntentAction.execute_provided_commands.value,
        IntentAction.execute_provided_commands_and_analyze.value,
    }:
        data["requires_confirmation"] = False

    return data


def calculate_effective_confidence(decision: IntentDecision) -> Tuple[float, str]:
    value = float(decision.confidence)
    reasons: List[str] = []

    if decision.action == IntentAction.need_clarification:
        if value >= CONFIDENCE_CLARIFY_THRESHOLD:
            value = min(value, CONFIDENCE_CLARIFY_THRESHOLD - 0.01)
            reasons.append("clarification_action_caps_confidence")

    if decision.action in {
        IntentAction.execute_provided_commands,
        IntentAction.execute_provided_commands_and_analyze,
    } and not decision.commands:
        value = min(value, CONFIDENCE_ACCEPT_THRESHOLD - 0.01)
        reasons.append("execute_action_has_empty_commands")

    if decision.action == IntentAction.analyze_existing_evidence and decision.commands:
        value = min(value, CONFIDENCE_ACCEPT_THRESHOLD - 0.01)
        reasons.append("existing_evidence_action_should_not_include_new_commands")

    if decision.action == IntentAction.advice_analysis and decision.should_execute_commands:
        value = min(value, CONFIDENCE_ACCEPT_THRESHOLD - 0.01)
        reasons.append("advice_action_should_not_execute")

    if value < 0:
        value = 0.0
    if value > 1:
        value = 1.0

    return value, ",".join(reasons)


def _attach_backend_metadata(
    decision: IntentDecision,
    request_id: str,
    conversation_id: Optional[str],
    user: Optional[str],
    started_at: float,
    llm_result: Optional[Dict[str, Any]],
    raw_content: str,
    effective_confidence: float,
    confidence_adjust_reason: str,
) -> None:
    elapsed_ms = int((time.time() - started_at) * 1000)
    decision.metadata.update({
        "arbiter_version": ARBITER_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id or "",
        "user": user or "",
        "llm_confidence": float(decision.confidence),
        "effective_confidence": float(effective_confidence),
        "confidence_adjust_reason": confidence_adjust_reason,
        "accept_threshold": CONFIDENCE_ACCEPT_THRESHOLD,
        "clarify_threshold": CONFIDENCE_CLARIFY_THRESHOLD,
        "arbiter_elapsed_ms": elapsed_ms,
        "raw_content_preview": str(raw_content or "")[:2000],
    })

    if isinstance(llm_result, dict):
        decision.metadata.update({
            "llm_status": llm_result.get("status"),
            "llm_http_status": llm_result.get("http_status"),
            "llm_latency_ms": llm_result.get("latency_ms"),
            "llm_model": llm_result.get("model"),
            "llm_base_url_used": llm_result.get("base_url_used"),
            "json_mode_fallback_used": bool(llm_result.get("json_mode_fallback_used")),
        })


def _safe_llm_error(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"message": str(result)[:500]}

    return {
        "status": result.get("status"),
        "error_code": result.get("error_code"),
        "http_status": result.get("http_status"),
        "message": str(result.get("message") or result.get("error") or "")[:1000],
        "config": result.get("config"),
        "base_url_used": result.get("base_url_used"),
    }


def _safe_json_preview(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, default=str)[:2000]
    except Exception:
        return str(data)[:2000]
