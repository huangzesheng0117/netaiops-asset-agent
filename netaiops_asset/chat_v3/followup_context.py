# -*- coding: utf-8 -*-
"""
V3 follow-up context bridge.

This module only reads, normalizes and persists conversation context. It does
not decide user intent, classify user text, call CMDB/MCP, or execute commands.

Primary intent remains decided by the LLM Intent Arbiter. The bridge provides
structured context to the Arbiter and response generator, observes eligible
canary return paths regardless of V2/V3 ownership, and keeps the original request
conversation_id stable even when legacy history code creates another effective ID.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_CONTEXT_DIR = "/var/lib/netaiops-asset-agent/data/v3_followup_context"
DEFAULT_MAX_TURNS = 30
DEFAULT_MAX_ANSWER_CHARS = 4000
DEFAULT_MAX_SUMMARY_CHARS = 12000


def _context_dir() -> Path:
    path = Path(
        os.getenv(
            "NETAIOPS_V3_FOLLOWUP_CONTEXT_DIR",
            DEFAULT_CONTEXT_DIR,
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _context_path(conversation_id: str) -> Path:
    value = str(conversation_id or "").strip()
    if not value:
        raise ValueError("conversation_id is required")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return _context_dir() / f"conversation_{digest}.json"


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _safe_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _compact_response(response: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _safe_dict(response.get("parsed"))
    return {
        "status": response.get("status"),
        "planner_source": response.get("planner_source"),
        "action": response.get("v3_takeover_action") or response.get("action"),
        "answer_summary": _truncate(
            response.get("answer") or response.get("message"),
            DEFAULT_MAX_ANSWER_CHARS,
        ),
        "parsed": {
            key: parsed.get(key)
            for key in (
                "intent",
                "reason",
                "current_topic",
                "current_intent",
                "device_name",
                "mgmt_ip",
                "device_type",
            )
            if key in parsed
        },
    }



def _normalize_turn(turn: Any) -> Dict[str, Any]:
    data = _safe_dict(turn)
    response = _safe_dict(data.get("response"))
    return {
        "turn_id": data.get("turn_id"),
        "turn_fingerprint": data.get("turn_fingerprint"),
        "time": data.get("time") or data.get("created_at"),
        "question": _truncate(data.get("question"), 2000),
        "answer_summary": _truncate(
            data.get("answer_summary")
            or response.get("answer")
            or response.get("message"),
            DEFAULT_MAX_ANSWER_CHARS,
        ),
        "planner_source": data.get("planner_source") or response.get("planner_source"),
        "status": data.get("status") or response.get("status"),
        "action": data.get("action")
        or response.get("v3_takeover_action")
        or response.get("action"),
        "route_label": data.get("route_label")
        or response.get("v3_takeover_route_label"),
        "effective_conversation_id": data.get("effective_conversation_id")
        or response.get("conversation_id"),
        "record_source": data.get("record_source"),
    }




def _dedupe_turns(turns: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    result: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in turns:
        item = _normalize_turn(raw)
        key = str(item.get("turn_fingerprint") or "").strip()
        if not key:
            key = json.dumps(
                {
                    "question": item.get("question"),
                    "answer_summary": item.get("answer_summary"),
                    "time": item.get("time"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        if key in seen:
            continue
        seen.add(key)
        if item.get("question") or item.get("answer_summary"):
            result.append(item)
    return result[-DEFAULT_MAX_TURNS:]



def _load_v2_context(
    conversation_id: Optional[str],
    user: Optional[str],
) -> Dict[str, Any]:
    try:
        from netaiops_asset.chat_v2.context import load_v2_context

        data = load_v2_context(
            conversation_id=conversation_id,
            user=user,
        )
        return _safe_dict(data)
    except Exception:
        return {}


def _load_legacy_conversation(conversation_id: Optional[str]) -> Dict[str, Any]:
    if not conversation_id:
        return {}
    try:
        from netaiops_asset.agent.conversation_store import get_conversation

        data = get_conversation(conversation_id)
        return _safe_dict(data)
    except Exception:
        return {}


def _load_v3_store(conversation_id: Optional[str]) -> Dict[str, Any]:
    if not conversation_id:
        return {}
    try:
        return _read_json(_context_path(conversation_id))
    except Exception:
        return {}


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return deepcopy(value)
    return None


def _compact_analysis(value: Any) -> Any:
    data = _safe_dict(value)
    if not data:
        return None
    return {
        key: data.get(key)
        for key in (
            "updated_at",
            "question",
            "conclusion",
            "answer_summary",
            "analysis",
            "analyses",
            "counts",
            "next_steps",
            "facts",
        )
        if key in data
    }


def _build_rolling_summary(
    existing: Any,
    recent_turns: list[Dict[str, Any]],
) -> str:
    current = _truncate(existing, DEFAULT_MAX_SUMMARY_CHARS)
    if current:
        return current

    lines: list[str] = []
    for turn in recent_turns[-6:]:
        question = _truncate(turn.get("question"), 500)
        answer = _truncate(turn.get("answer_summary"), 900)
        if question:
            lines.append(f"用户：{question}")
        if answer:
            lines.append(f"助手：{answer}")
    return _truncate("\n".join(lines), DEFAULT_MAX_SUMMARY_CHARS)


def build_followup_context(
    conversation_id: Optional[str],
    user: Optional[str] = None,
) -> Dict[str, Any]:
    """Load and normalize context without deciding whether text is a follow-up."""
    original_conversation_id = str(conversation_id or "").strip()
    v2_context = _load_v2_context(original_conversation_id or None, user)
    legacy_conversation = _load_legacy_conversation(original_conversation_id or None)
    v3_store = _load_v3_store(original_conversation_id or None)

    sources: list[str] = []
    if v2_context:
        sources.append("v2_conversation_context")
    if legacy_conversation:
        sources.append("legacy_conversation_store")
    if v3_store:
        sources.append("v3_followup_context_store")

    turns: list[Dict[str, Any]] = []
    turns.extend(_safe_list(v2_context.get("recent_turns")))
    turns.extend(_safe_list(legacy_conversation.get("turns")))
    turns.extend(_safe_list(v3_store.get("turns")))
    recent_turns = _dedupe_turns(turns)

    current_device = _first_nonempty(
        v2_context.get("current_device"),
        v3_store.get("current_device"),
    )
    current_topic = _first_nonempty(
        v2_context.get("current_topic"),
        v3_store.get("current_topic"),
    )
    current_intent = _first_nonempty(
        v2_context.get("current_intent"),
        v3_store.get("current_intent"),
    )
    active_focus = _first_nonempty(
        v2_context.get("active_focus"),
        v3_store.get("active_focus"),
    )

    last_command_suggestions = _safe_list(
        _first_nonempty(
            v2_context.get("last_command_suggestions"),
            v3_store.get("last_command_suggestions"),
        )
    )
    last_executions = _safe_list(
        _first_nonempty(
            v2_context.get("last_executions"),
            v3_store.get("last_executions"),
        )
    )
    last_analysis = _first_nonempty(
        v2_context.get("last_analysis"),
        v3_store.get("last_analysis"),
    )
    last_bulk_analysis = _first_nonempty(
        v2_context.get("last_bulk_analysis"),
        v3_store.get("last_bulk_analysis"),
    )
    last_followup_analysis = _first_nonempty(
        v2_context.get("last_followup_analysis"),
        v3_store.get("last_followup_analysis"),
    )
    last_prometheus_evidence = _first_nonempty(
        v2_context.get("last_prometheus_evidence"),
        v3_store.get("last_prometheus_evidence"),
    )

    rolling_summary = _build_rolling_summary(
        _first_nonempty(
            v2_context.get("rolling_summary"),
            v3_store.get("rolling_summary"),
        ),
        recent_turns,
    )

    has_execution_evidence = bool(
        last_executions
        or last_analysis
        or last_bulk_analysis
        or last_prometheus_evidence
    )
    available = bool(
        recent_turns
        or current_device
        or current_topic
        or current_intent
        or active_focus
        or rolling_summary
        or has_execution_evidence
    )

    arbiter_context = {
        "original_conversation_id": original_conversation_id,
        "followup_context_available": available,
        "followup_context_source": "+".join(sources) if sources else "none",
        "followup_context_turn_count": len(recent_turns),
        "current_device": current_device,
        "current_topic": current_topic,
        "current_intent": current_intent,
        "active_focus": active_focus,
        "pending_commands": [],
        "last_command_suggestions": last_command_suggestions[-20:],
        "last_executions": last_executions[-20:],
        "last_audit_path": _first_nonempty(
            v2_context.get("last_audit_path"),
            v3_store.get("last_audit_path"),
        ),
        "rolling_summary": rolling_summary,
        "recent_turns": recent_turns[-6:],
        "last_analysis": _compact_analysis(last_analysis),
        "last_bulk_analysis": _compact_analysis(last_bulk_analysis),
        "last_followup_analysis": _compact_analysis(last_followup_analysis),
        "last_prometheus_evidence": deepcopy(last_prometheus_evidence),
    }

    generator_context = deepcopy(arbiter_context)
    generator_context.update(
        {
            "has_execution_evidence": has_execution_evidence,
            "context_sources": list(sources),
        }
    )

    return {
        "available": available,
        "source": "+".join(sources) if sources else "none",
        "turn_count": len(recent_turns),
        "topic": current_topic,
        "has_execution_evidence": has_execution_evidence,
        "original_conversation_id": original_conversation_id,
        "arbiter_context": arbiter_context,
        "generator_context": generator_context,
    }



def record_v3_turn(
    *,
    conversation_id: str,
    user: Optional[str],
    question: str,
    response: Dict[str, Any],
    action: str,
    route_label: str,
    effective_conversation_id: Optional[str] = None,
    record_source: str = "v3_taken_return",
) -> Dict[str, Any]:
    """Persist one compact return-path observation under the original ID."""
    original_conversation_id = str(conversation_id or "").strip()
    if not original_conversation_id:
        raise ValueError("conversation_id is required")

    answer_summary = _truncate(
        response.get("answer") or response.get("message"),
        DEFAULT_MAX_ANSWER_CHARS,
    )
    if not answer_summary:
        raise ValueError("answer or message is required")

    path = _context_path(original_conversation_id)
    current = _read_json(path)
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    parsed = _safe_dict(response.get("parsed"))
    effective_id = str(
        effective_conversation_id
        or response.get("conversation_id")
        or original_conversation_id
    ).strip()
    normalized_record_source = str(
        record_source or "return_path_observation"
    ).strip()

    fingerprint_payload = {
        "original_conversation_id": original_conversation_id,
        "effective_conversation_id": effective_id,
        "question": _truncate(question, 2000),
        "answer_summary": answer_summary,
        "planner_source": response.get("planner_source"),
        "status": response.get("status"),
        "action": action,
        "route_label": route_label,
        "record_source": normalized_record_source,
    }
    turn_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    turn = {
        "turn_id": str(uuid.uuid4()),
        "turn_fingerprint": turn_fingerprint,
        "time": now,
        "question": fingerprint_payload["question"],
        "answer_summary": answer_summary,
        "planner_source": response.get("planner_source"),
        "status": response.get("status"),
        "action": action,
        "route_label": route_label,
        "effective_conversation_id": effective_id,
        "record_source": normalized_record_source,
    }

    turns = _safe_list(current.get("turns"))
    previous_turn_count = len(_dedupe_turns(turns))
    turns.append(turn)
    turns = _dedupe_turns(turns)[-DEFAULT_MAX_TURNS:]
    deduplicated = len(turns) == previous_turn_count

    current.update(
        {
            "schema_version": "v3_followup_context_2",
            "original_conversation_id": original_conversation_id,
            "user": user,
            "created_at": current.get("created_at") or now,
            "updated_at": now,
            "turns": turns,
            "current_device": _first_nonempty(
                parsed.get("current_device"),
                current.get("current_device"),
            ),
            "current_topic": _first_nonempty(
                parsed.get("current_topic"),
                current.get("current_topic"),
            ),
            "current_intent": _first_nonempty(
                parsed.get("current_intent"),
                parsed.get("intent"),
                current.get("current_intent"),
            ),
            "rolling_summary": _build_rolling_summary("", turns),
        }
    )
    _atomic_write(path, current)

    return {
        "path": str(path),
        "turn_count": len(turns),
        "previous_turn_count": previous_turn_count,
        "deduplicated": deduplicated,
        "turn_fingerprint": turn_fingerprint,
        "record_source": normalized_record_source,
        "original_conversation_id": original_conversation_id,
        "effective_conversation_id": effective_id,
    }



def delete_followup_context(conversation_id: str) -> bool:
    try:
        path = _context_path(conversation_id)
    except Exception:
        return False
    if path.exists():
        path.unlink()
        return True
    return False
