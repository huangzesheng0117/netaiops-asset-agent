#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V4.3-1 architecture checker using real AST scopes and explicit error codes.

The complete transformed target tree is the positive fixture. Negative fixtures
are syntax-valid, single-fault copies of that same target. The checker validates
real functions, assignments, calls, call ordering and data flow; it does not use
marker counts or broad repository text scans as proof of business behavior.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional

EXPECTED_ACTIONS = {
    "general_chat": "GeneralChatHandler",
    "advice_analysis": "AdviceAnalysisHandler",
    "need_clarification": "ClarificationHandler",
    "cmdb_query": "CmdbQueryHandler",
    "generate_commands": "GenerateCommandsHandler",
}
EXECUTION_ACTIONS = {
    "execute_provided_commands",
    "execute_provided_commands_and_analyze",
    "confirm_execute_pending",
    "analyze_existing_evidence",
}
FORBIDDEN_EXECUTION_SYMBOLS = {
    "ConfirmedNetmikoExecutor",
    "store_pending_commands",
    "load_pending_commands",
    "try_handle_v2_execution_confirmation",
    "execute_commands",
    "send_command",
    "send_config_set",
}
FORBIDDEN_HANDLER_MODULE_PREFIXES = (
    "netaiops_asset.chat_v2.router",
    "netaiops_asset.chat_v2.plan_dispatcher",
    "netaiops_asset.chat_v2.llm_intent_planner",
    "netaiops_asset.chat_v2.confirmation",
    "netaiops_asset.netmiko.executor",
)


class ArchitectureError(RuntimeError):
    """Stable checker failure with a machine-testable code."""

    def __init__(self, code: str, detail: str):
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{self.code}: {self.detail}")


def fail(code: str, detail: str) -> None:
    raise ArchitectureError(code, detail)


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def parse(path: Path) -> ast.Module:
    if not path.is_file():
        fail("V431_FILE_MISSING", str(path))
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        fail("V431_SYNTAX_INVALID", f"{path}: {exc}")
    raise AssertionError("unreachable")


def class_node(tree: ast.Module, name: str) -> Optional[ast.ClassDef]:
    return next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name),
        None,
    )


