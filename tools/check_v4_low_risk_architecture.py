#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, Optional

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "netaiops_asset" / "chat_v4"
HANDLERS = PACKAGE / "handlers"

REQUIRED_FILES = {
    PACKAGE / "response_builder.py",
    PACKAGE / "audit_writer.py",
    PACKAGE / "action_dispatcher.py",
    HANDLERS / "__init__.py",
    HANDLERS / "base.py",
    HANDLERS / "general_chat.py",
    HANDLERS / "advice_analysis.py",
    HANDLERS / "clarification.py",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "app",
    "netaiops_asset.chat_v2.router",
    "netaiops_asset.chat_v2.semantic_router",
    "netaiops_asset.cmdb",
    "netaiops_asset.mcp",
    "netaiops_asset.netmiko",
    "netaiops_asset.observability",
    "netaiops_asset.troubleshoot",
    "requests",
    "urllib",
    "subprocess",
)
FORBIDDEN_SYMBOLS = {
    "CATEGORY_TOKENS",
    "ROUTE_KEYWORDS",
    "FOLLOWUP_KEYWORDS",
    "ADVICE_KEYWORDS",
    "try_handle_v2_chat",
    "build_v2_semantic_route",
    "parse_question",
}
FORBIDDEN_FUNCTION_PREFIXES = (
    "classify_",
    "detect_intent",
    "infer_intent",
    "route_by_keyword",
)
EXPECTED_HANDLER_CLASSES = {
    "general_chat": "GeneralChatHandler",
    "advice_analysis": "AdviceAnalysisHandler",
    "need_clarification": "ClarificationHandler",
}
OUT_OF_SCOPE_ACTIONS = {
    "cmdb_query",
    "generate_commands",
    "execute_provided_commands",
    "execute_provided_commands_and_analyze",
    "confirm_execute_pending",
    "analyze_existing_evidence",
}


def fail(message: str) -> None:
    raise SystemExit(f"V4_LOW_RISK_ARCHITECTURE_ERROR: {message}")


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def imported_modules(tree: ast.AST) -> list[str]:
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
    return result


def class_node(tree: ast.Module, name: str) -> Optional[ast.ClassDef]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def method_node(
    cls: ast.ClassDef,
    name: str,
) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


def intent_action_name(node: ast.AST) -> str:
    name = dotted_name(node)
    prefix = "IntentAction."
    if name.startswith(prefix):
        return name[len(prefix):]
    return ""


def assigned_call_collection(
    tree: ast.Module,
    variable_name: str,
) -> set[str]:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            list(node.targets)
            if isinstance(node, ast.Assign)
            else [node.target]
        )
        if not any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in targets
        ):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            fail(f"{variable_name} must be built by a deterministic collection call")
        if dotted_name(value.func) not in {"frozenset", "set"}:
            fail(f"{variable_name} must use frozenset/set")
        if len(value.args) != 1 or not isinstance(
            value.args[0],
            (ast.Set, ast.List, ast.Tuple),
        ):
            fail(f"{variable_name} must contain a literal collection")
        return {
            action
            for action in (
                intent_action_name(item)
                for item in value.args[0].elts
            )
            if action
        }
    fail(f"missing assignment: {variable_name}")
    return set()


def self_handlers_mapping(cls: ast.ClassDef) -> dict[str, str]:
    init = method_node(cls, "__init__")
    if init is None:
        fail("LowRiskActionDispatcher.__init__ is missing")
    for node in ast.walk(init):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            list(node.targets)
            if isinstance(node, ast.Assign)
            else [node.target]
        )
        if not any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "handlers"
            for target in targets
        ):
            continue
        if not isinstance(node.value, ast.Dict):
            fail("self.handlers must be a literal dict")
        result: dict[str, str] = {}
        for key, value in zip(node.value.keys, node.value.values):
            if key is None:
                fail("self.handlers cannot use dict unpacking")
            action = intent_action_name(key)
            if not action:
                fail("self.handlers key must be IntentAction.<action>")
            if not isinstance(value, ast.Call):
                fail("self.handlers value must instantiate a handler")
            result[action] = dotted_name(value.func)
        return result
    fail("missing self.handlers mapping")
    return {}


def names_in(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            result.add(child.id)
        elif isinstance(child, ast.Attribute):
            result.add(child.attr)
    return result


def action_selection_uses_question(dispatch: ast.AST) -> bool:
    question_names = {"question", "normalized_question"}
    action_names = {
        "action",
        "intent",
        "handlers",
        "LOW_RISK_ACTIONS",
        *EXPECTED_HANDLER_CLASSES,
        *OUT_OF_SCOPE_ACTIONS,
    }
    for node in ast.walk(dispatch):
        if not isinstance(node, (ast.If, ast.Match, ast.IfExp)):
            continue
        condition: ast.AST
        if isinstance(node, ast.If):
            condition = node.test
        elif isinstance(node, ast.IfExp):
            condition = node.test
        else:
            condition = node.subject
        used = names_in(condition)
        if used.intersection(question_names) and used.intersection(action_names):
            return True
    return False


def class_action_assignment(
    cls: ast.ClassDef,
) -> str:
    for node in cls.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            list(node.targets)
            if isinstance(node, ast.Assign)
            else [node.target]
        )
        if not any(
            isinstance(target, ast.Name) and target.id == "action"
            for target in targets
        ):
            continue
        return intent_action_name(node.value)
    return ""


def call_names(node: ast.AST) -> set[str]:
    return {
        dotted_name(child.func)
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    }


