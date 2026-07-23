#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from netaiops_asset.chat_v3.intent_schema import (
    CommandGenerationSpec,
    CmdbQuerySpec,
    IntentAction,
    IntentDecision,
)
from netaiops_asset.chat_v4.action_dispatcher import (
    LOW_RISK_ACTIONS,
    LowRiskActionDispatcher,
)
from netaiops_asset.chat_v4.audit_writer import AuditWriter
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.contracts import EntryStatus, OperationStatus
from netaiops_asset.chat_v4.entry_router import V4EntryRouter
from netaiops_asset.chat_v4.handlers.cmdb_query import CmdbQueryHandler
from netaiops_asset.chat_v4.handlers.generate_commands import GenerateCommandsHandler


def empty_followup_loader(conversation_id, user):
    return {}


def empty_legacy_loader(conversation_id):
    return {}


class FakeResolver:
    def resolve(self, keyword, probe_prometheus=True):
        return {
            "status": "ok",
            "hostname": keyword,
            "mgmt_ip": "192.0.2.10",
            "selected_cmdb": {
                "host_name": keyword,
                "mgmt_ip": "192.0.2.10",
                "device_type": "cisco_nxos",
            },
            "netmiko_match": {
                "name": keyword,
                "hostname": "192.0.2.10",
                "device_type": "cisco_nxos",
            },
            "warnings": [],
        }


class V431IntegrationTests(unittest.TestCase):
    def _dispatcher(self, root):
        dispatcher = LowRiskActionDispatcher(
            store=ContextStore(root=root / "context"),
            audit_writer=AuditWriter(root=root / "audit"),
            llm_client=None,
            allow_live_llm=False,
            followup_loader=empty_followup_loader,
            legacy_loader=empty_legacy_loader,
        )
        dispatcher.handlers[IntentAction.cmdb_query] = CmdbQueryHandler(
            query_detail=lambda **kwargs: {
                "status": "ok",
                "count": 1,
                "returned": 1,
                "items": [
                    {
                        "host_name": kwargs["keyword"],
                        "mgmt_ip": "192.0.2.10",
                        "device_type": "cisco_nxos",
                    }
                ],
            }
        )
        dispatcher.handlers[IntentAction.generate_commands] = GenerateCommandsHandler(
            resolver_factory=FakeResolver
        )
        return dispatcher

    def test_expected_v4_3_1_action_set(self):
        self.assertEqual(
            LOW_RISK_ACTIONS,
            frozenset(
                {
                    IntentAction.general_chat,
                    IntentAction.advice_analysis,
                    IntentAction.need_clarification,
                    IntentAction.cmdb_query,
                    IntentAction.generate_commands,
                }
            ),
        )

    def test_cmdb_full_dispatch_transaction_updates_context_and_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(root)
            decision = IntentDecision(
                action=IntentAction.cmdb_query,
                confidence=0.97,
                device_required=True,
                device_hint="device01",
                cmdb_query=CmdbQuerySpec(
                    operation="detail",
                    keyword="device01",
                    fields=["host_name", "mgmt_ip", "device_spec"],
                ),
            )
            result = dispatcher.dispatch(
                question="查设备资产",
                conversation_id="conv-v431-cmdb",
                request_id="req-v431-cmdb",
                request_user_field="v4_3_1_test",
                decision=decision,
            )
            self.assertEqual(result.status, EntryStatus.handled)
            self.assertEqual(result.action, IntentAction.cmdb_query)
            self.assertEqual(result.response.status, "ok")
            self.assertTrue(result.response.v4.context_recorded)
            self.assertTrue(result.response.v4.audit_id)
            loaded = dispatcher.store.load("conv-v431-cmdb")
            self.assertEqual(loaded.status, OperationStatus.ok)
            self.assertEqual(loaded.context.device_context["host_name"], "device01")
            self.assertEqual(loaded.context.topic, "cmdb_query")
            self.assertEqual(len(loaded.context.audit_refs), 1)

    def test_generate_commands_full_dispatch_has_no_side_effect(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(root)
            decision = IntentDecision(
                action=IntentAction.generate_commands,
                confidence=0.97,
                device_required=True,
                device_hint="device01",
                command_generation=CommandGenerationSpec(category="cpu"),
            )
            result = dispatcher.dispatch(
                question="生成 CPU 排查命令",
                conversation_id="conv-v431-generate",
                request_id="req-v431-generate",
                request_user_field="v4_3_1_test",
                decision=decision,
            )
            self.assertEqual(result.status, EntryStatus.handled)
            self.assertEqual(result.response.status, "confirmation_required")
            self.assertFalse(result.audit.side_effect_started)
            self.assertTrue(result.response.items)
            self.assertTrue(
                all(item["requires_confirmation"] for item in result.response.items)
            )
            self.assertTrue(
                all(not item["execution_started"] for item in result.response.items)
            )
            loaded = dispatcher.store.load("conv-v431-generate")
            self.assertEqual(loaded.status, OperationStatus.ok)
            self.assertEqual(loaded.context.topic, "cpu")
            self.assertEqual(loaded.context.pending, {})
            self.assertEqual(loaded.context.execution_evidence, [])

    def test_execute_actions_remain_explicit_stage_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dispatcher = self._dispatcher(root)
            for action in (
                IntentAction.execute_provided_commands,
                IntentAction.execute_provided_commands_and_analyze,
                IntentAction.confirm_execute_pending,
                IntentAction.analyze_existing_evidence,
            ):
                with self.subTest(action=action.value):
                    decision = IntentDecision(
                        action=action,
                        confidence=0.97,
                        commands=["show version"] if "execute_provided" in action.value else [],
                        commands_provided="execute_provided" in action.value,
                    )
                    result = dispatcher.dispatch(
                        question="structured unsupported action",
                        conversation_id=f"conv-v431-{action.value}",
                        request_id=f"req-v431-{action.value}",
                        request_user_field="v4_3_1_test",
                        decision=decision,
                    )
                    self.assertEqual(result.status, EntryStatus.fallback)
                    self.assertTrue(result.fallback_allowed)
                    self.assertEqual(
                        result.fallback_reason,
                        "action_not_enabled_in_v4_3_1",
                    )

    def test_entry_router_handles_new_actions_without_second_arbiter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            calls = []

            def arbiter(**kwargs):
                calls.append(kwargs)
                return IntentDecision(
                    action=IntentAction.cmdb_query,
                    confidence=0.97,
                    device_hint="device01",
                    cmdb_query=CmdbQuerySpec(
                        operation="detail",
                        keyword="device01",
                        fields=["host_name", "mgmt_ip", "device_spec"],
                    ),
                    metadata={"request_id": "req-router-v431"},
                )

            dispatcher = self._dispatcher(root)
            router = V4EntryRouter(
                enabled=True,
                allowed_actions=(
                    "general_chat,advice_analysis,need_clarification,"
                    "cmdb_query,generate_commands"
                ),
                allow_live_llm=False,
                min_confidence=0.80,
                store=dispatcher.store,
                audit_writer=dispatcher.audit_writer,
                arbiter=arbiter,
                dispatcher=dispatcher,
            )
            result = router.route(
                question="查设备资产",
                request_user_field="v4_3_1_test",
                conversation_id="",
                conversation_id_factory=lambda old, user: "conv-router-v431",
            )
            self.assertTrue(result.handled)
            self.assertFalse(result.fallback)
            self.assertEqual(result.action, "cmdb_query")
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
