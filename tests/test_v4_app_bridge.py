# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from netaiops_asset.chat_v3.intent_schema import IntentAction, IntentDecision
from netaiops_asset.chat_v4.action_dispatcher import LowRiskActionDispatcher
from netaiops_asset.chat_v4.audit_writer import AuditWriter
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import (
    EntryResult,
    EntryStatus,
    V4AuditRecord,
    V4Response,
    V4ResponseMeta,
)
from netaiops_asset.chat_v4.entry_router import EntryRouteResult, V4EntryRouter
from netaiops_asset.chat_v4.app_bridge import (
    build_v4_internal_error_transport,
    try_handle_v4_pre_route,
)


class FakeRouter(V4EntryRouter):
    def __init__(self, result, audit_writer):
        self.result = result
        self.audit_writer = audit_writer

    def route(self, **kwargs):
        factory = kwargs.get("conversation_id_factory")
        if self.result.handled and not self.result.effective_conversation_id:
            self.result.effective_conversation_id = factory(
                self.result.original_conversation_id,
                kwargs.get("request_user_field") or "",
            )
            if self.result.entry_result is not None:
                self.result.entry_result.response.conversation_id = (
                    self.result.effective_conversation_id
                )
                self.result.entry_result.audit.conversation_id = (
                    self.result.effective_conversation_id
                )
        return self.result


def make_route_result(
    *,
    handled=True,
    fallback=False,
    action=IntentAction.general_chat,
    status=EntryStatus.handled,
    original_id="",
    effective_id="",
):
    decision = IntentDecision(
        action=action,
        confidence=0.95,
        clarification_question=(
            "请补充信息。"
            if action == IntentAction.need_clarification
            else ""
        ),
        metadata={"request_id": "request-bridge-001"},
    )
    response = V4Response(
        status=(
            "need_clarification"
            if action == IntentAction.need_clarification
            else "ok"
        ),
        answer="这是 V4 低风险 Handler 返回的测试回答。",
        conversation_id=effective_id,
        question="测试问题",
        action=action,
        v4=V4ResponseMeta(
            handler_key=action.value,
            confidence=0.95,
            audit_id="audit-bridge-001",
            context_recorded=True,
        ),
    )
    audit = V4AuditRecord(
        audit_id="audit-bridge-001",
        conversation_id=effective_id,
        request_id="request-bridge-001",
        action=action,
        handler_key=action.value,
        status="ok",
    )
    entry = EntryResult(
        status=status,
        action=action,
        handler_key=action.value,
        response=response,
        audit=audit,
        context={},
    )
    return EntryRouteResult(
        enabled=True,
        handled=handled,
        fallback=fallback,
        reason=(
            "v4_low_risk_entry_handled"
            if handled
            else "action_not_enabled_in_v4_2_3"
        ),
        request_id="request-bridge-001",
        action=action.value,
        original_conversation_id=original_id,
        effective_conversation_id=effective_id,
        decision=decision,
        plan={"action": action.value},
        followup_context={},
        entry_result=entry if handled else None,
    )


