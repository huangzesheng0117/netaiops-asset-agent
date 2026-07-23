# -*- coding: utf-8 -*-
"""
V3 Intent Arbiter schema.

This module only defines structured intent data and deterministic validation.
It does not call LLM, CMDB, MCP or execute any device command.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator, model_validator


INTENT_SCHEMA_VERSION = "v3_intent_arbiter_1"
CONFIDENCE_ACCEPT_THRESHOLD = 0.80
CONFIDENCE_CLARIFY_THRESHOLD = 0.50


CMDB_QUERY_OPERATIONS = frozenset({"auto", "devices", "detail", "by_ips"})
COMMAND_GENERATION_CATEGORIES = frozenset(
    {
        "device_health",
        "cpu",
        "memory",
        "route_table",
        "bgp",
        "bfd",
        "interface_status",
        "interface_error",
        "optical_power",
        "log",
    }
)


class CmdbQuerySpec(BaseModel):
    """Structured CMDB arguments selected by the LLM Intent Arbiter."""

    operation: str = "auto"
    keyword: str = ""
    filters: Dict[str, Any] = Field(default_factory=dict)
    fields: List[str] = Field(default_factory=list)
    ips: List[str] = Field(default_factory=list)
    page: int = 1
    page_size: int = 20

    @field_validator("operation", mode="before")
    @classmethod
    def normalize_operation(cls, value: Any) -> str:
        raw = str(value or "auto").strip().lower()
        aliases = {
            "list": "devices",
            "query": "devices",
            "device": "detail",
            "detail_query": "detail",
            "ips": "by_ips",
            "by_ip": "by_ips",
        }
        normalized = aliases.get(raw, raw)
        if normalized not in CMDB_QUERY_OPERATIONS:
            raise ValueError(f"unsupported CMDB operation: {normalized}")
        return normalized

    @field_validator("keyword", mode="before")
    @classmethod
    def normalize_keyword(cls, value: Any) -> str:
        return str(value or "").strip()[:512]

    @field_validator("filters", mode="before")
    @classmethod
    def normalize_filters(cls, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key).strip(): item
            for key, item in value.items()
            if str(key).strip() and item not in (None, "")
        }

    @field_validator("fields", "ips", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            values = [part.strip() for part in value.replace("，", ",").split(",")]
        elif isinstance(value, list):
            values = [str(item).strip() for item in value]
        else:
            return []
        result: List[str] = []
        for item in values:
            if item and item not in result:
                result.append(item)
        return result[:100]

    @field_validator("page", mode="before")
    @classmethod
    def normalize_page(cls, value: Any) -> int:
        try:
            return max(1, int(value or 1))
        except Exception:
            return 1

    @field_validator("page_size", mode="before")
    @classmethod
    def normalize_page_size(cls, value: Any) -> int:
        try:
            return min(max(1, int(value or 20)), 100)
        except Exception:
            return 20


class CommandGenerationSpec(BaseModel):
    """Structured deterministic command-generation arguments."""

    category: str = "device_health"
    interface: str = ""
    max_commands: int = 8

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: Any) -> str:
        raw = str(value or "device_health").strip().lower()
        aliases = {
            "health": "device_health",
            "cpu_check": "cpu",
            "memory_check": "memory",
            "route": "route_table",
            "routing": "route_table",
            "interface": "interface_status",
            "interface_down": "interface_status",
            "interface_errors": "interface_error",
            "optical": "optical_power",
            "transceiver": "optical_power",
            "logs": "log",
        }
        normalized = aliases.get(raw, raw)
        if normalized not in COMMAND_GENERATION_CATEGORIES:
            raise ValueError(f"unsupported command generation category: {normalized}")
        return normalized

    @field_validator("interface", mode="before")
    @classmethod
    def normalize_interface(cls, value: Any) -> str:
        return str(value or "").strip()[:256]

    @field_validator("max_commands", mode="before")
    @classmethod
    def normalize_max_commands(cls, value: Any) -> int:
        try:
            return min(max(1, int(value or 8)), 8)
        except Exception:
            return 8


class IntentAction(str, Enum):
    generate_commands = "generate_commands"
    execute_provided_commands = "execute_provided_commands"
    execute_provided_commands_and_analyze = "execute_provided_commands_and_analyze"
    confirm_execute_pending = "confirm_execute_pending"
    analyze_existing_evidence = "analyze_existing_evidence"
    advice_analysis = "advice_analysis"
    cmdb_query = "cmdb_query"
    general_chat = "general_chat"
    need_clarification = "need_clarification"


class IntentDecision(BaseModel):
    schema_version: str = INTENT_SCHEMA_VERSION
    action: IntentAction
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    device_required: bool = False
    device_hint: str = ""
    commands_provided: bool = False
    commands: List[str] = Field(default_factory=list)

    need_existing_evidence: bool = False
    should_generate_commands: bool = False
    should_execute_commands: bool = False
    should_analyze_after_execution: bool = False

    # V3 confirmed rule:
    # If user directly provides commands, chatbot does not require a second confirmation.
    requires_confirmation: bool = False

    clarification_question: str = ""
    reason: str = ""
    raw_user_text: str = ""
    context_summary: str = ""
    cmdb_query: CmdbQuerySpec = Field(default_factory=CmdbQuerySpec)
    command_generation: CommandGenerationSpec = Field(
        default_factory=CommandGenerationSpec
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "schema_version",
        "device_hint",
        "clarification_question",
        "reason",
        "raw_user_text",
        "context_summary",
        mode="before",
    )
    @classmethod
    def none_to_empty_string(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("commands", mode="before")
    @classmethod
    def normalize_commands(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @model_validator(mode="after")
    def normalize_action_flags(self) -> "IntentDecision":
        action = self.action

        if action == IntentAction.generate_commands:
            self.should_generate_commands = True
            self.should_execute_commands = False
            self.should_analyze_after_execution = False
            self.commands_provided = False
            self.commands = []
            self.requires_confirmation = True

        if action in {
            IntentAction.execute_provided_commands,
            IntentAction.execute_provided_commands_and_analyze,
            IntentAction.confirm_execute_pending,
        }:
            self.should_execute_commands = True

        if action == IntentAction.execute_provided_commands_and_analyze:
            self.should_analyze_after_execution = True

        if action == IntentAction.analyze_existing_evidence:
            self.need_existing_evidence = True

        if self.commands:
            self.commands_provided = True

        if action in {
            IntentAction.execute_provided_commands,
            IntentAction.execute_provided_commands_and_analyze,
        }:
            self.requires_confirmation = False

        if action == IntentAction.need_clarification and not self.clarification_question:
            self.clarification_question = "请补充更明确的设备、目标或操作意图。"

        return self

    @property
    def llm_confidence(self) -> float:
        return self.confidence

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= CONFIDENCE_ACCEPT_THRESHOLD

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < CONFIDENCE_CLARIFY_THRESHOLD


def build_need_clarification(question: str, reason: str) -> IntentDecision:
    return IntentDecision(
        action=IntentAction.need_clarification,
        confidence=0.0,
        raw_user_text=question or "",
        reason=reason or "need_clarification",
    )
