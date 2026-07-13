# -*- coding: utf-8 -*-
"""Read-only legacy context adapter and lazy V4 migration.

Legacy stores are read but never deleted or modified by this module.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import uuid
from typing import Any, Callable, Dict, Optional

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    CanonicalContext,
    ContextErrorKind,
    ContextMigration,
    ContextOperationResult,
    ContextTurn,
    OperationStatus,
    utc_now,
)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _default_followup_loader(
    conversation_id: str,
    user: Optional[str],
) -> Dict[str, Any]:
    from netaiops_asset.chat_v3.followup_context import build_followup_context

    return _safe_dict(build_followup_context(conversation_id, user=user))


def _default_legacy_loader(conversation_id: str) -> Dict[str, Any]:
    from netaiops_asset.agent.conversation_store import get_conversation

    return _safe_dict(get_conversation(conversation_id))


def _normalize_action(value: Any) -> Optional[IntentAction]:
    if value in (None, ""):
        return None
    try:
        return IntentAction(str(getattr(value, "value", value)))
    except ValueError:
        return None


def _turn_from_value(value: Any) -> Optional[ContextTurn]:
    data = _safe_dict(value)
    response = _safe_dict(data.get("response"))
    question = str(data.get("question") or "").strip()
    answer = str(
        data.get("answer_summary")
        or response.get("answer")
        or response.get("message")
        or ""
    ).strip()
    if not question and not answer:
        return None
    return ContextTurn(
        turn_id=str(data.get("turn_id") or "") or str(uuid.uuid4()),
        turn_fingerprint=str(data.get("turn_fingerprint") or ""),
        created_at=str(
            data.get("time")
            or data.get("created_at")
            or utc_now()
        ),
        question=ContextStore._truncate(question, 2000),
        answer_summary=ContextStore._truncate(answer, 4000),
        action=_normalize_action(
            data.get("action")
            or response.get("v3_takeover_action")
            or response.get("action")
        ),
        planner_source=str(
            data.get("planner_source")
            or response.get("planner_source")
            or ""
        ),
        route_label=str(
            data.get("route_label")
            or response.get("v3_takeover_route_label")
            or ""
        ),
        effective_conversation_id=str(
            data.get("effective_conversation_id")
            or response.get("conversation_id")
            or ""
        ),
        record_source=str(data.get("record_source") or "legacy_migration"),
    )


def build_canonical_from_legacy(
    conversation_id: str,
    user: Optional[str] = None,
    *,
    followup_loader: Callable[[str, Optional[str]], Dict[str, Any]] = _default_followup_loader,
    legacy_loader: Callable[[str], Dict[str, Any]] = _default_legacy_loader,
) -> ContextOperationResult:
    try:
        normalized_id = ContextStore._normalize_conversation_id(conversation_id)
        followup = _safe_dict(followup_loader(normalized_id, user))
        legacy = _safe_dict(legacy_loader(normalized_id))
    except Exception as exc:
        return ContextOperationResult(
            status=OperationStatus.error,
            error_kind=ContextErrorKind.migration,
            detail=f"legacy context load error: {type(exc).__name__}: {exc}",
        )

    sources: list[str] = []
    followup_source = str(followup.get("source") or "").strip()
    if followup and (
        followup.get("available")
        or followup.get("turn_count")
        or followup_source not in {"", "none"}
    ):
        sources.append("v3_followup_context_bridge")
    if legacy:
        sources.append("legacy_conversation_store")

    if not sources:
        return ContextOperationResult(
            status=OperationStatus.not_found,
            detail="no legacy context source available",
        )

    arbiter = _safe_dict(followup.get("arbiter_context"))
    generator = _safe_dict(followup.get("generator_context"))
    legacy_turns = _safe_list(legacy.get("turns"))
    bridge_turns = _safe_list(arbiter.get("recent_turns"))

    turns: list[ContextTurn] = []
    seen: set[str] = set()
    seen_semantic: set[str] = set()
    for raw in legacy_turns + bridge_turns:
        turn = _turn_from_value(raw)
        if turn is None or turn.turn_fingerprint in seen:
            continue
        semantic_key = hashlib.sha256(
            json.dumps(
                {
                    "question": turn.question,
                    "answer_summary": turn.answer_summary,
                    "action": turn.action.value if turn.action else "",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if semantic_key in seen_semantic:
            continue
        seen.add(turn.turn_fingerprint)
        seen_semantic.add(semantic_key)
        turns.append(turn)

    current_device = _safe_dict(
        arbiter.get("current_device")
        or generator.get("current_device")
    )
    current_intent = arbiter.get("current_intent")
    last_intent: Dict[str, Any] = {}
    if current_intent:
        last_intent = {
            "action": str(getattr(current_intent, "value", current_intent)),
            "source": "legacy_context",
        }

    execution_evidence = []
    for item in _safe_list(arbiter.get("last_executions")):
        if isinstance(item, dict):
            execution_evidence.append(deepcopy(item))
    prometheus = arbiter.get("last_prometheus_evidence")
    if prometheus:
        execution_evidence.append({
            "type": "prometheus",
            "value": deepcopy(prometheus),
        })

    analysis_history = []
    for key in (
        "last_analysis",
        "last_bulk_analysis",
        "last_followup_analysis",
    ):
        value = arbiter.get(key)
        if value:
            analysis_history.append({
                "type": key,
                "value": deepcopy(value),
            })

    original_id = str(
        followup.get("original_conversation_id")
        or normalized_id
    )
    effective_id = normalized_id
    if turns and turns[-1].effective_conversation_id:
        effective_id = turns[-1].effective_conversation_id

    context = CanonicalContext(
        conversation_id=normalized_id,
        request_user_field=str(user or legacy.get("user") or ""),
        title=str(legacy.get("title") or ""),
        created_at=str(legacy.get("created_at") or utc_now()),
        updated_at=str(legacy.get("updated_at") or utc_now()),
        device_context=ContextStore.sanitize_value(current_device),
        topic=str(
            arbiter.get("current_topic")
            or generator.get("current_topic")
            or ""
        ),
        rolling_summary=str(arbiter.get("rolling_summary") or ""),
        recent_turns=turns[-30:],
        last_intent=ContextStore.sanitize_value(last_intent),
        execution_evidence=ContextStore.sanitize_value(
            execution_evidence[-20:]
        ),
        analysis_history=ContextStore.sanitize_value(
            analysis_history[-20:]
        ),
        migration=ContextMigration(
            status="migrated",
            migrated_at=utc_now(),
            sources=sources,
            source_versions={
                "v3_followup_context_bridge": str(followup_source or "none"),
                "legacy_conversation_store": "legacy",
            },
            original_conversation_id=original_id,
            effective_conversation_id=effective_id,
            notes=["legacy stores were read only and were not deleted"],
        ),
    )
    return ContextOperationResult(
        status=OperationStatus.ok,
        context=context,
        migrated=True,
        metadata={"sources": sources},
    )


def load_or_migrate(
    store: ContextStore,
    conversation_id: str,
    user: Optional[str] = None,
    *,
    followup_loader: Callable[[str, Optional[str]], Dict[str, Any]] = _default_followup_loader,
    legacy_loader: Callable[[str], Dict[str, Any]] = _default_legacy_loader,
) -> ContextOperationResult:
    loaded = store.load(conversation_id)
    if loaded.status == OperationStatus.ok:
        return loaded
    if loaded.status == OperationStatus.error:
        return loaded

    migrated = build_canonical_from_legacy(
        conversation_id,
        user,
        followup_loader=followup_loader,
        legacy_loader=legacy_loader,
    )
    if migrated.status != OperationStatus.ok or migrated.context is None:
        return migrated

    saved = store.save(migrated.context, expected_revision=0)
    if saved.status != OperationStatus.ok:
        if saved.error_kind == ContextErrorKind.conflict:
            concurrent = store.load(conversation_id)
            if concurrent.status == OperationStatus.ok:
                concurrent.migrated = False
                concurrent.metadata["migration_race_resolved"] = True
                return concurrent
        saved.error_kind = saved.error_kind or ContextErrorKind.migration
        return saved
    saved.migrated = True
    saved.metadata.update(migrated.metadata)
    return saved
