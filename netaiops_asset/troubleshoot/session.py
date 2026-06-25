# -*- coding: utf-8 -*-
"""
Troubleshooting session store.

This module stores V2 troubleshooting sessions as JSON files.

Safety:
- It only writes local session/evidence JSON.
- It does not execute device commands.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


DEFAULT_SESSION_DIR = os.getenv(
    "NETAIOPS_TROUBLESHOOT_SESSION_DIR",
    "/var/lib/netaiops-asset-agent/data/v2_troubleshoot_sessions",
)


@dataclass
class EvidenceRecord:
    evidence_id: str
    source: str
    evidence_type: str
    status: str
    title: str
    summary: str
    payload: Dict[str, Any]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TroubleSession:
    session_id: str
    question: str
    keyword: str
    status: str
    created_at: str
    updated_at: str
    evidences: List[Dict[str, Any]]
    summary: str
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TroubleSessionStore:
    def __init__(self, session_dir: str = DEFAULT_SESSION_DIR) -> None:
        self.session_dir = session_dir
        os.makedirs(self.session_dir, exist_ok=True)

    def create_session(self, question: str, keyword: str) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        session = TroubleSession(
            session_id=str(uuid.uuid4()),
            question=str(question or ""),
            keyword=str(keyword or ""),
            status="created",
            created_at=now,
            updated_at=now,
            evidences=[],
            summary="",
            warnings=[],
        )
        data = session.to_dict()
        self.save_session(data)
        return data

    def add_evidence(
        self,
        session_id: str,
        source: str,
        evidence_type: str,
        status: str,
        title: str,
        summary: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session = self.load_session(session_id)
        evidence = EvidenceRecord(
            evidence_id=str(uuid.uuid4()),
            source=source,
            evidence_type=evidence_type,
            status=status,
            title=title,
            summary=summary,
            payload=payload or {},
            created_at=datetime.now().isoformat(),
        ).to_dict()

        session.setdefault("evidences", []).append(evidence)
        session["updated_at"] = datetime.now().isoformat()
        self.save_session(session)
        return evidence

    def update_session(
        self,
        session_id: str,
        status: Optional[str] = None,
        summary: Optional[str] = None,
        warnings: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        session = self.load_session(session_id)
        if status is not None:
            session["status"] = status
        if summary is not None:
            session["summary"] = summary
        if warnings is not None:
            session["warnings"] = warnings
        session["updated_at"] = datetime.now().isoformat()
        self.save_session(session)
        return session

    def load_session(self, session_id: str) -> Dict[str, Any]:
        path = self._path(session_id)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_session(self, session: Dict[str, Any]) -> str:
        session_id = session["session_id"]
        path = self._path(session_id)
        tmp = path + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

        os.replace(tmp, path)
        return path

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for name in sorted(os.listdir(self.session_dir), reverse=True):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.session_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                items.append({
                    "session_id": data.get("session_id"),
                    "question": data.get("question"),
                    "keyword": data.get("keyword"),
                    "status": data.get("status"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "evidence_count": len(data.get("evidences") or []),
                    "path": path,
                })
            except Exception:
                continue

            if len(items) >= limit:
                break

        return items

    def _path(self, session_id: str) -> str:
        safe = str(session_id).replace("/", "_").replace("..", "_")
        return os.path.join(self.session_dir, safe + ".json")


def session_path(session_id: str, session_dir: str = DEFAULT_SESSION_DIR) -> str:
    return os.path.join(session_dir, str(session_id).replace("/", "_").replace("..", "_") + ".json")
