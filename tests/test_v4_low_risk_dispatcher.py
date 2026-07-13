#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from netaiops_asset.chat_v3.intent_schema import IntentAction, IntentDecision
from netaiops_asset.chat_v4.action_dispatcher import (
    LowRiskActionDispatcher,
)
from netaiops_asset.chat_v4.audit_writer import (
    AuditWriteResult,
    AuditWriter,
)
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    CanonicalContext,
    ContextErrorKind,
    ContextOperationResult,
    EntryStatus,
    OperationStatus,
)


def empty_followup_loader(conversation_id, user):
    return {}


def empty_legacy_loader(conversation_id):
    return {}


class FakeLLM:
    def __init__(self):
        self.max_tokens = 1200
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return {
            "status": "ok",
            "content": (
                "这是由隔离测试 LLM 生成的完整回答，用于验证低风险 "
                "Handler、统一 Response、Context 和 Audit 主路径。"
            ),
            "requested_model": "glm-5.2",
            "reported_model": "glm-5.2",
            "finish_reason": "stop",
            "max_tokens_used": kwargs.get("max_tokens"),
            "content_length": 60,
        }


class FailingAuditWriter:
    def write(self, record):
        return AuditWriteResult(
            status=OperationStatus.error,
            error_kind=ContextErrorKind.write,
            detail="injected audit write failure",
        )


class FailingAppendStore(ContextStore):
    def append_turn(self, *args, **kwargs):
        return ContextOperationResult(
            status=OperationStatus.error,
            error_kind=ContextErrorKind.write,
            detail="injected context append failure",
        )


class FailingAuditRefStore(ContextStore):
    def add_audit_ref(self, *args, **kwargs):
        return ContextOperationResult(
            status=OperationStatus.error,
            error_kind=ContextErrorKind.write,
            detail="injected audit ref failure",
        )


class ReadErrorStore(ContextStore):
    def load(self, *args, **kwargs):
        return ContextOperationResult(
            status=OperationStatus.error,
            error_kind=ContextErrorKind.permission,
            detail="injected context read failure",
        )


