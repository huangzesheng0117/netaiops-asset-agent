#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from netaiops_asset.chat_v3.intent_schema import IntentAction, IntentDecision
from netaiops_asset.chat_v4.audit_adapter import build_audit_record
from netaiops_asset.chat_v4.contracts import EntryStatus
from netaiops_asset.chat_v4.handlers.base import HandlerOutcome
from netaiops_asset.chat_v4.response_builder import (
    build_error_entry,
    build_handled_entry,
    build_stage_fallback_entry,
    build_v4_error_response,
    build_v4_response,
)


class V4ResponseBuilderTests(unittest.TestCase):
    def test_general_chat_response_contract(self):
        decision = IntentDecision(
            action=IntentAction.general_chat,
            confidence=0.97,
            reason="llm decision",
        )
        outcome = HandlerOutcome.success(
            action=IntentAction.general_chat,
            handler_key="general_chat",
            answer="StackWise Virtual 可以把两台交换机组成一个逻辑控制平面。",
            source="fake_llm",
        )
        response = build_v4_response(
            question="解释一下 StackWise Virtual",
            conversation_id="conv-response-1",
            decision=decision,
            outcome=outcome,
            audit_id="audit-1",
            context_recorded=True,
        )
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.action, IntentAction.general_chat)
        self.assertEqual(response.planner_source, "v4_intent_arbiter")
        self.assertEqual(response.v4.handler_key, "general_chat")
        self.assertEqual(response.v4.audit_id, "audit-1")
        self.assertTrue(response.v4.context_recorded)
        self.assertFalse(response.v4.side_effect_started)
        self.assertFalse(response.v4.fallback_used)

    def test_clarification_response_and_entry_contract(self):
        decision = IntentDecision(
            action=IntentAction.need_clarification,
            confidence=0.20,
            clarification_question="请补充设备名称。",
        )
        outcome = HandlerOutcome.success(
            action=IntentAction.need_clarification,
            handler_key="need_clarification",
            answer="请补充设备名称。",
            status="need_clarification",
            source="deterministic_clarification",
        )
        response = build_v4_response(
            question="这个设备怎么办？",
            conversation_id="conv-response-2",
            decision=decision,
            outcome=outcome,
        )
        audit = build_audit_record(
            conversation_id="conv-response-2",
            request_id="req-2",
            action=decision.action,
            handler_key="need_clarification",
            status="ok",
        )
        entry = build_handled_entry(
            decision=decision,
            response=response,
            audit=audit,
        )
        self.assertEqual(response.status, "need_clarification")
        self.assertEqual(entry.status, EntryStatus.clarification)

    def test_action_mismatch_is_rejected(self):
        decision = IntentDecision(
            action=IntentAction.general_chat,
            confidence=0.90,
        )
        outcome = HandlerOutcome.success(
            action=IntentAction.advice_analysis,
            handler_key="advice_analysis",
            answer="建议先核对主备状态，再评估风险。",
        )
        with self.assertRaises(ValueError):
            build_v4_response(
                question="解释一下",
                conversation_id="conv-response-3",
                decision=decision,
                outcome=outcome,
            )

    def test_success_outcome_requires_answer(self):
        with self.assertRaises(ValueError):
            HandlerOutcome.success(
                action=IntentAction.general_chat,
                handler_key="general_chat",
                answer="   ",
            )

    def test_error_response_is_not_marked_success(self):
        decision = IntentDecision(
            action=IntentAction.advice_analysis,
            confidence=0.88,
        )
        response = build_v4_error_response(
            question="是否建议切换？",
            conversation_id="conv-response-4",
            decision=decision,
            handler_key="advice_analysis",
            audit_id="audit-error",
            context_recorded=False,
        )
        audit = build_audit_record(
            conversation_id="conv-response-4",
            request_id="req-4",
            action=decision.action,
            handler_key="advice_analysis",
            status="error",
        )
        entry = build_error_entry(
            decision=decision,
            response=response,
            audit=audit,
            context_metadata={"audit_write_status": "error"},
        )
        self.assertEqual(response.status, "error")
        self.assertEqual(entry.status, EntryStatus.error)
        self.assertFalse(response.v4.context_recorded)

    def test_stage_fallback_requires_explicit_reason(self):
        decision = IntentDecision(
            action=IntentAction.cmdb_query,
            confidence=0.99,
        )
        audit = build_audit_record(
            conversation_id="conv-response-5",
            request_id="req-5",
            action=decision.action,
            handler_key="",
            status="fallback",
            fallback_allowed=True,
            fallback_reason="action_not_enabled_in_v4_2_2",
        )
        entry = build_stage_fallback_entry(
            decision=decision,
            reason="action_not_enabled_in_v4_2_2",
            audit=audit,
        )
        self.assertEqual(entry.status, EntryStatus.fallback)
        self.assertTrue(entry.fallback_allowed)
        self.assertIsNone(entry.response)


if __name__ == "__main__":
    unittest.main(verbosity=2)
