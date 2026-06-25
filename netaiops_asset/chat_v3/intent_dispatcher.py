# -*- coding: utf-8 -*-
"""
V3 intent dispatcher skeleton.

Responsibilities:
- Consume IntentDecision JSON from LLM Intent Arbiter.
- Build a deterministic dispatch plan.
- Re-apply command splitting and safety checks before any future execution.
- Do not call CMDB, MCP, Netmiko or evidence analyzer yet.
- Do not modify frontend contract yet.

This is a staging module for V3.2 shadow mode and V3.3 high-confidence switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from netaiops_asset.chat_v3.command_splitter import split_command_list, split_commands
from netaiops_asset.chat_v3.intent_arbiter import decide_intent
from netaiops_asset.chat_v3.intent_schema import (
    CONFIDENCE_ACCEPT_THRESHOLD,
    CONFIDENCE_CLARIFY_THRESHOLD,
    IntentAction,
    IntentDecision,
)
from netaiops_asset.chat_v3.safety_guard import CommandCheck, SafetyCheckResult, check_commands


DISPATCHER_VERSION = "v3_intent_dispatcher_1"


@dataclass
class DispatchPlan:
    action: str
    accepted: bool
    handler_key: str
    response_mode: str
    confidence: float
    effective_confidence: float

    device_required: bool = False
    device_hint: str = ""

    commands: List[str] = field(default_factory=list)
    safe_commands: List[str] = field(default_factory=list)
    blocked_commands: List[Dict[str, Any]] = field(default_factory=list)
    safety_allowed: bool = True
    safety_reason: str = ""

    requires_confirmation: bool = False
    should_generate_commands: bool = False
    should_execute_commands: bool = False
    should_analyze_after_execution: bool = False
    need_existing_evidence: bool = False

    reason: str = ""
    notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dispatcher_version": DISPATCHER_VERSION,
            "action": self.action,
            "accepted": self.accepted,
            "handler_key": self.handler_key,
            "response_mode": self.response_mode,
            "confidence": self.confidence,
            "effective_confidence": self.effective_confidence,
            "device_required": self.device_required,
            "device_hint": self.device_hint,
            "commands": self.commands,
            "safe_commands": self.safe_commands,
            "blocked_commands": self.blocked_commands,
            "safety_allowed": self.safety_allowed,
            "safety_reason": self.safety_reason,
            "requires_confirmation": self.requires_confirmation,
            "should_generate_commands": self.should_generate_commands,
            "should_execute_commands": self.should_execute_commands,
            "should_analyze_after_execution": self.should_analyze_after_execution,
            "need_existing_evidence": self.need_existing_evidence,
            "reason": self.reason,
            "notes": self.notes,
            "metadata": self.metadata,
        }


def build_dispatch_plan(
    question: str,
    decision: Optional[IntentDecision] = None,
    context: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
    conversation_id: Optional[str] = None,
    llm_client: Any = None,
    max_commands: int = 20,
) -> DispatchPlan:
    """Build a deterministic dispatch plan from a V3 IntentDecision.

    If decision is not provided, this function calls V3 Intent Arbiter.
    This function still does not execute anything.
    """

    if decision is None:
        decision = decide_intent(
            question=question,
            context=context,
            user=user,
            conversation_id=conversation_id,
            llm_client=llm_client,
        )

    effective_confidence = get_effective_confidence(decision)
    notes: List[str] = []
    action = decision.action

    if effective_confidence < CONFIDENCE_CLARIFY_THRESHOLD:
        return _clarification_plan(
            decision=decision,
            effective_confidence=effective_confidence,
            reason="effective_confidence_below_clarify_threshold",
        )

    if action == IntentAction.need_clarification:
        return _clarification_plan(
            decision=decision,
            effective_confidence=effective_confidence,
            reason=decision.reason or "arbiter_requires_clarification",
        )

    if effective_confidence < CONFIDENCE_ACCEPT_THRESHOLD:
        notes.append("effective_confidence_below_accept_threshold")
        if action in {
            IntentAction.execute_provided_commands,
            IntentAction.execute_provided_commands_and_analyze,
            IntentAction.confirm_execute_pending,
        }:
            return _clarification_plan(
                decision=decision,
                effective_confidence=effective_confidence,
                reason="low_confidence_command_execution_not_accepted",
                notes=notes,
            )

    if action == IntentAction.generate_commands:
        return DispatchPlan(
            action=action.value,
            accepted=True,
            handler_key="generate_commands",
            response_mode="command_suggestion",
            confidence=decision.confidence,
            effective_confidence=effective_confidence,
            device_required=decision.device_required,
            device_hint=decision.device_hint,
            requires_confirmation=False,
            should_generate_commands=True,
            reason=decision.reason,
            notes=notes,
            metadata=_base_metadata(decision),
        )

    if action in {
        IntentAction.execute_provided_commands,
        IntentAction.execute_provided_commands_and_analyze,
    }:
        commands = _extract_commands_for_execution(question, decision)
        safety = check_commands(commands, max_commands=max_commands)
        return _execution_plan(
            decision=decision,
            action=action,
            effective_confidence=effective_confidence,
            commands=commands,
            safety=safety,
            notes=notes,
        )

    if action == IntentAction.confirm_execute_pending:
        return DispatchPlan(
            action=action.value,
            accepted=True,
            handler_key="confirm_execute_pending",
            response_mode="execute_pending",
            confidence=decision.confidence,
            effective_confidence=effective_confidence,
            device_required=decision.device_required,
            device_hint=decision.device_hint,
            requires_confirmation=False,
            should_execute_commands=True,
            reason=decision.reason,
            notes=notes,
            metadata=_base_metadata(decision),
        )

    if action == IntentAction.analyze_existing_evidence:
        return DispatchPlan(
            action=action.value,
            accepted=True,
            handler_key="analyze_existing_evidence",
            response_mode="analysis",
            confidence=decision.confidence,
            effective_confidence=effective_confidence,
            device_required=decision.device_required,
            device_hint=decision.device_hint,
            need_existing_evidence=True,
            reason=decision.reason,
            notes=notes,
            metadata=_base_metadata(decision),
        )

    if action == IntentAction.advice_analysis:
        return DispatchPlan(
            action=action.value,
            accepted=True,
            handler_key="advice_analysis",
            response_mode="advice",
            confidence=decision.confidence,
            effective_confidence=effective_confidence,
            device_required=decision.device_required,
            device_hint=decision.device_hint,
            reason=decision.reason,
            notes=notes,
            metadata=_base_metadata(decision),
        )

    if action == IntentAction.cmdb_query:
        return DispatchPlan(
            action=action.value,
            accepted=True,
            handler_key="cmdb_query",
            response_mode="cmdb",
            confidence=decision.confidence,
            effective_confidence=effective_confidence,
            device_required=decision.device_required,
            device_hint=decision.device_hint,
            reason=decision.reason,
            notes=notes,
            metadata=_base_metadata(decision),
        )

    if action == IntentAction.general_chat:
        return DispatchPlan(
            action=action.value,
            accepted=True,
            handler_key="general_chat",
            response_mode="chat",
            confidence=decision.confidence,
            effective_confidence=effective_confidence,
            device_required=False,
            device_hint=decision.device_hint,
            reason=decision.reason,
            notes=notes,
            metadata=_base_metadata(decision),
        )

    return _clarification_plan(
        decision=decision,
        effective_confidence=effective_confidence,
        reason=f"unsupported_action:{action}",
        notes=notes,
    )


def get_effective_confidence(decision: IntentDecision) -> float:
    value = decision.metadata.get("effective_confidence", decision.confidence)
    try:
        value = float(value)
    except Exception:
        value = float(decision.confidence)

    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _extract_commands_for_execution(question: str, decision: IntentDecision) -> List[str]:
    if decision.commands:
        result = split_command_list(decision.commands, max_commands=200)
        if result.commands:
            return result.commands

    raw_user_text = decision.raw_user_text or question or ""
    result = split_commands(raw_user_text, max_commands=200)
    return result.commands


def _execution_plan(
    decision: IntentDecision,
    action: IntentAction,
    effective_confidence: float,
    commands: List[str],
    safety: SafetyCheckResult,
    notes: List[str],
) -> DispatchPlan:
    handler_key = "execute_provided_commands"
    response_mode = "execute"

    if action == IntentAction.execute_provided_commands_and_analyze:
        handler_key = "execute_provided_commands_and_analyze"
        response_mode = "execute_and_analyze"

    blocked = [
        item.as_dict() if isinstance(item, CommandCheck) else dict(item)
        for item in safety.blocked_commands
    ]

    if not commands:
        notes = list(notes)
        notes.append("no_commands_extracted")
        return DispatchPlan(
            action=IntentAction.need_clarification.value,
            accepted=False,
            handler_key="need_clarification",
            response_mode="clarification",
            confidence=decision.confidence,
            effective_confidence=0.0,
            device_required=decision.device_required,
            device_hint=decision.device_hint,
            commands=[],
            safe_commands=[],
            blocked_commands=[],
            safety_allowed=False,
            safety_reason="no_commands_extracted",
            requires_confirmation=False,
            should_execute_commands=False,
            should_analyze_after_execution=False,
            reason="action_requires_commands_but_none_extracted",
            notes=notes,
            metadata=_base_metadata(decision),
        )

    return DispatchPlan(
        action=action.value if safety.allowed else "blocked_unsafe_commands",
        accepted=bool(safety.allowed),
        handler_key=handler_key if safety.allowed else "blocked_unsafe_commands",
        response_mode=response_mode if safety.allowed else "safety_block",
        confidence=decision.confidence,
        effective_confidence=effective_confidence if safety.allowed else 0.0,
        device_required=decision.device_required,
        device_hint=decision.device_hint,
        commands=commands,
        safe_commands=safety.safe_commands,
        blocked_commands=blocked,
        safety_allowed=bool(safety.allowed),
        safety_reason=safety.reason,
        requires_confirmation=False,
        should_execute_commands=bool(safety.allowed),
        should_analyze_after_execution=bool(
            safety.allowed and action == IntentAction.execute_provided_commands_and_analyze
        ),
        reason=decision.reason,
        notes=notes,
        metadata=_base_metadata(decision),
    )


def _clarification_plan(
    decision: IntentDecision,
    effective_confidence: float,
    reason: str,
    notes: Optional[List[str]] = None,
) -> DispatchPlan:
    final_notes = list(notes or [])
    if reason:
        final_notes.append(reason)

    question = decision.clarification_question or "请补充更明确的设备、目标或操作意图。"

    return DispatchPlan(
        action=IntentAction.need_clarification.value,
        accepted=False,
        handler_key="need_clarification",
        response_mode="clarification",
        confidence=decision.confidence,
        effective_confidence=effective_confidence,
        device_required=decision.device_required,
        device_hint=decision.device_hint,
        safety_allowed=True,
        requires_confirmation=False,
        reason=question,
        notes=final_notes,
        metadata=_base_metadata(decision),
    )


def _base_metadata(decision: IntentDecision) -> Dict[str, Any]:
    return {
        "schema_version": decision.schema_version,
        "llm_confidence": decision.confidence,
        "raw_user_text": decision.raw_user_text,
        "context_summary": decision.context_summary,
        "confidence_adjust_reason": decision.metadata.get("confidence_adjust_reason", ""),
        "arbiter_metadata": decision.metadata,
    }
