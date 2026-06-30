#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable
import json
import re


LOW_RISK_ACTIONS = {"general_chat", "advice_analysis"}
ROUTE_TYPES = {
    "general_chat",
    "advice_analysis",
    "followup",
    "cmdb_query",
    "command_explanation",
    "command_execution",
    "config_change",
    "inline_command",
    "semantic_route",
    "batch_route",
    "unknown",
}

CATEGORY_TOKENS: dict[str, tuple[str, ...]] = {
    "config_change": (
        "配置变更",
        "修改配置",
        "下发配置",
        "删除配置",
        "保存配置",
        "write memory",
        "copy running-config startup-config",
        "shutdown",
        "no shutdown",
        "reload",
        "clear ",
        "debug ",
        "delete ",
        "set firewall",
        "config firewall",
    ),
    "command_execution": (
        "执行命令",
        "帮我执行",
        "直接执行",
        "执行一下",
        "跑一下",
        "下发命令",
        "在设备上执行",
        "send_command",
        "netmiko",
        "execute_command",
    ),
    "cmdb_query": (
        "cmdb",
        "query_cmdb",
        "networkserver",
        "管理ip",
        "管理 ip",
        "设备查询",
        "查设备",
        "查一下设备",
        "设备名称",
        "device_name",
        "device_ip",
        "device_type",
    ),
    "followup": (
        "followup",
        "follow_up",
        "继续",
        "上一个",
        "上一轮",
        "刚才",
        "这个设备",
        "这个接口",
        "那它",
        "那这个",
        "进一步",
        "conversation",
        "history",
        "append_turn",
    ),
    "command_explanation": (
        "解释命令",
        "命令含义",
        "命令是什么意思",
        "show interface status 是什么意思",
        "show interface",
        "show logging",
        "display interface",
        "这条命令",
        "只解释",
        "不要执行",
    ),
    "advice_analysis": (
        "advice_analysis",
        "advice",
        "建议",
        "风险",
        "是否建议",
        "是否可以",
        "如何处理",
        "怎么处理",
        "排查思路",
        "定位思路",
        "根因",
        "影响",
        "batch67_advice",
    ),
    "general_chat": (
        "general_chat",
        "文本解释",
        "解释一下",
        "什么是",
        "是什么",
        "含义",
        "区别",
        "原理",
        "白话",
        "总结一下",
    ),
    "semantic_route": (
        "semantic",
        "semantic_route",
        "planner_source",
        "route decision",
        "intent",
        "arbiter",
    ),
    "batch_route": (
        "batch",
        "batch63",
        "batch67",
        "batch68",
        "batch69",
    ),
}

TYPE_PRIORITY = (
    "config_change",
    "command_execution",
    "cmdb_query",
    "followup",
    "command_explanation",
    "advice_analysis",
    "general_chat",
    "semantic_route",
    "batch_route",
)

ACTION_MAP: dict[str, str | None] = {
    "general_chat": "general_chat",
    "advice_analysis": "advice_analysis",
    "followup": "advice_analysis",
    "command_explanation": "general_chat",
    "cmdb_query": "cmdb_query",
    "semantic_route": None,
    "batch_route": None,
    "inline_command": None,
    "command_execution": None,
    "config_change": None,
    "unknown": None,
}

RISK_MAP: dict[str, str] = {
    "general_chat": "low",
    "advice_analysis": "low",
    "command_explanation": "low",
    "followup": "medium",
    "semantic_route": "medium",
    "batch_route": "medium",
    "cmdb_query": "medium",
    "inline_command": "medium",
    "command_execution": "high",
    "config_change": "high",
    "unknown": "unknown",
}

V3_4_2_STAGE_ALLOWED_ROUTE_TYPES = {
    "general_chat",
    "advice_analysis",
}


@dataclass(frozen=True)
class LegacyRouteContext:
    question: str = ""
    context: str = ""
    function: str = ""
    route_paths: tuple[str, ...] = ()
    return_kind: str = ""
    snippet: str = ""
    contains_jsonresponse: bool | None = None
    wrapped_by_v3: bool | None = None
    legacy_signal_category: str = ""


@dataclass(frozen=True)
class LegacyRouteDecision:
    route_type: str
    v3_action: str | None
    risk_level: str
    confidence: float
    matched_tokens: tuple[str, ...]
    takeover_candidate: bool
    fallback_required: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\u3000", " ").strip()


def _combined_text(ctx: LegacyRouteContext) -> str:
    parts = [
        ctx.question,
        ctx.context,
        ctx.function,
        " ".join(ctx.route_paths),
        ctx.return_kind,
        ctx.snippet,
        ctx.legacy_signal_category,
    ]
    return "\n".join(_normalize_text(item) for item in parts if _normalize_text(item))


def _token_hits(text: str, tokens: Iterable[str]) -> tuple[str, ...]:
    lower = text.lower()
    hits: list[str] = []
    for token in tokens:
        token_lower = token.lower()
        if token_lower in lower:
            hits.append(token)
    return tuple(dict.fromkeys(hits))


def _hinted_route_type(ctx: LegacyRouteContext) -> str:
    hint = _normalize_text(ctx.legacy_signal_category).lower()
    aliases = {
        "general_chat": "general_chat",
        "advice_analysis": "advice_analysis",
        "followup": "followup",
        "cmdb_query": "cmdb_query",
        "inline_command": "inline_command",
        "semantic_route": "semantic_route",
        "batch_route": "batch_route",
    }
    return aliases.get(hint, "")


