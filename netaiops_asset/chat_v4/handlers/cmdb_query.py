# -*- coding: utf-8 -*-
"""Deterministic V4.3-1 read-only CMDB handler.

Business action selection belongs exclusively to the LLM Intent Arbiter. This
handler consumes the structured CmdbQuerySpec already present in IntentDecision,
validates fields and filters, and delegates to the existing read-only CMDB tools.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.handlers.base import HandlerOutcome, HandlerRequest
from netaiops_asset.cmdb.field_map import (
    CMDB_FIELDS,
    DEFAULT_FIELDS,
    DETAIL_FIELDS,
    QUERY_FILTER_FIELDS,
    SENSITIVE_FIELDS,
    field_labels,
    normalize_field_name,
)
from netaiops_asset.tools.cmdb_tools import (
    tool_query_cmdb_device_detail,
    tool_query_cmdb_devices,
    tool_query_cmdb_devices_by_ips,
)

_ALLOWED_FILTER_OPERATORS = {
    "exact",
    "eq",
    "icontains",
    "contains",
    "in",
    "startswith",
    "endswith",
}


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


class CmdbQueryHandler:
    """Execute only structured, read-only CMDB queries."""

    action = IntentAction.cmdb_query
    handler_key = "cmdb_query"

    def __init__(
        self,
        *,
        query_devices: Callable[..., Dict[str, Any]] = tool_query_cmdb_devices,
        query_detail: Callable[..., Dict[str, Any]] = tool_query_cmdb_device_detail,
        query_by_ips: Callable[..., Dict[str, Any]] = tool_query_cmdb_devices_by_ips,
    ) -> None:
        self.query_devices = query_devices
        self.query_detail = query_detail
        self.query_by_ips = query_by_ips

    @staticmethod
    def _known_fields() -> set[str]:
        return {
            str(item.get("name") or "").strip()
            for item in CMDB_FIELDS
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }

    @classmethod
    def _validate_fields(
        cls,
        raw_fields: Iterable[Any],
        *,
        detail: bool,
    ) -> tuple[list[str], str]:
        known = cls._known_fields()
        sensitive = {str(item) for item in SENSITIVE_FIELDS}
        requested = [str(item or "").strip() for item in raw_fields]
        requested = [item for item in requested if item]

        if not requested:
            defaults = DETAIL_FIELDS if detail else DEFAULT_FIELDS
            safe_defaults = [
                str(item)
                for item in defaults
                if str(item) in known and str(item) not in sensitive
            ]
            return safe_defaults, ""

        result: list[str] = []
        for raw in requested:
            normalized = normalize_field_name(raw)
            if normalized in sensitive or raw in sensitive:
                return [], f"sensitive CMDB field is not allowed: {raw}"
            if normalized not in known:
                return [], f"unknown CMDB field: {raw}"
            if normalized not in result:
                result.append(normalized)
        return result, ""

    @classmethod
    def _validate_filters(
        cls,
        raw_filters: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        known = cls._known_fields()
        filterable = {str(item) for item in QUERY_FILTER_FIELDS}
        sensitive = {str(item) for item in SENSITIVE_FIELDS}
        result: Dict[str, Any] = {}

        for raw_key, value in dict(raw_filters or {}).items():
            key = str(raw_key or "").strip()
            if not key or value in (None, ""):
                continue
            if key == "search":
                result[key] = value
                continue

            if "__" in key:
                base, operator = key.split("__", 1)
            else:
                base, operator = key, "exact"
            normalized = normalize_field_name(base)
            if normalized not in known:
                return {}, f"unknown CMDB filter field: {base}"
            if normalized in sensitive:
                return {}, f"sensitive CMDB filter is not allowed: {base}"
            if normalized not in filterable:
                return {}, f"CMDB field is not filterable: {base}"
            if operator not in _ALLOWED_FILTER_OPERATORS:
                return {}, f"unsupported CMDB filter operator: {operator}"
            final_key = normalized if operator in {"exact", "eq"} else f"{normalized}__{operator}"
            result[final_key] = value
        return result, ""

    @staticmethod
    def _context_device(request: HandlerRequest) -> Dict[str, Any]:
        value = request.canonical_context.device_context
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _sanitize_items(
        items: Any,
        fields: list[str],
    ) -> list[Dict[str, Any]]:
        if not isinstance(items, list):
            return []
        safe: list[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            safe.append({field: item.get(field) for field in fields})
        return safe

    @staticmethod
    def _device_context(items: list[Dict[str, Any]]) -> Dict[str, Any]:
        if len(items) != 1:
            return {}
        item = items[0]
        allowed = (
            "host_name",
            "mgmt_ip",
            "device_spec",
            "device_type",
            "manufacturer",
            "IDC",
            "server_room",
            "rack",
            "status",
        )
        return {
            key: item.get(key)
            for key in allowed
            if item.get(key) not in (None, "")
        }

    def handle(self, request: HandlerRequest) -> HandlerOutcome:
        if request.decision.action != self.action:
            return HandlerOutcome.failure(
                action=request.decision.action,
                handler_key=self.handler_key,
                detail=(
                    "handler action mismatch: expected cmdb_query, got "
                    f"{request.decision.action.value}"
                ),
            )

        spec = request.decision.cmdb_query
        context_device = self._context_device(request)
        keyword = _first_non_empty(
            spec.keyword,
            request.decision.device_hint,
            context_device.get("host_name"),
            context_device.get("hostname"),
            context_device.get("device_name"),
            context_device.get("mgmt_ip"),
        )
        operation = str(spec.operation or "auto").strip().lower()
        ips = [str(item).strip() for item in spec.ips if str(item).strip()]
        if operation == "auto":
            operation = "by_ips" if ips else ("detail" if keyword else "devices")

        fields, field_error = self._validate_fields(
            spec.fields,
            detail=operation == "detail",
        )
        if field_error:
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail=field_error,
                metadata={"readonly": True, "operation": operation},
            )

        filters, filter_error = self._validate_filters(spec.filters)
        if filter_error:
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail=filter_error,
                metadata={"readonly": True, "operation": operation},
            )

        if operation == "detail":
            if not keyword:
                return HandlerOutcome.failure(
                    action=self.action,
                    handler_key=self.handler_key,
                    detail="CMDB detail query requires a structured keyword or device hint",
                    metadata={"readonly": True, "operation": operation},
                )
            result = self.query_detail(keyword=keyword, fields=fields)
            tool_name = "query_cmdb_device_detail"
        elif operation == "by_ips":
            if not ips:
                return HandlerOutcome.failure(
                    action=self.action,
                    handler_key=self.handler_key,
                    detail="CMDB by_ips query requires a non-empty structured IP list",
                    metadata={"readonly": True, "operation": operation},
                )
            result = self.query_by_ips(
                ips=ips,
                fields=fields,
                page_size=min(max(int(spec.page_size), 1), 100),
            )
            tool_name = "query_cmdb_devices_by_ips"
        elif operation == "devices":
            result = self.query_devices(
                filters=filters,
                fields=fields,
                page=max(int(spec.page), 1),
                page_size=min(max(int(spec.page_size), 1), 100),
            )
            tool_name = "query_cmdb_devices"
        else:
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail=f"unsupported structured CMDB operation: {operation}",
                metadata={"readonly": True, "operation": operation},
            )

        if not isinstance(result, dict):
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail="CMDB tool returned a non-dict result",
                metadata={"readonly": True, "operation": operation},
            )
        if result.get("status") != "ok":
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail=(
                    str(result.get("error_code") or "CMDB_QUERY_FAILED")
                    + ": "
                    + str(result.get("message") or "CMDB query failed")
                ),
                metadata={
                    "readonly": True,
                    "operation": operation,
                    "tool_name": result.get("tool_name") or tool_name,
                    "error_code": result.get("error_code"),
                    "http_status": result.get("http_status"),
                },
            )

        items = self._sanitize_items(result.get("items"), fields)
        count = max(int(result.get("count") or 0), len(items))
        returned = len(items)
        status = "not_found" if count == 0 else ("partial" if returned < count else "ok")
        if status == "not_found":
            answer = "CMDB 查询完成，但没有找到符合结构化条件的网络设备记录。"
        elif status == "partial":
            answer = f"CMDB 查询完成，共匹配 {count} 条，本次返回 {returned} 条。"
        else:
            answer = f"CMDB 查询完成，共返回 {returned} 条网络设备记录。"

        labels = field_labels()
        return HandlerOutcome.success(
            action=self.action,
            handler_key=self.handler_key,
            answer=answer,
            status=status,
            source="cmdb_readonly_tool",
            items=items,
            columns=fields,
            field_labels={key: labels.get(key, key) for key in fields},
            metadata={
                "readonly": True,
                "side_effect_started": False,
                "operation": operation,
                "tool_name": result.get("tool_name") or tool_name,
                "count": count,
                "returned": returned,
                "context_topic": "cmdb_query",
                "device_context": self._device_context(items),
                "requested_fields": fields,
                "requested_filters": filters,
            },
        )