def function_node(tree: ast.Module, name: str):
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def method_node(cls: ast.ClassDef, name: str):
    return next(
        (
            node
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def assignment_parts(
    node: ast.AST,
) -> tuple[list[ast.AST], Optional[ast.AST]]:
    """Normalize Assign/AnnAssign/NamedExpr without guessing from source text."""
    if isinstance(node, ast.Assign):
        return list(node.targets), node.value
    if isinstance(node, ast.AnnAssign):
        return [node.target], node.value
    if isinstance(node, ast.NamedExpr):
        return [node.target], node.value
    return [], None


def imported_modules(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def imported_names(tree: ast.AST, module_name: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            result.update(alias.asname or alias.name for alias in node.names)
    return result


def call_nodes(node: ast.AST, call_name: str) -> list[ast.Call]:
    return sorted(
        [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and dotted_name(child.func) == call_name
        ],
        key=lambda item: (getattr(item, "lineno", 0), getattr(item, "col_offset", 0)),
    )


def call_names(node: ast.AST) -> set[str]:
    return {
        dotted_name(child.func)
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    }


def attribute_paths(node: ast.AST) -> set[str]:
    return {
        dotted_name(child)
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute)
    }


def string_constants(node: ast.AST) -> set[str]:
    return {
        str(child.value)
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def intent_action_name(node: ast.AST) -> str:
    name = dotted_name(node)
    return name.split(".", 1)[1] if name.startswith("IntentAction.") else ""


def target_is_name(target: ast.AST, name: str) -> bool:
    return isinstance(target, ast.Name) and target.id == name


def target_is_self_attribute(target: ast.AST, attribute: str) -> bool:
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and target.attr == attribute
    )


def literal_action_set(tree: ast.Module, variable: str) -> set[str]:
    for node in tree.body:
        targets, value = assignment_parts(node)
        if not any(target_is_name(target, variable) for target in targets):
            continue
        if not isinstance(value, ast.Call) or dotted_name(value.func) not in {
            "set",
            "frozenset",
        }:
            fail(
                "V431_ACTION_SET_INVALID",
                f"{variable} must use a deterministic set/frozenset call",
            )
        if len(value.args) != 1 or not isinstance(
            value.args[0],
            (ast.Set, ast.List, ast.Tuple),
        ):
            fail(
                "V431_ACTION_SET_INVALID",
                f"{variable} must contain a literal action collection",
            )
        actions = {
            intent_action_name(item)
            for item in value.args[0].elts
            if intent_action_name(item)
        }
        if len(actions) != len(value.args[0].elts):
            fail(
                "V431_ACTION_SET_INVALID",
                f"{variable} contains a non-IntentAction entry",
            )
        return actions
    fail("V431_ACTION_SET_INVALID", f"missing action collection: {variable}")
    return set()


def handler_mapping(dispatcher_class: ast.ClassDef) -> dict[str, str]:
    init = method_node(dispatcher_class, "__init__")
    if init is None:
        fail("V431_HANDLER_MAP_INVALID", "LowRiskActionDispatcher.__init__ is missing")
    matches: list[ast.Dict] = []
    for node in ast.walk(init):
        targets, value = assignment_parts(node)
        if any(target_is_self_attribute(target, "handlers") for target in targets):
            if not isinstance(value, ast.Dict):
                fail(
                    "V431_HANDLER_MAP_INVALID",
                    "self.handlers must be assigned a literal dict",
                )
            matches.append(value)
    if len(matches) != 1:
        fail(
            "V431_HANDLER_MAP_INVALID",
            f"expected one self.handlers assignment, actual={len(matches)}",
        )
    result: dict[str, str] = {}
    mapping = matches[0]
    for key, value in zip(mapping.keys, mapping.values):
        action = intent_action_name(key) if key is not None else ""
        if not action or not isinstance(value, ast.Call):
            fail("V431_HANDLER_MAP_INVALID", "invalid self.handlers mapping entry")
        handler = dotted_name(value.func)
        if action in result:
            fail("V431_HANDLER_MAP_INVALID", f"duplicate handler action: {action}")
        result[action] = handler
    return result


def assigned_literal(tree: ast.Module, variable: str):
    for node in tree.body:
        targets, value = assignment_parts(node)
        if not any(target_is_name(target, variable) for target in targets):
            continue
        try:
            return ast.literal_eval(value)
        except Exception as exc:
            fail(
                "V431_LITERAL_CONTRACT_INVALID",
                f"{variable} must be a literal: {exc}",
            )
    fail("V431_LITERAL_CONTRACT_INVALID", f"missing literal assignment: {variable}")


def _root_name(node: ast.AST) -> str:
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def request_aliases(handle: ast.AST) -> set[str]:
    aliases = {"request"}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(handle):
            targets, value = assignment_parts(node)
            if value is None:
                continue
            source = ""
            if isinstance(value, ast.Name):
                source = value.id
            if source not in aliases:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases


def assert_no_question_dependency(handle: ast.AST, class_name: str) -> None:
    aliases = request_aliases(handle)
    for node in ast.walk(handle):
        if isinstance(node, ast.Attribute) and node.attr == "question":
            if _root_name(node) in aliases:
                fail(
                    "V431_HANDLER_QUESTION_DEPENDENCY",
                    f"{class_name} reads request.question",
                )
        if isinstance(node, ast.Call) and dotted_name(node.func) == "getattr":
            if len(node.args) >= 2:
                owner, attr = node.args[0], node.args[1]
                if (
                    isinstance(owner, ast.Name)
                    and owner.id in aliases
                    and isinstance(attr, ast.Constant)
                    and attr.value == "question"
                ):
                    fail(
                        "V431_HANDLER_QUESTION_DEPENDENCY",
                        f"{class_name} reads request.question through getattr",
                    )


def assert_handler_action(
    tree: ast.Module,
    class_name: str,
    action: str,
) -> tuple[ast.ClassDef, ast.FunctionDef | ast.AsyncFunctionDef]:
    cls = class_node(tree, class_name)
    if cls is None:
        fail("V431_HANDLER_CLASS_MISSING", class_name)
    assignments: list[str] = []
    for node in cls.body:
        targets, value = assignment_parts(node)
        if any(target_is_name(target, "action") for target in targets):
            assignments.append(intent_action_name(value))
    if assignments != [action]:
        fail(
            "V431_HANDLER_ACTION_INVALID",
            f"{class_name}.action expected={action} actual={assignments}",
        )
    handle = method_node(cls, "handle")
    if handle is None:
        fail("V431_HANDLER_HANDLE_MISSING", f"{class_name}.handle")
    assert_no_question_dependency(handle, class_name)
    return cls, handle


def keyword_map(call: ast.Call) -> dict[str, ast.AST]:
    return {item.arg: item.value for item in call.keywords if item.arg}


def assert_dispatcher_context_dataflow(dispatcher_cls: ast.ClassDef) -> None:
    dispatch = method_node(dispatcher_cls, "dispatch")
    if dispatch is None:
        fail("V431_DISPATCHER_MISSING", "LowRiskActionDispatcher.dispatch")
    calls = call_nodes(dispatch, "self.store.append_turn")
    if len(calls) != 1:
        fail(
            "V431_DISPATCHER_CONTEXT_FLOW_INVALID",
            f"expected one append_turn call, actual={len(calls)}",
        )
    keywords = keyword_map(calls[0])
    for name in ("topic", "device_context"):
        if name not in keywords:
            fail(
                "V431_DISPATCHER_CONTEXT_FLOW_INVALID",
                f"append_turn missing keyword: {name}",
            )
    topic_paths = attribute_paths(keywords["topic"])
    if not {
        "outcome.metadata",
        "context_read.context.topic",
    }.issubset(topic_paths):
        fail(
            "V431_DISPATCHER_CONTEXT_FLOW_INVALID",
            "topic must use outcome.metadata with context_read.context.topic fallback",
        )
    device_paths = attribute_paths(keywords["device_context"])
    if not {
        "outcome.metadata",
        "context_read.context.device_context",
    }.issubset(device_paths):
        fail(
            "V431_DISPATCHER_CONTEXT_FLOW_INVALID",
            "device_context must use outcome.metadata with context_read.context.device_context fallback",
        )
    invalid_roots = {
        path
        for path in topic_paths | device_paths
        if path == "context.topic"
        or path.startswith("context.topic.")
        or path == "context.device_context"
        or path.startswith("context.device_context.")
    }
    if invalid_roots:
        fail(
            "V431_DISPATCHER_CONTEXT_FLOW_INVALID",
            f"invalid local context scope: {sorted(invalid_roots)}",
        )


def assert_no_execution_symbols(path: Path, tree: ast.Module) -> None:
    forbidden_imports = {
        module
        for module in imported_modules(tree)
        if any(
            module == prefix or module.startswith(prefix + ".")
            for prefix in FORBIDDEN_HANDLER_MODULE_PREFIXES
        )
    }
    if forbidden_imports:
        fail(
            "V431_FORBIDDEN_IMPORT",
            f"{path.name}: {sorted(forbidden_imports)}",
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_EXECUTION_SYMBOLS:
            fail(
                "V431_FORBIDDEN_EXECUTION_SYMBOL",
                f"{path.name}: {node.id}",
            )
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_EXECUTION_SYMBOLS:
            fail(
                "V431_FORBIDDEN_EXECUTION_SYMBOL",
                f"{path.name}: {node.attr}",
            )


def first_call_line(handle: ast.AST, name: str, code: str) -> int:
    calls = call_nodes(handle, name)
    if not calls:
        fail(code, f"missing call: {name}")
    return int(getattr(calls[0], "lineno", 0))


def assert_cmdb_call_chain(
    cls: ast.ClassDef,
    handle: ast.FunctionDef | ast.AsyncFunctionDef,
) -> None:
    guard_lines = [
        first_call_line(handle, "self._validate_fields", "V431_CMDB_CALL_CHAIN_INVALID"),
        first_call_line(handle, "self._validate_filters", "V431_CMDB_CALL_CHAIN_INVALID"),
    ]
    query_names = ("self.query_detail", "self.query_by_ips", "self.query_devices")
    query_lines = [
        int(getattr(call, "lineno", 0))
        for name in query_names
        for call in call_nodes(handle, name)
    ]
    if len(query_lines) != 3:
        fail(
            "V431_CMDB_CALL_CHAIN_INVALID",
            f"expected three read-only query delegates, actual={len(query_lines)}",
        )
    if max(guard_lines) >= min(query_lines):
        fail(
            "V431_CMDB_CALL_CHAIN_INVALID",
            "field/filter validation must precede every CMDB query delegate",
        )
    cls_calls = call_names(cls)
    for required in {
        "normalize_field_name",
        "HandlerOutcome.success",
        "HandlerOutcome.failure",
    }:
        if required not in cls_calls:
            fail("V431_CMDB_CALL_CHAIN_INVALID", f"missing call: {required}")
    if not {
        "request.decision.cmdb_query",
        "request.decision.device_hint",
        "request.canonical_context.device_context",
    }.issubset(attribute_paths(cls)):
        fail(
            "V431_CMDB_STRUCTURED_INPUT_INVALID",
            "CMDB handler must consume structured decision/context inputs",
        )


def require_false_keyword(call: ast.Call, keyword: str, code: str) -> None:
    values = [item.value for item in call.keywords if item.arg == keyword]
    if len(values) != 1 or not isinstance(values[0], ast.Constant) or values[0].value is not False:
        fail(code, f"{dotted_name(call.func)} requires {keyword}=False")


def _dict_literal_items(node: ast.AST) -> dict[str, ast.AST]:
    if not isinstance(node, ast.Dict):
        return {}
    result: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            result[str(key.value)] = value
    return result


def assert_command_output_contract(handle: ast.AST) -> None:
    success_calls = call_nodes(handle, "HandlerOutcome.success")
    if len(success_calls) < 2:
        fail(
            "V431_COMMAND_OUTPUT_CONTRACT_INVALID",
            f"expected at least two success outcomes, actual={len(success_calls)}",
        )
    required = {
        "command_source": "system_generated",
        "requires_confirmation": True,
        "execution_started": False,
        "pending_created": False,
        "side_effect_started": False,
    }
    for call in success_calls:
        keywords = keyword_map(call)
        metadata = _dict_literal_items(keywords.get("metadata", ast.Constant(None)))
        for key, expected in required.items():
            value = metadata.get(key)
            if not isinstance(value, ast.Constant) or value.value != expected:
                fail(
                    "V431_COMMAND_OUTPUT_CONTRACT_INVALID",
                    f"HandlerOutcome.success metadata {key} must be {expected!r}",
                )


def assert_command_call_chain(
    cls: ast.ClassDef,
    handle: ast.FunctionDef | ast.AsyncFunctionDef,
) -> None:
    ordered_names = [
        "self.resolver_factory",
        "resolver.resolve",
        "self.catalog",
        "self.splitter",
        "self.safety_checker",
        "self.guard_factory",
        "guard.validate",
    ]
    lines = [
        first_call_line(handle, name, "V431_COMMAND_CALL_CHAIN_INVALID")
        for name in ordered_names
    ]
    if lines != sorted(lines) or len(set(lines)) != len(lines):
        fail(
            "V431_COMMAND_CALL_CHAIN_INVALID",
            f"invalid deterministic call order: {list(zip(ordered_names, lines))}",
        )
    resolver_calls = call_nodes(handle, "resolver.resolve")
    if len(resolver_calls) != 1:
        fail(
            "V431_COMMAND_CALL_CHAIN_INVALID",
            f"expected one resolver.resolve call, actual={len(resolver_calls)}",
        )
    require_false_keyword(
        resolver_calls[0],
        "probe_prometheus",
        "V431_COMMAND_RESOLVER_PROBE_INVALID",
    )
    if "HandlerOutcome.failure" not in call_names(cls):
        fail("V431_COMMAND_CALL_CHAIN_INVALID", "missing HandlerOutcome.failure")
    if not {
        "request.decision.command_generation",
        "request.decision.device_hint",
        "request.canonical_context.device_context",
    }.issubset(attribute_paths(cls)):
        fail(
            "V431_COMMAND_STRUCTURED_INPUT_INVALID",
            "command handler must consume structured decision/context inputs",
        )
    assert_command_output_contract(handle)




def subscript_string_key(node: ast.AST) -> str:
    if not isinstance(node, ast.Subscript):
        return ""
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return ""


def is_named_subscript(node: ast.AST, owner: str, key: str) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and dotted_name(node.value) == owner
        and subscript_string_key(node) == key
    )


def loaded_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def data_get_keys(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or dotted_name(child.func) != "data.get":
            continue
        if not child.args:
            continue
        first = child.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            result.add(first.value)
    return result


def data_key_assignments(
    scope: ast.AST,
    key: str,
) -> list[tuple[ast.AST, ast.AST]]:
    result: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(scope):
        targets, value = assignment_parts(node)
        if value is None:
            continue
        if any(is_named_subscript(target, "data", key) for target in targets):
            result.append((node, value))
    return sorted(result, key=lambda item: getattr(item[0], "lineno", 0))


def local_name_assignments(
    scope: ast.AST,
    name: str,
) -> list[tuple[ast.AST, ast.AST]]:
    result: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(scope):
        targets, value = assignment_parts(node)
        if value is None:
            continue
        if any(target_is_name(target, name) for target in targets):
            result.append((node, value))
    return sorted(result, key=lambda item: getattr(item[0], "lineno", 0))


def local_assignment_value(scope: ast.AST, name: str) -> ast.AST:
    matches = local_name_assignments(scope, name)
    if len(matches) != 1:
        fail(
            "V431_ASSIGNMENT_CONTRACT_INVALID",
            f"expected one assignment to {name}, actual={len(matches)}",
        )
    return matches[0][1]


def static_text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(static_text(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return "{DYNAMIC_VALUE}"
    return "".join(static_text(child) for child in ast.iter_child_nodes(node))


def _literal_matches(node: ast.AST, expected: object) -> bool:
    if expected == []:
        return isinstance(node, ast.List) and not node.elts
    return isinstance(node, ast.Constant) and node.value == expected


def assert_structured_mapping_normalization(
    normalize: ast.AST,
    *,
    key: str,
    local_name: str,
    allowed_aliases: set[str],
) -> None:
    final_assignments = data_key_assignments(normalize, key)
    if len(final_assignments) != 1:
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"data[{key!r}] assignment count={len(final_assignments)}",
        )
    final_node, final_value = final_assignments[0]
    if not (
        isinstance(final_value, ast.Call)
        and dotted_name(final_value.func) == "dict"
        and len(final_value.args) == 1
        and not final_value.keywords
    ):
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"data[{key!r}] must be normalized through dict(...)",
        )
    source_expression = final_value.args[0]
    if local_name not in loaded_names(source_expression):
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"data[{key!r}] does not consume local {local_name}",
        )
    if not any(
        isinstance(child, ast.Dict) and not child.keys
        for child in ast.walk(source_expression)
    ):
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"data[{key!r}] lacks an empty-dict fallback",
        )

    source_assignments = local_name_assignments(normalize, local_name)
    if not source_assignments:
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"missing local normalization variable: {local_name}",
        )
    observed_get_keys: set[str] = set()
    for _node, value in source_assignments:
        observed_get_keys.update(data_get_keys(value))
    allowed_keys = {key, *allowed_aliases}
    if key not in observed_get_keys:
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"{local_name} never reads data.get({key!r})",
        )
    unexpected = observed_get_keys - allowed_keys
    if unexpected:
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"{local_name} reads unexpected payload keys: {sorted(unexpected)}",
        )
    if min(getattr(node, "lineno", 0) for node, _ in source_assignments) >= getattr(
        final_node,
        "lineno",
        0,
    ):
        fail(
            "V431_ARBITER_NORMALIZER_INVALID",
            f"{local_name} must be prepared before data[{key!r}] is written",
        )


