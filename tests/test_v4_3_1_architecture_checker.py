#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import importlib.util
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

PROJECT = Path(__file__).resolve().parents[1]
CHECKER_PATH = PROJECT / "tools/check_v4_3_1_architecture.py"
SPEC = importlib.util.spec_from_file_location("v431_checker", CHECKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load V4.3-1 checker")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def parse_path(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def write_valid_tree(path: Path, tree: ast.Module) -> None:
    ast.fix_missing_locations(tree)
    text = ast.unparse(tree).rstrip() + "\n"
    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")


def find_class(tree: ast.Module, name: str) -> ast.ClassDef:
    cls = CHECKER.class_node(tree, name)
    if cls is None:
        raise AssertionError(f"missing class in fixture: {name}")
    return cls


def find_method(cls: ast.ClassDef, name: str):
    method = CHECKER.method_node(cls, name)
    if method is None:
        raise AssertionError(f"missing method in fixture: {cls.name}.{name}")
    return method


def find_function(tree: ast.Module, name: str):
    function = CHECKER.function_node(tree, name)
    if function is None:
        raise AssertionError(f"missing function in fixture: {name}")
    return function


def find_module_assignment(tree: ast.Module, name: str):
    matches = []
    for node in tree.body:
        targets, _value = CHECKER.assignment_parts(node)
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            matches.append(node)
    if len(matches) != 1:
        raise AssertionError(f"expected one assignment for {name}, actual={len(matches)}")
    return matches[0]


def insert_after_future(tree: ast.Module, node: ast.stmt) -> None:
    index = 0
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        index = 1
    while (
        index < len(tree.body)
        and isinstance(tree.body[index], ast.ImportFrom)
        and tree.body[index].module == "__future__"
    ):
        index += 1
    tree.body.insert(index, node)


def mutate_add_execution_action(tree: ast.Module) -> None:
    node = find_module_assignment(tree, "LOW_RISK_ACTIONS")
    _targets, value = CHECKER.assignment_parts(node)
    if not isinstance(value, ast.Call) or not value.args:
        raise AssertionError("invalid LOW_RISK_ACTIONS fixture")
    collection = value.args[0]
    if not isinstance(collection, (ast.Set, ast.List, ast.Tuple)):
        raise AssertionError("invalid LOW_RISK_ACTIONS collection")
    collection.elts.append(
        ast.Attribute(
            value=ast.Name(id="IntentAction", ctx=ast.Load()),
            attr="execute_provided_commands",
            ctx=ast.Load(),
        )
    )


def mutate_wrong_handler_mapping(tree: ast.Module) -> None:
    cls = find_class(tree, "LowRiskActionDispatcher")
    init = find_method(cls, "__init__")
    changed = 0
    for node in ast.walk(init):
        targets, value = CHECKER.assignment_parts(node)
        if not any(CHECKER.target_is_self_attribute(target, "handlers") for target in targets):
            continue
        if not isinstance(value, ast.Dict):
            raise AssertionError("handlers fixture is not a dict")
        for key, mapped in zip(value.keys, value.values):
            if key is not None and CHECKER.intent_action_name(key) == "generate_commands":
                if not isinstance(mapped, ast.Call):
                    raise AssertionError("generate handler mapping is not a call")
                mapped.func = ast.Name(id="CmdbQueryHandler", ctx=ast.Load())
                changed += 1
    if changed != 1:
        raise AssertionError(f"expected one handler mapping mutation, actual={changed}")


def insert_question_read(tree: ast.Module, class_name: str, alias: bool = False) -> None:
    handle = find_method(find_class(tree, class_name), "handle")
    if alias:
        injected = [
            ast.Assign(
                targets=[ast.Name(id="request_alias", ctx=ast.Store())],
                value=ast.Name(id="request", ctx=ast.Load()),
            ),
            ast.Assign(
                targets=[ast.Name(id="injected_question", ctx=ast.Store())],
                value=ast.Attribute(
                    value=ast.Name(id="request_alias", ctx=ast.Load()),
                    attr="question",
                    ctx=ast.Load(),
                ),
            ),
        ]
    else:
        injected = [
            ast.Assign(
                targets=[ast.Name(id="injected_question", ctx=ast.Store())],
                value=ast.Attribute(
                    value=ast.Name(id="request", ctx=ast.Load()),
                    attr="question",
                    ctx=ast.Load(),
                ),
            )
        ]
    handle.body[0:0] = injected


def insert_getattr_question_read(tree: ast.Module, class_name: str) -> None:
    handle = find_method(find_class(tree, class_name), "handle")
    handle.body.insert(
        0,
        ast.Assign(
            targets=[ast.Name(id="injected_question", ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id="getattr", ctx=ast.Load()),
                args=[
                    ast.Name(id="request", ctx=ast.Load()),
                    ast.Constant(value="question"),
                ],
                keywords=[],
            ),
        ),
    )


def mutate_add_pending_import(tree: ast.Module) -> None:
    insert_after_future(
        tree,
        ast.ImportFrom(
            module="netaiops_asset.chat_v2.confirmation",
            names=[ast.alias(name="store_pending_commands")],
            level=0,
        ),
    )


def mutate_add_executor_symbol(tree: ast.Module) -> None:
    insert_after_future(
        tree,
        ast.Assign(
            targets=[ast.Name(id="ConfirmedNetmikoExecutor", ctx=ast.Store())],
            value=ast.Constant(value=None),
        ),
    )


class ReplaceCall(ast.NodeTransformer):
    def __init__(self, old: str, new: ast.expr):
        self.old = old
        self.new = new
        self.changed = 0

    def visit_Call(self, node: ast.Call):
        self.generic_visit(node)
        if CHECKER.dotted_name(node.func) == self.old:
            self.changed += 1
            return ast.copy_location(self.new, node)
        return node


def mutate_remove_splitter(tree: ast.Module) -> None:
    handle = find_method(find_class(tree, "GenerateCommandsHandler"), "handle")
    transform = ReplaceCall(
        "self.splitter",
        ast.Call(
            func=ast.Name(id="list", ctx=ast.Load()),
            args=[ast.Name(id="raw_commands", ctx=ast.Load())],
            keywords=[],
        ),
    )
    transform.visit(handle)
    if transform.changed != 1:
        raise AssertionError(f"expected one splitter mutation, actual={transform.changed}")


def mutate_remove_cmdb_field_guard(tree: ast.Module) -> None:
    cls = find_class(tree, "CmdbQueryHandler")
    transform = ReplaceCall(
        "normalize_field_name",
        ast.Call(
            func=ast.Name(id="str", ctx=ast.Load()),
            args=[ast.Name(id="raw", ctx=ast.Load())],
            keywords=[],
        ),
    )
    transform.visit(cls)
    if transform.changed < 1:
        raise AssertionError("normalize_field_name mutation did not apply")


class ReplaceAttribute(ast.NodeTransformer):
    def __init__(self, old: str, new: ast.expr):
        self.old = old
        self.new = new
        self.changed = 0

    def visit_Attribute(self, node: ast.Attribute):
        self.generic_visit(node)
        if CHECKER.dotted_name(node) == self.old:
            self.changed += 1
            return ast.copy_location(self.new, node)
        return node


def mutate_wrong_context_scope(tree: ast.Module) -> None:
    dispatch = find_method(find_class(tree, "LowRiskActionDispatcher"), "dispatch")
    transform = ReplaceAttribute(
        "context_read.context.topic",
        ast.Attribute(
            value=ast.Name(id="context", ctx=ast.Load()),
            attr="topic",
            ctx=ast.Load(),
        ),
    )
    transform.visit(dispatch)
    if transform.changed != 1:
        raise AssertionError(f"expected one context mutation, actual={transform.changed}")


def mutate_resolver_probe_true(tree: ast.Module) -> None:
    handle = find_method(find_class(tree, "GenerateCommandsHandler"), "handle")
    calls = CHECKER.call_nodes(handle, "resolver.resolve")
    if len(calls) != 1:
        raise AssertionError(f"expected one resolver call, actual={len(calls)}")
    changed = 0
    for keyword in calls[0].keywords:
        if keyword.arg == "probe_prometheus":
            keyword.value = ast.Constant(value=True)
            changed += 1
    if changed != 1:
        raise AssertionError("probe_prometheus keyword mutation did not apply")


def mutate_stale_bridge_version(tree: ast.Module) -> None:
    import_changes = 0
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "netaiops_asset.chat_v4.entry_router"
        ):
            before = len(node.names)
            node.names = [
                alias for alias in node.names if alias.name != "V4_ENTRY_ROUTER_VERSION"
            ]
            import_changes += before - len(node.names)
    if import_changes != 1:
        raise AssertionError(f"expected one version import removal, actual={import_changes}")

    class ReplaceVersionName(ast.NodeTransformer):
        def __init__(self):
            self.changed = 0

        def visit_Name(self, node: ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id == "V4_ENTRY_ROUTER_VERSION":
                self.changed += 1
                return ast.copy_location(ast.Constant(value="v4.entry_router.2_3"), node)
            return node

    transform = ReplaceVersionName()
    transform.visit(tree)
    if transform.changed < 1:
        raise AssertionError("delegated version use mutation did not apply")


class RemoveDataAssignment(ast.NodeTransformer):
    def __init__(self, key: str):
        self.key = key
        self.changed = 0

    def visit_Assign(self, node: ast.Assign):
        self.generic_visit(node)
        targets, _value = CHECKER.assignment_parts(node)
        if any(CHECKER.is_named_subscript(target, "data", self.key) for target in targets):
            self.changed += 1
            return None
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.generic_visit(node)
        targets, _value = CHECKER.assignment_parts(node)
        if any(CHECKER.is_named_subscript(target, "data", self.key) for target in targets):
            self.changed += 1
            return None
        return node


def remove_normalized_data_key(tree: ast.Module, key: str) -> None:
    normalize = find_function(tree, "normalize_payload")
    transform = RemoveDataAssignment(key)
    transform.visit(normalize)
    if transform.changed != 1:
        raise AssertionError(
            f"expected one data[{key!r}] assignment removal, actual={transform.changed}"
        )


def mutate_remove_cmdb_normalization(tree: ast.Module) -> None:
    remove_normalized_data_key(tree, "cmdb_query")


def mutate_remove_command_generation_normalization(tree: ast.Module) -> None:
    remove_normalized_data_key(tree, "command_generation")


class ReplaceDottedAttribute(ast.NodeTransformer):
    def __init__(self, old: str, new: ast.expr):
        self.old = old
        self.new = new
        self.changed = 0

    def visit_Attribute(self, node: ast.Attribute):
        self.generic_visit(node)
        if CHECKER.dotted_name(node) == self.old:
            self.changed += 1
            return ast.copy_location(self.new, node)
        return node


def mutate_generate_branch_wrong_action(tree: ast.Module) -> None:
    normalize = find_function(tree, "normalize_payload")
    branch = CHECKER.find_generate_action_branch(normalize)
    replacement = ast.Attribute(
        value=ast.Attribute(
            value=ast.Name(id="IntentAction", ctx=ast.Load()),
            attr="cmdb_query",
            ctx=ast.Load(),
        ),
        attr="value",
        ctx=ast.Load(),
    )
    transform = ReplaceDottedAttribute(
        "IntentAction.generate_commands.value",
        replacement,
    )
    transform.visit(branch.test)
    if transform.changed != 1:
        raise AssertionError(
            f"expected one generate action comparison mutation, actual={transform.changed}"
        )


def set_generate_branch_value(tree: ast.Module, key: str, value: ast.expr) -> None:
    normalize = find_function(tree, "normalize_payload")
    branch = CHECKER.find_generate_action_branch(normalize)
    scope = ast.Module(body=list(branch.body), type_ignores=[])
    matches = CHECKER.data_key_assignments(scope, key)
    if len(matches) != 1:
        raise AssertionError(
            f"expected one generate branch assignment for {key}, actual={len(matches)}"
        )
    node, _old = matches[0]
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        node.value = value
    else:
        raise AssertionError(f"unsupported assignment node for {key}: {type(node).__name__}")


def mutate_generate_commands_not_cleared(tree: ast.Module) -> None:
    set_generate_branch_value(
        tree,
        "commands",
        ast.List(elts=[ast.Constant(value="show version")], ctx=ast.Load()),
    )


def mutate_generate_requires_confirmation_false(tree: ast.Module) -> None:
    set_generate_branch_value(
        tree,
        "requires_confirmation",
        ast.Constant(value=False),
    )


def mutate_generate_should_execute_true(tree: ast.Module) -> None:
    set_generate_branch_value(
        tree,
        "should_execute_commands",
        ast.Constant(value=True),
    )


class ReplaceStringFragmentOnce(ast.NodeTransformer):
    def __init__(self, old: str, new: str):
        self.old = old
        self.new = new
        self.changed = 0

    def visit_Constant(self, node: ast.Constant):
        if (
            self.changed == 0
            and isinstance(node.value, str)
            and self.old in node.value
        ):
            node.value = node.value.replace(self.old, self.new, 1)
            self.changed += 1
        return node


def mutate_prompt_missing_cmdb_schema(tree: ast.Module) -> None:
    prompt = find_function(tree, "build_intent_messages")
    transform = ReplaceStringFragmentOnce('"cmdb_query":', '"removed_cmdb_query":')
    transform.visit(prompt)
    if transform.changed != 1:
        raise AssertionError(
            f"expected one prompt CMDB schema mutation, actual={transform.changed}"
        )


def mutate_entry_fallback_reason(tree: ast.Module) -> None:
    route = find_method(find_class(tree, "V4EntryRouter"), "route")
    branch = CHECKER.find_stage_fallback_branch(route)
    calls = CHECKER.call_nodes(
        ast.Module(body=list(branch.body), type_ignores=[]),
        "EntryRouteResult",
    )
    if len(calls) != 1:
        raise AssertionError(f"expected one stage result call, actual={len(calls)}")
    changed = 0
    for keyword in calls[0].keywords:
        if keyword.arg == "reason":
            keyword.value = ast.Constant(value="action_not_enabled_in_v4_2_3")
            changed += 1
    if changed != 1:
        raise AssertionError("stage fallback reason mutation did not apply")


CHECKER_FIXTURE_FILES = (
    "netaiops_asset/chat_v4/action_dispatcher.py",
    "netaiops_asset/chat_v3/intent_schema.py",
    "netaiops_asset/chat_v3/intent_arbiter.py",
    "netaiops_asset/chat_v4/entry_router.py",
    "netaiops_asset/chat_v4/app_bridge.py",
    "netaiops_asset/chat_v4/handlers/cmdb_query.py",
    "netaiops_asset/chat_v4/handlers/generate_commands.py",
)


def copy_checker_fixture(source_root: Path, destination_root: Path) -> None:
    """Copy only the seven real source files consumed by check_project().

    The production repository may contain backup/, venv/ and other unrelated
    unreadable files.  Fault-injection fixtures must never traverse those paths.
    """
    for relative in CHECKER_FIXTURE_FILES:
        source = source_root / relative
        destination = destination_root / relative
        if not source.is_file():
            raise AssertionError(f"checker fixture source is missing: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


@contextmanager
def fresh_fixture() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="v431-checker-") as temp:
        fixture = Path(temp) / "target"
        copy_checker_fixture(PROJECT, fixture)
        yield fixture


class V431ArchitectureCheckerTests(unittest.TestCase):
    def _mutate_and_expect_code(
        self,
        relative: str,
        mutator: Callable[[ast.Module], None],
        expected_code: str,
    ) -> None:
        with fresh_fixture() as fixture:
            path = fixture / relative
            tree = parse_path(path)
            mutator(tree)
            write_valid_tree(path, tree)
            # A fault fixture is valid only when the modified source still compiles.
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            with self.assertRaises(CHECKER.ArchitectureError) as raised:
                CHECKER.check_project(fixture)
            self.assertEqual(raised.exception.code, expected_code)

    def test_real_complete_target_is_valid(self):
        CHECKER.check_project(PROJECT)

    def test_annotated_self_handlers_assignment_is_supported(self):
        tree = parse_path(PROJECT / "netaiops_asset/chat_v4/action_dispatcher.py")
        cls = find_class(tree, "LowRiskActionDispatcher")
        self.assertEqual(CHECKER.handler_mapping(cls), CHECKER.EXPECTED_ACTIONS)

    def test_plain_self_handlers_assignment_is_supported(self):
        source = """
class LowRiskActionDispatcher:
    def __init__(self):
        self.handlers = {
            IntentAction.general_chat: GeneralChatHandler(),
            IntentAction.advice_analysis: AdviceAnalysisHandler(),
            IntentAction.need_clarification: ClarificationHandler(),
            IntentAction.cmdb_query: CmdbQueryHandler(),
            IntentAction.generate_commands: GenerateCommandsHandler(),
        }
"""
        tree = ast.parse(source)
        cls = find_class(tree, "LowRiskActionDispatcher")
        self.assertEqual(CHECKER.handler_mapping(cls), CHECKER.EXPECTED_ACTIONS)

    def test_rejects_execution_action_in_handled_set(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/action_dispatcher.py",
            mutate_add_execution_action,
            "V431_ACTION_SET_INVALID",
        )

    def test_rejects_wrong_handler_mapping(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/action_dispatcher.py",
            mutate_wrong_handler_mapping,
            "V431_HANDLER_MAP_INVALID",
        )

    def test_rejects_cmdb_question_text_dependency(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/cmdb_query.py",
            lambda tree: insert_question_read(tree, "CmdbQueryHandler"),
            "V431_HANDLER_QUESTION_DEPENDENCY",
        )

    def test_rejects_command_question_alias_dependency(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/generate_commands.py",
            lambda tree: insert_question_read(tree, "GenerateCommandsHandler", alias=True),
            "V431_HANDLER_QUESTION_DEPENDENCY",
        )

    def test_rejects_command_question_getattr_dependency(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/generate_commands.py",
            lambda tree: insert_getattr_question_read(tree, "GenerateCommandsHandler"),
            "V431_HANDLER_QUESTION_DEPENDENCY",
        )

    def test_rejects_pending_import(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/generate_commands.py",
            mutate_add_pending_import,
            "V431_FORBIDDEN_IMPORT",
        )

    def test_rejects_executor_symbol(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/generate_commands.py",
            mutate_add_executor_symbol,
            "V431_FORBIDDEN_EXECUTION_SYMBOL",
        )

    def test_rejects_missing_splitter_stage(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/generate_commands.py",
            mutate_remove_splitter,
            "V431_COMMAND_CALL_CHAIN_INVALID",
        )

    def test_rejects_resolver_prometheus_probe(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/generate_commands.py",
            mutate_resolver_probe_true,
            "V431_COMMAND_RESOLVER_PROBE_INVALID",
        )

    def test_rejects_missing_cmdb_field_guard(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/handlers/cmdb_query.py",
            mutate_remove_cmdb_field_guard,
            "V431_CMDB_CALL_CHAIN_INVALID",
        )

    def test_rejects_dispatcher_wrong_context_scope(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/action_dispatcher.py",
            mutate_wrong_context_scope,
            "V431_DISPATCHER_CONTEXT_FLOW_INVALID",
        )

    def test_rejects_stale_bridge_version_with_valid_python(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/app_bridge.py",
            mutate_stale_bridge_version,
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
        )


    def test_real_arbiter_normalizer_uses_structural_enum_contract(self):
        tree = parse_path(PROJECT / "netaiops_asset/chat_v3/intent_arbiter.py")
        normalize = find_function(tree, "normalize_payload")
        CHECKER.assert_arbiter_normalizer_contract(normalize)
        branch = CHECKER.find_generate_action_branch(normalize)
        self.assertTrue(CHECKER.is_generate_action_test(branch.test))

    def test_real_arbiter_prompt_scope_is_valid(self):
        tree = parse_path(PROJECT / "netaiops_asset/chat_v3/intent_arbiter.py")
        CHECKER.assert_arbiter_prompt_contract(
            find_function(tree, "build_intent_messages")
        )

    def test_real_entry_stage_fallback_scope_is_valid(self):
        tree = parse_path(PROJECT / "netaiops_asset/chat_v4/entry_router.py")
        CHECKER.assert_entry_stage_fallback(tree)

    def test_real_bridge_version_delegation_scope_is_valid(self):
        tree = parse_path(PROJECT / "netaiops_asset/chat_v4/app_bridge.py")
        CHECKER.assert_bridge_version_delegation(tree)

    def test_rejects_missing_cmdb_payload_normalization(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v3/intent_arbiter.py",
            mutate_remove_cmdb_normalization,
            "V431_ARBITER_NORMALIZER_INVALID",
        )

    def test_rejects_missing_command_generation_payload_normalization(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v3/intent_arbiter.py",
            mutate_remove_command_generation_normalization,
            "V431_ARBITER_NORMALIZER_INVALID",
        )

    def test_rejects_wrong_generate_action_comparison(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v3/intent_arbiter.py",
            mutate_generate_branch_wrong_action,
            "V431_ARBITER_GENERATE_CONTRACT_INVALID",
        )

    def test_rejects_generated_commands_not_cleared(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v3/intent_arbiter.py",
            mutate_generate_commands_not_cleared,
            "V431_ARBITER_GENERATE_CONTRACT_INVALID",
        )

    def test_rejects_generated_requires_confirmation_false(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v3/intent_arbiter.py",
            mutate_generate_requires_confirmation_false,
            "V431_ARBITER_GENERATE_CONTRACT_INVALID",
        )

    def test_rejects_generated_should_execute_true(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v3/intent_arbiter.py",
            mutate_generate_should_execute_true,
            "V431_ARBITER_GENERATE_CONTRACT_INVALID",
        )

    def test_rejects_prompt_missing_structured_cmdb_schema(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v3/intent_arbiter.py",
            mutate_prompt_missing_cmdb_schema,
            "V431_ARBITER_PROMPT_INVALID",
        )

    def test_rejects_entry_stage_fallback_reason_mismatch(self):
        self._mutate_and_expect_code(
            "netaiops_asset/chat_v4/entry_router.py",
            mutate_entry_fallback_reason,
            "V431_ENTRY_FALLBACK_INVALID",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
