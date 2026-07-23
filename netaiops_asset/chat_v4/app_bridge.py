# -*- coding: utf-8 -*-
"""FastAPI/legacy-history bridge for the V4.3-1 pre-route entry router.

This module adapts V4 EntryResult to the existing frontend payload and keeps the
legacy conversation list readable. It does not choose action from user text.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Optional
import uuid

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.audit_adapter import build_audit_record
from netaiops_asset.chat_v4.audit_writer import AuditWriter
from netaiops_asset.chat_v4.contracts import EntryStatus
from netaiops_asset.chat_v4.entry_router import (
    EntryRouteResult,
    V4_ENTRY_ROUTER_VERSION,
    V4EntryRouter,
    route_v4_entry,
)

PUBLIC_HISTORY_ERROR = (
    "V4 已完成意图与响应处理，但历史会话同步失败。"
    "本次结果已记录到 V4 审计，请保留请求信息后重试。"
)


def _serialize_model(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return deepcopy(value)
    method = getattr(value, "model_dump", None)
    if callable(method):
        data = method(mode="json")
        return deepcopy(data) if isinstance(data, dict) else {}
    method = getattr(value, "as_dict", None)
    if callable(method):
        data = method()
        return deepcopy(data) if isinstance(data, dict) else {}
    return {}


def _ensure_conversation_id(
    original_conversation_id: str,
    request_user_field: str,
    *,
    get_conversation_fn: Callable[[str], Any],
    create_conversation_fn: Callable[..., Dict[str, Any]],
) -> str:
    original = str(original_conversation_id or "").strip()
    if original:
        existing = get_conversation_fn(original)
        if isinstance(existing, dict) and str(
            existing.get("conversation_id") or ""
        ).strip() == original:
            return original

    created = create_conversation_fn(
        title="新对话",
        user=request_user_field or "web_user",
    )
    conversation_id = str(
        (created or {}).get("conversation_id") or ""
    ).strip()
    if not conversation_id:
        raise ValueError("create_conversation returned an empty conversation_id")
    return conversation_id


def _mark_audit_history_error(
    route_result: EntryRouteResult,
    *,
    detail: str,
    audit_writer: AuditWriter,
) -> Dict[str, Any]:
    entry = route_result.entry_result
    if entry is None or entry.audit is None:
        return {
            "status": "not_available",
            "detail": "entry audit is missing",
        }

    audit = entry.audit.model_copy(deep=True)
    audit.status = "error"
    audit.metadata["legacy_history_sync_error"] = detail
    audit.metadata["legacy_history_recorded"] = False
    write_result = audit_writer.write(audit)
    return {
        "status": write_result.status.value,
        "detail": write_result.detail,
        "path": write_result.path,
        "error_kind": (
            write_result.error_kind.value
            if write_result.error_kind is not None
            else ""
        ),
    }


def _build_transport_payload(
    route_result: EntryRouteResult,
) -> Dict[str, Any]:
    entry = route_result.entry_result
    if entry is None or entry.response is None:
        return {
            "status": "error",
            "answer": (
                "V4 Entry Router 已接管本次请求，但没有生成可返回的响应。"
            ),
            "items": [],
            "columns": [],
            "field_labels": {},
            "count": 0,
            "returned": 0,
            "conversation_id": route_result.effective_conversation_id,
            "question": "",
            "action": route_result.action,
            "planner_source": "v4_intent_arbiter",
            "request_id": route_result.request_id,
            "v4_pre_route": True,
            "v4_entry_status": "error",
            "v4_entry_reason": "missing_entry_response",
        }

    payload = _serialize_model(entry.response)
    payload["request_id"] = route_result.request_id
    payload["conversation_id"] = route_result.effective_conversation_id
    payload["v4_pre_route"] = True
    payload["v4_entry_status"] = entry.status.value
    payload["v4_entry_reason"] = route_result.reason
    payload["v4_original_conversation_id"] = (
        route_result.original_conversation_id
    )
    payload["v4_effective_conversation_id"] = (
        route_result.effective_conversation_id
    )
    payload["v4_entry_router_version"] = V4_ENTRY_ROUTER_VERSION
    payload["v4_fallback_used"] = False
    v4_meta = payload.get("v4")
    if not isinstance(v4_meta, dict):
        v4_meta = {}
        payload["v4"] = v4_meta
    v4_meta.update(
        {
            "entry_status": entry.status.value,
            "entry_router_version": V4_ENTRY_ROUTER_VERSION,
            "legacy_history_recorded": False,
        }
    )
    return payload


PUBLIC_INTERNAL_ERROR = (
    "V4 Entry Router 内部处理失败，本次请求未回退到旧路由。"
    "错误已尽可能记录，请保留请求信息后联系管理员。"
)


def build_v4_internal_error_transport(
    *,
    question: str,
    request_user_field: str,
    conversation_id: str,
    detail: str,
    audit_writer: Optional[AuditWriter] = None,
) -> Dict[str, Any]:
    """Build a visible V4 error without re-entering the legacy business route."""

    request_id = str(uuid.uuid4())
    writer = audit_writer or AuditWriter()
    audit = build_audit_record(
        conversation_id=str(conversation_id or "").strip(),
        request_id=request_id,
        action=IntentAction.need_clarification,
        handler_key="v4_entry_router_internal_error",
        status="error",
        side_effect_started=False,
        fallback_allowed=False,
        context_read_status="unknown",
        context_write_status="not_attempted",
        metadata={
            "stage": "v4.3-1",
            "internal_error": str(detail or "")[:2000],
            "request_user_field": str(request_user_field or ""),
            "action_available": False,
        },
    )
    audit_write = writer.write(audit)
    payload = {
        "status": "error",
        "answer": PUBLIC_INTERNAL_ERROR,
        "items": [],
        "count": 0,
        "returned": 0,
        "columns": [],
        "field_labels": {},
        "conversation_id": str(conversation_id or "").strip(),
        "question": str(question or ""),
        "action": IntentAction.need_clarification.value,
        "planner_source": "v4_entry_router",
        "request_id": request_id,
        "v4_pre_route": True,
        "v4_entry_status": EntryStatus.error.value,
        "v4_entry_reason": "v4_entry_router_internal_error",
        "v4_fallback_used": False,
        "v4_original_conversation_id": str(conversation_id or "").strip(),
        "v4_effective_conversation_id": str(conversation_id or "").strip(),
        "v4_entry_router_version": V4_ENTRY_ROUTER_VERSION,
        "v4": {
            "schema_version": "v4.response.v1",
            "handler_key": "v4_entry_router_internal_error",
            "confidence": 0.0,
            "side_effect_started": False,
            "fallback_used": False,
            "audit_id": audit.audit_id if audit_write.ok else "",
            "context_recorded": False,
            "entry_status": EntryStatus.error.value,
            "entry_router_version": V4_ENTRY_ROUTER_VERSION,
            "legacy_history_recorded": False,
            "audit_write_status": audit_write.status.value,
            "audit_error_kind": (
                audit_write.error_kind.value
                if audit_write.error_kind is not None
                else ""
            ),
        },
    }
    return {
        "handled": True,
        "response": payload,
        "shadow_state": {},
        "route": {
            "enabled": True,
            "handled": True,
            "fallback": False,
            "reason": "v4_entry_router_internal_error",
            "request_id": request_id,
            "action": IntentAction.need_clarification.value,
            "error": str(detail or "")[:2000],
        },
        "history_error": "",
    }


def try_handle_v4_pre_route(
    *,
    question: str,
    request_user_field: str,
    conversation_id: str,
    get_conversation_fn: Callable[[str], Any],
    create_conversation_fn: Callable[..., Dict[str, Any]],
    append_turn_fn: Callable[..., Any],
    router: Optional[V4EntryRouter] = None,
) -> Dict[str, Any]:
    """Return a transport dict consumed by the existing middleware."""

    active_router = router or V4EntryRouter()

    def conversation_id_factory(
        original_id: str,
        user: str,
    ) -> str:
        return _ensure_conversation_id(
            original_id,
            user,
            get_conversation_fn=get_conversation_fn,
            create_conversation_fn=create_conversation_fn,
        )

    route_result = route_v4_entry(
        question=question,
        request_user_field=request_user_field,
        conversation_id=conversation_id,
        conversation_id_factory=conversation_id_factory,
        router=active_router,
    )

    if not route_result.handled:
        return {
            "handled": False,
            "response": None,
            "shadow_state": route_result.shadow_state,
            "route": route_result.as_dict(),
        }

    payload = _build_transport_payload(route_result)
    effective_id = str(
        route_result.effective_conversation_id or ""
    ).strip()
    history_error = ""
    try:
        appended_id, _turn = append_turn_fn(
            effective_id,
            str(question or ""),
            payload,
            user=request_user_field or None,
        )
        appended_id = str(appended_id or "").strip()
        if appended_id != effective_id:
            raise RuntimeError(
                "legacy history conversation_id mismatch: "
                f"expected={effective_id}, actual={appended_id}"
            )
        payload["conversation_id"] = appended_id
        payload["v4_effective_conversation_id"] = appended_id
        payload.setdefault("v4", {})[
            "legacy_history_recorded"
        ] = True
        payload["v4_legacy_history_recorded"] = True
    except Exception as exc:
        history_error = f"{type(exc).__name__}: {exc}"
        audit_update = _mark_audit_history_error(
            route_result,
            detail=history_error,
            audit_writer=active_router.audit_writer,
        )
        payload["status"] = "error"
        payload["answer"] = PUBLIC_HISTORY_ERROR
        payload["v4_entry_status"] = EntryStatus.error.value
        payload["v4_legacy_history_recorded"] = False
        payload["v4_legacy_history_error"] = history_error
        payload["v4_legacy_history_audit_update"] = audit_update
        payload.setdefault("v4", {}).update(
            {
                "legacy_history_recorded": False,
                "legacy_history_error": history_error,
            }
        )

    return {
        "handled": True,
        "response": payload,
        "shadow_state": {},
        "route": route_result.as_dict(),
        "history_error": history_error,
    }