def _is_generate_action_expression(node: ast.AST) -> bool:
    return dotted_name(node) == "IntentAction.generate_commands.value"


def _is_data_action_expression(node: ast.AST) -> bool:
    return is_named_subscript(node, "data", "action")


def is_generate_action_test(node: ast.AST) -> bool:
    if not (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
    ):
        return False
    left = node.left
    right = node.comparators[0]
    return (
        _is_data_action_expression(left) and _is_generate_action_expression(right)
    ) or (
        _is_generate_action_expression(left) and _is_data_action_expression(right)
    )


def find_generate_action_branch(normalize: ast.AST) -> ast.If:
    matches = [
        node
        for node in ast.walk(normalize)
        if isinstance(node, ast.If) and is_generate_action_test(node.test)
    ]
    if len(matches) != 1:
        fail(
            "V431_ARBITER_GENERATE_CONTRACT_INVALID",
            f"generate_commands normalization branch count={len(matches)}",
        )
    return matches[0]


def assert_generate_action_normalization(normalize: ast.AST) -> None:
    branch = find_generate_action_branch(normalize)
    scope = ast.Module(body=list(branch.body), type_ignores=[])
    expected = {
        "commands": [],
        "commands_provided": False,
        "should_generate_commands": True,
        "should_execute_commands": False,
        "should_analyze_after_execution": False,
        "requires_confirmation": True,
    }
    for key, expected_value in expected.items():
        matches = data_key_assignments(scope, key)
        if len(matches) != 1:
            fail(
                "V431_ARBITER_GENERATE_CONTRACT_INVALID",
                f"generate_commands branch assignment count for {key}={len(matches)}",
            )
        _node, value = matches[0]
        if not _literal_matches(value, expected_value):
            fail(
                "V431_ARBITER_GENERATE_CONTRACT_INVALID",
                f"generate_commands branch sets {key} incorrectly",
            )


