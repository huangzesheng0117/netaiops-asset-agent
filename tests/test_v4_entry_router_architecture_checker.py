#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CHECKER_PATH = PROJECT / "tools" / "check_v4_entry_router_architecture.py"
SPEC = importlib.util.spec_from_file_location("v4_entry_arch_checker", CHECKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load V4 entry architecture checker")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class V4EntryRouterArchitectureCheckerTests(unittest.TestCase):
    def _copy_real_target(self, destination: Path) -> None:
        (destination / "netaiops_asset" / "chat_v4").mkdir(parents=True)
        shutil.copy2(PROJECT / "app.py", destination / "app.py")
        for name in sorted(CHECKER.REQUIRED_FILES):
            shutil.copy2(
                PROJECT / "netaiops_asset" / "chat_v4" / name,
                destination / "netaiops_asset" / "chat_v4" / name,
            )

    def _case(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        self._copy_real_target(root)
        return td, root

    @staticmethod
    def _replace(path: Path, old: str, new: str) -> None:
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1:
            raise AssertionError(
                f"fixture anchor count mismatch for {path}: "
                f"count={source.count(old)} anchor={old!r}"
            )
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    @staticmethod
    def _insert_before_marker(path: Path, marker: str, lines: list[str]) -> None:
        source = path.read_text(encoding="utf-8")
        matches = [line for line in source.splitlines() if marker in line]
        if len(matches) != 1:
            raise AssertionError(
                f"marker count mismatch for {path}: count={len(matches)} marker={marker!r}"
            )
        marker_line = matches[0]
        indent = marker_line[: len(marker_line) - len(marker_line.lstrip())]
        block = "".join(indent + line + "\n" for line in lines)
        anchor = marker_line + "\n"
        path.write_text(source.replace(anchor, block + anchor, 1), encoding="utf-8")

    def _assert_rejected(self, root: Path, expected: str) -> None:
        with self.assertRaises(CHECKER.ArchitectureError) as caught:
            CHECKER.validate_project(root)
        self.assertIn(expected, str(caught.exception))

    def test_real_complete_target_tree_passes(self):
        checked = CHECKER.validate_project(PROJECT)
        self.assertEqual(set(checked), set(CHECKER.REQUIRED_FILES))

    def test_delegated_internal_error_builder_is_valid(self):
        td, root = self._case()
        with td:
            CHECKER.validate_project(root)

    def test_internal_exception_cannot_return_legacy_fallback(self):
        td, root = self._case()
        with td:
            app = root / "app.py"
            self._replace(
                app,
                '''    except Exception as exc:\n        return build_v4_internal_error_transport(\n            question=str(question or ""),\n            request_user_field=str(user or ""),\n            conversation_id=str(conversation_id or ""),\n            detail=repr(exc),\n        )\n''',
                '''    except Exception as exc:\n        return {\n            "handled": False,\n            "response": None,\n            "shadow_state": {},\n            "route": {\n                "enabled": True,\n                "handled": False,\n                "fallback": True,\n                "reason": "legacy_fallback",\n            },\n        }\n''',
            )
            self._assert_rejected(
                root,
                "internal pre-route exception must delegate",
            )

    def test_internal_exception_cannot_call_legacy_business_route(self):
        td, root = self._case()
        with td:
            app = root / "app.py"
            self._replace(
                app,
                '''    except Exception as exc:\n        return build_v4_internal_error_transport(\n''',
                '''    except Exception as exc:\n        try_handle_v2_chat(str(question or ""))\n        return build_v4_internal_error_transport(\n''',
            )
            self._assert_rejected(root, "calls legacy business routes")

    def test_init_import_failure_requires_explicit_technical_fallback(self):
        td, root = self._case()
        with td:
            app = root / "app.py"
            self._replace(
                app,
                '"reason": "v4_entry_router_init_failed",',
                '"reason": "generic_fallback",',
            )
            self._assert_rejected(
                root,
                "init_fallback.route.reason must be",
            )

    def test_middleware_rejects_legacy_call_before_v4(self):
        td, root = self._case()
        with td:
            app = root / "app.py"
            self._insert_before_marker(
                app,
                "# V4_2_3_PRE_ROUTE_CALL_MARKER_BEGIN",
                [
                    "_forbidden_early = try_handle_v2_chat(",
                    "    question, user=user, conversation_id=conversation_id",
                    ")",
                ],
            )
            self._assert_rejected(root, "does not precede try_handle_v2_chat")

    def test_question_cannot_select_fixed_action(self):
        td, root = self._case()
        with td:
            entry = root / "netaiops_asset" / "chat_v4" / "entry_router.py"
            self._replace(
                entry,
                '''        original_id = str(conversation_id or "").strip()\n\n        if not self.enabled:\n''',
                '''        original_id = str(conversation_id or "").strip()\n\n        if normalized_question:\n            selected_action = IntentAction.general_chat\n\n        if not self.enabled:\n''',
            )
            self._assert_rejected(
                root,
                "question text participates in deterministic action/handler selection",
            )

    def test_bridge_payload_requires_visible_action(self):
        td, root = self._case()
        with td:
            bridge = root / "netaiops_asset" / "chat_v4" / "app_bridge.py"
            self._replace(
                bridge,
                '''        "question": str(question or ""),
        "action": IntentAction.need_clarification.value,
        "planner_source": "v4_entry_router",
''',
                '''        "question": str(question or ""),
        "action_missing": IntentAction.need_clarification.value,
        "planner_source": "v4_entry_router",
''',
            )
            self._assert_rejected(root, "internal_error.payload missing key: action")

    def test_bridge_payload_cannot_claim_fallback(self):
        td, root = self._case()
        with td:
            bridge = root / "netaiops_asset" / "chat_v4" / "app_bridge.py"
            self._replace(
                bridge,
                '        "v4_fallback_used": False,\n',
                '        "v4_fallback_used": True,\n',
            )
            self._assert_rejected(
                root,
                "internal_error.payload.v4_fallback_used must be False",
            )

    def test_bridge_transport_cannot_fallback(self):
        td, root = self._case()
        with td:
            bridge = root / "netaiops_asset" / "chat_v4" / "app_bridge.py"
            self._replace(
                bridge,
                '''            "handled": True,\n            "fallback": False,\n            "reason": "v4_entry_router_internal_error",\n''',
                '''            "handled": True,\n            "fallback": True,\n            "reason": "v4_entry_router_internal_error",\n''',
            )
            self._assert_rejected(
                root,
                "internal_error.transport.route.fallback must be False",
            )

    def test_bridge_audit_cannot_allow_fallback(self):
        td, root = self._case()
        with td:
            bridge = root / "netaiops_asset" / "chat_v4" / "app_bridge.py"
            self._replace(
                bridge,
                '        fallback_allowed=False,\n',
                '        fallback_allowed=True,\n',
            )
            self._assert_rejected(
                root,
                "internal_error.audit.fallback_allowed must be False",
            )

    def test_bridge_handler_key_is_stable_and_visible(self):
        td, root = self._case()
        with td:
            bridge = root / "netaiops_asset" / "chat_v4" / "app_bridge.py"
            self._replace(
                bridge,
                '        handler_key="v4_entry_router_internal_error",\n',
                '        handler_key="generic_internal_error",\n',
            )
            self._assert_rejected(
                root,
                "internal_error.audit.handler_key must be",
            )

    def test_marker_is_only_idempotency_boundary(self):
        td, root = self._case()
        with td:
            app = root / "app.py"
            self._replace(
                app,
                "# V4_2_3_ENTRY_ROUTER_MARKER_BEGIN",
                "# V4_2_3_ENTRY_ROUTER_MARKER_BEGIN_DUPLICATED\n"
                "# V4_2_3_ENTRY_ROUTER_MARKER_BEGIN",
            )
            self._assert_rejected(root, "patch idempotency marker count mismatch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