class V4AppBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.audit_writer = AuditWriter(root / "audit")
        self.created = []
        self.appended = []
        self.conversations = {}

    def tearDown(self):
        self.tmp.cleanup()

    def get_conversation(self, conversation_id):
        return self.conversations.get(conversation_id)

    def create_conversation(self, title=None, user=None):
        cid = f"created-{len(self.created) + 1}"
        conv = {
            "conversation_id": cid,
            "title": title,
            "user": user,
            "turns": [],
        }
        self.created.append(conv)
        self.conversations[cid] = conv
        return conv

    def append_turn(self, conversation_id, question, response, user=None):
        self.appended.append(
            {
                "conversation_id": conversation_id,
                "question": question,
                "response": response,
                "user": user,
            }
        )
        return conversation_id, {"turn_id": "turn-1"}

    def test_new_conversation_created_only_for_handled_action(self):
        result = make_route_result()
        router = FakeRouter(result, self.audit_writer)
        output = try_handle_v4_pre_route(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="",
            get_conversation_fn=self.get_conversation,
            create_conversation_fn=self.create_conversation,
            append_turn_fn=self.append_turn,
            router=router,
        )
        self.assertTrue(output["handled"])
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.appended), 1)
        payload = output["response"]
        self.assertEqual(payload["conversation_id"], "created-1")
        self.assertTrue(payload["v4_pre_route"])
        self.assertEqual(payload["planner_source"], "v4_intent_arbiter")
        self.assertTrue(payload["v4"]["legacy_history_recorded"])
        self.assertEqual(payload["request_id"], "request-bridge-001")

    def test_existing_conversation_is_reused(self):
        self.conversations["existing-1"] = {
            "conversation_id": "existing-1",
            "turns": [],
        }
        result = make_route_result(original_id="existing-1")
        router = FakeRouter(result, self.audit_writer)
        output = try_handle_v4_pre_route(
            question="继续解释",
            request_user_field="tester",
            conversation_id="existing-1",
            get_conversation_fn=self.get_conversation,
            create_conversation_fn=self.create_conversation,
            append_turn_fn=self.append_turn,
            router=router,
        )
        self.assertTrue(output["handled"])
        self.assertEqual(self.created, [])
        self.assertEqual(
            output["response"]["conversation_id"],
            "existing-1",
        )

    def test_fallback_does_not_create_or_append_history(self):
        result = make_route_result(
            handled=False,
            fallback=True,
            action=IntentAction.cmdb_query,
        )
        router = FakeRouter(result, self.audit_writer)
        output = try_handle_v4_pre_route(
            question="查管理 IP",
            request_user_field="tester",
            conversation_id="",
            get_conversation_fn=self.get_conversation,
            create_conversation_fn=self.create_conversation,
            append_turn_fn=self.append_turn,
            router=router,
        )
        self.assertFalse(output["handled"])
        self.assertEqual(self.created, [])
        self.assertEqual(self.appended, [])
        self.assertEqual(
            output["shadow_state"]["decision"].action,
            IntentAction.cmdb_query,
        )

    def test_clarification_frontend_contract(self):
        result = make_route_result(
            action=IntentAction.need_clarification,
            status=EntryStatus.clarification,
        )
        router = FakeRouter(result, self.audit_writer)
        output = try_handle_v4_pre_route(
            question="",
            request_user_field="tester",
            conversation_id="",
            get_conversation_fn=self.get_conversation,
            create_conversation_fn=self.create_conversation,
            append_turn_fn=self.append_turn,
            router=router,
        )
        payload = output["response"]
        self.assertEqual(payload["status"], "need_clarification")
        self.assertEqual(payload["action"], "need_clarification")
        self.assertEqual(payload["v4_entry_status"], "clarification")

    def test_history_failure_is_visible_and_audit_is_updated(self):
        result = make_route_result()
        initial_write = self.audit_writer.write(result.entry_result.audit)
        self.assertTrue(initial_write.ok)
        router = FakeRouter(result, self.audit_writer)

        def broken_append(*args, **kwargs):
            raise PermissionError("history denied")

        output = try_handle_v4_pre_route(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="",
            get_conversation_fn=self.get_conversation,
            create_conversation_fn=self.create_conversation,
            append_turn_fn=broken_append,
            router=router,
        )
        payload = output["response"]
        self.assertTrue(output["handled"])
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["v4_legacy_history_recorded"])
        self.assertIn("PermissionError", payload["v4_legacy_history_error"])
        self.assertEqual(
            payload["v4_legacy_history_audit_update"]["status"],
            "ok",
        )
        audit_data = (
            self.audit_writer.path_for("audit-bridge-001")
            .read_text(encoding="utf-8")
        )
        self.assertIn("legacy_history_sync_error", audit_data)
        self.assertIn('"status":"error"', audit_data)

    def test_history_conversation_id_mismatch_is_visible(self):
        result = make_route_result()
        router = FakeRouter(result, self.audit_writer)

        def mismatched_append(*args, **kwargs):
            return "different-id", {}

        output = try_handle_v4_pre_route(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="",
            get_conversation_fn=self.get_conversation,
            create_conversation_fn=self.create_conversation,
            append_turn_fn=mismatched_append,
            router=router,
        )
        self.assertEqual(output["response"]["status"], "error")
        self.assertIn(
            "conversation_id mismatch",
            output["response"]["v4_legacy_history_error"],
        )


    def test_empty_question_uses_real_router_dispatcher_and_bridge(self):
        store = ContextStore(self.root / "context-empty-e2e")
        dispatcher = LowRiskActionDispatcher(
            store=store,
            audit_writer=self.audit_writer,
            allow_live_llm=False,
            followup_loader=lambda *_args, **_kwargs: {},
            legacy_loader=lambda *_args, **_kwargs: {},
        )
        empty_decision = IntentDecision(
            action=IntentAction.need_clarification,
            confidence=0.0,
            reason="empty_user_question",
            clarification_question="请补充您的具体问题。",
            metadata={
                "request_id": "request-empty-bridge-e2e",
                "effective_confidence": 0.0,
            },
        )
        router = V4EntryRouter(
            enabled=True,
            allowed_actions=(
                "general_chat,advice_analysis,need_clarification"
            ),
            allow_live_llm=False,
            min_confidence=0.80,
            store=store,
            audit_writer=self.audit_writer,
            arbiter=lambda **_kwargs: empty_decision,
            plan_builder=lambda **kwargs: {
                "action": kwargs["decision"].action.value,
                "handler_key": kwargs["decision"].action.value,
                "accepted": True,
            },
            dispatcher=dispatcher,
        )
        output = try_handle_v4_pre_route(
            question="",
            request_user_field="tester",
            conversation_id="",
            get_conversation_fn=self.get_conversation,
            create_conversation_fn=self.create_conversation,
            append_turn_fn=self.append_turn,
            router=router,
        )
        payload = output["response"]
        self.assertTrue(output["handled"])
        self.assertEqual(payload["status"], "need_clarification")
        self.assertEqual(payload["action"], "need_clarification")
        self.assertEqual(payload["planner_source"], "v4_intent_arbiter")
        self.assertTrue(payload["v4_pre_route"])
        self.assertEqual(payload["v4_entry_status"], "clarification")
        self.assertTrue(payload["v4"]["context_recorded"])
        self.assertTrue(payload["v4"]["legacy_history_recorded"])
        loaded = store.load(payload["conversation_id"])
        self.assertEqual(loaded.status.value, "ok")
        self.assertEqual(len(loaded.context.recent_turns), 1)
        self.assertEqual(loaded.context.recent_turns[0].question, "")

    def test_internal_exception_transport_is_visible_not_fallback(self):
        output = build_v4_internal_error_transport(
            question="解释 BGP",
            request_user_field="tester",
            conversation_id="conv-internal-error",
            detail="RuntimeError: injected internal failure",
            audit_writer=self.audit_writer,
        )
        payload = output["response"]
        self.assertTrue(output["handled"])
        self.assertFalse(output["route"]["fallback"])
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["planner_source"], "v4_entry_router")
        self.assertTrue(payload["v4_pre_route"])
        self.assertFalse(payload["v4_fallback_used"])
        self.assertEqual(payload["v4_entry_status"], "error")
        self.assertTrue(payload["v4"]["audit_id"])
        self.assertEqual(
            len(list((self.root / "audit").glob("audit_*.json"))),
            1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
