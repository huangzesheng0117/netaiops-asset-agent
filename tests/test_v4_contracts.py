# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from pydantic import ValidationError

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.audit_adapter import build_audit_record
from netaiops_asset.chat_v4.contracts import (
    CanonicalContext,
    ContextMigration,
    ContextTurn,
    EntryResult,
    EntryStatus,
    V4AuditRecord,
    V4Response,
    V4ResponseMeta,
)


class V4ContractTests(unittest.TestCase):
    def test_response_contract_normalizes_counts(self):
        response = V4Response(
            items=[{"name": "device01"}],
            action=IntentAction.cmdb_query,
            v4=V4ResponseMeta(handler_key="cmdb_query", confidence=0.93),
        )
        self.assertEqual(response.count, 1)
        self.assertEqual(response.returned, 1)
        self.assertEqual(response.v4.schema_version, "v4.response.v1")

    def test_response_rejects_returned_greater_than_count(self):
        with self.assertRaises(ValidationError):
            V4Response(count=1, returned=2)

    def test_entry_fallback_requires_reason_and_allowed(self):
        with self.assertRaises(ValidationError):
            EntryResult(
                status=EntryStatus.fallback,
                action=IntentAction.general_chat,
                fallback_allowed=False,
            )
        entry = EntryResult(
            status=EntryStatus.fallback,
            action=IntentAction.general_chat,
            fallback_allowed=True,
            fallback_reason="llm_transport_error",
        )
        self.assertTrue(entry.fallback_allowed)

    def test_entry_side_effect_disables_fallback(self):
        entry = EntryResult(
            status=EntryStatus.error,
            action=IntentAction.execute_provided_commands,
            side_effect_started=True,
            fallback_allowed=True,
        )
        self.assertFalse(entry.fallback_allowed)

    def test_clarification_status_requires_clarification_action(self):
        with self.assertRaises(ValidationError):
            EntryResult(
                status=EntryStatus.clarification,
                action=IntentAction.general_chat,
            )
        entry = EntryResult(
            status=EntryStatus.clarification,
            action=IntentAction.need_clarification,
        )
        self.assertEqual(entry.action, IntentAction.need_clarification)

    def test_context_turn_fingerprint_and_context_dedupe(self):
        first = ContextTurn(
            question="继续分析",
            answer_summary="结论不变",
            action=IntentAction.analyze_existing_evidence,
        )
        duplicate = ContextTurn(
            question="继续分析",
            answer_summary="结论不变",
            action=IntentAction.analyze_existing_evidence,
        )
        context = CanonicalContext(
            conversation_id="conversation-contract-001",
            recent_turns=[first, duplicate],
            audit_refs=["audit-1", "audit-1", "audit-2"],
            migration=ContextMigration(status="native"),
        )
        self.assertEqual(len(context.recent_turns), 1)
        self.assertEqual(context.audit_refs, ["audit-1", "audit-2"])
        self.assertEqual(len(first.turn_fingerprint), 64)

    def test_schema_versions_are_strict(self):
        with self.assertRaises(ValidationError):
            CanonicalContext(
                schema_version="v4.context.v999",
                conversation_id="conversation-contract-002",
            )
        with self.assertRaises(ValidationError):
            V4AuditRecord(schema_version="v4.audit.v999")

    def test_audit_adapter_redacts_secrets_and_side_effect_fallback(self):
        record = build_audit_record(
            conversation_id="conversation-contract-003",
            request_id="request-1",
            action=IntentAction.execute_provided_commands,
            handler_key="execute_provided_commands",
            status="error",
            side_effect_started=True,
            fallback_allowed=True,
            metadata={
                "api_key": "top-secret",
                "nested": {"access_token": "secret-token"},
                "max_tokens": 1200,
            },
        )
        self.assertFalse(record.fallback_allowed)
        self.assertEqual(record.metadata["api_key"], "[REDACTED]")
        self.assertEqual(
            record.metadata["nested"]["access_token"],
            "[REDACTED]",
        )
        self.assertEqual(record.metadata["max_tokens"], 1200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