def assert_arbiter_normalizer_contract(normalize: ast.AST) -> None:
    assert_structured_mapping_normalization(
        normalize,
        key="cmdb_query",
        local_name="cmdb_query",
        allowed_aliases={"cmdb"},
    )
    assert_structured_mapping_normalization(
        normalize,
        key="command_generation",
        local_name="command_generation",
        allowed_aliases={"command_spec"},
    )
    assert_generate_action_normalization(normalize)


def assert_arbiter_prompt_contract(prompt: ast.AST) -> None:
    try:
        system_value = local_assignment_value(prompt, "system")
        examples_value = local_assignment_value(prompt, "examples")
    except ArchitectureError as exc:
        fail("V431_ARBITER_PROMPT_INVALID", exc.detail)
    system_text = static_text(system_value)
    examples_text = static_text(examples_value)
    compact_system = "".join(system_text.split())
    compact_examples = "".join(examples_text.split())

    required_system = {
        '"cmdb_query":{',
        '"command_generation":{',
        "command_source=system_generated",
        "requires_confirmation=true",
        "commands保持为空",
    }
    missing_system = sorted(item for item in required_system if item not in compact_system)
    if missing_system:
        fail(
            "V431_ARBITER_PROMPT_INVALID",
            f"system prompt missing scoped contracts: {missing_system}",
        )

    required_examples = {
        '"action":"cmdb_query"',
        '"cmdb_query":{"operation":"detail"',
        '"action":"generate_commands"',
        '"command_generation":{"category":"cpu"',
        '"requires_confirmation":true',
    }
    missing_examples = sorted(
        item for item in required_examples if item not in compact_examples
    )
    if missing_examples:
        fail(
            "V431_ARBITER_PROMPT_INVALID",
            f"examples missing scoped contracts: {missing_examples}",
        )