class V4LowRiskDispatcherTests(unittest.TestCase):
    def _dispatcher(
        self,
        context_root,
        audit_root,
        *,
        store_cls=ContextStore,
        writer=None,
        llm=None,
    ):
        return LowRiskActionDispatcher(
            store=store_cls(root=context_root),
            audit_writer=writer or AuditWriter(root=audit_root),
            llm_client=llm or FakeLLM(),
            allow_live_llm=True,
            followup_loader=empty_followup_loader,
            legacy_loader=empty_legacy_loader,
        )

    def _decision(self, action):
        return IntentDecision(
            action=action,
            confidence=0.96,
            reason="LLM Arbiter selected action",
            clarification_question=(
                "请补充设备名称、目标和已有证据。"
                if action == IntentAction.need_clarification
                else ""
            ),
        )

    def test_general_chat_full_transaction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            llm = FakeLLM()
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
                llm=llm,
            )
            result = dispatcher.dispatch(
                question="解释一下 StackWise Virtual",
                conversation_id="conv-dispatch-general",
                request_id="req-dispatch-general",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )

            self.assertEqual(result.status, EntryStatus.handled)
            self.assertEqual(result.action, IntentAction.general_chat)
            self.assertEqual(result.response.status, "ok")
            self.assertTrue(result.response.answer)
            self.assertTrue(result.response.v4.context_recorded)
            self.assertTrue(result.response.v4.audit_id)
            self.assertEqual(result.context["context_write_status"], "ok")
            self.assertEqual(result.context["audit_write_status"], "ok")
            self.assertEqual(result.context["audit_ref_status"], "ok")
            self.assertEqual(llm.calls, 1)

            loaded = dispatcher.store.load("conv-dispatch-general")
            self.assertEqual(loaded.status, OperationStatus.ok)
            self.assertEqual(len(loaded.context.recent_turns), 1)
            self.assertEqual(len(loaded.context.audit_refs), 1)

            audit_files = list((root / "audit").glob("audit_*.json"))
            self.assertEqual(len(audit_files), 1)
            payload = json.loads(audit_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["action"], "general_chat")
            self.assertEqual(payload["status"], "ok")
            self.assertFalse(payload["side_effect_started"])

    def test_advice_and_clarification_contracts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            llm = FakeLLM()
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
                llm=llm,
            )
            advice = dispatcher.dispatch(
                question="是否建议先隔离流量？",
                conversation_id="conv-dispatch-advice",
                request_id="req-dispatch-advice",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.advice_analysis),
            )
            clarification = dispatcher.dispatch(
                question="这个设备怎么办？",
                conversation_id="conv-dispatch-clarify",
                request_id="req-dispatch-clarify",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.need_clarification),
            )
            self.assertEqual(advice.status, EntryStatus.handled)
            self.assertEqual(advice.response.action, IntentAction.advice_analysis)
            self.assertEqual(clarification.status, EntryStatus.clarification)
            self.assertEqual(
                clarification.response.status,
                "need_clarification",
            )
            self.assertEqual(llm.calls, 1)

    def test_unsupported_action_returns_explicit_stage_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
            )
            result = dispatcher.dispatch(
                question="查一下设备信息",
                conversation_id="conv-dispatch-cmdb",
                request_id="req-dispatch-cmdb",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.cmdb_query),
            )
            self.assertEqual(result.status, EntryStatus.fallback)
            self.assertTrue(result.fallback_allowed)
            self.assertEqual(
                result.fallback_reason,
                "action_not_enabled_in_v4_2_2",
            )
            self.assertIsNone(result.response)
            self.assertEqual(
                result.context["context_read_status"],
                "not_attempted",
            )
            self.assertEqual(result.context["audit_write_status"], "ok")
            self.assertTrue(result.context["audit_ref"])
            self.assertEqual(
                len(list((root / "audit").glob("audit_*.json"))),
                1,
            )
            self.assertEqual(
                list((root / "context").glob("**/*.json")),
                [],
            )

    def test_unsupported_action_audit_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
                writer=FailingAuditWriter(),
            )
            result = dispatcher.dispatch(
                question="查一下设备信息",
                conversation_id="conv-dispatch-fallback-audit-error",
                request_id="req-dispatch-fallback-audit-error",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.cmdb_query),
            )
            self.assertEqual(result.status, EntryStatus.error)
            self.assertFalse(result.fallback_allowed)
            self.assertEqual(result.context["audit_write_status"], "error")
            self.assertEqual(result.context["audit_error_kind"], "write")
            self.assertIn(
                "injected audit write failure",
                result.context["audit_error"],
            )

    def test_duplicate_dispatch_deduplicates_context_turn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
            )
            kwargs = dict(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-dedupe",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )
            first = dispatcher.dispatch(
                request_id="req-dedupe-1",
                **kwargs,
            )
            second = dispatcher.dispatch(
                request_id="req-dedupe-2",
                **kwargs,
            )
            self.assertEqual(first.status, EntryStatus.handled)
            self.assertEqual(second.status, EntryStatus.handled)
            self.assertTrue(second.context["context_deduplicated"])
            loaded = dispatcher.store.load("conv-dispatch-dedupe")
            self.assertEqual(len(loaded.context.recent_turns), 1)
            self.assertEqual(len(loaded.context.audit_refs), 2)

    def test_context_read_error_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
                store_cls=ReadErrorStore,
            )
            result = dispatcher.dispatch(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-read-error",
                request_id="req-dispatch-read-error",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )
            self.assertEqual(result.status, EntryStatus.error)
            self.assertEqual(
                result.context["context_read_error_kind"],
                "permission",
            )
            self.assertIn(
                "injected context read failure",
                result.context["context_read_detail"],
            )

    def test_context_write_error_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
                store_cls=FailingAppendStore,
            )
            result = dispatcher.dispatch(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-write-error",
                request_id="req-dispatch-write-error",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )
            self.assertEqual(result.status, EntryStatus.error)
            self.assertEqual(
                result.context["context_write_error_kind"],
                "write",
            )
            self.assertIn(
                "injected context append failure",
                result.context["context_write_detail"],
            )

    def test_audit_write_error_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
                writer=FailingAuditWriter(),
            )
            result = dispatcher.dispatch(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-audit-error",
                request_id="req-dispatch-audit-error",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )
            self.assertEqual(result.status, EntryStatus.error)
            self.assertEqual(result.context["audit_write_status"], "error")
            self.assertEqual(result.context["audit_error_kind"], "write")
            self.assertIn(
                "injected audit write failure",
                result.context["audit_error"],
            )

    def test_audit_reference_error_is_visible_and_audited(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
                store_cls=FailingAuditRefStore,
            )
            result = dispatcher.dispatch(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-ref-error",
                request_id="req-dispatch-ref-error",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )
            self.assertEqual(result.status, EntryStatus.error)
            self.assertEqual(result.context["audit_ref_status"], "error")
            self.assertEqual(result.context["audit_ref_error_kind"], "write")
            audit_files = list((root / "audit").glob("audit_*.json"))
            self.assertGreaterEqual(len(audit_files), 1)

    def test_audit_writer_replace_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
            )
            result = dispatcher.dispatch(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-audit-replace",
                request_id="req-dispatch-audit-replace",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )
            self.assertEqual(result.status, EntryStatus.handled)
            audit_path = Path(result.context["audit_path"])
            before = audit_path.read_bytes()
            result.audit.status = "error"
            with patch(
                "netaiops_asset.chat_v4.audit_writer.os.replace",
                side_effect=OSError("injected replace failure"),
            ):
                written = dispatcher.audit_writer.write(result.audit)
            self.assertFalse(written.ok)
            self.assertEqual(written.error_kind, ContextErrorKind.write)
            self.assertIn("injected replace failure", written.detail)
            self.assertEqual(audit_path.read_bytes(), before)
            self.assertEqual(
                list((root / "audit").glob("*.tmp")),
                [],
            )

    def test_audit_writer_permission_error_is_classified(self):
        class PermissionWriter(AuditWriter):
            def _ensure_root(self):
                raise PermissionError("injected audit permission failure")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
            )
            result = dispatcher.dispatch(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-audit-permission",
                request_id="req-dispatch-audit-permission",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            )
            writer = PermissionWriter(root=root / "permission-audit")
            written = writer.write(result.audit)
            self.assertFalse(written.ok)
            self.assertEqual(
                written.error_kind,
                ContextErrorKind.permission,
            )
            self.assertIn(
                "injected audit permission failure",
                written.detail,
            )

    def test_audit_writer_redacts_secret_and_is_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(
                root / "context",
                root / "audit",
            )
            audit = dispatcher.dispatch(
                question="解释一下 BGP",
                conversation_id="conv-dispatch-audit-sanitize",
                request_id="req-dispatch-audit-sanitize",
                request_user_field="v4_2_2_test",
                decision=self._decision(IntentAction.general_chat),
            ).audit
            audit.metadata["api_key"] = "never-write-me"
            written = dispatcher.audit_writer.write(audit)
            self.assertTrue(written.ok)
            payload = Path(written.path).read_text(encoding="utf-8")
            self.assertNotIn("never-write-me", payload)
            self.assertIn("[REDACTED]", payload)
            self.assertEqual(
                list((root / "audit").glob("*.tmp")),
                [],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
