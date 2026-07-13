# -*- coding: utf-8 -*-
"""Versioned V4 contracts.

This module defines data contracts only. It does not classify user text, call an
LLM, query CMDB/MCP, or execute commands.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netaiops_asset.chat_v3.intent_schema import IntentAction

V4_ENTRY_SCHEMA_VERSION = "v4.entry.v1"
V4_RESPONSE_SCHEMA_VERSION = "v4.response.v1"
V4_AUDIT_SCHEMA_VERSION = "v4.audit.v1"
V4_CONTEXT_SCHEMA_VERSION = "v4.context.v1"

MAX_CONTEXT_TURNS = 30
MAX_CONTEXT_EVIDENCE = 20
MAX_CONTEXT_ANALYSIS = 20
MAX_CONTEXT_AUDIT_REFS = 50
MAX_QUESTION_CHARS = 2000
MAX_ANSWER_CHARS = 4000
MAX_SUMMARY_CHARS = 12000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntryStatus(str, Enum):
    handled = "handled"
    clarification = "clarification"
    fallback = "fallback"
    error = "error"


class OperationStatus(str, Enum):
    ok = "ok"
    not_found = "not_found"
    error = "error"


class ContextErrorKind(str, Enum):
    not_found = "not_found"
    corrupt = "corrupt"
    permission = "permission"
    write = "write"
    schema = "schema"
    migration = "migration"
    conflict = "conflict"
    invalid = "invalid"


class V4ResponseMeta(StrictModel):
    schema_version: str = V4_RESPONSE_SCHEMA_VERSION
    handler_key: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    side_effect_started: bool = False
    fallback_used: bool = False
    audit_id: str = ""
    context_recorded: bool = False

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != V4_RESPONSE_SCHEMA_VERSION:
            raise ValueError("unsupported V4 response schema version")
        return value


class V4Response(StrictModel):
    status: str = "ok"
    answer: str = ""
    items: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)
    returned: int = Field(default=0, ge=0)
    columns: List[str] = Field(default_factory=list)
    field_labels: Dict[str, str] = Field(default_factory=dict)
    conversation_id: str = ""
    question: str = ""
    action: IntentAction = IntentAction.general_chat
    planner_source: str = "v4_intent_arbiter"
    v4: V4ResponseMeta = Field(default_factory=V4ResponseMeta)

    @model_validator(mode="after")
    def normalize_counts(self) -> "V4Response":
        if self.returned == 0 and self.items:
            self.returned = len(self.items)
        if self.count == 0 and self.items:
            self.count = len(self.items)
        if self.returned > self.count:
            raise ValueError("returned cannot exceed count")
        return self


class V4AuditRecord(StrictModel):
    schema_version: str = V4_AUDIT_SCHEMA_VERSION
    audit_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=utc_now)
    conversation_id: str = ""
    request_id: str = ""
    action: IntentAction = IntentAction.general_chat
    handler_key: str = ""
    status: str = ""
    side_effect_started: bool = False
    fallback_allowed: bool = False
    fallback_reason: str = ""
    context_read_status: str = ""
    context_write_status: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != V4_AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported V4 audit schema version")
        return value

    @model_validator(mode="after")
    def prevent_fallback_after_side_effect(self) -> "V4AuditRecord":
        if self.side_effect_started:
            self.fallback_allowed = False
        return self


class ContextTurn(StrictModel):
    turn_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    turn_fingerprint: str = ""
    created_at: str = Field(default_factory=utc_now)
    question: str = Field(default="", max_length=MAX_QUESTION_CHARS)
    answer_summary: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    action: Optional[IntentAction] = None
    planner_source: str = ""
    route_label: str = ""
    effective_conversation_id: str = ""
    record_source: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def build_fingerprint(self) -> "ContextTurn":
        if not self.turn_fingerprint:
            payload = {
                "question": self.question,
                "answer_summary": self.answer_summary,
                "action": self.action.value if self.action else "",
                "planner_source": self.planner_source,
                "route_label": self.route_label,
                "effective_conversation_id": self.effective_conversation_id,
                "record_source": self.record_source,
            }
            self.turn_fingerprint = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        return self


class ContextMigration(StrictModel):
    status: str = "native"
    migrated_at: str = ""
    sources: List[str] = Field(default_factory=list)
    source_versions: Dict[str, str] = Field(default_factory=dict)
    original_conversation_id: str = ""
    effective_conversation_id: str = ""
    notes: List[str] = Field(default_factory=list)


class CanonicalContext(StrictModel):
    schema_version: str = V4_CONTEXT_SCHEMA_VERSION
    conversation_id: str = Field(min_length=1, max_length=256)
    request_user_field: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=160)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    revision: int = Field(default=0, ge=0)
    device_context: Dict[str, Any] = Field(default_factory=dict)
    topic: str = Field(default="", max_length=512)
    rolling_summary: str = Field(default="", max_length=MAX_SUMMARY_CHARS)
    recent_turns: List[ContextTurn] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_TURNS,
    )
    last_intent: Dict[str, Any] = Field(default_factory=dict)
    pending: Dict[str, Any] = Field(default_factory=dict)
    execution_evidence: List[Dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_EVIDENCE,
    )
    analysis_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_ANALYSIS,
    )
    audit_refs: List[str] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_AUDIT_REFS,
    )
    migration: ContextMigration = Field(default_factory=ContextMigration)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != V4_CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported V4 context schema version")
        return value

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("invalid conversation_id")
        return normalized

    @model_validator(mode="after")
    def dedupe_bounded_fields(self) -> "CanonicalContext":
        turns: List[ContextTurn] = []
        seen_turns: set[str] = set()
        for turn in self.recent_turns:
            if turn.turn_fingerprint in seen_turns:
                continue
            seen_turns.add(turn.turn_fingerprint)
            turns.append(turn)
        self.recent_turns = turns[-MAX_CONTEXT_TURNS:]

        refs: List[str] = []
        seen_refs: set[str] = set()
        for raw in self.audit_refs:
            value = str(raw or "").strip()
            if not value or value in seen_refs:
                continue
            seen_refs.add(value)
            refs.append(value)
        self.audit_refs = refs[-MAX_CONTEXT_AUDIT_REFS:]
        return self


class ContextOperationResult(StrictModel):
    status: OperationStatus
    context: Optional[CanonicalContext] = None
    error_kind: Optional[ContextErrorKind] = None
    detail: str = ""
    path: str = ""
    quarantine_path: str = ""
    migrated: bool = False
    deduplicated: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> "ContextOperationResult":
        if self.status == OperationStatus.ok and self.context is None:
            raise ValueError("ok result requires context")
        if self.status == OperationStatus.not_found:
            self.error_kind = ContextErrorKind.not_found
        if self.status == OperationStatus.error and self.error_kind is None:
            raise ValueError("error result requires error_kind")
        return self


class EntryResult(StrictModel):
    schema_version: str = V4_ENTRY_SCHEMA_VERSION
    status: EntryStatus
    action: IntentAction
    handler_key: str = ""
    side_effect_started: bool = False
    fallback_allowed: bool = False
    fallback_reason: str = ""
    response: Optional[V4Response] = None
    audit: Optional[V4AuditRecord] = None
    context: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != V4_ENTRY_SCHEMA_VERSION:
            raise ValueError("unsupported V4 entry schema version")
        return value

    @model_validator(mode="after")
    def validate_entry_boundary(self) -> "EntryResult":
        if self.side_effect_started:
            self.fallback_allowed = False
        if self.status == EntryStatus.fallback:
            if not self.fallback_allowed or not self.fallback_reason:
                raise ValueError("fallback requires allowed=true and a reason")
        if self.status == EntryStatus.clarification:
            if self.action != IntentAction.need_clarification:
                raise ValueError("clarification status requires need_clarification action")
        return self
