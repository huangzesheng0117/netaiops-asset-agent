# -*- coding: utf-8 -*-
"""
V3 high-confidence takeover gate.

This module is pure decision logic. It does not call CMDB, LLM, Netmiko MCP,
Prometheus MCP, or any device execution path.

V3.3 default behavior:
- NETAIOPS_V3_TAKEOVER_ENABLED defaults to disabled.
- Only low-risk response actions are takeover candidates.
- Execute / command-generation / confirmation actions are blocked in V3.3-1.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Set


DEFAULT_MIN_EFFECTIVE_CONFIDENCE = 0.80

DEFAULT_ALLOWED_ACTIONS: Set[str] = {
    "general_chat",
    "advice_analysis",
    "need_clarification",
    "cmdb_query",
}

DEFAULT_BLOCKED_ACTIONS: Set[str] = {
    "generate_commands",
    "execute_provided_commands",
    "execute_provided_commands_and_analyze",
    "confirm_execute_pending",
    "analyze_existing_evidence",
    "blocked_unsafe_commands",
}

DEFAULT_ALLOWED_RESPONSE_MODES: Set[str] = {
    "chat",
    "advice",
    "clarification",
    "cmdb",
}


@dataclass
class TakeoverGateDecision:
    enabled: bool
    eligible: bool
    takeover: bool
    reason: str
    action: str
    handler_key: str
    response_mode: str
    confidence: float
    effective_confidence: float
    accepted: Optional[bool]
    requires_confirmation: Optional[bool]
    safety_allowed: Optional[bool]
    allowed_actions: List[str]
    blocked_actions: List[str]
    min_effective_confidence: float

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        try:
            data = asdict(value)
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            data = value.as_dict()
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            data = value.model_dump()
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            data = vars(value)
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    try:
        data = dict(value)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _norm_action(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on", "enabled"}:
        return True
    if value in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    if value < 0:
        return default
    if value > 1:
        return default
    return value


def _csv_env(name: str, default: Iterable[str]) -> Set[str]:
    raw = os.environ.get(name)
    if raw is None:
        return {str(item).strip() for item in default if str(item).strip()}
    values = {item.strip() for item in raw.split(",") if item.strip()}
    return values or {str(item).strip() for item in default if str(item).strip()}


def takeover_enabled() -> bool:
    return _bool_env("NETAIOPS_V3_TAKEOVER_ENABLED", default=False)


def takeover_dry_run_enabled() -> bool:
    return _bool_env("NETAIOPS_V3_TAKEOVER_DRY_RUN", default=True)


def allowed_actions() -> Set[str]:
    return _csv_env("NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS", DEFAULT_ALLOWED_ACTIONS)


def blocked_actions() -> Set[str]:
    return _csv_env("NETAIOPS_V3_TAKEOVER_BLOCKED_ACTIONS", DEFAULT_BLOCKED_ACTIONS)


def min_effective_confidence() -> float:
    return _float_env("NETAIOPS_V3_TAKEOVER_MIN_EFFECTIVE_CONFIDENCE", DEFAULT_MIN_EFFECTIVE_CONFIDENCE)


def evaluate_takeover(
    *,
    plan: Any = None,
    decision: Any = None,
    enabled: Optional[bool] = None,
    min_confidence: Optional[float] = None,
    allowed: Optional[Iterable[str]] = None,
    blocked: Optional[Iterable[str]] = None,
) -> TakeoverGateDecision:
    plan_dict = _as_dict(plan)
    decision_dict = _as_dict(decision)

    action = _norm_action(plan_dict.get("action") or decision_dict.get("action"))
    handler_key = _norm_action(plan_dict.get("handler_key") or action)
    response_mode = _norm_action(plan_dict.get("response_mode"))

    accepted = plan_dict.get("accepted")
    requires_confirmation = plan_dict.get("requires_confirmation")
    safety_allowed = plan_dict.get("safety_allowed")

    confidence_raw = plan_dict.get("confidence")
    if confidence_raw is None:
        confidence_raw = decision_dict.get("confidence")

    effective_raw = plan_dict.get("effective_confidence")
    if effective_raw is None:
        effective_raw = confidence_raw

    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.0

    try:
        effective_confidence = float(effective_raw)
    except Exception:
        effective_confidence = 0.0

    allowed_set = {str(item).strip() for item in (allowed if allowed is not None else allowed_actions()) if str(item).strip()}
    blocked_set = {str(item).strip() for item in (blocked if blocked is not None else blocked_actions()) if str(item).strip()}
    threshold = float(min_confidence if min_confidence is not None else min_effective_confidence())
    is_enabled = takeover_enabled() if enabled is None else bool(enabled)

    reason = "eligible"
    eligible = True

    if handler_key in blocked_set or action in blocked_set:
        eligible = False
        reason = "blocked_action"
    elif handler_key not in allowed_set and action not in allowed_set:
        eligible = False
        reason = "not_in_allowed_actions"
    elif effective_confidence < threshold:
        eligible = False
        reason = "low_effective_confidence"
    elif response_mode and response_mode not in DEFAULT_ALLOWED_RESPONSE_MODES:
        eligible = False
        reason = "response_mode_not_allowed"
    elif requires_confirmation is True:
        eligible = False
        reason = "requires_confirmation"
    elif safety_allowed is False:
        eligible = False
        reason = "safety_not_allowed"
    elif accepted is False and handler_key not in {"need_clarification"} and action not in {"need_clarification"}:
        eligible = False
        reason = "plan_not_accepted"

    takeover = bool(is_enabled and eligible)

    return TakeoverGateDecision(
        enabled=is_enabled,
        eligible=eligible,
        takeover=takeover,
        reason=reason,
        action=action,
        handler_key=handler_key,
        response_mode=response_mode,
        confidence=confidence,
        effective_confidence=effective_confidence,
        accepted=accepted if isinstance(accepted, bool) else None,
        requires_confirmation=requires_confirmation if isinstance(requires_confirmation, bool) else None,
        safety_allowed=safety_allowed if isinstance(safety_allowed, bool) else None,
        allowed_actions=sorted(allowed_set),
        blocked_actions=sorted(blocked_set),
        min_effective_confidence=threshold,
    )


def evaluate_shadow_record(payload: Dict[str, Any], *, enabled: Optional[bool] = None) -> Dict[str, Any]:
    plan = payload.get("v3_plan") or {}
    decision = payload.get("v3_decision") or {}
    gate = evaluate_takeover(plan=plan, decision=decision, enabled=enabled)
    result = gate.as_dict()
    result.update(
        {
            "conversation_id": payload.get("conversation_id"),
            "v2_route": payload.get("v2_route"),
            "is_diff": payload.get("is_diff"),
            "question_prefix": str(payload.get("question") or "")[:160],
        }
    )
    return result