def _constant_value(node: ast.AST, expected: object) -> bool:
    return isinstance(node, ast.Constant) and node.value == expected


def _dotted_value(node: ast.AST, expected: str) -> bool:
    return dotted_name(node) == expected


def _require_keyword_constant(
    call: ast.Call,
    key: str,
    expected: object,
    code: str,
    scope: str,
) -> None:
    values = [item.value for item in call.keywords if item.arg == key]
    if len(values) != 1 or not _constant_value(values[0], expected):
        fail(code, f"{scope}.{key} must be {expected!r}")


def _require_keyword_dotted(
    call: ast.Call,
    key: str,
    expected: str,
    code: str,
    scope: str,
) -> None:
    values = [item.value for item in call.keywords if item.arg == key]
    if len(values) != 1 or not _dotted_value(values[0], expected):
        fail(code, f"{scope}.{key} must be {expected}")


def is_stage_fallback_test(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.NotIn)
        and len(node.comparators) == 1
        and dotted_name(node.left) == "decision.action"
        and dotted_name(node.comparators[0]) == "self.allowed_actions"
    )


def find_stage_fallback_branch(route: ast.AST) -> ast.If:
    matches = [
        node
        for node in ast.walk(route)
        if isinstance(node, ast.If) and is_stage_fallback_test(node.test)
    ]
    if len(matches) != 1:
        fail(
            "V431_ENTRY_FALLBACK_INVALID",
            f"stage fallback branch count={len(matches)}",
        )
    return matches[0]


