# -*- coding: utf-8 -*-
"""NetAIOps V4 core package.

V4.2-1 exports versioned contracts, canonical context storage, legacy migration,
and audit adaptation. V4.2-2 adds low-risk handlers and unified responses.
V4.2-3 adds the pre-route Entry Router and FastAPI bridge.
"""

from netaiops_asset.chat_v4.audit_adapter import (
    attach_audit_reference,
    build_audit_record,
)
from netaiops_asset.chat_v4.context_migration import (
    build_canonical_from_legacy,
    load_or_migrate,
)
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    V4_AUDIT_SCHEMA_VERSION,
    V4_CONTEXT_SCHEMA_VERSION,
    V4_ENTRY_SCHEMA_VERSION,
    V4_RESPONSE_SCHEMA_VERSION,
    CanonicalContext,
    ContextErrorKind,
    ContextMigration,
    ContextOperationResult,
    ContextTurn,
    EntryResult,
    EntryStatus,
    OperationStatus,
    V4AuditRecord,
    V4Response,
    V4ResponseMeta,
)
from netaiops_asset.chat_v4.entry_router import (
    V4_ENTRY_ROUTER_VERSION,
    EntryRouteResult,
    V4EntryRouter,
    canonical_to_followup_context,
    route_v4_entry,
)

__all__ = [
    "V4_ENTRY_SCHEMA_VERSION",
    "V4_RESPONSE_SCHEMA_VERSION",
    "V4_AUDIT_SCHEMA_VERSION",
    "V4_CONTEXT_SCHEMA_VERSION",
    "V4_ENTRY_ROUTER_VERSION",
    "EntryStatus",
    "OperationStatus",
    "ContextErrorKind",
    "V4ResponseMeta",
    "V4Response",
    "V4AuditRecord",
    "ContextTurn",
    "ContextMigration",
    "CanonicalContext",
    "ContextOperationResult",
    "EntryResult",
    "ContextStore",
    "build_canonical_from_legacy",
    "load_or_migrate",
    "build_audit_record",
    "attach_audit_reference",
    "EntryRouteResult",
    "V4EntryRouter",
    "canonical_to_followup_context",
    "route_v4_entry",
]
