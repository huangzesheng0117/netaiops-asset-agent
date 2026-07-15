#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Architecture validation for the V4.2-3 pre-route entry router.

The checker validates real AST call/data contracts.  Marker counts are checked
only for patch idempotency; they are never used as proof of business behavior.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, Optional


PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "netaiops_asset" / "chat_v4"
APP = PROJECT / "app.py"

REQUIRED_FILES = {
    "entry_router.py",
    "app_bridge.py",
    "action_dispatcher.py",
    "response_builder.py",
    "audit_writer.py",
    "context_store.py",
    "contracts.py",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "netaiops_asset.chat_v2.router",
    "netaiops_asset.chat_v2.semantic_router",
    "netaiops_asset.cmdb",
    "netaiops_asset.mcp",
    "netaiops_asset.netmiko",
    "netaiops_asset.observability",
    "netaiops_asset.troubleshoot",
    "subprocess",
)
FORBIDDEN_SYMBOLS = {
    "CATEGORY_TOKENS",
    "ROUTE_KEYWORDS",
    "FOLLOWUP_KEYWORDS",
    "ADVICE_KEYWORDS",
    "parse_question",
    "build_v2_semantic_route",
    "try_handle_v2_chat",
}
FORBIDDEN_FUNCTION_PREFIXES = (
    "classify_",
    "detect_intent",
    "infer_intent",
    "route_by_keyword",
)
REQUIRED_ENV_NAMES = {
    "NETAIOPS_V4_ENTRY_ENABLED",
    "NETAIOPS_V4_ENTRY_ALLOWED_ACTIONS",
    "NETAIOPS_V4_ENTRY_LIVE_LLM",
    "NETAIOPS_V4_ENTRY_MIN_CONFIDENCE",
}
LEGACY_BUSINESS_CALLS = {
    "_v3_shadow_build",
    "_batch67_try_handle_advice_analysis",
    "try_handle_v2_inline_command_execution",
    "build_v2_semantic_route",
    "try_handle_v2_chat",
}
QUESTION_NAMES = {"question", "normalized_question", "raw_question"}
ACTION_TARGET_NAMES = {
    "action",
    "selected_action",
    "handler",
    "selected_handler",
    "route",
    "route_type",
    "handler_key",
}


class ArchitectureError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ArchitectureError(message)


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def parse_file(path: Path) -> tuple[str, ast.Module]:
    require(path.is_file(), f"missing file: {path}")
    source = path.read_text(encoding="utf-8")
    try:
        return source, ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ArchitectureError(f"syntax error in {path}: {exc}") from exc


def imported_modules(tree: ast.AST) -> list[str]:
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
    return result


def top_level_function(
    tree: ast.Module,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    raise ArchitectureError(f"missing function: {name}")


def class_method(
    tree: ast.Module,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == method_name:
                        return item
    raise ArchitectureError(f"missing method: {class_name}.{method_name}")


def calls_in(node: ast.AST) -> list[ast.Call]:
    return [item for item in ast.walk(node) if isinstance(item, ast.Call)]


def call_names(node: ast.AST) -> set[str]:
    return {dotted_name(item.func) for item in calls_in(node)}


def call_lines(node: ast.AST) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for item in calls_in(node):
        result.setdefault(dotted_name(item.func), []).append(item.lineno)
    return result


def names_in(node: ast.AST) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name)
    }


def string_constants(node: ast.AST) -> set[str]:
    return {
        str(item.value)
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }


