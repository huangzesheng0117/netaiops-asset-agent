#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CHECKER_PATH = PROJECT / "tools" / "check_v4_low_risk_architecture.py"
SPEC = importlib.util.spec_from_file_location(
    "v4_low_risk_architecture_checker_under_test",
    CHECKER_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load V4 low-risk architecture checker")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def scan_dispatch(source: str) -> bool:
    tree = ast.parse(source)
    cls = CHECKER.class_node(tree, "LowRiskActionDispatcher")
    if cls is None:
        raise AssertionError("fixture dispatcher class is missing")
    dispatch = CHECKER.method_node(cls, "dispatch")
    if dispatch is None:
        raise AssertionError("fixture dispatch method is missing")
    return CHECKER.action_selection_uses_question(dispatch)


class V4LowRiskArchitectureCheckerTests(unittest.TestCase):
    def test_real_target_dispatcher_is_not_question_driven(self):
        source = (
            PROJECT
            / "netaiops_asset"
            / "chat_v4"
            / "action_dispatcher.py"
        ).read_text(encoding="utf-8")
        self.assertFalse(scan_dispatch(source))

    def test_allows_empty_question_contract_validation(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        intent = self._normalize_decision(decision)
        if (
            not normalized_question
            and intent.action != IntentAction.need_clarification
        ):
            raise ValueError("question is required")
        handler = self.handlers[intent.action]
        return handler.handle(intent)
'''
        self.assertFalse(scan_dispatch(source))

    def test_allows_question_shape_validation_that_only_raises(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        intent = self._normalize_decision(decision)
        if len(normalized_question) > 2000:
            raise ValueError("question is too long")
        return self.handlers[intent.action].handle(intent)
'''
        self.assertFalse(scan_dispatch(source))

    def test_allows_error_response_carrying_question_and_decision(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        intent = self._normalize_decision(decision)
        if self.store_is_unavailable():
            return self._error_entry(
                question=normalized_question,
                decision=intent,
                detail="context unavailable",
            )
        handler = self.handlers[intent.action]
        return handler.handle(intent)
'''
        self.assertFalse(scan_dispatch(source))

    def test_allows_error_response_inside_question_validation_branch(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        intent = self._normalize_decision(decision)
        if not normalized_question:
            return self._error_entry(
                question=normalized_question,
                decision=intent,
                detail="empty input",
            )
        return self.handlers[intent.action].handle(intent)
'''
        self.assertFalse(scan_dispatch(source))

    def test_rejects_keyword_condition_selecting_handler(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        intent = self._normalize_decision(decision)
        if "建议" in normalized_question:
            handler = self.handlers[IntentAction.advice_analysis]
        else:
            handler = self.handlers[intent.action]
        return handler.handle(intent)
'''
        self.assertTrue(scan_dispatch(source))

    def test_rejects_question_tainted_action_assignment(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        intent = self._normalize_decision(decision)
        intent.action = (
            IntentAction.advice_analysis
            if "建议" in normalized_question
            else intent.action
        )
        return self.handlers[intent.action].handle(intent)
'''
        self.assertTrue(scan_dispatch(source))

    def test_rejects_question_tainted_route_mapping(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        selected_action = ROUTE_MAP.get(normalized_question)
        return self.handlers[selected_action].handle(decision)
'''
        self.assertTrue(scan_dispatch(source))

    def test_rejects_direct_handler_mapping_lookup_by_question(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        return self.handlers[normalized_question].handle(decision)
'''
        self.assertTrue(scan_dispatch(source))

    def test_rejects_question_condition_returning_fixed_handler(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        intent = self._normalize_decision(decision)
        if "建议" in normalized_question:
            return self.handlers[IntentAction.advice_analysis].handle(intent)
        return self.handlers[intent.action].handle(intent)
'''
        self.assertTrue(scan_dispatch(source))

    def test_rejects_question_condition_returning_fixed_action_string(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        if "建议" in normalized_question:
            return "advice_analysis"
        return decision.action
'''
        self.assertTrue(scan_dispatch(source))

    def test_rejects_question_match_selecting_action(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        match normalized_question:
            case "advice":
                action = IntentAction.advice_analysis
            case _:
                action = decision.action
        return self.handlers[action].handle(decision)
'''
        self.assertTrue(scan_dispatch(source))

    def test_rejects_intent_action_constructor_from_question(self):
        source = '''
class LowRiskActionDispatcher:
    def dispatch(self, question, decision):
        normalized_question = str(question or "").strip()
        return IntentAction(normalized_question)
'''
        self.assertTrue(scan_dispatch(source))


if __name__ == "__main__":
    unittest.main(verbosity=2)