def legacy_route_to_v3_action(route_type: str) -> str | None:
    return ACTION_MAP.get(route_type, None)


def classify_legacy_route(
    question: str = "",
    context: str = "",
    function: str = "",
    route_paths: Iterable[str] | None = None,
    return_kind: str = "",
    snippet: str = "",
    contains_jsonresponse: bool | None = None,
    wrapped_by_v3: bool | None = None,
    legacy_signal_category: str = "",
) -> LegacyRouteDecision:
    ctx = LegacyRouteContext(
        question=question or "",
        context=context or "",
        function=function or "",
        route_paths=tuple(route_paths or ()),
        return_kind=return_kind or "",
        snippet=snippet or "",
        contains_jsonresponse=contains_jsonresponse,
        wrapped_by_v3=wrapped_by_v3,
        legacy_signal_category=legacy_signal_category or "",
    )
    text = _combined_text(ctx)

    matched_by_type: dict[str, tuple[str, ...]] = {}
    for route_type, tokens in CATEGORY_TOKENS.items():
        hits = _token_hits(text, tokens)
        if hits:
            matched_by_type[route_type] = hits

    route_type = "unknown"
    matched_tokens: tuple[str, ...] = ()

    # Explicit high-risk tokens always override inventory hints.
    for item in ("config_change", "command_execution"):
        if item in matched_by_type:
            route_type = item
            matched_tokens = matched_by_type[item]
            break

    if route_type == "unknown":
        hinted = _hinted_route_type(ctx)
        if hinted:
            route_type = hinted
            matched_tokens = matched_by_type.get(hinted, (ctx.legacy_signal_category,))

    if route_type == "unknown":
        for item in TYPE_PRIORITY:
            if item in matched_by_type:
                route_type = item
                matched_tokens = matched_by_type[item]
                break

    if route_type == "inline_command":
        if "command_explanation" in matched_by_type:
            route_type = "command_explanation"
            matched_tokens = matched_by_type["command_explanation"]
        elif "command_execution" in matched_by_type:
            route_type = "command_execution"
            matched_tokens = matched_by_type["command_execution"]

    v3_action = legacy_route_to_v3_action(route_type)
    risk_level = RISK_MAP.get(route_type, "unknown")
    confidence = 0.0
    if route_type != "unknown":
        confidence = 0.70
    if matched_tokens:
        confidence = min(0.95, confidence + min(len(matched_tokens), 5) * 0.04)
    if ctx.legacy_signal_category:
        confidence = min(0.97, confidence + 0.05)

    takeover_candidate = (
        route_type in V3_4_2_STAGE_ALLOWED_ROUTE_TYPES
        and v3_action in LOW_RISK_ACTIONS
        and risk_level == "low"
    )
    fallback_required = not takeover_candidate

    reason = (
        f"route_type={route_type}; risk={risk_level}; action={v3_action or 'none'}; "
        f"matched={','.join(matched_tokens) if matched_tokens else 'none'}"
    )

    return LegacyRouteDecision(
        route_type=route_type,
        v3_action=v3_action,
        risk_level=risk_level,
        confidence=round(confidence, 4),
        matched_tokens=matched_tokens,
        takeover_candidate=takeover_candidate,
        fallback_required=fallback_required,
        reason=reason,
    )


def should_allow_v3_takeover_for_legacy_route(
    decision: LegacyRouteDecision | dict[str, Any],
    *,
    canary_triggered: bool,
    allowed_actions: Iterable[str] = LOW_RISK_ACTIONS,
    stage: str = "v3.4.2",
) -> bool:
    if isinstance(decision, dict):
        route_type = str(decision.get("route_type") or "")
        action = decision.get("v3_action")
        risk = str(decision.get("risk_level") or "")
        candidate = bool(decision.get("takeover_candidate"))
    else:
        route_type = decision.route_type
        action = decision.v3_action
        risk = decision.risk_level
        candidate = decision.takeover_candidate

    if not canary_triggered:
        return False
    if stage == "v3.4.2" and route_type not in V3_4_2_STAGE_ALLOWED_ROUTE_TYPES:
        return False
    if action not in set(allowed_actions):
        return False
    if risk != "low":
        return False
    return bool(candidate)


def legacy_route_decision_from_inventory_record(record: dict[str, Any]) -> LegacyRouteDecision:
    return classify_legacy_route(
        question=record.get("question", ""),
        context=record.get("context", ""),
        function=record.get("function", ""),
        route_paths=record.get("route_paths", ()) or (),
        return_kind=record.get("return_kind", ""),
        snippet=record.get("snippet", ""),
        contains_jsonresponse=record.get("contains_jsonresponse"),
        wrapped_by_v3=record.get("wrapped_by_v3"),
        legacy_signal_category=record.get("category", "") or record.get("route_class", ""),
    )


def registry_metadata() -> dict[str, Any]:
    return {
        "version": "v3.4.2",
        "purpose": "legacy route registry, no runtime behavior change until app.py wires it in later batches",
        "route_types": sorted(ROUTE_TYPES),
        "low_risk_actions": sorted(LOW_RISK_ACTIONS),
        "stage_allowed_route_types": sorted(V3_4_2_STAGE_ALLOWED_ROUTE_TYPES),
        "action_map": ACTION_MAP,
        "risk_map": RISK_MAP,
    }


__all__ = [
    "LegacyRouteContext",
    "LegacyRouteDecision",
    "classify_legacy_route",
    "legacy_route_to_v3_action",
    "should_allow_v3_takeover_for_legacy_route",
    "legacy_route_decision_from_inventory_record",
    "registry_metadata",
]
