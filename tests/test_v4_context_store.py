# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    CanonicalContext,
    ContextErrorKind,
    OperationStatus,
)


def _append_worker(root: str, conversation_id: str, index: int) -> None:
    store = ContextStore(root)
    result = store.append_turn(
        conversation_id,
        question=f"question-{index}",
        answer_summary=f"answer-{index}",
        action=IntentAction.general_chat,
        planner_source="unit",
        request_user_field="worker",
    )
    if result.status != OperationStatus.ok:
        raise RuntimeError(result.model_dump())


class V4ContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "v4_context"
        self.store = ContextStore(self.root)
        self.conversation_id = "conversation-store-001"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_round_trip_atomic_write_and_no_tmp_residue(self):
        context = self.store.new_context(
            self.conversation_id,
            request_user_field="tester",
            title="Store Test",
        )
        context.topic = "BGP"
        result = self.store.save(context, expected_revision=0)
        self.assertEqual(result.status, OperationStatus.ok)
        self.assertEqual(result.context.revision, 1)
        path = Path(result.path)
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertFalse(list(path.parent.glob("*.tmp")))
        loaded = self.store.load(self.conversation_id)
        self.assertEqual(loaded.status, OperationStatus.ok)
        self.assertEqual(loaded.context.topic, "BGP")
        self.assertEqual(loaded.context.request_user_field, "tester")


    def test_in_place_mutator_returning_none_is_persisted(self):
        def mutator(context):
            context.topic = "OSPF"
            return None

        result = self.store.update(
            self.conversation_id,
            mutator,
            request_user_field="tester",
        )
        self.assertEqual(result.status, OperationStatus.ok)
        self.assertEqual(result.context.topic, "OSPF")
        loaded = self.store.load(self.conversation_id)
        self.assertEqual(loaded.context.topic, "OSPF")

    def test_duplicate_turn_is_not_appended_twice(self):
        first = self.store.append_turn(
            self.conversation_id,
            question="same question",
            answer_summary="same answer",
            action=IntentAction.general_chat,
        )
        second = self.store.append_turn(
            self.conversation_id,
            question="same question",
            answer_summary="same answer",
            action=IntentAction.general_chat,
        )
        self.assertEqual(first.status, OperationStatus.ok)
        self.assertEqual(second.status, OperationStatus.ok)
        self.assertFalse(first.deduplicated)
        self.assertTrue(second.deduplicated)
        loaded = self.store.load(self.conversation_id)
        self.assertEqual(len(loaded.context.recent_turns), 1)

    def test_sensitive_values_redacted_and_raw_output_bounded(self):
        huge = "x" * 10000
        context = CanonicalContext(
            conversation_id=self.conversation_id,
            metadata={
                "api_key": "secret",
                "nested": {
                    "password": "secret2",
                    "auth_token": "secret3",
                    "client_secret": "secret4",
                },
                "max_tokens": 1200,
                "raw_output": huge,
                "output_preview": huge,
            },
        )
        result = self.store.save(context, expected_revision=0)
        self.assertEqual(result.status, OperationStatus.ok)
        metadata = result.context.metadata
        self.assertEqual(metadata["api_key"], "[REDACTED]")
        self.assertEqual(metadata["nested"]["password"], "[REDACTED]")
        self.assertEqual(metadata["nested"]["auth_token"], "[REDACTED]")
        self.assertEqual(metadata["nested"]["client_secret"], "[REDACTED]")
        self.assertEqual(metadata["max_tokens"], 1200)
        self.assertLessEqual(len(metadata["raw_output"]), 2000)
        self.assertTrue(metadata["raw_output"].endswith("...<truncated>"))
        self.assertLessEqual(len(metadata["output_preview"]), 2000)

    def test_corrupt_file_is_quarantined_and_not_overwritten(self):
        self.store._ensure_dirs()
        path = self.store.path_for(self.conversation_id)
        path.write_text("{broken-json", encoding="utf-8")
        loaded = self.store.load(self.conversation_id)
        self.assertEqual(loaded.status, OperationStatus.error)
        self.assertEqual(loaded.error_kind, ContextErrorKind.corrupt)
        self.assertFalse(path.exists())
        self.assertTrue(Path(loaded.quarantine_path).is_file())
        update = self.store.update(
            self.conversation_id,
            lambda context: context,
        )
        self.assertEqual(update.status, OperationStatus.ok)
        self.assertEqual(update.context.revision, 1)
        self.assertTrue(Path(loaded.quarantine_path).is_file())

    def test_schema_error_is_quarantined(self):
        self.store._ensure_dirs()
        path = self.store.path_for(self.conversation_id)
        path.write_text(
            json.dumps({
                "schema_version": "v4.context.v999",
                "conversation_id": self.conversation_id,
            }),
            encoding="utf-8",
        )
        loaded = self.store.load(self.conversation_id)
        self.assertEqual(loaded.status, OperationStatus.error)
        self.assertEqual(loaded.error_kind, ContextErrorKind.schema)
        self.assertFalse(path.exists())
        self.assertTrue(Path(loaded.quarantine_path).is_file())

    def test_permission_error_is_observable(self):
        context = self.store.new_context(self.conversation_id)
        saved = self.store.save(context, expected_revision=0)
        self.assertEqual(saved.status, OperationStatus.ok)
        with patch.object(
            self.store,
            "_read_bytes",
            side_effect=PermissionError("denied"),
        ):
            loaded = self.store.load(self.conversation_id)
        self.assertEqual(loaded.status, OperationStatus.error)
        self.assertEqual(loaded.error_kind, ContextErrorKind.permission)
        self.assertIn("PermissionError", loaded.detail)

    def test_atomic_replace_failure_preserves_original_and_cleans_temp(self):
        context = self.store.new_context(self.conversation_id)
        first = self.store.save(context, expected_revision=0)
        self.assertEqual(first.status, OperationStatus.ok)
        path = Path(first.path)
        original = path.read_bytes()
        context.topic = "changed"
        with patch(
            "netaiops_asset.chat_v4.context_store.os.replace",
            side_effect=OSError("replace failed"),
        ):
            failed = self.store.save(context, expected_revision=1)
        self.assertEqual(failed.status, OperationStatus.error)
        self.assertEqual(failed.error_kind, ContextErrorKind.write)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_oversized_serialized_context_rejected(self):
        small_store = ContextStore(self.root, max_context_bytes=4096)
        context = CanonicalContext(
            conversation_id=self.conversation_id,
            metadata={
                f"key_{index}": "v" * 200
                for index in range(50)
            },
        )
        result = small_store.save(context, expected_revision=0)
        self.assertEqual(result.status, OperationStatus.error)
        self.assertIn(
            result.error_kind,
            {ContextErrorKind.invalid, ContextErrorKind.write},
        )
        self.assertFalse(small_store.path_for(self.conversation_id).exists())

    def test_revision_conflict_is_explicit(self):
        context = self.store.new_context(self.conversation_id)
        first = self.store.save(context, expected_revision=0)
        self.assertEqual(first.status, OperationStatus.ok)
        stale = first.context.model_copy(deep=True)
        stale.topic = "stale update"
        conflict = self.store.save(stale, expected_revision=0)
        self.assertEqual(conflict.status, OperationStatus.error)
        self.assertEqual(conflict.error_kind, ContextErrorKind.conflict)

    def test_concurrent_process_append_keeps_all_turns(self):
        workers = 8
        ctx = multiprocessing.get_context("fork")
        processes = [
            ctx.Process(
                target=_append_worker,
                args=(str(self.root), self.conversation_id, index),
            )
            for index in range(workers)
        ]
        for process in processes:
            process.start()
        deadline = time.monotonic() + 30
        for process in processes:
            process.join(max(0.0, deadline - time.monotonic()))
        alive = [process for process in processes if process.is_alive()]
        for process in alive:
            process.terminate()
            process.join(5)
        self.assertFalse(alive, "concurrent append workers timed out")
        for process in processes:
            self.assertEqual(process.exitcode, 0)
        loaded = self.store.load(self.conversation_id)
        self.assertEqual(loaded.status, OperationStatus.ok)
        self.assertEqual(len(loaded.context.recent_turns), workers)
        self.assertEqual(loaded.context.revision, workers)

    def test_conversation_id_is_hashed_and_turns_are_bounded(self):
        traversal_like_id = "../unsafe/conversation-id"
        for index in range(35):
            result = self.store.append_turn(
                traversal_like_id,
                question=f"q-{index}",
                answer_summary=f"a-{index}",
                action=IntentAction.general_chat,
            )
            self.assertEqual(result.status, OperationStatus.ok)
        path = self.store.path_for(traversal_like_id)
        self.assertEqual(path.parent, self.store.context_dir)
        self.assertNotIn("unsafe", path.name)
        loaded = self.store.load(traversal_like_id)
        self.assertEqual(loaded.status, OperationStatus.ok)
        self.assertEqual(len(loaded.context.recent_turns), 30)
        self.assertEqual(loaded.context.recent_turns[0].question, "q-5")

    def test_audit_reference_is_bounded_and_deduplicated(self):
        first = self.store.add_audit_ref(
            self.conversation_id,
            "audit-1",
        )
        second = self.store.add_audit_ref(
            self.conversation_id,
            "audit-1",
        )
        self.assertEqual(first.status, OperationStatus.ok)
        self.assertFalse(first.deduplicated)
        self.assertTrue(second.deduplicated)
        loaded = self.store.load(self.conversation_id)
        self.assertEqual(loaded.context.audit_refs, ["audit-1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
