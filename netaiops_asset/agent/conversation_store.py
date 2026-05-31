from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from netaiops_asset.config_loader import get_config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime() -> dict[str, Any]:
    return get_config().get("runtime", {})


def _base_dir() -> Path:
    path = Path(_runtime().get("conversation_dir", "/var/lib/netaiops-asset-agent/data/conversations"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _max_turns() -> int:
    return max(1, int(_runtime().get("conversation_max_turns", 50) or 50))


def _max_items_per_turn() -> int:
    return max(0, int(_runtime().get("conversation_max_items_per_turn", 100) or 100))


def _safe_id(conversation_id: str) -> str:
    value = str(conversation_id or "").strip()
    if not re.fullmatch(r"[a-fA-F0-9-]{20,80}", value):
        raise ValueError("invalid conversation_id")
    return value


def _path(conversation_id: str) -> Path:
    return _base_dir() / f"{_safe_id(conversation_id)}.json"


def _title_from_question(question: str) -> str:
    text = re.sub(r"\s+", " ", str(question or "")).strip()
    if not text:
        return "新对话"
    return text[:28] + ("..." if len(text) > 28 else "")


def _cleanup_old_conversations() -> None:
    retention_days = int(_runtime().get("conversation_retention_days", 180) or 180)
    if retention_days <= 0:
        return

    cutoff = time.time() - retention_days * 86400
    for f in _base_dir().glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception:
            continue


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _trim_response(response: dict[str, Any]) -> dict[str, Any]:
    max_items = _max_items_per_turn()
    data = deepcopy(response)

    items = data.get("items")
    if isinstance(items, list):
        data["items"] = items[:max_items]
        data["saved_items"] = len(data["items"])
        data["items_truncated_for_storage"] = len(items) > len(data["items"])

    return data


def create_conversation(title: str | None = None, user: str | None = None) -> dict[str, Any]:
    _cleanup_old_conversations()

    cid = str(uuid.uuid4())
    now = _now()
    conv = {
        "conversation_id": cid,
        "title": title or "新对话",
        "user": user or "web_user",
        "created_at": now,
        "updated_at": now,
        "max_turns": _max_turns(),
        "max_items_per_turn": _max_items_per_turn(),
        "turns": [],
    }

    _atomic_write(_path(cid), conv)
    return conv


def list_conversations(limit: int = 50) -> list[dict[str, Any]]:
    _cleanup_old_conversations()

    items: list[dict[str, Any]] = []
    for f in _base_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            turns = data.get("turns", [])
            items.append({
                "conversation_id": data.get("conversation_id"),
                "title": data.get("title") or "未命名对话",
                "user": data.get("user"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "turn_count": len(turns),
                "last_question": turns[-1].get("question") if turns else "",
            })
        except Exception:
            continue

    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return items[:limit]


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    try:
        p = _path(conversation_id)
    except ValueError:
        return None

    if not p.exists():
        return None

    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def append_turn(
    conversation_id: str | None,
    question: str,
    response: dict[str, Any],
    user: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if conversation_id:
        conv = get_conversation(conversation_id)
        if conv is None:
            conv = create_conversation(title=_title_from_question(question), user=user)
    else:
        conv = create_conversation(title=_title_from_question(question), user=user)

    if not conv.get("turns"):
        conv["title"] = _title_from_question(question)

    cid = conv["conversation_id"]
    turn = {
        "turn_id": str(uuid.uuid4()),
        "time": _now(),
        "question": question,
        "response": {
            "status": response.get("status"),
            "answer": response.get("answer"),
            "request_id": response.get("request_id"),
            "action": response.get("action"),
            "export_url": response.get("export_url"),
            "export_params": response.get("export_params"),
            "source_turn_id": response.get("source_turn_id"),
            "parsed": response.get("parsed"),
            "llm_plan": response.get("llm_plan"),
            "planner_source": response.get("planner_source"),
            "columns": response.get("columns"),
            "field_labels": response.get("field_labels"),
            "count": response.get("count"),
            "returned": response.get("returned"),
            "items": response.get("items"),
        },
    }

    turn["response"] = _trim_response(turn["response"])

    conv.setdefault("turns", []).append(turn)
    if len(conv["turns"]) > _max_turns():
        conv["turns"] = conv["turns"][-_max_turns():]

    conv["updated_at"] = _now()
    conv["user"] = user or conv.get("user") or "web_user"
    conv["max_turns"] = _max_turns()
    conv["max_items_per_turn"] = _max_items_per_turn()

    _atomic_write(_path(cid), conv)
    return cid, turn


def delete_conversation(conversation_id: str) -> bool:
    try:
        p = _path(conversation_id)
    except ValueError:
        return False

    if p.exists():
        p.unlink()
        return True
    return False
