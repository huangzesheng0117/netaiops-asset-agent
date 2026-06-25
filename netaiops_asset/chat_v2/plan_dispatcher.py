# -*- coding: utf-8 -*-
"""
V2 Plan Validator + Action Dispatcher.

Purpose:
- Validate LLM/fallback intent plans.
- Normalize actions, categories and entities.
- Decide the safe local route:
  - v2_chat_router
  - v2_execution_confirmation
  - v2_followup_analysis
  - v1_cmdb
  - need_clarification

Safety:
- Does not execute CLI.
- Does not call Netmiko.
- Does not call Prometheus.
- Only validates and dispatches structured plans.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from netaiops_asset.chat_v2.llm_intent_planner import (
    CATEGORY_TO_V2_INTENT,
    CMDB_ACTIONS,
    V2_ACTIONS,
    extract_device_from_text,
    extract_interface_from_text,
    interface_from_plan,
    keyword_from_plan,
    plan_v2_intent,
)


VALID_ACTIONS = set(V2_ACTIONS) | set(CMDB_ACTIONS) | {
    "asset_query",
    "cmdb_query",
    "need_clarification",
}

VALID_ROUTES = {
    "v2_chat_router",
    "v2_execution_confirmation",
    "v2_followup_analysis",
    "v1_cmdb",
    "need_clarification",
}

ACTION_TO_ROUTE = {
    "suggest_commands": "v2_chat_router",
    "prometheus_query": "v2_chat_router",
    "execute_pending": "v2_execution_confirmation",
    "execute_all_pending": "v2_execution_confirmation",
    "followup_analysis": "v2_followup_analysis",
    "cmdb_query": "v1_cmdb",
    "asset_query": "v1_cmdb",
    "need_clarification": "need_clarification",
}


def validate_and_dispatch_plan(
    plan: Optional[Dict[str, Any]],
    question: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan = dict(plan or {})
    question = str(question or "")

    warnings: List[str] = []
    errors: List[str] = []

    action = normalize_action(plan.get("action"))
    category = normalize_category(plan.get("category"))
    entities = normalize_entities(plan.get("entities"), question=question, context=context)

    source = str(plan.get("source") or "unknown")
    llm_status = plan.get("llm_status")
    degraded = source != "llm"

    if degraded:
        warnings.append("plan_source_is_not_llm:{}".format(source))

    if llm_status:
        warnings.append("llm_status:{}".format(llm_status))

    if action not in VALID_ACTIONS:
        warnings.append("unknown_action:{}; normalized_to_need_clarification".format(action))
        action = "need_clarification"

    v2_intent = plan.get("v2_intent") or CATEGORY_TO_V2_INTENT.get(category)

    route = ACTION_TO_ROUTE.get(action, "need_clarification")

    if action in ("suggest_commands", "prometheus_query") and not v2_intent:
        if category == "cmdb":
            route = "v1_cmdb"
        else:
            route = "need_clarification"
            errors.append("missing_v2_intent_for_v2_action")

    if route == "v2_chat_router":
        if not entities.get("device_name") and not entities.get("mgmt_ip"):
            inherited = inherit_device_from_context(context)
            if inherited:
                entities.update({k: v for k, v in inherited.items() if v and not entities.get(k)})
                warnings.append("device_inherited_from_context")
            else:
                route = "need_clarification"
                errors.append("missing_device_for_v2_chat_router")

    if route == "v2_execution_confirmation":
        # Execution confirmation must still be handled by confirmation.py,
        # where YES and pending-command checks are enforced.
        pass

    if route == "v2_followup_analysis":
        if not context:
            warnings.append("followup_without_context")
        # Follow-up can still be routed. followup.py will decide whether
        # context has enough evidence.

    confidence = safe_float(plan.get("confidence"), 0.0)

    status = "ok"
    if errors and route == "need_clarification":
        status = "need_clarification"
    elif errors:
        status = "invalid"

    return {
        "status": status,
        "route": route,
        "action": action,
        "category": category,
        "v2_intent": v2_intent,
        "entities": entities,
        "confidence": confidence,
        "source": source,
        "degraded": degraded,
        "warnings": warnings,
        "errors": errors,
        "raw_plan": plan,
    }


def build_dispatch_debug_payload(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    plan = plan_v2_intent(question, context=context, user=user)
    dispatch = validate_and_dispatch_plan(plan, question=question, context=context)

    return {
        "status": "ok",
        "planner": "v2_llm_first_intent_planner",
        "dispatcher": "v2_plan_validator_dispatcher",
        "question": question,
        "plan": plan,
        "dispatch": dispatch,
    }


def normalize_action(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "suggest": "suggest_commands",
        "suggest_command": "suggest_commands",
        "suggest_commands": "suggest_commands",
        "commands": "suggest_commands",
        "command_suggestion": "suggest_commands",
        "execute": "execute_pending",
        "execute_command": "execute_pending",
        "execute_pending": "execute_pending",
        "execute_all": "execute_all_pending",
        "execute_all_pending": "execute_all_pending",
        "run_pending": "execute_pending",
        "run_all_pending": "execute_all_pending",
        "followup": "followup_analysis",
        "followup_analysis": "followup_analysis",
        "analysis": "followup_analysis",
        "summary": "followup_analysis",
        "prometheus": "prometheus_query",
        "prometheus_query": "prometheus_query",
        "metric_query": "prometheus_query",
        "cmdb": "cmdb_query",
        "asset": "cmdb_query",
        "asset_query": "cmdb_query",
        "cmdb_query": "cmdb_query",
        "clarify": "need_clarification",
        "need_clarification": "need_clarification",
    }
    return mapping.get(text, text or "need_clarification")


def normalize_category(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "cpu_check": "cpu",
        "cpu_high": "cpu",
        "cpu": "cpu",
        "memory": "memory",
        "mem": "memory",
        "route": "route_table",
        "routing": "route_table",
        "route_table": "route_table",
        "bgp": "bgp",
        "bfd": "bfd",
        "interface": "interface_status",
        "interface_status": "interface_status",
        "interface_down": "interface_down",
        "port_down": "interface_down",
        "interface_error": "interface_error",
        "interface_errors": "interface_error",
        "crc": "interface_error",
        "discard": "interface_error",
        "drop": "interface_error",
        "packet_loss": "interface_error",
        "optical": "optical_power",
        "optical_power": "optical_power",
        "light_power": "optical_power",
        "transceiver": "transceiver",
        "log": "log",
        "logs": "log",
        "device_health": "device_health",
        "health": "device_health",
        "cmdb": "cmdb",
        "asset": "cmdb",
        "unknown": "unknown",
    }
    return mapping.get(text, text or "unknown")


def normalize_entities(
    entities: Any,
    question: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    if not isinstance(entities, dict):
        entities = {}

    result = {
        "device_name": first_non_empty(
            entities.get("device_name"),
            entities.get("hostname"),
            entities.get("host_name"),
            entities.get("device"),
        ),
        "mgmt_ip": first_non_empty(
            entities.get("mgmt_ip"),
            entities.get("management_ip"),
            entities.get("ip"),
        ),
        "interface": first_non_empty(
            entities.get("interface"),
            entities.get("interface_name"),
            entities.get("port"),
        ),
        "peer": str(entities.get("peer") or ""),
        "time_range": str(entities.get("time_range") or ""),
        "metric": str(entities.get("metric") or ""),
        "symptom": str(entities.get("symptom") or ""),
    }

    if not result["device_name"] and not result["mgmt_ip"]:
        result["device_name"] = extract_device_from_text(question)

    if not result["interface"]:
        result["interface"] = extract_interface_from_text(question)
    else:
        result["interface"] = interface_from_plan({"entities": result}) or result["interface"]

    return {k: str(v or "").strip() for k, v in result.items()}


def inherit_device_from_context(context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(context, dict):
        return {}

    current_device = context.get("current_device") or {}
    if not isinstance(current_device, dict):
        return {}

    return {
        "device_name": str(current_device.get("device_name") or current_device.get("hostname") or ""),
        "mgmt_ip": str(current_device.get("mgmt_ip") or ""),
    }


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default
