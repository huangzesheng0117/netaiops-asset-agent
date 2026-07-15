# -*- coding: utf-8 -*-
"""Shared request/outcome contracts for V4.2-2 low-risk handlers.

Handlers receive an already validated IntentDecision. They do not classify user
text, query CMDB/MCP, or execute commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol

from netaiops_asset.chat_v3.intent_schema import IntentAction, IntentDecision
from netaiops_asset.chat_v3.response_generator import generate_v3_response
from netaiops_asset.chat_v4.contracts import CanonicalContext


@dataclass(frozen=True)
class HandlerRequest:
    question: str
    conversation_id: str
    request_id: str
    request_user_field: str
    decision: IntentDecision
    canonical_context: CanonicalContext
    allow_live_llm: bool = False
    llm_client: Any = None

    def __post_init__(self) -> None:
        if (
            not str(self.question or "").strip()
            and self.decision.action != IntentAction.need_clarification
        ):
            raise ValueError("question is required")
        if not str(self.conversation_id or "").strip():
            raise ValueError("conversation_id is required")
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        if self.canonical_context.conversation_id != self.conversation_id:
            raise ValueError("canonical context conversation_id mismatch")


@dataclass
class HandlerOutcome:
    ok: bool
    action: IntentAction
    handler_key: str
    answer: str = ""
    status: str = "ok"
    source: str = ""
    detail: str = ""
    items: list[Dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    field_labels: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        *,
        action: IntentAction,
        handler_key: str,
        answer: str,
        status: str = "ok",
        source: str = "",
        items: list[Dict[str, Any]] | None = None,
        columns: list[str] | None = None,
        field_labels: Dict[str, str] | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> "HandlerOutcome":
        normalized_answer = str(answer or "").strip()
        if not normalized_answer:
            raise ValueError("successful handler outcome requires a non-empty answer")
        return cls(
            ok=True,
            action=action,
            handler_key=str(handler_key or action.value),
            answer=normalized_answer,
            status=str(status or "ok"),
            source=str(source or ""),
            items=list(items or []),
            columns=[str(value) for value in list(columns or [])],
            field_labels={
                str(key): str(value)
                for key, value in dict(field_labels or {}).items()
            },
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(
        cls,
        *,
        action: IntentAction,
        handler_key: str,
        detail: str,
        source: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> "HandlerOutcome":
        return cls(
            ok=False,
            action=action,
            handler_key=str(handler_key or action.value),
            status="error",
            source=str(source or ""),
            detail=str(detail or "handler_failed"),
            metadata=dict(metadata or {}),
        )


class LowRiskHandler(Protocol):
    action: IntentAction
    handler_key: str

    def handle(self, request: HandlerRequest) -> HandlerOutcome:
        ...


def ensure_expected_action(
    request: HandlerRequest,
    expected: IntentAction,
) -> HandlerOutcome | None:
    if request.decision.action == expected:
        return None
    return HandlerOutcome.failure(
        action=request.decision.action,
        handler_key=expected.value,
        detail=(
            "handler action mismatch: expected={} actual={}".format(
                expected.value,
                request.decision.action.value,
            )
        ),
        source="v4_action_contract",
    )


def generate_with_v3_adapter(
    request: HandlerRequest,
    *,
    expected_action: IntentAction,
    response_mode: str,
) -> HandlerOutcome:
    mismatch = ensure_expected_action(request, expected_action)
    if mismatch is not None:
        return mismatch

    plan = {
        "action": expected_action.value,
        "handler_key": expected_action.value,
        "response_mode": response_mode,
        "reason": request.decision.reason,
    }
    decision_payload = request.decision.model_dump(mode="python")
    canonical_payload = request.canonical_context.model_dump(mode="python")
    context = {
        "canonical_context": canonical_payload,
        "followup_context": {
            "followup_context_available": bool(
                request.canonical_context.recent_turns
                or request.canonical_context.rolling_summary
                or request.canonical_context.device_context
            ),
            "rolling_summary": request.canonical_context.rolling_summary,
            "recent_turns": [
                turn.model_dump(mode="python")
                for turn in request.canonical_context.recent_turns[-6:]
            ],
            "current_device": request.canonical_context.device_context,
            "current_topic": request.canonical_context.topic,
            "last_intent": request.canonical_context.last_intent,
        },
    }
    gate = {
        "enabled": True,
        "eligible": True,
        "takeover": True,
        "reason": "v4_2_2_low_risk_handler",
    }

    generated = generate_v3_response(
        question=request.question,
        conversation_id=request.conversation_id,
        plan=plan,
        decision=decision_payload,
        context=context,
        gate=gate,
        allow_live_llm=request.allow_live_llm,
        llm_client=request.llm_client,
    )
    generated_data = generated.as_dict()

    metadata = {
        "generator_reason": generated_data.get("reason"),
        "generator_source": generated_data.get("source"),
        "llm_status": generated_data.get("llm_status"),
        "llm_error": generated_data.get("llm_error"),
        "gate": generated_data.get("gate") or {},
        "readiness": generated_data.get("readiness") or {},
    }

    if generated_data.get("ready") is not True:
        return HandlerOutcome.failure(
            action=expected_action,
            handler_key=expected_action.value,
            detail=str(
                generated_data.get("reason")
                or generated_data.get("llm_error")
                or "response_not_ready"
            ),
            source=str(generated_data.get("source") or "v3_response_adapter"),
            metadata=metadata,
        )

    answer = str(generated_data.get("answer") or "").strip()
    if not answer:
        return HandlerOutcome.failure(
            action=expected_action,
            handler_key=expected_action.value,
            detail="empty_handler_answer",
            source=str(generated_data.get("source") or "v3_response_adapter"),
            metadata=metadata,
        )

    return HandlerOutcome.success(
        action=expected_action,
        handler_key=expected_action.value,
        answer=answer,
        status=str(generated_data.get("status") or "ok"),
        source=str(generated_data.get("source") or "v3_response_adapter"),
        items=list(generated_data.get("items") or []),
        columns=list(generated_data.get("columns") or []),
        field_labels=dict(generated_data.get("field_labels") or {}),
        metadata=metadata,
    )