def assert_entry_stage_fallback(entry_tree: ast.Module) -> None:
    entry_cls = class_node(entry_tree, "V4EntryRouter")
    route = method_node(entry_cls, "route") if entry_cls is not None else None
    if route is None:
        fail("V431_ENTRY_FALLBACK_INVALID", "V4EntryRouter.route is missing")
    branch = find_stage_fallback_branch(route)
    scope = ast.Module(body=list(branch.body), type_ignores=[])
    audit_calls = call_nodes(scope, "self._write_stage_audit")
    result_calls = call_nodes(scope, "EntryRouteResult")
    if len(audit_calls) != 1 or len(result_calls) != 1:
        fail(
            "V431_ENTRY_FALLBACK_INVALID",
            f"stage fallback delegates audit={len(audit_calls)} result={len(result_calls)}",
        )
    audit_call = audit_calls[0]
    result_call = result_calls[0]
    if getattr(audit_call, "lineno", 0) >= getattr(result_call, "lineno", 0):
        fail("V431_ENTRY_FALLBACK_INVALID", "stage audit must precede fallback return")

    _require_keyword_constant(
        audit_call,
        "status",
        "fallback",
        "V431_ENTRY_FALLBACK_INVALID",
        "stage_audit",
    )
    _require_keyword_constant(
        audit_call,
        "fallback_allowed",
        True,
        "V431_ENTRY_FALLBACK_INVALID",
        "stage_audit",
    )
    _require_keyword_constant(
        audit_call,
        "fallback_reason",
        "action_not_enabled_in_v4_3_1",
        "V431_ENTRY_FALLBACK_INVALID",
        "stage_audit",
    )
    for key, expected in {
        "enabled": True,
        "handled": False,
        "fallback": True,
        "reason": "action_not_enabled_in_v4_3_1",
    }.items():
        _require_keyword_constant(
            result_call,
            key,
            expected,
            "V431_ENTRY_FALLBACK_INVALID",
            "stage_result",
        )
    _require_keyword_dotted(
        result_call,
        "action",
        "decision.action.value",
        "V431_ENTRY_FALLBACK_INVALID",
        "stage_result",
    )
    _require_keyword_dotted(
        result_call,
        "decision",
        "decision",
        "V431_ENTRY_FALLBACK_INVALID",
        "stage_result",
    )