def v4_response_planner_sources(tree: ast.Module) -> list[str]:
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if dotted_name(node.func) != "V4Response":
            continue
        for keyword in node.keywords:
            if keyword.arg != "planner_source":
                continue
            if isinstance(keyword.value, ast.Constant):
                values.append(str(keyword.value.value))
            else:
                values.append("<non-literal>")
    return values


def main() -> int:
    missing = sorted(
        str(path.relative_to(PROJECT))
        for path in REQUIRED_FILES
        if not path.is_file()
    )
    if missing:
        fail(f"missing files: {missing}")

    trees: dict[Path, ast.Module] = {}
    for path in sorted(REQUIRED_FILES):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            fail(f"syntax error in {path.relative_to(PROJECT)}: {exc}")
        trees[path] = tree

        for module in imported_modules(tree):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            ):
                fail(
                    f"forbidden import in {path.relative_to(PROJECT)}: "
                    f"{module}"
                )

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.lower()
                if any(
                    lowered.startswith(prefix)
                    for prefix in FORBIDDEN_FUNCTION_PREFIXES
                ):
                    fail(
                        f"forbidden classifier function in "
                        f"{path.relative_to(PROJECT)}: {node.name}"
                    )
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_SYMBOLS:
                fail(
                    f"forbidden symbol in "
                    f"{path.relative_to(PROJECT)}: {node.id}"
                )
            if (
                isinstance(node, ast.Attribute)
                and node.attr in FORBIDDEN_SYMBOLS
            ):
                fail(
                    f"forbidden attribute in "
                    f"{path.relative_to(PROJECT)}: {node.attr}"
                )

    dispatcher_tree = trees[PACKAGE / "action_dispatcher.py"]
    dispatcher_class = class_node(
        dispatcher_tree,
        "LowRiskActionDispatcher",
    )
    if dispatcher_class is None:
        fail("LowRiskActionDispatcher class is missing")

    expected_actions = set(EXPECTED_HANDLER_CLASSES)
    actual_actions = assigned_call_collection(
        dispatcher_tree,
        "LOW_RISK_ACTIONS",
    )
    if actual_actions != expected_actions:
        fail(
            "LOW_RISK_ACTIONS mismatch: "
            f"expected={sorted(expected_actions)} "
            f"actual={sorted(actual_actions)}"
        )

    handlers = self_handlers_mapping(dispatcher_class)
    if handlers != EXPECTED_HANDLER_CLASSES:
        fail(
            "handler mapping mismatch: "
            f"expected={EXPECTED_HANDLER_CLASSES} actual={handlers}"
        )

    dispatch = method_node(dispatcher_class, "dispatch")
    if dispatch is None:
        fail("LowRiskActionDispatcher.dispatch is missing")
    if action_selection_uses_question(dispatch):
        fail("question text participates in action selection")
    dispatch_calls = call_names(dispatch)
    for required in {
        "self._load_context",
        "handler.handle",
        "self.store.append_turn",
        "self._write_audit",
        "attach_audit_reference",
        "build_v4_response",
        "build_handled_entry",
        "build_stage_fallback_entry",
    }:
        if required not in dispatch_calls:
            fail(f"dispatcher missing deterministic stage: {required}")

    for action, class_name in EXPECTED_HANDLER_CLASSES.items():
        handler_path = (
            HANDLERS / f"{action}.py"
            if action != "need_clarification"
            else HANDLERS / "clarification.py"
        )
        cls = class_node(trees[handler_path], class_name)
        if cls is None:
            fail(f"missing handler class: {class_name}")
        if class_action_assignment(cls) != action:
            fail(
                f"{class_name}.action mismatch: "
                f"{class_action_assignment(cls)!r}"
            )
        handle = method_node(cls, "handle")
        if handle is None:
            fail(f"{class_name}.handle is missing")

    response_tree = trees[PACKAGE / "response_builder.py"]
    response_calls = call_names(response_tree)
    for required in {
        "V4Response",
        "V4ResponseMeta",
        "EntryResult",
    }:
        if required not in response_calls:
            fail(f"response builder missing contract call: {required}")
    planner_sources = v4_response_planner_sources(response_tree)
    if not planner_sources or set(planner_sources) != {"v4_intent_arbiter"}:
        fail(
            "V4Response planner_source must be the literal "
            "'v4_intent_arbiter'"
        )

    base_tree = trees[HANDLERS / "base.py"]
    base_calls = call_names(base_tree)
    if "generate_v3_response" not in base_calls:
        fail("low-risk adapter does not reuse generate_v3_response")
    if any(action in call_names(base_tree) for action in OUT_OF_SCOPE_ACTIONS):
        fail("base handler contains an out-of-scope action call")

    audit_tree = trees[PACKAGE / "audit_writer.py"]
    audit_calls = call_names(audit_tree)
    for required_call in {
        "tempfile.mkstemp",
        "os.fsync",
        "os.replace",
        "ContextStore.sanitize_value",
    }:
        if required_call not in audit_calls:
            fail(f"audit writer missing capability: {required_call}")

    print("low_risk_action_set=OK")
    print("handler_action_contract=OK")
    print("question_not_used_for_action_selection=OK")
    print("no_cmdb_mcp_device_execution_import=OK")
    print("v3_response_generator_adapter_reuse=OK")
    print("unified_v4_response_contract=OK")
    print("audit_atomic_writer=OK")
    print("architecture_check_uses_ast_structure=OK")
    print("result=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
