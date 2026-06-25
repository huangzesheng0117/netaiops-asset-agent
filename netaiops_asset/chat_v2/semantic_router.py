# -*- coding: utf-8 -*-
"""
V2 unified semantic router.

Principle:
- LLM Planner decides semantic intent.
- Plan Dispatcher decides semantic route.
- Local code only enforces safety, structure, fallback and audit.

This module does NOT execute CLI.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from netaiops_asset.chat_v2.context import load_v2_context
from netaiops_asset.chat_v2.llm_intent_planner import plan_v2_intent
from netaiops_asset.chat_v2.plan_dispatcher import validate_and_dispatch_plan


SEMANTIC_ROUTES = {
    "v2_execution_confirmation",
    "v2_followup_analysis",
    "v2_chat_router",
    "v1_cmdb",
    "need_clarification",
}


def build_v2_semantic_route(
    question: str,
    user: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    question = str(question or "").strip()

    context = None
    try:
        context = load_v2_context(conversation_id=conversation_id, user=user)
    except Exception as exc:
        context = None
        context_error = repr(exc)
    else:
        context_error = None

    plan = plan_v2_intent(question, context=context, user=user)
    dispatch = validate_and_dispatch_plan(plan, question=question, context=context)

    route = dispatch.get("route")
    action = dispatch.get("action")

    if route not in SEMANTIC_ROUTES:
        route = "need_clarification"

    # If LLM explicitly says execute_pending / execute_all_pending, trust semantic route.
    # Local code must not re-decide whether "确认可以执行" means execute.
    if action in ("execute_pending", "execute_all_pending"):
        route = "v2_execution_confirmation"
        dispatch["route"] = route

    # If LLM explicitly says followup_analysis, trust semantic route.
    if action == "followup_analysis":
        route = "v2_followup_analysis"
        dispatch["route"] = route

    return {
        "status": "ok",
        "route": route,
        "action": action,
        "category": dispatch.get("category"),
        "v2_intent": dispatch.get("v2_intent"),
        "entities": dispatch.get("entities") or {},
        "confidence": dispatch.get("confidence"),
        "source": dispatch.get("source"),
        "degraded": dispatch.get("degraded"),
        "warnings": dispatch.get("warnings") or [],
        "errors": dispatch.get("errors") or [],
        "llm_plan": plan,
        "dispatch_plan": dispatch,
        "context_available": bool(context),
        "context_error": context_error,
    }


def semantic_confirm_question_from_route(question: str, decision: Dict[str, Any]) -> str:
    """
    Convert an LLM-confirmed execution route into the existing confirmation executor input.

    This is not semantic recognition. The semantic decision is already made by LLM.
    Here we only preserve optional numeric target if the user explicitly wrote 第N条.
    """
    q = str(question or "").strip()

    m = re.search(r"第\s*(\d+)\s*条", q)
    if m:
        return "确认执行第{}条命令 YES".format(m.group(1))

    return "确认执行全部命令 YES"


def is_semantic_route_enabled() -> bool:
    return True
