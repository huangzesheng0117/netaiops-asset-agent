# -*- coding: utf-8 -*-
"""Deterministic V4 audit contract adapter."""

from __future__ import annotations

from typing import Any, Dict, Optional

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    ContextOperationResult,
    V4AuditRecord,
)


def build_audit_record(
    *,
    conversation_id: str,
    request_id: str,
    action: IntentAction,
    handler_key: str,
    status: str,
    side_effect_started: bool = False,
    fallback_allowed: bool = False,
    fallback_reason: str = "",
    context_read_status: str = "",
    context_write_status: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> V4AuditRecord:
    return V4AuditRecord(
        conversation_id=conversation_id,
        request_id=request_id,
        action=action,
        handler_key=handler_key,
        status=status,
        side_effect_started=side_effect_started,
        fallback_allowed=fallback_allowed,
        fallback_reason=fallback_reason,
        context_read_status=context_read_status,
        context_write_status=context_write_status,
        metadata=ContextStore.sanitize_value(metadata or {}),
    )


def attach_audit_reference(
    store: ContextStore,
    *,
    conversation_id: str,
    audit_ref: str,
    request_user_field: str = "",
) -> ContextOperationResult:
    return store.add_audit_ref(
        conversation_id,
        audit_ref,
        request_user_field=request_user_field,
    )
