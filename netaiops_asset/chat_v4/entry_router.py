# -*- coding: utf-8 -*-
"""V4.2-3 pre-route entry router for low-risk actions.

The LLM Intent Arbiter is the only component that selects business action.
This module applies deterministic stage, confidence and context-availability
gates, then dispatches only V4.2 low-risk actions.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
import os
from typing import Any, Callable, Dict, Iterable, Optional
import uuid

from netaiops_asset.chat_v3.intent_arbiter import decide_intent
from netaiops_asset.chat_v3.intent_dispatcher import build_dispatch_plan
from netaiops_asset.chat_v3.intent_schema import (
    CONFIDENCE_ACCEPT_THRESHOLD,
    IntentAction,
    IntentDecision,
)
from netaiops_asset.chat_v4.action_dispatcher import (
    LOW_RISK_ACTIONS,
    LowRiskActionDispatcher,
)
from netaiops_asset.chat_v4.audit_adapter import build_audit_record
from netaiops_asset.chat_v4.audit_writer import AuditWriteResult, AuditWriter
from netaiops_asset.chat_v4.context_migration import build_canonical_from_legacy
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    CanonicalContext,
    EntryResult,
    EntryStatus,
    OperationStatus,
)

V4_ENTRY_ROUTER_VERSION = "v4.entry_router.2_3"
DEFAULT_ALLOWED_ACTIONS = frozenset(LOW_RISK_ACTIONS)
TECHNICAL_FALLBACK_REASONS = frozenset(
    {
        "llm_client_unavailable",
        "llm_call_failed",
    }
)


def _env_bool(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def _normalize_action(value: Any) -> IntentAction:
    if isinstance(value, IntentAction):
        return value
    return IntentAction(str(getattr(value, "value", value)))


def _parse_allowed_actions(
    value: Optional[Iterable[Any] | str],
) -> frozenset[IntentAction]:
    if value is None:
        raw_items: list[Any] = [
            item.strip()
            for item in str(
                os.getenv(
                    "NETAIOPS_V4_ENTRY_ALLOWED_ACTIONS",
                    ",".join(
                        action.value
                        for action in sorted(
                            DEFAULT_ALLOWED_ACTIONS,
                            key=lambda item: item.value,
                        )
                    ),
                )
                or ""
            ).split(",")
            if item.strip()
        ]
    elif isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raw_items = list(value)

    actions = frozenset(_normalize_action(item) for item in raw_items)
    invalid = actions - DEFAULT_ALLOWED_ACTIONS
    if invalid:
        raise ValueError(
            "V4.2-3 allowed actions must be a subset of low-risk actions: "
            + ",".join(sorted(item.value for item in invalid))
        )
    return actions


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        data = asdict(value)
        return dict(data) if isinstance(data, dict) else {}
    for method_name in ("model_dump", "as_dict", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                data = method()
            except TypeError:
                data = method(mode="python")
            if isinstance(data, dict):
                return dict(data)
    return {}


def _action_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(getattr(value, "value", value))


def canonical_to_followup_context(
    context: Optional[CanonicalContext],
    *,
    original_conversation_id: str,
    source: str,
    context_error: str = "",
) -> Dict[str, Any]:
    """Convert canonical state into the existing Arbiter context shape."""

    if context is None:
        arbiter_context = {
            "original_conversation_id": str(original_conversation_id or ""),
            "followup_context_available": False,
            "followup_context_source": source or "none",
            "followup_context_turn_count": 0,
            "current_device": {},
            "current_topic": "",
            "current_intent": "",
            "active_focus": {},
            "pending_commands": [],
            "last_command_suggestions": [],
            "last_executions": [],
            "last_audit_path": "",
            "rolling_summary": "",
            "recent_turns": [],
            "last_analysis": None,
            "last_bulk_analysis": None,
            "last_followup_analysis": None,
            "last_prometheus_evidence": None,
            "context_error": context_error,
        }
        generator_context = dict(arbiter_context)
        generator_context.update(
            {
                "has_execution_evidence": False,
                "context_sources": [source] if source and source != "none" else [],
            }
        )
        return {
            "available": False,
            "source": source or "none",
            "turn_count": 0,
            "topic": "",
            "has_execution_evidence": False,
            "original_conversation_id": str(original_conversation_id or ""),
            "arbiter_context": arbiter_context,
            "generator_context": generator_context,
            "context_error": context_error,
        }

    turns = [
        item.model_dump(mode="json")
        for item in context.recent_turns[-6:]
    ]
    current_intent = _action_value(context.last_intent.get("action"))
    execution_evidence = deepcopy(context.execution_evidence[-20:])
    analysis_history = deepcopy(context.analysis_history[-20:])
    last_analysis = analysis_history[-1] if analysis_history else None
    available = bool(
        turns
        or context.device_context
        or context.topic
        or context.rolling_summary
        or execution_evidence
        or analysis_history
    )
    arbiter_context = {
        "original_conversation_id": str(original_conversation_id or ""),
        "followup_context_available": available,
        "followup_context_source": source,
        "followup_context_turn_count": len(context.recent_turns),
        "current_device": deepcopy(context.device_context),
        "current_topic": context.topic,
        "current_intent": current_intent,
        "active_focus": {
            "device": deepcopy(context.device_context),
            "topic": context.topic,
            "intent": current_intent,
        },
        "pending_commands": deepcopy(context.pending.get("commands") or []),
        "last_command_suggestions": [],
        "last_executions": execution_evidence,
        "last_audit_path": context.audit_refs[-1] if context.audit_refs else "",
        "rolling_summary": context.rolling_summary,
        "recent_turns": turns,
        "last_analysis": last_analysis,
        "last_bulk_analysis": None,
        "last_followup_analysis": None,
        "last_prometheus_evidence": None,
        "context_error": context_error,
    }
    generator_context = deepcopy(arbiter_context)
    generator_context.update(
        {
            "has_execution_evidence": bool(execution_evidence),
            "context_sources": [source] if source else [],
        }
    )
    return {
        "available": available,
        "source": source,
        "turn_count": len(context.recent_turns),
        "topic": context.topic,
        "has_execution_evidence": bool(execution_evidence),
        "original_conversation_id": str(original_conversation_id or ""),
        "arbiter_context": arbiter_context,
        "generator_context": generator_context,
        "context_error": context_error,
    }


@dataclass
class EntryRouteResult:
    enabled: bool
    handled: bool
    fallback: bool
    reason: str
    request_id: str = ""
    action: str = ""
    original_conversation_id: str = ""
    effective_conversation_id: str = ""
    decision: Optional[IntentDecision] = None
    plan: Any = None
    followup_context: Dict[str, Any] = None  # type: ignore[assignment]
    entry_result: Optional[EntryResult] = None
    audit_write_status: str = ""
    audit_path: str = ""
    audit_error: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if self.followup_context is None:
            self.followup_context = {}

    @property
    def shadow_state(self) -> Dict[str, Any]:
        if self.decision is None:
            return {}
        return {
            "enabled": True,
            "decision": self.decision,
            "plan": self.plan,
            "followup_context": deepcopy(self.followup_context),
            "error": self.error,
            "v4_entry_router": {
                "version": V4_ENTRY_ROUTER_VERSION,
                "handled": self.handled,
                "fallback": self.fallback,
                "reason": self.reason,
                "request_id": self.request_id,
                "action": self.action,
            },
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "handled": self.handled,
            "fallback": self.fallback,
            "reason": self.reason,
            "request_id": self.request_id,
            "action": self.action,
            "original_conversation_id": self.original_conversation_id,
            "effective_conversation_id": self.effective_conversation_id,
            "decision": _as_dict(self.decision),
            "plan": _as_dict(self.plan),
            "followup_context": deepcopy(self.followup_context),
            "entry_result": _as_dict(self.entry_result),
            "audit_write_status": self.audit_write_status,
            "audit_path": self.audit_path,
            "audit_error": self.audit_error,
            "error": self.error,
        }


class V4EntryRouter:
    """Route low-risk actions before legacy V2/V3 business branches."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        allowed_actions: Optional[Iterable[Any] | str] = None,
        allow_live_llm: Optional[bool] = None,
        min_confidence: Optional[float] = None,
        llm_client: Any = None,
        store: Optional[ContextStore] = None,
        audit_writer: Optional[AuditWriter] = None,
        arbiter: Callable[..., IntentDecision] = decide_intent,
        plan_builder: Callable[..., Any] = build_dispatch_plan,
        dispatcher: Optional[LowRiskActionDispatcher] = None,
        legacy_builder: Callable[..., Any] = build_canonical_from_legacy,
    ) -> None:
        self.enabled = (
            _env_bool("NETAIOPS_V4_ENTRY_ENABLED", "0")
            if enabled is None
            else bool(enabled)
        )
        self.allowed_actions = _parse_allowed_actions(allowed_actions)
        self.allow_live_llm = (
            _env_bool("NETAIOPS_V4_ENTRY_LIVE_LLM", "0")
            if allow_live_llm is None
            else bool(allow_live_llm)
        )
        self.min_confidence = (
            _env_float(
                "NETAIOPS_V4_ENTRY_MIN_CONFIDENCE",
                CONFIDENCE_ACCEPT_THRESHOLD,
            )
            if min_confidence is None
            else float(min_confidence)
        )
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")

        self.llm_client = llm_client
        self.store = store or ContextStore()
        self.audit_writer = audit_writer or AuditWriter()
        self.arbiter = arbiter
        self.plan_builder = plan_builder
        self.legacy_builder = legacy_builder
        self.dispatcher = dispatcher or LowRiskActionDispatcher(
            store=self.store,
            audit_writer=self.audit_writer,
            llm_client=self.llm_client,
            allow_live_llm=self.allow_live_llm,
        )

    def _read_context_snapshot(
        self,
        conversation_id: str,
        request_user_field: str,
    ) -> Dict[str, Any]:
        normalized_id = str(conversation_id or "").strip()
        if not normalized_id:
            return canonical_to_followup_context(
                None,
                original_conversation_id="",
                source="none",
            )

        loaded = self.store.load(
            normalized_id,
            quarantine_invalid=False,
        )
        if loaded.status == OperationStatus.ok and loaded.context is not None:
            return canonical_to_followup_context(
                loaded.context,
                original_conversation_id=normalized_id,
                source="v4_canonical_context",
            )

        if loaded.status == OperationStatus.error:
            return canonical_to_followup_context(
                None,
                original_conversation_id=normalized_id,
                source="v4_context_error",
                context_error=loaded.detail or "canonical context read failed",
            )

        migrated = self.legacy_builder(
            normalized_id,
            request_user_field or None,
        )
        if (
            getattr(migrated, "status", None) == OperationStatus.ok
            and getattr(migrated, "context", None) is not None
        ):
            return canonical_to_followup_context(
                migrated.context,
                original_conversation_id=normalized_id,
                source="legacy_readonly_snapshot",
            )
        if getattr(migrated, "status", None) == OperationStatus.error:
            return canonical_to_followup_context(
                None,
                original_conversation_id=normalized_id,
                source="legacy_context_error",
                context_error=str(getattr(migrated, "detail", "") or ""),
            )
        return canonical_to_followup_context(
            None,
            original_conversation_id=normalized_id,
            source="none",
        )

    @staticmethod
    def _effective_confidence(decision: IntentDecision) -> float:
        raw = decision.metadata.get(
            "effective_confidence",
            decision.confidence,
        )
        try:
            return max(0.0, min(1.0, float(raw)))
        except Exception:
            return max(0.0, min(1.0, float(decision.confidence)))

    def _clarification_reason(
        self,
        decision: IntentDecision,
        followup_context: Dict[str, Any],
    ) -> str:
        if decision.action == IntentAction.need_clarification:
            return decision.reason or "arbiter_requires_clarification"

        if self._effective_confidence(decision) < self.min_confidence:
            return "effective_confidence_below_v4_accept_threshold"

        arbiter_context = dict(
            followup_context.get("arbiter_context") or {}
        )
        current_device = arbiter_context.get("current_device") or {}
        if (
            decision.device_required
            and not str(decision.device_hint or "").strip()
            and not current_device
        ):
            return "required_device_missing"

        if (
            decision.need_existing_evidence
            and not bool(followup_context.get("has_execution_evidence"))
        ):
            return "required_execution_evidence_missing"

        if str(followup_context.get("context_error") or "").strip():
            return "canonical_context_unavailable"

        return ""

    def _to_clarification(
        self,
        decision: IntentDecision,
        *,
        question: str,
        reason: str,
    ) -> IntentDecision:
        metadata = deepcopy(decision.metadata)
        metadata.update(
            {
                "v4_original_action": decision.action.value,
                "v4_original_confidence": float(decision.confidence),
                "v4_effective_confidence": self._effective_confidence(decision),
                "v4_clarification_reason": reason,
                "v4_entry_router_version": V4_ENTRY_ROUTER_VERSION,
            }
        )
        clarification_question = str(
            decision.clarification_question or ""
        ).strip()
        if not clarification_question:
            if reason == "required_device_missing":
                clarification_question = (
                    "请补充需要处理的设备名称或管理 IP，以及具体目标。"
                )
            elif reason == "required_execution_evidence_missing":
                clarification_question = (
                    "当前没有可供继续分析的执行证据，请先提供输出或说明要分析的上一轮结果。"
                )
            elif reason == "canonical_context_unavailable":
                clarification_question = (
                    "当前会话上下文暂时不可用，请重新说明设备、目标和必要背景。"
                )
            else:
                clarification_question = (
                    "当前意图置信度不足，请补充更明确的设备、目标或操作意图。"
                )
        return IntentDecision(
            action=IntentAction.need_clarification,
            confidence=self._effective_confidence(decision),
            device_required=False,
            device_hint="",
            commands_provided=False,
            commands=[],
            need_existing_evidence=False,
            should_generate_commands=False,
            should_execute_commands=False,
            should_analyze_after_execution=False,
            requires_confirmation=False,
            clarification_question=clarification_question,
            reason=reason,
            raw_user_text=str(question or ""),
            context_summary=decision.context_summary,
            metadata=metadata,
        )

    def _write_stage_audit(
        self,
        *,
        conversation_id: str,
        request_id: str,
        decision: IntentDecision,
        status: str,
        fallback_allowed: bool,
        fallback_reason: str,
        metadata: Dict[str, Any],
    ) -> AuditWriteResult:
        audit = build_audit_record(
            conversation_id=str(conversation_id or ""),
            request_id=request_id,
            action=decision.action,
            handler_key="",
            status=status,
            side_effect_started=False,
            fallback_allowed=fallback_allowed,
            fallback_reason=fallback_reason,
            context_read_status="read_only_snapshot",
            context_write_status="not_attempted",
            metadata={
                "stage": "v4.2-3",
                "entry_router_version": V4_ENTRY_ROUTER_VERSION,
                **metadata,
            },
        )
        return self.audit_writer.write(audit)

    def route(
        self,
        *,
        question: str,
        request_user_field: str,
        conversation_id: str = "",
        conversation_id_factory: Optional[
            Callable[[str, str], str]
        ] = None,
    ) -> EntryRouteResult:
        normalized_question = str(question or "").strip()
        normalized_user = str(request_user_field or "").strip()
        original_id = str(conversation_id or "").strip()

        if not self.enabled:
            return EntryRouteResult(
                enabled=False,
                handled=False,
                fallback=True,
                reason="v4_entry_disabled",
                original_conversation_id=original_id,
            )

        followup_context = self._read_context_snapshot(
            original_id,
            normalized_user,
        )
        arbiter_context = dict(
            followup_context.get("arbiter_context") or {}
        )
        decision = self.arbiter(
            question=normalized_question,
            context=arbiter_context,
            user=normalized_user or None,
            conversation_id=original_id or None,
            llm_client=self.llm_client,
        )
        if not isinstance(decision, IntentDecision):
            decision = IntentDecision.model_validate(decision)

        request_id = str(
            decision.metadata.get("request_id") or uuid.uuid4()
        )
        technical_reason = str(decision.reason or "")
        plan = self.plan_builder(
            question=normalized_question,
            decision=decision,
            context=arbiter_context,
            user=normalized_user or None,
            conversation_id=original_id or None,
        )

        if technical_reason in TECHNICAL_FALLBACK_REASONS:
            audit_write = self._write_stage_audit(
                conversation_id=original_id,
                request_id=request_id,
                decision=decision,
                status="fallback",
                fallback_allowed=True,
                fallback_reason=technical_reason,
                metadata={"technical_fallback": True},
            )
            return EntryRouteResult(
                enabled=True,
                handled=False,
                fallback=True,
                reason=technical_reason,
                request_id=request_id,
                action=decision.action.value,
                original_conversation_id=original_id,
                decision=decision,
                plan=plan,
                followup_context=followup_context,
                audit_write_status=audit_write.status.value,
                audit_path=audit_write.path,
                audit_error=audit_write.detail,
            )

        clarification_reason = self._clarification_reason(
            decision,
            followup_context,
        )
        if clarification_reason:
            decision = self._to_clarification(
                decision,
                question=normalized_question,
                reason=clarification_reason,
            )
            plan = self.plan_builder(
                question=normalized_question,
                decision=decision,
                context=arbiter_context,
                user=normalized_user or None,
                conversation_id=original_id or None,
            )

        if decision.action not in self.allowed_actions:
            audit_write = self._write_stage_audit(
                conversation_id=original_id,
                request_id=request_id,
                decision=decision,
                status="fallback",
                fallback_allowed=True,
                fallback_reason="action_not_enabled_in_v4_2_3",
                metadata={
                    "allowed_actions": sorted(
                        item.value for item in self.allowed_actions
                    )
                },
            )
            return EntryRouteResult(
                enabled=True,
                handled=False,
                fallback=True,
                reason="action_not_enabled_in_v4_2_3",
                request_id=request_id,
                action=decision.action.value,
                original_conversation_id=original_id,
                decision=decision,
                plan=plan,
                followup_context=followup_context,
                audit_write_status=audit_write.status.value,
                audit_path=audit_write.path,
                audit_error=audit_write.detail,
            )

        if conversation_id_factory is None:
            return EntryRouteResult(
                enabled=True,
                handled=False,
                fallback=True,
                reason="conversation_id_factory_missing",
                request_id=request_id,
                action=decision.action.value,
                original_conversation_id=original_id,
                decision=decision,
                plan=plan,
                followup_context=followup_context,
                error="conversation_id_factory is required for handled actions",
            )

        try:
            effective_id = str(
                conversation_id_factory(original_id, normalized_user) or ""
            ).strip()
        except Exception as exc:
            return EntryRouteResult(
                enabled=True,
                handled=False,
                fallback=True,
                reason="conversation_id_factory_failed",
                request_id=request_id,
                action=decision.action.value,
                original_conversation_id=original_id,
                decision=decision,
                plan=plan,
                followup_context=followup_context,
                error=f"{type(exc).__name__}: {exc}",
            )
        if not effective_id:
            return EntryRouteResult(
                enabled=True,
                handled=False,
                fallback=True,
                reason="conversation_id_factory_empty",
                request_id=request_id,
                action=decision.action.value,
                original_conversation_id=original_id,
                decision=decision,
                plan=plan,
                followup_context=followup_context,
                error="conversation_id_factory returned an empty id",
            )

        entry = self.dispatcher.dispatch(
            question=normalized_question,
            conversation_id=effective_id,
            request_id=request_id,
            request_user_field=normalized_user,
            decision=decision,
        )
        handled = entry.status in {
            EntryStatus.handled,
            EntryStatus.clarification,
            EntryStatus.error,
        }
        return EntryRouteResult(
            enabled=True,
            handled=handled,
            fallback=entry.status == EntryStatus.fallback,
            reason=(
                "v4_low_risk_entry_handled"
                if handled
                else entry.fallback_reason or "dispatcher_fallback"
            ),
            request_id=request_id,
            action=decision.action.value,
            original_conversation_id=original_id,
            effective_conversation_id=effective_id,
            decision=decision,
            plan=plan,
            followup_context=followup_context,
            entry_result=entry,
        )


def route_v4_entry(
    *,
    question: str,
    request_user_field: str,
    conversation_id: str = "",
    conversation_id_factory: Optional[
        Callable[[str, str], str]
    ] = None,
    router: Optional[V4EntryRouter] = None,
) -> EntryRouteResult:
    active_router = router or V4EntryRouter()
    return active_router.route(
        question=question,
        request_user_field=request_user_field,
        conversation_id=conversation_id,
        conversation_id_factory=conversation_id_factory,
    )
