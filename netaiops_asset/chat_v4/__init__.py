# -*- coding: utf-8 -*-
"""NetAIOps V4 core package.

V4.2-1 exports versioned contracts, canonical context storage, legacy migration,
and audit adaptation. It does not connect V4 to app.py or alter production
routing.
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

__all__ = [
    "V4_ENTRY_SCHEMA_VERSION",
    "V4_RESPONSE_SCHEMA_VERSION",
    "V4_AUDIT_SCHEMA_VERSION",
    "V4_CONTEXT_SCHEMA_VERSION",
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
]
