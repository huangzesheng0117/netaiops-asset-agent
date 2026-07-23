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


QUESTION_NAMES = {"question", "normalized_question"}
ACTION_SELECTION_TARGET_NAMES = {
    "action",
    "selected_action",
    "intent",
    "decision",
    "handler",
    "selected_handler",
    "handler_key",
    "route",
    "route_key",
}
ACTION_MAPPING_NAMES = {
    "handlers",
    "ROUTE_MAP",
    "ACTION_MAP",
    "HANDLER_MAP",
    "route_map",
    "action_map",
    "handler_map",
}
ACTION_LITERAL_NAMES = {
    *EXPECTED_HANDLER_CLASSES,
    *OUT_OF_SCOPE_ACTIONS,
}


def target_names(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            result.add(child.id)
        elif isinstance(child, ast.Attribute):
            result.add(child.attr)
    return result


def uses_question(node: ast.AST) -> bool:
    return bool(names_in(node).intersection(QUESTION_NAMES))


def mapping_lookup_uses_question(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Subscript):
            if (
                target_names(child.value).intersection(ACTION_MAPPING_NAMES)
                and uses_question(child.slice)
            ):
                return True
        elif isinstance(child, ast.Call):
            function_name = dotted_name(child.func)
            if function_name == "IntentAction" and any(
                uses_question(argument) for argument in child.args
            ):
                return True
            if isinstance(child.func, ast.Attribute):
                if (
                    child.func.attr in {"get", "__getitem__"}
                    and target_names(child.func.value).intersection(
                        ACTION_MAPPING_NAMES
                    )
                    and any(uses_question(argument) for argument in child.args)
                ):
                    return True
    return False


def expression_is_action_selector(node: ast.AST) -> bool:
    if intent_action_name(node):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in ACTION_LITERAL_NAMES
    if isinstance(node, ast.Subscript):
        return bool(target_names(node.value).intersection(ACTION_MAPPING_NAMES))
    if isinstance(node, ast.Name):
        return node.id in {
            "action",
            "selected_action",
            "handler",
            "selected_handler",
            "route",
            "route_key",
        }
    if isinstance(node, ast.Attribute):
        return node.attr in {
            "action",
            "handler",
            "selected_handler",
            "route",
            "route_key",
        }
    if isinstance(node, ast.Call):
        function_name = dotted_name(node.func)
        if function_name == "IntentAction":
            return True
        if function_name in EXPECTED_HANDLER_CLASSES.values():
            return True
        if any(
            isinstance(child, ast.Subscript)
            and target_names(child.value).intersection(ACTION_MAPPING_NAMES)
            for child in ast.walk(node)
        ):
            return True
        if any(
            isinstance(child, (ast.Name, ast.Attribute))
            and (
                getattr(child, "id", "") in {"handler", "selected_handler"}
                or getattr(child, "attr", "")
                in {"handler", "selected_handler"}
            )
            for child in ast.walk(node.func)
        ):
            return True
    return False


def assignment_selects_action_from_question(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
        value = node.value
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
        value = node.value
    elif isinstance(node, ast.NamedExpr):
        targets = [node.target]
        value = node.value
    else:
        return False
    if value is None:
        return False
    selected_targets: set[str] = set()
    for target in targets:
        selected_targets.update(target_names(target))
    return bool(
        selected_targets.intersection(ACTION_SELECTION_TARGET_NAMES)
        and uses_question(value)
    )


def branch_selects_action(statements: Iterable[ast.stmt]) -> bool:
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                    value = node.value
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                    value = node.value
                else:
                    targets = [node.target]
                    value = node.value
                selected_targets: set[str] = set()
                for target in targets:
                    selected_targets.update(target_names(target))
                if selected_targets.intersection(ACTION_SELECTION_TARGET_NAMES):
                    return True
                if value is not None and expression_is_action_selector(value):
                    return True
            elif isinstance(node, ast.Return):
                if (
                    node.value is not None
                    and expression_is_action_selector(node.value)
                ):
                    return True
            elif isinstance(node, ast.Expr):
                if expression_is_action_selector(node.value):
                    return True
    return False


def action_selection_uses_question(dispatch: ast.AST) -> bool:
    for node in ast.walk(dispatch):
        if mapping_lookup_uses_question(node):
            return True
        if assignment_selects_action_from_question(node):
            return True
        if isinstance(node, ast.If):
            if uses_question(node.test) and (
                branch_selects_action(node.body)
                or branch_selects_action(node.orelse)
            ):
                return True
        elif isinstance(node, ast.IfExp):
            if uses_question(node.test) and (
                expression_is_action_selector(node.body)
                or expression_is_action_selector(node.orelse)
            ):
                return True
        elif isinstance(node, ast.Match):
            if uses_question(node.subject) and any(
                branch_selects_action(case.body)
                for case in node.cases
            ):
                return True
        elif isinstance(node, ast.Return):
            if (
                node.value is not None
                and uses_question(node.value)
                and expression_is_action_selector(node.value)
            ):
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
    if not expected_actions.issubset(actual_actions):
        fail(
            "legacy low-risk actions are missing: "
            f"required={sorted(expected_actions)} "
            f"actual={sorted(actual_actions)}"
        )

    handlers = self_handlers_mapping(dispatcher_class)
    for action, expected_class in EXPECTED_HANDLER_CLASSES.items():
        if handlers.get(action) != expected_class:
            fail(
                "legacy low-risk handler mapping mismatch: "
                f"action={action} expected={expected_class} "
                f"actual={handlers.get(action)}"
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
    print("legacy_low_risk_scope_has_no_cmdb_mcp_execution_import=OK")
    print("v3_response_generator_adapter_reuse=OK")
    print("unified_v4_response_contract=OK")
    print("audit_atomic_writer=OK")
    print("architecture_check_uses_ast_structure=OK")
    print("result=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