def strict_dict_items(node: ast.AST, code: str, scope: str) -> dict[str, ast.AST]:
    if not isinstance(node, ast.Dict):
        fail(code, f"{scope} must be a literal dict")
    result: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            fail(code, f"{scope} contains a non-string key")
        result[str(key.value)] = value
    return result


def _require_version_value(node: ast.AST, scope: str) -> None:
    if dotted_name(node) != "V4_ENTRY_ROUTER_VERSION":
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            f"{scope} must use delegated V4_ENTRY_ROUTER_VERSION",
        )


def assert_bridge_version_delegation(bridge_tree: ast.Module) -> None:
    bridge_names = imported_names(
        bridge_tree,
        "netaiops_asset.chat_v4.entry_router",
    )
    if "V4_ENTRY_ROUTER_VERSION" not in bridge_names:
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            "App Bridge must import V4_ENTRY_ROUTER_VERSION",
        )

    transport = function_node(bridge_tree, "_build_transport_payload")
    internal = function_node(bridge_tree, "build_v4_internal_error_transport")
    if transport is None or internal is None:
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            "App Bridge transport functions are missing",
        )

    transport_assignments: list[ast.AST] = []
    for node in ast.walk(transport):
        targets, value = assignment_parts(node)
        if value is None:
            continue
        if any(
            is_named_subscript(target, "payload", "v4_entry_router_version")
            for target in targets
        ):
            transport_assignments.append(value)
    if len(transport_assignments) != 1:
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            f"transport version assignment count={len(transport_assignments)}",
        )
    _require_version_value(
        transport_assignments[0],
        "_build_transport_payload.payload.v4_entry_router_version",
    )

    update_calls = call_nodes(transport, "v4_meta.update")
    if len(update_calls) != 1 or len(update_calls[0].args) != 1:
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            "transport v4_meta.update contract is missing",
        )
    update_items = strict_dict_items(
        update_calls[0].args[0],
        "V431_BRIDGE_VERSION_DELEGATION_INVALID",
        "transport v4_meta.update",
    )
    if "entry_router_version" not in update_items:
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            "transport v4 metadata lacks entry_router_version",
        )
    _require_version_value(
        update_items["entry_router_version"],
        "transport v4.entry_router_version",
    )

    try:
        payload_value = local_assignment_value(internal, "payload")
    except ArchitectureError as exc:
        fail("V431_BRIDGE_VERSION_DELEGATION_INVALID", exc.detail)
    payload_items = strict_dict_items(
        payload_value,
        "V431_BRIDGE_VERSION_DELEGATION_INVALID",
        "internal payload",
    )
    if "v4_entry_router_version" not in payload_items or "v4" not in payload_items:
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            "internal payload lacks version fields",
        )
    _require_version_value(
        payload_items["v4_entry_router_version"],
        "internal payload.v4_entry_router_version",
    )
    nested_v4 = strict_dict_items(
        payload_items["v4"],
        "V431_BRIDGE_VERSION_DELEGATION_INVALID",
        "internal payload.v4",
    )
    if "entry_router_version" not in nested_v4:
        fail(
            "V431_BRIDGE_VERSION_DELEGATION_INVALID",
            "internal payload.v4 lacks entry_router_version",
        )
    _require_version_value(
        nested_v4["entry_router_version"],
        "internal payload.v4.entry_router_version",
    )


