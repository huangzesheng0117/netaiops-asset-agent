# -*- coding: utf-8 -*-
"""
V3 shadow mode JSONL logger.

Responsibilities:
- Record V2 route/result summary and V3 Arbiter/Dispatcher plan side by side.
- Provide files for later diff analysis.
- Never affect user-visible frontend behavior.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional


SHADOW_LOGGER_VERSION = "v3_shadow_logger_1"
DEFAULT_SHADOW_DIR = "/var/lib/netaiops-asset-agent/data/v3_intent_shadow"


def ensure_shadow_dir(path: str = DEFAULT_SHADOW_DIR) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def today_log_path(path: str = DEFAULT_SHADOW_DIR, now: Optional[float] = None) -> Path:
    ts = time.localtime(now or time.time())
    return ensure_shadow_dir(path) / time.strftime("shadow_%Y%m%d.jsonl", ts)


def write_shadow_record(
    question: str,
    conversation_id: Optional[str] = None,
    user: Optional[str] = None,
    v2_route: Optional[str] = None,
    v2_summary: Optional[Dict[str, Any]] = None,
    v3_decision: Any = None,
    v3_plan: Any = None,
    is_diff: Optional[bool] = None,
    shadow_dir: str = DEFAULT_SHADOW_DIR,
    extra: Optional[Dict[str, Any]] = None,
) -> Path:
    record = build_shadow_record(
        question=question,
        conversation_id=conversation_id,
        user=user,
        v2_route=v2_route,
        v2_summary=v2_summary,
        v3_decision=v3_decision,
        v3_plan=v3_plan,
        is_diff=is_diff,
        extra=extra,
    )

    path = today_log_path(shadow_dir)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    return path


def build_shadow_record(
    question: str,
    conversation_id: Optional[str] = None,
    user: Optional[str] = None,
    v2_route: Optional[str] = None,
    v2_summary: Optional[Dict[str, Any]] = None,
    v3_decision: Any = None,
    v3_plan: Any = None,
    is_diff: Optional[bool] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    decision_payload = to_plain_data(v3_decision)
    plan_payload = to_plain_data(v3_plan)

    if is_diff is None:
        is_diff = infer_diff(v2_route=v2_route, v3_plan=plan_payload)

    return {
        "shadow_logger_version": SHADOW_LOGGER_VERSION,
        "record_id": str(uuid.uuid4()),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "pid": os.getpid(),
        "conversation_id": conversation_id or "",
        "user": user or "",
        "question": str(question or ""),
        "v2_route": v2_route or "",
        "v2_summary": v2_summary or {},
        "v3_decision": decision_payload,
        "v3_plan": plan_payload,
        "is_diff": bool(is_diff),
        "extra": extra or {},
    }


def infer_diff(v2_route: Optional[str], v3_plan: Dict[str, Any]) -> bool:
    if not v2_route:
        return False

    v2 = normalize_route_name(v2_route)
    v3 = normalize_route_name(str(v3_plan.get("handler_key") or v3_plan.get("action") or ""))

    if not v2 or not v3:
        return False

    return v2 != v3


def normalize_route_name(value: str) -> str:
    value = str(value or "").strip().lower()
    aliases = {
        "v2_chat_router": "generate_commands",
        "chat_router": "generate_commands",
        "inline_command_execute": "execute_provided_commands",
        "v2_inline_command_execute": "execute_provided_commands",
        "v2_followup_analysis": "analyze_existing_evidence",
        "followup_analysis": "analyze_existing_evidence",
        "v2_advice_analysis": "advice_analysis",
        "advice": "advice_analysis",
        "cmdb": "cmdb_query",
        "cmdb_query": "cmdb_query",
        "general": "general_chat",
        "general_chat": "general_chat",
    }
    return aliases.get(value, value)


def to_plain_data(value: Any) -> Any:
    if value is None:
        return {}

    if hasattr(value, "as_dict") and callable(value.as_dict):
        return value.as_dict()

    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump()

    if is_dataclass(value):
        return asdict(value)

    if isinstance(value, dict):
        return value

    if isinstance(value, (list, tuple)):
        return [to_plain_data(item) for item in value]

    return {"value": str(value)}
