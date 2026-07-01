#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


VALID_LEGACY_ROUTE_TYPES = frozenset(
    {
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
)

VALID_MIGRATION_STAGES = frozenset(
    {
        "inventory_only",
        "metadata_only",
        "v3.4-3",
        "v3.4-4",
        "v3.4-5",
        "v3.4-6",
        "deferred_to_v3.5",
        "deferred",
        "unknown",
    }
)

MAPPED_V3_ACTION_BY_ROUTE_TYPE: dict[str, str | None] = {
    "general_chat": "general_chat",
    "advice_analysis": "advice_analysis",
    "followup": "analyze_existing_evidence",
    "cmdb_query": "cmdb_query",
    "command_explanation": "general_chat",
    "command_execution": "execute_provided_commands",
    "config_change": None,
    "inline_command": None,
    "semantic_route": None,
    "batch_route": None,
    "unknown": None,
}

RISK_BOUNDARY_BY_ROUTE_TYPE: dict[str, str] = {
    "general_chat": "low",
    "advice_analysis": "low",
    "followup": "medium",
    "cmdb_query": "medium",
    "command_explanation": "low",
    "command_execution": "high",
    "config_change": "blocked",
    "inline_command": "medium",
    "semantic_route": "medium",
    "batch_route": "medium",
    "unknown": "unknown",
}

FALLBACK_POLICY_BY_ROUTE_TYPE: dict[str, str] = {
    "general_chat": "v2_general_chat_or_existing_return",
    "advice_analysis": "v2_advice_analysis",
    "followup": "v2_followup_or_existing_context_logic",
    "cmdb_query": "v2_cmdb_query",
    "command_explanation": "v2_general_chat_or_command_explain",
    "command_execution": "v2_inline_command_execute_with_existing_safety",
    "config_change": "blocked_or_existing_safety_guard",
    "inline_command": "v2_inline_command_branch",
    "semantic_route": "existing_semantic_route_fallback",
    "batch_route": "existing_batch_route_fallback",
    "unknown": "v2_fallback",
}

PROHIBITED_NATURAL_LANGUAGE_FIELDS = frozenset(
    {
        "question",
        "context",
        "snippet",
        "prompt",
        "message",
        "raw_text",
        "text",
        "user_input",
        "user_message",
    }
)


@dataclass(frozen=True)
class LegacyRouteDescriptor:
    legacy_branch_id: str
    explicit_legacy_route_type: str
    source_function: str
    return_path: str
    known_legacy_behavior: str = ""
    migration_stage: str = "unknown"


@dataclass(frozen=True)
class LegacyRouteMetadata:
    legacy_branch_id: str
    legacy_route_type: str
    source_function: str
    return_path: str
    known_legacy_behavior: str
    mapped_v3_action: str | None
    migration_stage: str
    fallback_policy: str
    risk_boundary: str
    runtime_takeover_allowed: bool
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LegacyRouteResolution:
    descriptor: LegacyRouteDescriptor
    metadata: LegacyRouteMetadata
    fallback_required: bool
    arbiter_required: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": asdict(self.descriptor),
            "metadata": self.metadata.to_dict(),
            "fallback_required": self.fallback_required,
            "arbiter_required": self.arbiter_required,
            "reason": self.reason,
        }


