# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from netaiops_asset.chat_v3.intent_schema import IntentAction, IntentDecision
from netaiops_asset.chat_v4.action_dispatcher import LowRiskActionDispatcher
from netaiops_asset.chat_v4.audit_writer import AuditWriter
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    ContextOperationResult,
    EntryResult,
    EntryStatus,
    OperationStatus,
    V4AuditRecord,
    V4Response,
    V4ResponseMeta,
)
from netaiops_asset.chat_v4.entry_router import (
    V4EntryRouter,
    canonical_to_followup_context,
)


class FakeDispatcher:
    def __init__(self, status=EntryStatus.handled):
        self.status = status
        self.calls = []

    def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        decision = kwargs["decision"]
        response_status = (
            "need_clarification"
            if decision.action == IntentAction.need_clarification
            else ("error" if self.status == EntryStatus.error else "ok")
        )
        response = V4Response(
            status=response_status,
            answer="测试回答内容足够长，确保统一 V4 响应可以通过校验。",
            conversation_id=kwargs["conversation_id"],
            question=kwargs["question"],
            action=decision.action,
            v4=V4ResponseMeta(
                handler_key=decision.action.value,
                confidence=decision.confidence,
                context_recorded=True,
            ),
        )
        audit = V4AuditRecord(
            conversation_id=kwargs["conversation_id"],
            request_id=kwargs["request_id"],
            action=decision.action,
            handler_key=decision.action.value,
            status=response_status,
        )
        return EntryResult(
            status=self.status,
            action=decision.action,
            handler_key=decision.action.value,
            response=response,
            audit=audit,
            context={},
        )


def decision(
    action,
    confidence=0.95,
    *,
    reason="test",
    device_required=False,
    device_hint="",
    need_existing_evidence=False,
):
    return IntentDecision(
        action=action,
        confidence=confidence,
        reason=reason,
        device_required=device_required,
        device_hint=device_hint,
        need_existing_evidence=need_existing_evidence,
        clarification_question=(
            "请补充信息。"
            if action == IntentAction.need_clarification
            else ""
        ),
        metadata={
            "request_id": "request-test-001",
            "effective_confidence": confidence,
        },
    )