def check_project(project: Path) -> None:
    project = project.resolve()
    paths = {
        "dispatcher": project / "netaiops_asset/chat_v4/action_dispatcher.py",
        "schema": project / "netaiops_asset/chat_v3/intent_schema.py",
        "arbiter": project / "netaiops_asset/chat_v3/intent_arbiter.py",
        "entry": project / "netaiops_asset/chat_v4/entry_router.py",
        "bridge": project / "netaiops_asset/chat_v4/app_bridge.py",
        "cmdb": project / "netaiops_asset/chat_v4/handlers/cmdb_query.py",
        "generate": project / "netaiops_asset/chat_v4/handlers/generate_commands.py",
    }
    trees = {name: parse(path) for name, path in paths.items()}

    dispatcher_tree = trees["dispatcher"]
    dispatcher_cls = class_node(dispatcher_tree, "LowRiskActionDispatcher")
    if dispatcher_cls is None:
        fail("V431_DISPATCHER_MISSING", "LowRiskActionDispatcher")
    actual_actions = literal_action_set(dispatcher_tree, "LOW_RISK_ACTIONS")
    if actual_actions != set(EXPECTED_ACTIONS):
        fail(
            "V431_ACTION_SET_INVALID",
            f"expected={sorted(EXPECTED_ACTIONS)} actual={sorted(actual_actions)}",
        )
    if actual_actions.intersection(EXECUTION_ACTIONS):
        fail(
            "V431_EXECUTION_ACTION_IN_SCOPE",
            str(sorted(actual_actions.intersection(EXECUTION_ACTIONS))),
        )
    actual_mapping = handler_mapping(dispatcher_cls)
    if actual_mapping != EXPECTED_ACTIONS:
        fail(
            "V431_HANDLER_MAP_INVALID",
            f"expected={EXPECTED_ACTIONS} actual={actual_mapping}",
        )
    assert_dispatcher_context_dataflow(dispatcher_cls)

    cmdb_tree = trees["cmdb"]
    cmdb_cls, cmdb_handle = assert_handler_action(
        cmdb_tree,
        "CmdbQueryHandler",
        "cmdb_query",
    )
    assert_no_execution_symbols(paths["cmdb"], cmdb_tree)
    assert_cmdb_call_chain(cmdb_cls, cmdb_handle)

    generate_tree = trees["generate"]
    generate_cls, generate_handle = assert_handler_action(
        generate_tree,
        "GenerateCommandsHandler",
        "generate_commands",
    )
    assert_no_execution_symbols(paths["generate"], generate_tree)
    assert_command_call_chain(generate_cls, generate_handle)

    schema_tree = trees["schema"]
    for class_name in ("CmdbQuerySpec", "CommandGenerationSpec"):
        if class_node(schema_tree, class_name) is None:
            fail("V431_INTENT_SPEC_MISSING", class_name)
    decision_cls = class_node(schema_tree, "IntentDecision")
    if decision_cls is None:
        fail("V431_INTENT_FIELDS_INVALID", "IntentDecision is missing")
    decision_fields = {
        node.target.id
        for node in decision_cls.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    if not {"cmdb_query", "command_generation"}.issubset(decision_fields):
        fail(
            "V431_INTENT_FIELDS_INVALID",
            f"actual={sorted(decision_fields)}",
        )

    arbiter_tree = trees["arbiter"]
    prompt = function_node(arbiter_tree, "build_intent_messages")
    normalize = function_node(arbiter_tree, "normalize_payload")
    if prompt is None or normalize is None:
        fail("V431_ARBITER_CONTRACT_INVALID", "prompt or normalizer function missing")
    assert_arbiter_prompt_contract(prompt)
    assert_arbiter_normalizer_contract(normalize)

    entry_tree = trees["entry"]
    if assigned_literal(entry_tree, "V4_ENTRY_ROUTER_VERSION") != "v4.entry_router.3_1":
        fail("V431_ENTRY_VERSION_INVALID", "expected v4.entry_router.3_1")
    assert_entry_stage_fallback(entry_tree)

    assert_bridge_version_delegation(trees["bridge"])

def main() -> int:
    try:
        check_project(Path(__file__).resolve().parents[1])
    except ArchitectureError as exc:
        raise SystemExit(f"V4_3_1_ARCHITECTURE_ERROR[{exc.code}]: {exc.detail}")
    print("v4_3_1_exact_action_set=OK")
    print("v4_3_1_annotated_handler_mapping=OK")
    print("structured_cmdb_contract=OK")
    print("structured_command_generation_contract=OK")
    print("dispatcher_context_real_scope=OK")
    print("command_splitter_and_two_safety_layers=OK")
    print("no_execution_or_pending_path=OK")
    print("no_question_driven_handler_spec=OK")
    print("entry_router_and_bridge_contract=OK")
    print("architecture_check_uses_real_ast_scope=OK")
    print("result=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
