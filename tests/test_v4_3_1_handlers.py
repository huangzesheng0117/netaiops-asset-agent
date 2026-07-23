#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from netaiops_asset.chat_v3.intent_schema import (
    CommandGenerationSpec,
    CmdbQuerySpec,
    IntentAction,
    IntentDecision,
)
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.handlers import HandlerRequest
from netaiops_asset.chat_v4.handlers.cmdb_query import CmdbQueryHandler
from netaiops_asset.chat_v4.handlers.generate_commands import (
    GenerateCommandsHandler,
    build_command_specs,
)


class FakeResolver:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def resolve(self, keyword, probe_prometheus=True):
        self.calls.append((keyword, probe_prometheus))
        return dict(self.payload)


class V431HandlerTests(unittest.TestCase):
    def _request(self, decision, *, device_context=None):
        root = Path(tempfile.mkdtemp(prefix="v431-handler-context-"))
        store = ContextStore(root=root)
        context = store.new_context(
            "conv-v431-handler",
            request_user_field="v4_3_1_test",
        )
        context.device_context = dict(device_context or {})
        return HandlerRequest(
            question="测试问题文本不参与后端 action 选择",
            conversation_id="conv-v431-handler",
            request_id="req-v431-handler",
            request_user_field="v4_3_1_test",
            decision=decision,
            canonical_context=context,
            allow_live_llm=False,
            llm_client=None,
        )

    def test_generate_contract_forces_confirmation_and_clears_commands(self):
        decision = IntentDecision(
            action=IntentAction.generate_commands,
            confidence=0.96,
            device_required=True,
            device_hint="device01",
            commands_provided=True,
            commands=["show version"],
            requires_confirmation=False,
            command_generation=CommandGenerationSpec(category="cpu"),
        )
        self.assertTrue(decision.should_generate_commands)
        self.assertTrue(decision.requires_confirmation)
        self.assertFalse(decision.commands_provided)
        self.assertEqual(decision.commands, [])

    def test_cmdb_detail_success_sanitizes_tool_output(self):
        calls = []

        def detail(**kwargs):
            calls.append(kwargs)
            return {
                "status": "ok",
                "count": 1,
                "returned": 1,
                "items": [
                    {
                        "host_name": "device01",
                        "mgmt_ip": "192.0.2.10",
                        "device_type": "cisco_nxos",
                        "IDC": "SH16",
                        "password": "must-not-leak",
                    }
                ],
            }

        handler = CmdbQueryHandler(query_detail=detail)
        decision = IntentDecision(
            action=IntentAction.cmdb_query,
            confidence=0.97,
            device_required=True,
            device_hint="device01",
            cmdb_query=CmdbQuerySpec(
                operation="detail",
                keyword="device01",
                fields=["host_name", "mgmt_ip", "device_spec", "IDC"],
            ),
        )
        outcome = handler.handle(self._request(decision))
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(calls[0]["keyword"], "device01")
        self.assertNotIn("password", outcome.items[0])
        self.assertEqual(outcome.metadata["operation"], "detail")
        self.assertFalse(outcome.metadata["side_effect_started"])
        self.assertEqual(outcome.metadata["device_context"]["host_name"], "device01")

    def test_cmdb_sensitive_field_is_rejected_before_tool_call(self):
        called = []

        def detail(**kwargs):
            called.append(kwargs)
            return {"status": "ok", "items": []}

        decision = IntentDecision(
            action=IntentAction.cmdb_query,
            confidence=0.97,
            device_hint="device01",
            cmdb_query=CmdbQuerySpec(
                operation="detail",
                keyword="device01",
                fields=["host_name", "password"],
            ),
        )
        with patch(
            "netaiops_asset.chat_v4.handlers.cmdb_query.SENSITIVE_FIELDS",
            {"password"},
        ):
            outcome = CmdbQueryHandler(query_detail=detail).handle(
                self._request(decision)
            )
        self.assertFalse(outcome.ok)
        self.assertIn("sensitive", outcome.detail)
        self.assertEqual(called, [])

    def test_cmdb_unknown_filter_is_rejected(self):
        called = []

        def devices(**kwargs):
            called.append(kwargs)
            return {"status": "ok", "items": []}

        decision = IntentDecision(
            action=IntentAction.cmdb_query,
            confidence=0.97,
            cmdb_query=CmdbQuerySpec(
                operation="devices",
                filters={"not_a_field__icontains": "x"},
            ),
        )
        outcome = CmdbQueryHandler(query_devices=devices).handle(
            self._request(decision)
        )
        self.assertFalse(outcome.ok)
        self.assertIn("unknown CMDB filter field", outcome.detail)
        self.assertEqual(called, [])

    def test_cmdb_list_partial_and_not_found_contracts(self):
        def partial(**kwargs):
            return {
                "status": "ok",
                "count": 3,
                "returned": 1,
                "items": [{"host_name": "device01", "mgmt_ip": "192.0.2.10"}],
            }

        decision = IntentDecision(
            action=IntentAction.cmdb_query,
            confidence=0.97,
            cmdb_query=CmdbQuerySpec(
                operation="devices",
                filters={"IDC__icontains": "SH"},
                fields=["host_name", "mgmt_ip"],
            ),
        )
        outcome = CmdbQueryHandler(query_devices=partial).handle(
            self._request(decision)
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.metadata["count"], 3)

        empty = CmdbQueryHandler(
            query_devices=lambda **kwargs: {
                "status": "ok",
                "count": 0,
                "returned": 0,
                "items": [],
            }
        ).handle(self._request(decision))
        self.assertTrue(empty.ok)
        self.assertEqual(empty.status, "not_found")

    def test_cmdb_tool_error_is_visible(self):
        decision = IntentDecision(
            action=IntentAction.cmdb_query,
            confidence=0.97,
            cmdb_query=CmdbQuerySpec(operation="devices"),
        )
        outcome = CmdbQueryHandler(
            query_devices=lambda **kwargs: {
                "status": "error",
                "error_code": "CMDB_TIMEOUT",
                "message": "timeout",
            }
        ).handle(self._request(decision))
        self.assertFalse(outcome.ok)
        self.assertIn("CMDB_TIMEOUT", outcome.detail)

    def test_command_generation_success_uses_splitter_and_two_guards(self):
        resolver = FakeResolver(
            {
                "status": "ok",
                "hostname": "device01",
                "mgmt_ip": "192.0.2.10",
                "selected_cmdb": {
                    "host_name": "device01",
                    "mgmt_ip": "192.0.2.10",
                    "device_type": "cisco_nxos",
                },
                "netmiko_match": {
                    "name": "device01",
                    "hostname": "192.0.2.10",
                    "device_type": "cisco_nxos",
                },
                "warnings": [],
            }
        )
        decision = IntentDecision(
            action=IntentAction.generate_commands,
            confidence=0.97,
            device_required=True,
            device_hint="device01",
            command_generation=CommandGenerationSpec(
                category="cpu",
                max_commands=8,
            ),
        )
        outcome = GenerateCommandsHandler(
            resolver_factory=lambda: resolver
        ).handle(self._request(decision))
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(outcome.status, "confirmation_required")
        self.assertGreater(len(outcome.items), 0)
        self.assertEqual(resolver.calls, [("device01", False)])
        for item in outcome.items:
            self.assertEqual(item["command_source"], "system_generated")
            self.assertTrue(item["requires_confirmation"])
            self.assertFalse(item["execution_started"])
            self.assertFalse(item["pending_created"])
            self.assertEqual(item["guard_status"], "passed")
        self.assertTrue(outcome.metadata["requires_confirmation"])
        self.assertFalse(outcome.metadata["execution_started"])
        self.assertFalse(outcome.metadata["pending_created"])
        self.assertFalse(outcome.metadata["side_effect_started"])

    def test_command_generation_inherits_structured_context_device(self):
        resolver = FakeResolver(
            {
                "status": "ok",
                "hostname": "context-device",
                "mgmt_ip": "192.0.2.20",
                "selected_cmdb": {
                    "host_name": "context-device",
                    "mgmt_ip": "192.0.2.20",
                    "device_type": "huawei_vrp",
                },
                "netmiko_match": {
                    "name": "context-device",
                    "hostname": "192.0.2.20",
                    "device_type": "huawei_vrp",
                },
            }
        )
        decision = IntentDecision(
            action=IntentAction.generate_commands,
            confidence=0.97,
            device_required=True,
            device_hint="",
            command_generation=CommandGenerationSpec(category="device_health"),
        )
        request = self._request(
            decision,
            device_context={"host_name": "context-device"},
        )
        outcome = GenerateCommandsHandler(
            resolver_factory=lambda: resolver
        ).handle(request)
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(resolver.calls, [("context-device", False)])
        self.assertTrue(
            all(item["command"].startswith("display ") for item in outcome.items)
        )

    def test_command_generation_blocks_dangerous_catalog_output(self):
        resolver = FakeResolver(
            {
                "status": "ok",
                "hostname": "device01",
                "mgmt_ip": "192.0.2.10",
                "selected_cmdb": {"device_type": "cisco_nxos"},
                "netmiko_match": {
                    "name": "device01",
                    "device_type": "cisco_nxos",
                },
            }
        )
        decision = IntentDecision(
            action=IntentAction.generate_commands,
            confidence=0.97,
            device_hint="device01",
            command_generation=CommandGenerationSpec(category="device_health"),
        )
        outcome = GenerateCommandsHandler(
            resolver_factory=lambda: resolver,
            catalog=lambda **kwargs: [
                {"command": "show version", "purpose": "read"},
                {"command": "reload", "purpose": "danger"},
            ],
        ).handle(self._request(decision))
        self.assertFalse(outcome.ok)
        self.assertIn("read-only safety contract", outcome.detail)
        self.assertFalse(outcome.metadata["execution_started"])
        self.assertFalse(outcome.metadata["pending_created"])

    def test_command_generation_not_found_does_not_execute_or_create_pending(self):
        resolver = FakeResolver({"status": "not_found", "warnings": []})
        decision = IntentDecision(
            action=IntentAction.generate_commands,
            confidence=0.97,
            device_hint="missing-device",
        )
        outcome = GenerateCommandsHandler(
            resolver_factory=lambda: resolver
        ).handle(self._request(decision))
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "not_found")
        self.assertEqual(outcome.items, [])
        self.assertFalse(outcome.metadata["execution_started"])
        self.assertFalse(outcome.metadata["pending_created"])

    def test_platform_category_catalog_passes_real_splitter_and_guards(self):
        platforms = [
            "cisco_nxos",
            "huawei_vrp",
            "hp_comware",
            "fortinet",
            "f5_tmsh",
            "hillstone",
        ]
        categories = [
            "device_health",
            "cpu",
            "memory",
            "route_table",
            "bgp",
            "bfd",
            "interface_status",
            "interface_error",
            "optical_power",
            "log",
        ]
        for platform in platforms:
            for category in categories:
                with self.subTest(platform=platform, category=category):
                    specs = build_command_specs(
                        category=category,
                        platform=platform,
                        interface_name="Ethernet1/1",
                    )
                    self.assertTrue(specs)
                    commands = [str(item.get("command") or "") for item in specs]
                    split = GenerateCommandsHandler().splitter(
                        commands,
                        max_commands=8,
                    )
                    self.assertTrue(split.commands)
                    safety = GenerateCommandsHandler().safety_checker(
                        split.commands,
                        max_commands=8,
                    )
                    self.assertTrue(safety.allowed, safety.as_dict())
                    from netaiops_asset.netmiko.cli_guard import CliReadOnlyGuard
                    guard = CliReadOnlyGuard()
                    for command in split.commands:
                        checked = guard.validate(
                            command,
                            platform=platform,
                            device_type=platform,
                        )
                        self.assertEqual(
                            checked.status,
                            "passed",
                            {
                                "platform": platform,
                                "category": category,
                                "command": command,
                                "guard": checked.to_dict(),
                            },
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