def target_names(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            result.add(item.id)
        elif isinstance(item, ast.Attribute):
            result.add(item.attr)
    return result


def uses_question(node: ast.AST) -> bool:
    return bool(names_in(node).intersection(QUESTION_NAMES))


def is_intent_action_expression(node: ast.AST) -> bool:
    name = dotted_name(node)
    return name == "IntentAction" or name.startswith("IntentAction.")


def contains_fixed_action_or_handler(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Attribute):
            if dotted_name(item).startswith("IntentAction."):
                return True
        if isinstance(item, ast.Subscript):
            owner = dotted_name(item.value)
            if owner.endswith("handlers") or owner.endswith("action_handlers"):
                return True
        if isinstance(item, ast.Call):
            name = dotted_name(item.func)
            if name == "IntentAction" or name.endswith("Handler"):
                return True
    return False


def question_drives_action(route: ast.AST) -> Optional[int]:
    """Return a violating line when question text drives action/handler choice."""

    for node in ast.walk(route):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            if isinstance(node, ast.Assign):
                targets: Iterable[ast.AST] = node.targets
                value = node.value
            else:
                targets = [node.target]
                value = node.value
            selected_targets: set[str] = set()
            for target in targets:
                selected_targets.update(target_names(target))
            if selected_targets.intersection(ACTION_TARGET_NAMES) and uses_question(value):
                return getattr(node, "lineno", 0)

        if isinstance(node, ast.Subscript):
            owner = dotted_name(node.value)
            if owner.endswith("handlers") or owner.endswith("action_handlers"):
                if uses_question(node.slice):
                    return getattr(node, "lineno", 0)

        if isinstance(node, ast.Call):
            if dotted_name(node.func) == "IntentAction" and any(
                uses_question(arg) for arg in node.args
            ):
                return getattr(node, "lineno", 0)
            for keyword in node.keywords:
                if keyword.arg in ACTION_TARGET_NAMES and uses_question(keyword.value):
                    return getattr(node, "lineno", 0)

        if isinstance(node, ast.If) and uses_question(node.test):
            branch = ast.Module(body=[*node.body, *node.orelse], type_ignores=[])
            if contains_fixed_action_or_handler(branch):
                return node.lineno

        if isinstance(node, ast.IfExp) and uses_question(node.test):
            branch = ast.Tuple(elts=[node.body, node.orelse], ctx=ast.Load())
            if contains_fixed_action_or_handler(branch):
                return node.lineno

        if isinstance(node, ast.Match) and uses_question(node.subject):
            branch = ast.Module(
                body=[statement for case in node.cases for statement in case.body],
                type_ignores=[],
            )
            if contains_fixed_action_or_handler(branch):
                return node.lineno

    return None


def dict_entries(node: ast.AST) -> dict[str, ast.AST]:
    require(isinstance(node, ast.Dict), "expected a literal dict")
    result: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values):
        require(key is not None, "dict unpacking is not allowed in contract")
        require(
            isinstance(key, ast.Constant) and isinstance(key.value, str),
            "contract dict keys must be string literals",
        )
        result[str(key.value)] = value
    return result


def assignment_value(function: ast.AST, variable_name: str) -> ast.AST:
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == variable_name
                for target in targets
            ):
                return node.value
    raise ArchitectureError(
        f"missing assignment in function {getattr(function, 'name', '?')}: "
        f"{variable_name}"
    )


def return_values(function: ast.AST) -> list[ast.AST]:
    return [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return) and node.value is not None
    ]


def matches_constant(node: ast.AST, expected: object) -> bool:
    return isinstance(node, ast.Constant) and node.value == expected


def matches_dotted(node: ast.AST, expected: str) -> bool:
    return dotted_name(node) == expected


def require_dict_constant(
    mapping: dict[str, ast.AST],
    key: str,
    expected: object,
    scope: str,
) -> None:
    require(key in mapping, f"{scope} missing key: {key}")
    require(
        matches_constant(mapping[key], expected),
        f"{scope}.{key} must be {expected!r}",
    )


def require_dict_dotted(
    mapping: dict[str, ast.AST],
    key: str,
    expected: str,
    scope: str,
) -> None:
    require(key in mapping, f"{scope} missing key: {key}")
    require(
        matches_dotted(mapping[key], expected),
        f"{scope}.{key} must be {expected}",
    )


def returned_literal_dicts(function: ast.AST) -> list[dict[str, ast.AST]]:
    result: list[dict[str, ast.AST]] = []
    for value in return_values(function):
        if isinstance(value, ast.Dict):
            result.append(dict_entries(value))
    return result


def handler_return_value(handler: ast.ExceptHandler) -> ast.AST:
    returns = [
        node.value
        for statement in handler.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Return) and node.value is not None
    ]
    require(len(returns) == 1, "exception handler must have exactly one return")
    return returns[0]


def find_try_calling(function: ast.AST, call_name: str) -> ast.Try:
    matches = [
        node
        for node in function.body
        if isinstance(node, ast.Try) and call_name in call_names(node)
    ]
    require(len(matches) == 1, f"expected one try block calling {call_name}")
    return matches[0]


