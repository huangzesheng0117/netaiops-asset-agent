# -*- coding: utf-8 -*-
"""Unified V4 response construction for low-risk handlers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from netaiops_asset.chat_v3.intent_schema import IntentAction, IntentDecision
from netaiops_asset.chat_v4.contracts import (
    EntryResult,
    EntryStatus,
    V4AuditRecord,
    V4Response,
    V4ResponseMeta,
)
from netaiops_asset.chat_v4.handlers.base import HandlerOutcome


DEFAULT_PUBLIC_ERROR = (
    "本次请求未能完成内部响应、上下文或审计处理，请保留当前请求信息后重试。"
)


def build_v4_response(
    *,
    question: str,
    conversation_id: str,
    decision: IntentDecision,
    outcome: HandlerOutcome,
    audit_id: str = "",
    context_recorded: bool = False,
) -> V4Response:
    if outcome.ok is not True:
        raise ValueError("cannot build success response from failed handler outcome")
    if outcome.action != decision.action:
        raise ValueError("handler outcome action does not match IntentDecision")
    if not str(outcome.answer or "").strip():
        raise ValueError("successful V4 response requires a non-empty answer")

    expected_status = (
        "need_clarification"
        if decision.action == IntentAction.need_clarification
        else "ok"
    )
    status = str(outcome.status or expected_status)
    if decision.action == IntentAction.need_clarification:
        status = "need_clarification"
    elif status == "need_clarification":
        raise ValueError(
            "need_clarification response status requires need_clarification action"
        )

    return V4Response(
        status=status,
        answer=str(outcome.answer).strip(),
        items=list(outcome.items),
        columns=list(outcome.columns),
        field_labels=dict(outcome.field_labels),
        conversation_id=str(conversation_id or ""),
        question=str(question or ""),
        action=decision.action,
        planner_source="v4_intent_arbiter",
        v4=V4ResponseMeta(
            handler_key=str(outcome.handler_key or decision.action.value),
            confidence=float(decision.confidence),
            side_effect_started=False,
            fallback_used=False,
            audit_id=str(audit_id or ""),
            context_recorded=bool(context_recorded),
        ),
    )


def build_v4_error_response(
    *,
    question: str,
    conversation_id: str,
    decision: IntentDecision,
    handler_key: str,
    public_message: str = DEFAULT_PUBLIC_ERROR,
    audit_id: str = "",
    context_recorded: bool = False,
) -> V4Response:
    answer = str(public_message or DEFAULT_PUBLIC_ERROR).strip()
    if not answer:
        answer = DEFAULT_PUBLIC_ERROR
    return V4Response(
        status="error",
        answer=answer,
        conversation_id=str(conversation_id or ""),
        question=str(question or ""),
        action=decision.action,
        planner_source="v4_intent_arbiter",
        v4=V4ResponseMeta(
            handler_key=str(handler_key or decision.action.value),
            confidence=float(decision.confidence),
            side_effect_started=False,
            fallback_used=False,
            audit_id=str(audit_id or ""),
            context_recorded=bool(context_recorded),
        ),
    )


def build_handled_entry(
    *,
    decision: IntentDecision,
    response: V4Response,
    audit: V4AuditRecord,
    context_metadata: Optional[Dict[str, Any]] = None,
) -> EntryResult:
    status = (
        EntryStatus.clarification
        if decision.action == IntentAction.need_clarification
        else EntryStatus.handled
    )
    return EntryResult(
        status=status,
        action=decision.action,
        handler_key=response.v4.handler_key,
        side_effect_started=False,
        fallback_allowed=False,
        fallback_reason="",
        response=response,
        audit=audit,
        context=dict(context_metadata or {}),
    )


def build_error_entry(
    *,
    decision: IntentDecision,
    response: V4Response,
    audit: V4AuditRecord,
    context_metadata: Optional[Dict[str, Any]] = None,
) -> EntryResult:
    return EntryResult(
        status=EntryStatus.error,
        action=decision.action,
        handler_key=response.v4.handler_key,
        side_effect_started=False,
        fallback_allowed=False,
        fallback_reason="",
        response=response,
        audit=audit,
        context=dict(context_metadata or {}),
    )


def build_stage_fallback_entry(
    *,
    decision: IntentDecision,
    reason: str,
    audit: V4AuditRecord,
    context_metadata: Optional[Dict[str, Any]] = None,
) -> EntryResult:
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("stage fallback requires a reason")
    return EntryResult(
        status=EntryStatus.fallback,
        action=decision.action,
        handler_key="",
        side_effect_started=False,
        fallback_allowed=True,
        fallback_reason=normalized_reason,
        response=None,
        audit=audit,
        context=dict(context_metadata or {}),
    )
