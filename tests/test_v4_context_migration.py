# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from netaiops_asset.chat_v4.context_migration import (
    build_canonical_from_legacy,
    load_or_migrate,
)
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    ContextErrorKind,
    OperationStatus,
)


class V4ContextMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "v4_context"
        self.store = ContextStore(self.root)
        self.conversation_id = "conversation-migration-001"
        self.followup_source = {
            "available": True,
            "source": "v2_conversation_context+v3_followup_context_store",
            "turn_count": 1,
            "original_conversation_id": self.conversation_id,
            "arbiter_context": {
                "current_device": {
                    "device_name": "SW01",
                    "mgmt_ip": "10.0.0.1",
                },
                "current_topic": "BGP",
                "current_intent": "analyze_existing_evidence",
                "rolling_summary": "用户正在排查 BGP。",
                "recent_turns": [
                    {
                        "question": "BGP 是否正常？",
                        "answer_summary": "邻居当前已建立。",
                        "action": "analyze_existing_evidence",
                        "effective_conversation_id": "effective-001",
                    }
                ],
                "last_executions": [
                    {
                        "command": "show bgp summary",
                        "status": "success",
                        "raw_output": "x" * 5000,
                    }
                ],
                "last_analysis": {
                    "conclusion": "BGP 正常",
                    "api_key": "must-redact",
                },
            },
            "generator_context": {},
        }
        self.legacy_source = {
            "conversation_id": self.conversation_id,
            "title": "BGP 排查",
            "user": "tester",
            "created_at": "2026-07-10T00:00:00+00:00",
            "updated_at": "2026-07-10T00:10:00+00:00",
            "turns": [
                {
                    "turn_id": "legacy-turn-1",
                    "time": "2026-07-10T00:05:00+00:00",
                    "question": "BGP 是否正常？",
                    "response": {
                        "answer": "邻居当前已建立。",
                        "action": "analyze_existing_evidence",
                        "planner_source": "v3_response_generator",
                        "conversation_id": "effective-001",
                    },
                }
            ],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def followup_loader(self, conversation_id, user):
        self.assertEqual(conversation_id, self.conversation_id)
        self.assertEqual(user, "tester")
        return self.followup_source

    def legacy_loader(self, conversation_id):
        self.assertEqual(conversation_id, self.conversation_id)
        return self.legacy_source

    def test_build_canonical_from_legacy(self):
        result = build_canonical_from_legacy(
            self.conversation_id,
            "tester",
            followup_loader=self.followup_loader,
            legacy_loader=self.legacy_loader,
        )
        self.assertEqual(result.status, OperationStatus.ok)
        self.assertTrue(result.migrated)
        context = result.context
        self.assertEqual(context.title, "BGP 排查")
        self.assertEqual(context.topic, "BGP")
        self.assertEqual(context.device_context["device_name"], "SW01")
        self.assertEqual(context.migration.status, "migrated")
        self.assertIn(
            "legacy_conversation_store",
            context.migration.sources,
        )
        self.assertEqual(
            context.migration.effective_conversation_id,
            "effective-001",
        )
        self.assertEqual(len(context.recent_turns), 1)
        self.assertLessEqual(
            len(context.execution_evidence[0]["raw_output"]),
            2000,
        )
        self.assertEqual(
            context.analysis_history[0]["value"]["api_key"],
            "[REDACTED]",
        )

    def test_lazy_migration_persists_once_and_does_not_mutate_sources(self):
        followup_before = repr(self.followup_source)
        legacy_before = repr(self.legacy_source)
        first = load_or_migrate(
            self.store,
            self.conversation_id,
            "tester",
            followup_loader=self.followup_loader,
            legacy_loader=self.legacy_loader,
        )
        self.assertEqual(first.status, OperationStatus.ok)
        self.assertTrue(first.migrated)
        self.assertEqual(first.context.revision, 1)
        self.assertEqual(repr(self.followup_source), followup_before)
        self.assertEqual(repr(self.legacy_source), legacy_before)

        def fail_followup(*args, **kwargs):
            raise AssertionError("legacy loader must not run after migration")

        second = load_or_migrate(
            self.store,
            self.conversation_id,
            "tester",
            followup_loader=fail_followup,
            legacy_loader=lambda value: fail_followup(value),
        )
        self.assertEqual(second.status, OperationStatus.ok)
        self.assertFalse(second.migrated)
        self.assertEqual(second.context.revision, 1)

    def test_no_legacy_source_returns_not_found(self):
        result = build_canonical_from_legacy(
            self.conversation_id,
            "tester",
            followup_loader=lambda conversation_id, user: {},
            legacy_loader=lambda conversation_id: {},
        )
        self.assertEqual(result.status, OperationStatus.not_found)
        self.assertEqual(result.error_kind, ContextErrorKind.not_found)

    def test_legacy_loader_error_is_observable(self):
        def broken_loader(conversation_id, user):
            raise PermissionError("legacy denied")

        result = build_canonical_from_legacy(
            self.conversation_id,
            "tester",
            followup_loader=broken_loader,
            legacy_loader=lambda conversation_id: {},
        )
        self.assertEqual(result.status, OperationStatus.error)
        self.assertEqual(result.error_kind, ContextErrorKind.migration)
        self.assertIn("PermissionError", result.detail)

    def test_corrupt_v4_context_blocks_lazy_migration(self):
        self.store._ensure_dirs()
        path = self.store.path_for(self.conversation_id)
        path.write_text("{broken", encoding="utf-8")
        result = load_or_migrate(
            self.store,
            self.conversation_id,
            "tester",
            followup_loader=self.followup_loader,
            legacy_loader=self.legacy_loader,
        )
        self.assertEqual(result.status, OperationStatus.error)
        self.assertEqual(result.error_kind, ContextErrorKind.corrupt)
        self.assertTrue(Path(result.quarantine_path).exists())
        self.assertFalse(self.store.path_for(self.conversation_id).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
