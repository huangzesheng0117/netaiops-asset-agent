# -*- coding: utf-8 -*-
"""V4.2-2 dispatcher for low-risk, no-side-effect actions.

The dispatcher consumes an IntentDecision produced by the LLM Intent Arbiter.
It never derives action from question text.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional
import uuid

from netaiops_asset.chat_v3.intent_schema import (
    IntentAction,
    IntentDecision,
)
from netaiops_asset.chat_v4.audit_adapter import (
    attach_audit_reference,
    build_audit_record,
)
from netaiops_asset.chat_v4.audit_writer import (
    AuditWriteResult,
    AuditWriter,
)
from netaiops_asset.chat_v4.context_migration import load_or_migrate
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    CanonicalContext,
    ContextErrorKind,
    ContextOperationResult,
    EntryResult,
    EntryStatus,
    OperationStatus,
    V4AuditRecord,
)
from netaiops_asset.chat_v4.handlers import (
    AdviceAnalysisHandler,
    ClarificationHandler,
    GeneralChatHandler,
    HandlerOutcome,
    HandlerRequest,
    LowRiskHandler,
)
from netaiops_asset.chat_v4.response_builder import (
    build_error_entry,
    build_handled_entry,
    build_stage_fallback_entry,
    build_v4_error_response,
    build_v4_response,
)

LOW_RISK_ACTIONS = frozenset(
    {
        IntentAction.general_chat,
        IntentAction.advice_analysis,
        IntentAction.need_clarification,
    }
)


class LowRiskActionDispatcher:
    def __init__(
        self,
        *,
        store: Optional[ContextStore] = None,
        audit_writer: Optional[AuditWriter] = None,
        llm_client: Any = None,
        allow_live_llm: bool = False,
        followup_loader: Optional[
            Callable[[str, Optional[str]], Dict[str, Any]]
        ] = None,
        legacy_loader: Optional[Callable[[str], Dict[str, Any]]] = None,
    ) -> None:
        self.store = store or ContextStore()
        self.audit_writer = audit_writer or AuditWriter()
        self.llm_client = llm_client
        self.allow_live_llm = bool(allow_live_llm)
        self.followup_loader = followup_loader
        self.legacy_loader = legacy_loader
        self.handlers: Dict[IntentAction, LowRiskHandler] = {
            IntentAction.general_chat: GeneralChatHandler(),
            IntentAction.advice_analysis: AdviceAnalysisHandler(),
            IntentAction.need_clarification: ClarificationHandler(),
        }

    @staticmethod
    def _normalize_decision(value: Any) -> IntentDecision:
        if isinstance(value, IntentDecision):
            return value
        return IntentDecision.model_validate(value)

    def _load_context(
        self,
        conversation_id: str,
        request_user_field: str,
    ) -> ContextOperationResult:
        kwargs: Dict[str, Any] = {}
        if self.followup_loader is not None:
            kwargs["followup_loader"] = self.followup_loader
        if self.legacy_loader is not None:
            kwargs["legacy_loader"] = self.legacy_loader
        result = load_or_migrate(
            self.store,
            conversation_id,
            request_user_field or None,
            **kwargs,
        )
        if result.status == OperationStatus.not_found:
            return ContextOperationResult(
                status=OperationStatus.ok,
                context=self.store.new_context(
                    conversation_id,
                    request_user_field=request_user_field,
                ),
                detail="new canonical context prepared",
                metadata={"source_status": "not_found"},
            )
        return result

    def _write_audit(
        self,
        audit: V4AuditRecord,
    ) -> AuditWriteResult:
        return self.audit_writer.write(audit)

    def _error_entry(
        self,
        *,
        decision: IntentDecision,
        question: str,
        conversation_id: str,
        request_id: str,
        request_user_field: str,
        handler_key: str,
        detail: str,
        context_metadata: Dict[str, Any],
        context_read_status: str,
        context_write_status: str,
        context_recorded: bool,
    ) -> EntryResult:
        audit = build_audit_record(
            conversation_id=conversation_id,
            request_id=request_id,
            action=decision.action,
            handler_key=handler_key,
            status="error",
            side_effect_started=False,
            fallback_allowed=False,
            context_read_status=context_read_status,
            context_write_status=context_write_status,
            metadata={
                "detail": detail,
                "request_user_field": request_user_field,
            },
        )
        audit_write = self._write_audit(audit)
        context_metadata = dict(context_metadata)
        context_metadata.update(
            {
                "audit_write_status": audit_write.status.value,
                "audit_error_kind": (
                    audit_write.error_kind.value
                    if audit_write.error_kind is not None
                    else ""
                ),
                "audit_error": audit_write.detail,
                "audit_path": audit_write.path,
            }
        )
        response = build_v4_error_response(
            question=question,
            conversation_id=conversation_id,
            decision=decision,
            handler_key=handler_key,
            audit_id=audit.audit_id if audit_write.ok else "",
            context_recorded=context_recorded,
        )
        return build_error_entry(
            decision=decision,
            response=response,
            audit=audit,
            context_metadata=context_metadata,
        )

    def dispatch(
        self,
        *,
        question: str,
        conversation_id: str,
        request_id: str,
        request_user_field: str,
        decision: Any,
    ) -> EntryResult:
        normalized_question = str(question or "").strip()
        normalized_conversation_id = str(conversation_id or "").strip()
        normalized_request_id = str(request_id or "").strip()
        normalized_user = str(request_user_field or "").strip()

        if not normalized_question:
            raise ValueError("question is required")
        if not normalized_conversation_id:
            raise ValueError("conversation_id is required")
        if not normalized_request_id:
            raise ValueError("request_id is required")

        intent = self._normalize_decision(decision)

        if intent.action not in LOW_RISK_ACTIONS:
            audit = build_audit_record(
                conversation_id=normalized_conversation_id,
                request_id=normalized_request_id,
                action=intent.action,
                handler_key="",
                status="fallback",
                side_effect_started=False,
                fallback_allowed=True,
                fallback_reason="action_not_enabled_in_v4_2_2",
                context_read_status="not_attempted",
                context_write_status="not_attempted",
                metadata={"stage": "v4.2-2"},
            )
            audit_write = self._write_audit(audit)
            fallback_metadata = {
                "context_read_status": "not_attempted",
                "context_write_status": "not_attempted",
                "audit_write_status": audit_write.status.value,
                "audit_error_kind": (
                    audit_write.error_kind.value
                    if audit_write.error_kind is not None
                    else ""
                ),
                "audit_error": audit_write.detail,
                "audit_path": audit_write.path,
                "audit_ref": audit_write.audit_ref,
            }
            if not audit_write.ok:
                response = build_v4_error_response(
                    question=normalized_question,
                    conversation_id=normalized_conversation_id,
                    decision=intent,
                    handler_key="",
                    context_recorded=False,
                )
                return build_error_entry(
                    decision=intent,
                    response=response,
                    audit=audit,
                    context_metadata=fallback_metadata,
                )
            return build_stage_fallback_entry(
                decision=intent,
                reason="action_not_enabled_in_v4_2_2",
                audit=audit,
                context_metadata=fallback_metadata,
            )

        handler = self.handlers[intent.action]
        context_read = self._load_context(
            normalized_conversation_id,
            normalized_user,
        )
        read_status = context_read.status.value
        context_metadata: Dict[str, Any] = {
            "context_read_status": read_status,
            "context_read_error_kind": (
                context_read.error_kind.value
                if context_read.error_kind is not None
                else ""
            ),
            "context_read_detail": context_read.detail,
            "context_path": context_read.path,
            "context_migrated": bool(context_read.migrated),
        }

        if (
            context_read.status != OperationStatus.ok
            or context_read.context is None
        ):
            return self._error_entry(
                decision=intent,
                question=normalized_question,
                conversation_id=normalized_conversation_id,
                request_id=normalized_request_id,
                request_user_field=normalized_user,
                handler_key=handler.handler_key,
                detail=(
                    context_read.detail
                    or "canonical context read failed"
                ),
                context_metadata=context_metadata,
                context_read_status=read_status,
                context_write_status="not_attempted",
                context_recorded=False,
            )

        request = HandlerRequest(
            question=normalized_question,
            conversation_id=normalized_conversation_id,
            request_id=normalized_request_id,
            request_user_field=normalized_user,
            decision=intent,
            canonical_context=context_read.context,
            allow_live_llm=self.allow_live_llm,
            llm_client=self.llm_client,
        )
        outcome: HandlerOutcome = handler.handle(request)

        if outcome.ok is not True or not str(outcome.answer or "").strip():
            return self._error_entry(
                decision=intent,
                question=normalized_question,
                conversation_id=normalized_conversation_id,
                request_id=normalized_request_id,
                request_user_field=normalized_user,
                handler_key=handler.handler_key,
                detail=outcome.detail or "low-risk handler failed",
                context_metadata={
                    **context_metadata,
                    "handler_source": outcome.source,
                    "handler_metadata": outcome.metadata,
                },
                context_read_status=read_status,
                context_write_status="not_attempted",
                context_recorded=False,
            )

        context_write = self.store.append_turn(
            normalized_conversation_id,
            question=normalized_question,
            answer_summary=outcome.answer,
            action=intent.action,
            planner_source="v4_intent_arbiter",
            route_label="v4_2_2_low_risk_handler",
            effective_conversation_id=normalized_conversation_id,
            record_source="v4_low_risk_handler",
            request_user_field=normalized_user,
            topic=context_read.context.topic,
            device_context=context_read.context.device_context,
            last_intent={
                "action": intent.action.value,
                "confidence": float(intent.confidence),
                "reason": intent.reason,
                "source": "v4_intent_arbiter",
            },
            metadata={
                "request_id": normalized_request_id,
                "handler_key": handler.handler_key,
                "handler_source": outcome.source,
            },
        )
        context_metadata.update(
            {
                "context_write_status": context_write.status.value,
                "context_write_error_kind": (
                    context_write.error_kind.value
                    if context_write.error_kind is not None
                    else ""
                ),
                "context_write_detail": context_write.detail,
                "context_path": context_write.path or context_metadata.get(
                    "context_path",
                    "",
                ),
                "context_deduplicated": bool(context_write.deduplicated),
                "context_revision": (
                    context_write.context.revision
                    if context_write.context is not None
                    else None
                ),
            }
        )

        if (
            context_write.status != OperationStatus.ok
            or context_write.context is None
        ):
            return self._error_entry(
                decision=intent,
                question=normalized_question,
                conversation_id=normalized_conversation_id,
                request_id=normalized_request_id,
                request_user_field=normalized_user,
                handler_key=handler.handler_key,
                detail=context_write.detail or "canonical context write failed",
                context_metadata=context_metadata,
                context_read_status=read_status,
                context_write_status=context_write.status.value,
                context_recorded=False,
            )

        audit = build_audit_record(
            conversation_id=normalized_conversation_id,
            request_id=normalized_request_id,
            action=intent.action,
            handler_key=handler.handler_key,
            status="ok",
            side_effect_started=False,
            fallback_allowed=False,
            context_read_status=read_status,
            context_write_status=context_write.status.value,
            metadata={
                "handler_source": outcome.source,
                "handler_metadata": outcome.metadata,
                "context_deduplicated": bool(context_write.deduplicated),
                "context_revision": context_write.context.revision,
            },
        )
        audit_write = self._write_audit(audit)
        context_metadata.update(
            {
                "audit_write_status": audit_write.status.value,
                "audit_error_kind": (
                    audit_write.error_kind.value
                    if audit_write.error_kind is not None
                    else ""
                ),
                "audit_error": audit_write.detail,
                "audit_path": audit_write.path,
                "audit_ref": audit_write.audit_ref,
            }
        )
        if not audit_write.ok:
            return self._error_entry(
                decision=intent,
                question=normalized_question,
                conversation_id=normalized_conversation_id,
                request_id=normalized_request_id,
                request_user_field=normalized_user,
                handler_key=handler.handler_key,
                detail=audit_write.detail or "V4 audit write failed",
                context_metadata=context_metadata,
                context_read_status=read_status,
                context_write_status=context_write.status.value,
                context_recorded=True,
            )

        audit_ref_write = attach_audit_reference(
            self.store,
            conversation_id=normalized_conversation_id,
            audit_ref=audit_write.audit_ref,
            request_user_field=normalized_user,
        )
        context_metadata.update(
            {
                "audit_ref_status": audit_ref_write.status.value,
                "audit_ref_error_kind": (
                    audit_ref_write.error_kind.value
                    if audit_ref_write.error_kind is not None
                    else ""
                ),
                "audit_ref_error": audit_ref_write.detail,
                "context_revision": (
                    audit_ref_write.context.revision
                    if audit_ref_write.context is not None
                    else context_metadata.get("context_revision")
                ),
            }
        )
        if audit_ref_write.status != OperationStatus.ok:
            audit.status = "error"
            audit.context_write_status = audit_ref_write.status.value
            audit.metadata["audit_ref_error"] = audit_ref_write.detail
            self._write_audit(audit)
            return self._error_entry(
                decision=intent,
                question=normalized_question,
                conversation_id=normalized_conversation_id,
                request_id=normalized_request_id,
                request_user_field=normalized_user,
                handler_key=handler.handler_key,
                detail=audit_ref_write.detail or "audit reference write failed",
                context_metadata=context_metadata,
                context_read_status=read_status,
                context_write_status=audit_ref_write.status.value,
                context_recorded=True,
            )

        try:
            response = build_v4_response(
                question=normalized_question,
                conversation_id=normalized_conversation_id,
                decision=intent,
                outcome=outcome,
                audit_id=audit.audit_id,
                context_recorded=True,
            )
        except Exception as exc:
            audit.status = "error"
            audit.metadata["response_build_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
            self._write_audit(audit)
            return self._error_entry(
                decision=intent,
                question=normalized_question,
                conversation_id=normalized_conversation_id,
                request_id=normalized_request_id,
                request_user_field=normalized_user,
                handler_key=handler.handler_key,
                detail=f"response build error: {type(exc).__name__}: {exc}",
                context_metadata=context_metadata,
                context_read_status=read_status,
                context_write_status=audit_ref_write.status.value,
                context_recorded=True,
            )

        return build_handled_entry(
            decision=intent,
            response=response,
            audit=audit,
            context_metadata=context_metadata,
        )


def dispatch_low_risk_action(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    request_user_field: str,
    decision: Any,
    store: Optional[ContextStore] = None,
    audit_writer: Optional[AuditWriter] = None,
    llm_client: Any = None,
    allow_live_llm: bool = False,
    followup_loader: Optional[
        Callable[[str, Optional[str]], Dict[str, Any]]
    ] = None,
    legacy_loader: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> EntryResult:
    dispatcher = LowRiskActionDispatcher(
        store=store,
        audit_writer=audit_writer,
        llm_client=llm_client,
        allow_live_llm=allow_live_llm,
        followup_loader=followup_loader,
        legacy_loader=legacy_loader,
    )
    return dispatcher.dispatch(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id or str(uuid.uuid4()),
        request_user_field=request_user_field,
        decision=decision,
    )