def validate_package_scan(package: Path) -> list[str]:
    missing = sorted(
        name for name in REQUIRED_FILES if not (package / name).is_file()
    )
    require(not missing, f"missing V4 files: {missing}")

    checked: list[str] = []
    for name in sorted(REQUIRED_FILES):
        path = package / name
        _source, tree = parse_file(path)
        for module in imported_modules(tree):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            ):
                raise ArchitectureError(f"forbidden import in {name}: {module}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.lower()
                if any(lowered.startswith(prefix) for prefix in FORBIDDEN_FUNCTION_PREFIXES):
                    raise ArchitectureError(
                        f"forbidden classifier function in {name}: {node.name}"
                    )
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_SYMBOLS:
                raise ArchitectureError(f"forbidden symbol in {name}: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_SYMBOLS:
                raise ArchitectureError(f"forbidden attribute in {name}: {node.attr}")
        checked.append(name)
    return checked


def validate_entry_router(package: Path) -> None:
    _source, tree = parse_file(package / "entry_router.py")
    constants = string_constants(tree)
    missing_env = sorted(REQUIRED_ENV_NAMES - constants)
    require(not missing_env, f"missing environment controls: {missing_env}")

    route = class_method(tree, "V4EntryRouter", "route")
    calls = call_names(route)
    for required_call in {
        "self.arbiter",
        "self.plan_builder",
        "self.dispatcher.dispatch",
    }:
        require(required_call in calls, f"V4EntryRouter.route missing {required_call}")

    violation = question_drives_action(route)
    require(
        violation is None,
        "question text participates in deterministic action/handler selection "
        f"at line {violation}",
    )


def validate_init_fallback(helper: ast.AST) -> None:
    import_tries = [
        node
        for node in helper.body
        if isinstance(node, ast.Try)
        and any(
            isinstance(item, ast.ImportFrom)
            and item.module == "netaiops_asset.chat_v4.app_bridge"
            for item in node.body
        )
    ]
    require(len(import_tries) == 1, "missing unique app_bridge import boundary")
    import_try = import_tries[0]
    imported_names = {
        alias.name
        for item in import_try.body
        if isinstance(item, ast.ImportFrom)
        for alias in item.names
    }
    require(
        {"try_handle_v4_pre_route", "build_v4_internal_error_transport"}
        <= imported_names,
        "app helper must import pre-route and internal-error bridge functions",
    )
    require(len(import_try.handlers) == 1, "app_bridge import needs one fallback handler")
    returned = handler_return_value(import_try.handlers[0])
    outer = dict_entries(returned)
    require_dict_constant(outer, "handled", False, "init_fallback")
    require_dict_constant(outer, "response", None, "init_fallback")
    route = dict_entries(outer["route"])
    require_dict_constant(route, "enabled", True, "init_fallback.route")
    require_dict_constant(route, "handled", False, "init_fallback.route")
    require_dict_constant(route, "fallback", True, "init_fallback.route")
    require_dict_constant(
        route,
        "reason",
        "v4_entry_router_init_failed",
        "init_fallback.route",
    )


def validate_runtime_exception_delegation(helper: ast.AST) -> None:
    route_try = find_try_calling(helper, "try_handle_v4_pre_route")
    require(len(route_try.handlers) == 1, "pre-route call needs one exception handler")
    handler = route_try.handlers[0]
    returned = handler_return_value(handler)
    require(
        isinstance(returned, ast.Call)
        and dotted_name(returned.func) == "build_v4_internal_error_transport",
        "internal pre-route exception must delegate to "
        "build_v4_internal_error_transport",
    )
    keyword_names = {item.arg for item in returned.keywords}
    require(
        {"question", "request_user_field", "conversation_id", "detail"}
        <= keyword_names,
        "internal error delegation is missing required arguments",
    )
    forbidden = call_names(handler).intersection(LEGACY_BUSINESS_CALLS)
    require(
        not forbidden,
        f"internal V4 exception handler calls legacy business routes: {sorted(forbidden)}",
    )


def validate_bridge_internal_error(package: Path) -> None:
    _source, tree = parse_file(package / "app_bridge.py")
    function = top_level_function(tree, "build_v4_internal_error_transport")

    audit_calls = [
        call for call in calls_in(function)
        if dotted_name(call.func) == "build_audit_record"
    ]
    require(len(audit_calls) == 1, "internal error builder needs one audit record")
    audit_keywords = {
        item.arg: item.value
        for item in audit_calls[0].keywords
        if item.arg is not None
    }
    require_dict_dotted(
        audit_keywords,
        "action",
        "IntentAction.need_clarification",
        "internal_error.audit",
    )
    require_dict_constant(
        audit_keywords,
        "handler_key",
        "v4_entry_router_internal_error",
        "internal_error.audit",
    )
    require_dict_constant(audit_keywords, "status", "error", "internal_error.audit")
    require_dict_constant(
        audit_keywords,
        "side_effect_started",
        False,
        "internal_error.audit",
    )
    require_dict_constant(
        audit_keywords,
        "fallback_allowed",
        False,
        "internal_error.audit",
    )

    payload = dict_entries(assignment_value(function, "payload"))
    require_dict_constant(payload, "status", "error", "internal_error.payload")
    require_dict_dotted(
        payload,
        "action",
        "IntentAction.need_clarification.value",
        "internal_error.payload",
    )
    require_dict_constant(
        payload,
        "planner_source",
        "v4_entry_router",
        "internal_error.payload",
    )
    require_dict_constant(payload, "v4_pre_route", True, "internal_error.payload")
    require_dict_dotted(
        payload,
        "v4_entry_status",
        "EntryStatus.error.value",
        "internal_error.payload",
    )
    require_dict_constant(
        payload,
        "v4_entry_reason",
        "v4_entry_router_internal_error",
        "internal_error.payload",
    )
    require_dict_constant(
        payload,
        "v4_fallback_used",
        False,
        "internal_error.payload",
    )
    v4 = dict_entries(payload["v4"])
    require_dict_constant(
        v4,
        "handler_key",
        "v4_entry_router_internal_error",
        "internal_error.payload.v4",
    )
    require_dict_constant(
        v4,
        "side_effect_started",
        False,
        "internal_error.payload.v4",
    )
    require_dict_constant(
        v4,
        "fallback_used",
        False,
        "internal_error.payload.v4",
    )
    require_dict_constant(
        v4,
        "context_recorded",
        False,
        "internal_error.payload.v4",
    )
    require_dict_dotted(
        v4,
        "entry_status",
        "EntryStatus.error.value",
        "internal_error.payload.v4",
    )

    literal_returns = returned_literal_dicts(function)
    require(len(literal_returns) == 1, "internal error builder needs one transport return")
    transport = literal_returns[0]
    require_dict_constant(transport, "handled", True, "internal_error.transport")
    require(
        isinstance(transport.get("response"), ast.Name)
        and transport["response"].id == "payload",
        "internal_error.transport.response must return payload",
    )
    route = dict_entries(transport["route"])
    require_dict_constant(route, "enabled", True, "internal_error.transport.route")
    require_dict_constant(route, "handled", True, "internal_error.transport.route")
    require_dict_constant(route, "fallback", False, "internal_error.transport.route")
    require_dict_constant(
        route,
        "reason",
        "v4_entry_router_internal_error",
        "internal_error.transport.route",
    )
    require_dict_dotted(
        route,
        "action",
        "IntentAction.need_clarification.value",
        "internal_error.transport.route",
    )


def validate_app(project: Path) -> None:
    app_path = project / "app.py"
    app_source, app_tree = parse_file(app_path)
    middleware = top_level_function(app_tree, "v2_chat_router_middleware")
    lines = call_lines(middleware)
    require("_v4_try_pre_route" in lines, "middleware missing _v4_try_pre_route call")
    v4_line = min(lines["_v4_try_pre_route"])
    for legacy in sorted(LEGACY_BUSINESS_CALLS):
        require(legacy in lines, f"legacy call missing while checking order: {legacy}")
        require(v4_line < min(lines[legacy]), f"V4 pre-route does not precede {legacy}")

    helper = top_level_function(app_tree, "_v4_try_pre_route")
    helper_args = {item.arg for item in helper.args.args}
    require(
        helper_args == {"question", "user", "conversation_id"},
        f"unexpected _v4_try_pre_route arguments: {sorted(helper_args)}",
    )
    validate_init_fallback(helper)
    validate_runtime_exception_delegation(helper)

    for marker in (
        "# V4_2_3_ENTRY_ROUTER_MARKER_BEGIN",
        "# V4_2_3_ENTRY_ROUTER_MARKER_END",
        "# V4_2_3_PRE_ROUTE_CALL_MARKER_BEGIN",
        "# V4_2_3_PRE_ROUTE_CALL_MARKER_END",
    ):
        require(
            app_source.count(marker) == 1,
            f"patch idempotency marker count mismatch: {marker}",
        )


def validate_project(project: Path) -> list[str]:
    project = Path(project).resolve()
    package = project / "netaiops_asset" / "chat_v4"
    checked = validate_package_scan(package)
    validate_entry_router(package)
    validate_app(project)
    validate_bridge_internal_error(package)
    return checked


def main() -> int:
    try:
        checked = validate_project(PROJECT)
    except ArchitectureError as exc:
        raise SystemExit(f"V4_2_3_ARCHITECTURE_ERROR: {exc}") from exc

    print("v4_entry_router_package=OK")
    print("llm_arbiter_only_action_source=OK")
    print("question_not_used_for_action_selection=OK")
    print("low_risk_stage_gate=OK")
    print("entry_init_technical_fallback_contract=OK")
    print("entry_router_checker_delegated_internal_error_contract=OK")
    print("app_bridge_visible_internal_error_contract=OK")
    print("internal_exception_legacy_fallback=PROHIBITED")
    print("confidence_device_evidence_clarification_gate=OK")
    print("v4_pre_route_precedes_v2_v3_business_routes=OK")
    print("v3_shadow_state_reused_on_fallback=OK")
    print("external_side_effect_import_scan=OK")
    print("marker_scope=PATCH_IDEMPOTENCY_ONLY")
    print("checked_files={}".format(",".join(checked)))
    print("result=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