class V4EntryRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = ContextStore(root / "context")
        self.audit_writer = AuditWriter(root / "audit")
        self.legacy_not_found = lambda *args, **kwargs: ContextOperationResult(
            status=OperationStatus.not_found,
            detail="none",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def make_router(
        self,
        arbiter_result,
        *,
        enabled=True,
        allowed_actions=None,
        dispatcher=None,
        min_confidence=0.80,
    ):
        calls = {"arbiter": 0, "plan": 0}

        def arbiter(**kwargs):
            calls["arbiter"] += 1
            return arbiter_result

        def plan_builder(**kwargs):
            calls["plan"] += 1
            return {
                "action": kwargs["decision"].action.value,
                "handler_key": kwargs["decision"].action.value,
                "accepted": True,
            }

        router = V4EntryRouter(
            enabled=enabled,
            allowed_actions=(
                allowed_actions
                if allowed_actions is not None
                else "general_chat,advice_analysis,need_clarification"
            ),
            allow_live_llm=False,
            min_confidence=min_confidence,
            store=self.store,
            audit_writer=self.audit_writer,
            arbiter=arbiter,
            plan_builder=plan_builder,
            dispatcher=dispatcher or FakeDispatcher(),
            legacy_builder=self.legacy_not_found,
        )
        return router, calls

    def test_disabled_does_not_call_arbiter(self):
        router, calls = self.make_router(
            decision(IntentAction.general_chat),
            enabled=False,
        )
        result = router.route(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: "conv",
        )
        self.assertFalse(result.handled)
        self.assertTrue(result.fallback)
        self.assertEqual(result.reason, "v4_entry_disabled")
        self.assertEqual(calls["arbiter"], 0)

    def test_general_chat_is_dispatched_once(self):
        dispatcher = FakeDispatcher()
        router, calls = self.make_router(
            decision(IntentAction.general_chat),
            dispatcher=dispatcher,
        )
        factory_calls = []
        result = router.route(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda original, user: (
                factory_calls.append((original, user)) or "conv-general"
            ),
        )
        self.assertTrue(result.handled)
        self.assertFalse(result.fallback)
        self.assertEqual(result.action, "general_chat")
        self.assertEqual(calls["arbiter"], 1)
        self.assertEqual(calls["plan"], 1)
        self.assertEqual(len(dispatcher.calls), 1)
        self.assertEqual(factory_calls, [("", "tester")])

    def test_advice_action_comes_only_from_decision(self):
        dispatcher = FakeDispatcher()
        router, _ = self.make_router(
            decision(IntentAction.advice_analysis),
            dispatcher=dispatcher,
        )
        result = router.route(
            question="这句话不应被本地关键词改变 action",
            request_user_field="tester",
            conversation_id="conv-advice",
            conversation_id_factory=lambda original, _user: original,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.action, "advice_analysis")
        self.assertEqual(
            dispatcher.calls[0]["decision"].action,
            IntentAction.advice_analysis,
        )

    def test_low_confidence_becomes_clarification(self):
        dispatcher = FakeDispatcher(status=EntryStatus.clarification)
        router, _ = self.make_router(
            decision(IntentAction.general_chat, confidence=0.60),
            dispatcher=dispatcher,
        )
        result = router.route(
            question="含糊问题",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: "conv-clarify",
        )
        routed = dispatcher.calls[0]["decision"]
        self.assertTrue(result.handled)
        self.assertEqual(routed.action, IntentAction.need_clarification)
        self.assertEqual(
            routed.reason,
            "effective_confidence_below_v4_accept_threshold",
        )
        self.assertFalse(routed.should_execute_commands)
        self.assertEqual(routed.commands, [])

    def test_required_device_missing_becomes_clarification(self):
        dispatcher = FakeDispatcher(status=EntryStatus.clarification)
        router, _ = self.make_router(
            decision(
                IntentAction.advice_analysis,
                device_required=True,
                device_hint="",
            ),
            dispatcher=dispatcher,
        )
        result = router.route(
            question="帮我分析这个设备",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: "conv-device",
        )
        routed = dispatcher.calls[0]["decision"]
        self.assertTrue(result.handled)
        self.assertEqual(routed.action, IntentAction.need_clarification)
        self.assertEqual(routed.reason, "required_device_missing")

    def test_required_evidence_missing_becomes_clarification(self):
        dispatcher = FakeDispatcher(status=EntryStatus.clarification)
        router, _ = self.make_router(
            decision(
                IntentAction.analyze_existing_evidence,
                need_existing_evidence=True,
            ),
            dispatcher=dispatcher,
        )
        result = router.route(
            question="继续分析",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: "conv-evidence",
        )
        routed = dispatcher.calls[0]["decision"]
        self.assertTrue(result.handled)
        self.assertEqual(routed.action, IntentAction.need_clarification)
        self.assertEqual(
            routed.reason,
            "required_execution_evidence_missing",
        )

    def test_unsupported_high_confidence_falls_back_without_factory(self):
        router, calls = self.make_router(
            decision(IntentAction.execute_provided_commands),
        )
        factory_calls = []
        result = router.route(
            question="查设备管理 IP",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: factory_calls.append(True),
        )
        self.assertFalse(result.handled)
        self.assertTrue(result.fallback)
        self.assertEqual(
            result.reason,
            "action_not_enabled_in_v4_3_1",
        )
        self.assertEqual(factory_calls, [])
        self.assertEqual(calls["arbiter"], 1)
        self.assertEqual(calls["plan"], 1)
        self.assertEqual(result.audit_write_status, "ok")
        self.assertTrue(Path(result.audit_path).is_file())
        self.assertEqual(
            result.shadow_state["decision"].action,
            IntentAction.execute_provided_commands,
        )

    def test_technical_llm_failure_falls_back(self):
        router, _ = self.make_router(
            decision(
                IntentAction.need_clarification,
                confidence=0.0,
                reason="llm_call_failed",
            ),
        )
        result = router.route(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: self.fail(
                "factory must not run"
            ),
        )
        self.assertFalse(result.handled)
        self.assertTrue(result.fallback)
        self.assertEqual(result.reason, "llm_call_failed")
        self.assertEqual(result.audit_write_status, "ok")

    def test_dispatcher_error_is_handled_and_not_fallback(self):
        dispatcher = FakeDispatcher(status=EntryStatus.error)
        router, _ = self.make_router(
            decision(IntentAction.general_chat),
            dispatcher=dispatcher,
        )
        result = router.route(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: "conv-error",
        )
        self.assertTrue(result.handled)
        self.assertFalse(result.fallback)
        self.assertEqual(result.entry_result.status, EntryStatus.error)

    def test_existing_canonical_context_is_passed_to_arbiter(self):
        context = self.store.new_context(
            "conv-existing",
            request_user_field="tester",
        )
        context.topic = "BGP"
        self.assertEqual(self.store.save(context).status, OperationStatus.ok)
        captured = {}

        def arbiter(**kwargs):
            captured["context"] = kwargs["context"]
            return decision(IntentAction.general_chat)

        router = V4EntryRouter(
            enabled=True,
            allowed_actions="general_chat",
            store=self.store,
            audit_writer=self.audit_writer,
            arbiter=arbiter,
            plan_builder=lambda **kwargs: {"action": "general_chat"},
            dispatcher=FakeDispatcher(),
            legacy_builder=self.legacy_not_found,
        )
        result = router.route(
            question="继续",
            request_user_field="tester",
            conversation_id="conv-existing",
            conversation_id_factory=lambda original, _user: original,
        )
        self.assertTrue(result.handled)
        self.assertEqual(captured["context"]["current_topic"], "BGP")
        self.assertEqual(
            captured["context"]["followup_context_source"],
            "v4_canonical_context",
        )

    def test_invalid_allowed_action_is_rejected(self):
        with self.assertRaises(ValueError):
            V4EntryRouter(
                enabled=True,
                allowed_actions="general_chat,execute_provided_commands",
                store=self.store,
                audit_writer=self.audit_writer,
                legacy_builder=self.legacy_not_found,
            )

    def test_environment_configuration_is_exact(self):
        with patch.dict(
            os.environ,
            {
                "NETAIOPS_V4_ENTRY_ENABLED": "1",
                "NETAIOPS_V4_ENTRY_ALLOWED_ACTIONS": (
                    "general_chat,advice_analysis,need_clarification,"
                    "cmdb_query,generate_commands"
                ),
                "NETAIOPS_V4_ENTRY_LIVE_LLM": "1",
                "NETAIOPS_V4_ENTRY_MIN_CONFIDENCE": "0.80",
            },
            clear=False,
        ):
            router = V4EntryRouter(
                store=self.store,
                audit_writer=self.audit_writer,
                legacy_builder=self.legacy_not_found,
            )
        self.assertTrue(router.enabled)
        self.assertTrue(router.allow_live_llm)
        self.assertEqual(router.min_confidence, 0.80)
        self.assertEqual(
            router.allowed_actions,
            {
                IntentAction.general_chat,
                IntentAction.advice_analysis,
                IntentAction.need_clarification,
                IntentAction.cmdb_query,
                IntentAction.generate_commands,
            },
        )

    def test_canonical_conversion_is_bounded_and_structured(self):
        context = self.store.new_context("conv-canonical")
        context.topic = "OSPF"
        context.execution_evidence = [{"command": "show ip ospf"}]
        converted = canonical_to_followup_context(
            context,
            original_conversation_id="conv-canonical",
            source="v4_canonical_context",
        )
        self.assertEqual(
            converted["arbiter_context"]["current_topic"],
            "OSPF",
        )
        self.assertTrue(converted["has_execution_evidence"])


    def test_empty_question_reaches_real_clarification_dispatcher(self):
        dispatcher = LowRiskActionDispatcher(
            store=self.store,
            audit_writer=self.audit_writer,
            allow_live_llm=False,
            followup_loader=lambda *_args, **_kwargs: {},
            legacy_loader=lambda *_args, **_kwargs: {},
        )
        router, calls = self.make_router(
            decision(
                IntentAction.need_clarification,
                confidence=0.0,
                reason="empty_user_question",
            ),
            dispatcher=dispatcher,
        )
        result = router.route(
            question="",
            request_user_field="tester",
            conversation_id="",
            conversation_id_factory=lambda *_: "conv-empty-real-dispatch",
        )
        self.assertTrue(result.handled)
        self.assertFalse(result.fallback)
        self.assertEqual(result.action, "need_clarification")
        self.assertEqual(result.entry_result.status, EntryStatus.clarification)
        self.assertEqual(
            result.entry_result.response.status,
            "need_clarification",
        )
        self.assertEqual(
            result.entry_result.response.action,
            IntentAction.need_clarification,
        )
        self.assertEqual(calls["arbiter"], 1)
        self.assertEqual(calls["plan"], 2)
        loaded = self.store.load("conv-empty-real-dispatch")
        self.assertEqual(loaded.status, OperationStatus.ok)
        self.assertEqual(len(loaded.context.recent_turns), 1)
        self.assertEqual(loaded.context.recent_turns[0].question, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
