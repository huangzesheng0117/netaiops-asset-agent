# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
APP = PROJECT / "app.py"


def dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def top_level_function(tree, name):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    raise AssertionError(f"missing function: {name}")


def call_lines(node):
    result = {}
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            name = dotted_name(item.func)
            result.setdefault(name, []).append(item.lineno)
    return result


class V4PreRouteIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(APP))

    def test_markers_are_single_and_balanced(self):
        self.assertEqual(
            self.source.count("# V4_2_3_ENTRY_ROUTER_MARKER_BEGIN"),
            1,
        )
        self.assertEqual(
            self.source.count("# V4_2_3_ENTRY_ROUTER_MARKER_END"),
            1,
        )
        self.assertEqual(
            self.source.count("# V4_2_3_PRE_ROUTE_CALL_MARKER_BEGIN"),
            1,
        )
        self.assertEqual(
            self.source.count("# V4_2_3_PRE_ROUTE_CALL_MARKER_END"),
            1,
        )

    def test_v4_call_precedes_legacy_business_routes(self):
        middleware = top_level_function(
            self.tree,
            "v2_chat_router_middleware",
        )
        lines = call_lines(middleware)
        v4_line = lines["_v4_try_pre_route"][0]
        for legacy_name in (
            "_v3_shadow_build",
            "_batch67_try_handle_advice_analysis",
            "try_handle_v2_inline_command_execution",
            "build_v2_semantic_route",
            "try_handle_v2_chat",
        ):
            self.assertIn(legacy_name, lines)
            self.assertLess(
                v4_line,
                min(lines[legacy_name]),
                f"V4 pre-route must precede {legacy_name}",
            )

    def test_app_helper_is_transport_only(self):
        helper = top_level_function(self.tree, "_v4_try_pre_route")
        lines = call_lines(helper)
        self.assertIn("try_handle_v4_pre_route", lines)
        argument_names = {arg.arg for arg in helper.args.args}
        self.assertEqual(
            argument_names,
            {"question", "user", "conversation_id"},
        )
        for node in ast.walk(helper):
            if isinstance(node, ast.Compare):
                compared = {
                    item.id
                    for item in ast.walk(node)
                    if isinstance(item, ast.Name)
                }
                self.assertNotIn("question", compared)

    def test_handled_v4_response_returns_before_v3_shadow(self):
        middleware = top_level_function(
            self.tree,
            "v2_chat_router_middleware",
        )
        source_segment = ast.get_source_segment(self.source, middleware) or ""
        handled_index = source_segment.index(
            'if v4_pre_route.get("handled"):'
        )
        return_index = source_segment.index(
            'return JSONResponse(v4_pre_route["response"])'
        )
        shadow_index = source_segment.index(
            "v3_shadow_state = v4_pre_route.get"
        )
        self.assertLess(handled_index, return_index)
        self.assertLess(return_index, shadow_index)

    def test_fallback_reuses_v4_arbiter_shadow_state(self):
        middleware = top_level_function(
            self.tree,
            "v2_chat_router_middleware",
        )
        source_segment = ast.get_source_segment(self.source, middleware) or ""
        self.assertIn(
            'v3_shadow_state = v4_pre_route.get("shadow_state")',
            source_segment,
        )
        self.assertIn(
            "if not v3_shadow_state:\n"
            "                v3_shadow_state = _v3_shadow_build(",
            source_segment,
        )


    def test_internal_exception_is_visible_v4_error_not_legacy_fallback(self):
        helper = top_level_function(self.tree, "_v4_try_pre_route")
        outer_try = next(
            node for node in helper.body
            if isinstance(node, ast.Try)
            and any(
                isinstance(item, ast.ImportFrom)
                and item.module == "netaiops_asset.chat_v4.app_bridge"
                for item in node.body
            )
        )
        route_try = next(
            node for node in helper.body
            if isinstance(node, ast.Try) and node is not outer_try
        )
        self.assertEqual(len(route_try.handlers), 1)
        handler = route_try.handlers[0]
        calls = call_lines(handler)
        self.assertIn("build_v4_internal_error_transport", calls)
        segment = ast.get_source_segment(self.source, handler) or ""
        self.assertNotIn('"handled": False', segment)
        self.assertNotIn('"fallback": True', segment)


if __name__ == "__main__":
    unittest.main(verbosity=2)