DEFAULT_LEGACY_ROUTE_REGISTRY: dict[str, LegacyRouteMetadata] = {
    "v2_general_chat_return": LegacyRouteMetadata(
        legacy_branch_id="v2_general_chat_return",
        legacy_route_type="general_chat",
        source_function="chat",
        return_path="route_return",
        known_legacy_behavior="existing low-risk general response path",
        mapped_v3_action="general_chat",
        migration_stage="v3.4-3",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["general_chat"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["general_chat"],
        runtime_takeover_allowed=False,
        notes="metadata only; user intent remains decided by LLM Intent Arbiter",
    ),
    "v2_advice_analysis_return": LegacyRouteMetadata(
        legacy_branch_id="v2_advice_analysis_return",
        legacy_route_type="advice_analysis",
        source_function="v2_chat_router_middleware",
        return_path="JSONResponse",
        known_legacy_behavior="existing pure advice analysis path",
        mapped_v3_action="advice_analysis",
        migration_stage="v3.4-3",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["advice_analysis"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["advice_analysis"],
        runtime_takeover_allowed=False,
        notes="metadata only; user intent remains decided by LLM Intent Arbiter",
    ),
    "v2_followup_return": LegacyRouteMetadata(
        legacy_branch_id="v2_followup_return",
        legacy_route_type="followup",
        source_function="v2_chat_router_middleware",
        return_path="JSONResponse",
        known_legacy_behavior="existing multi-turn context continuation path",
        mapped_v3_action="analyze_existing_evidence",
        migration_stage="v3.4-4",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["followup"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["followup"],
        runtime_takeover_allowed=False,
        notes="metadata only; follow-up convergence is deferred to V3.4-4",
    ),
    "v2_cmdb_query_return": LegacyRouteMetadata(
        legacy_branch_id="v2_cmdb_query_return",
        legacy_route_type="cmdb_query",
        source_function="chat",
        return_path="route_return",
        known_legacy_behavior="existing CMDB networkServer query path",
        mapped_v3_action="cmdb_query",
        migration_stage="deferred",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["cmdb_query"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["cmdb_query"],
        runtime_takeover_allowed=False,
        notes="metadata only; CMDB execution remains an explicit dispatcher action",
    ),
    "v2_inline_command_return": LegacyRouteMetadata(
        legacy_branch_id="v2_inline_command_return",
        legacy_route_type="inline_command",
        source_function="v2_chat_router_middleware",
        return_path="JSONResponse",
        known_legacy_behavior="existing inline command branch",
        mapped_v3_action=None,
        migration_stage="v3.4-5",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["inline_command"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["inline_command"],
        runtime_takeover_allowed=False,
        notes="metadata only; command splitting and safety convergence are not part of V3.4-2-fix",
    ),
    "v2_command_execution_return": LegacyRouteMetadata(
        legacy_branch_id="v2_command_execution_return",
        legacy_route_type="command_execution",
        source_function="v2_chat_router_middleware",
        return_path="JSONResponse",
        known_legacy_behavior="existing command execution branch",
        mapped_v3_action="execute_provided_commands",
        migration_stage="deferred_to_v3.5",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["command_execution"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["command_execution"],
        runtime_takeover_allowed=False,
        notes="metadata only; real execution must stay behind splitter and safety guard",
    ),
    "v2_semantic_route_return": LegacyRouteMetadata(
        legacy_branch_id="v2_semantic_route_return",
        legacy_route_type="semantic_route",
        source_function="v2_chat_router_middleware",
        return_path="JSONResponse",
        known_legacy_behavior="existing semantic route branch",
        mapped_v3_action=None,
        migration_stage="v3.4-6",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["semantic_route"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["semantic_route"],
        runtime_takeover_allowed=False,
        notes="metadata only; semantic branch must not remain a primary route judge",
    ),
    "v2_batch_route_return": LegacyRouteMetadata(
        legacy_branch_id="v2_batch_route_return",
        legacy_route_type="batch_route",
        source_function="v2_chat_router_middleware",
        return_path="JSONResponse",
        known_legacy_behavior="existing batch branch",
        mapped_v3_action=None,
        migration_stage="v3.4-6",
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE["batch_route"],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE["batch_route"],
        runtime_takeover_allowed=False,
        notes="metadata only; batch branch convergence is deferred",
    ),
}


def validate_legacy_route_type(route_type: str) -> str:
    normalized = (route_type or "").strip()
    if normalized not in VALID_LEGACY_ROUTE_TYPES:
        raise ValueError(f"invalid legacy route type: {normalized!r}")
    return normalized


def validate_migration_stage(stage: str) -> str:
    normalized = (stage or "unknown").strip()
    if normalized not in VALID_MIGRATION_STAGES:
        raise ValueError(f"invalid migration stage: {normalized!r}")
    return normalized


def legacy_route_to_v3_action(explicit_legacy_route_type: str) -> str | None:
    route_type = validate_legacy_route_type(explicit_legacy_route_type)
    return MAPPED_V3_ACTION_BY_ROUTE_TYPE[route_type]


def descriptor_from_dict(data: dict[str, Any]) -> LegacyRouteDescriptor:
    prohibited = sorted(PROHIBITED_NATURAL_LANGUAGE_FIELDS.intersection(data))
    if prohibited:
        raise ValueError(
            "Legacy Route Registry accepts explicit legacy route descriptors only; "
            f"natural-language fields are not allowed: {prohibited}"
        )

    route_type = data.get("explicit_legacy_route_type") or data.get("legacy_route_type")
    descriptor = LegacyRouteDescriptor(
        legacy_branch_id=str(data.get("legacy_branch_id") or "").strip(),
        explicit_legacy_route_type=validate_legacy_route_type(str(route_type or "")),
        source_function=str(data.get("source_function") or "").strip(),
        return_path=str(data.get("return_path") or "").strip(),
        known_legacy_behavior=str(data.get("known_legacy_behavior") or "").strip(),
        migration_stage=validate_migration_stage(str(data.get("migration_stage") or "unknown")),
    )
    if not descriptor.legacy_branch_id:
        raise ValueError("legacy_branch_id is required")
    if not descriptor.source_function:
        raise ValueError("source_function is required")
    if not descriptor.return_path:
        raise ValueError("return_path is required")
    return descriptor


def get_legacy_route_metadata(legacy_branch_id: str) -> LegacyRouteMetadata | None:
    return DEFAULT_LEGACY_ROUTE_REGISTRY.get((legacy_branch_id or "").strip())


def list_legacy_route_metadata() -> list[LegacyRouteMetadata]:
    return [DEFAULT_LEGACY_ROUTE_REGISTRY[key] for key in sorted(DEFAULT_LEGACY_ROUTE_REGISTRY)]


def _metadata_from_descriptor(descriptor: LegacyRouteDescriptor) -> LegacyRouteMetadata:
    route_type = validate_legacy_route_type(descriptor.explicit_legacy_route_type)
    stage = validate_migration_stage(descriptor.migration_stage)
    return LegacyRouteMetadata(
        legacy_branch_id=descriptor.legacy_branch_id,
        legacy_route_type=route_type,
        source_function=descriptor.source_function,
        return_path=descriptor.return_path,
        known_legacy_behavior=descriptor.known_legacy_behavior,
        mapped_v3_action=MAPPED_V3_ACTION_BY_ROUTE_TYPE[route_type],
        migration_stage=stage,
        fallback_policy=FALLBACK_POLICY_BY_ROUTE_TYPE[route_type],
        risk_boundary=RISK_BOUNDARY_BY_ROUTE_TYPE[route_type],
        runtime_takeover_allowed=False,
        notes="metadata only; runtime intent must be decided by LLM Intent Arbiter",
    )


def resolve_legacy_route(descriptor: LegacyRouteDescriptor) -> LegacyRouteResolution:
    validate_legacy_route_type(descriptor.explicit_legacy_route_type)
    validate_migration_stage(descriptor.migration_stage)

    registered = get_legacy_route_metadata(descriptor.legacy_branch_id)
    metadata = registered if registered is not None else _metadata_from_descriptor(descriptor)

    if metadata.legacy_route_type != descriptor.explicit_legacy_route_type:
        raise ValueError(
            "legacy_branch_id route type conflicts with explicit_legacy_route_type: "
            f"{metadata.legacy_route_type!r} != {descriptor.explicit_legacy_route_type!r}"
        )

    return LegacyRouteResolution(
        descriptor=descriptor,
        metadata=metadata,
        fallback_required=True,
        arbiter_required=True,
        reason=(
            "Legacy Route Registry is metadata-only. "
            "User intent must be decided by LLM Intent Arbiter; V2 fallback remains available."
        ),
    )


def resolve_legacy_route_dict(data: dict[str, Any]) -> LegacyRouteResolution:
    return resolve_legacy_route(descriptor_from_dict(data))


def registry_metadata() -> dict[str, Any]:
    return {
        "version": "v3.4.2-fix",
        "purpose": "metadata-only legacy route registry",
        "runtime_behavior_change": False,
        "parses_user_text": False,
        "arbiter_is_source_of_truth": True,
        "valid_legacy_route_types": sorted(VALID_LEGACY_ROUTE_TYPES),
        "valid_migration_stages": sorted(VALID_MIGRATION_STAGES),
        "registered_branch_ids": sorted(DEFAULT_LEGACY_ROUTE_REGISTRY),
        "mapped_v3_action_by_route_type": dict(sorted(MAPPED_V3_ACTION_BY_ROUTE_TYPE.items())),
        "risk_boundary_by_route_type": dict(sorted(RISK_BOUNDARY_BY_ROUTE_TYPE.items())),
    }


__all__ = [
    "LegacyRouteDescriptor",
    "LegacyRouteMetadata",
    "LegacyRouteResolution",
    "descriptor_from_dict",
    "get_legacy_route_metadata",
    "legacy_route_to_v3_action",
    "list_legacy_route_metadata",
    "registry_metadata",
    "resolve_legacy_route",
    "resolve_legacy_route_dict",
    "validate_legacy_route_type",
    "validate_migration_stage",
]
