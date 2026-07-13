#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "netaiops_asset" / "chat_v4"

REQUIRED_FILES = {
    "__init__.py",
    "contracts.py",
    "context_store.py",
    "context_migration.py",
    "audit_adapter.py",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "netaiops_asset.chat_v2.router",
    "netaiops_asset.chat_v2.semantic_router",
    "netaiops_asset.cmdb",
    "netaiops_asset.mcp",
    "netaiops_asset.netmiko",
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
EXPECTED_SCHEMA_CONSTANTS = {
    "V4_ENTRY_SCHEMA_VERSION": "v4.entry.v1",
    "V4_RESPONSE_SCHEMA_VERSION": "v4.response.v1",
    "V4_AUDIT_SCHEMA_VERSION": "v4.audit.v1",
    "V4_CONTEXT_SCHEMA_VERSION": "v4.context.v1",
}


def fail(message: str) -> None:
    raise SystemExit(f"V4_ARCHITECTURE_ERROR: {message}")


def imported_modules(tree: ast.AST) -> list[str]:
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
    return result


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def literal_assignments(tree: ast.Module) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        try:
            literal = ast.literal_eval(value)
        except (ValueError, TypeError):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = literal
    return values


def top_level_classes(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def method_names(tree: ast.Module, class_name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def called_names(tree: ast.AST) -> set[str]:
    return {
        dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }


def main() -> int:
    if not PACKAGE.is_dir():
        fail(f"missing package: {PACKAGE}")
    actual = {path.name for path in PACKAGE.glob("*.py")}
    missing = sorted(REQUIRED_FILES - actual)
    if missing:
        fail(f"missing files: {missing}")

    trees: dict[str, ast.Module] = {}
    checked = []
    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            fail(f"syntax error in {path.name}: {exc}")
        trees[path.name] = tree

        for module in imported_modules(tree):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            ):
                fail(f"forbidden import in {path.name}: {module}")

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.lower()
                if any(
                    lowered.startswith(prefix)
                    for prefix in FORBIDDEN_FUNCTION_PREFIXES
                ):
                    fail(
                        f"forbidden classifier function in "
                        f"{path.name}: {node.name}"
                    )
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_SYMBOLS:
                fail(f"forbidden symbol in {path.name}: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_SYMBOLS:
                fail(f"forbidden attribute in {path.name}: {node.attr}")
            if isinstance(node, ast.Call):
                target_name = dotted_name(node.func)
                if target_name in {
                    "os.system",
                    "os.popen",
                    "subprocess.run",
                    "subprocess.call",
                    "subprocess.check_call",
                    "subprocess.check_output",
                }:
                    fail(
                        f"forbidden process execution in "
                        f"{path.name}: {target_name}"
                    )
        checked.append(path.name)

    contracts_tree = trees["contracts.py"]
    assignments = literal_assignments(contracts_tree)
    for name, expected in EXPECTED_SCHEMA_CONSTANTS.items():
        if assignments.get(name) != expected:
            fail(
                f"schema constant mismatch: {name}="
                f"{assignments.get(name)!r}"
            )
    classes = top_level_classes(contracts_tree)
    for required_class in {
        "EntryResult",
        "V4Response",
        "V4AuditRecord",
        "CanonicalContext",
        "ContextOperationResult",
    }:
        if required_class not in classes:
            fail(f"missing contract class: {required_class}")

    store_tree = trees["context_store.py"]
    store_methods = method_names(store_tree, "ContextStore")
    for required_method in {
        "sanitize_value",
        "load",
        "save",
        "update",
        "append_turn",
        "add_audit_ref",
    }:
        if required_method not in store_methods:
            fail(f"missing ContextStore method: {required_method}")
    store_calls = called_names(store_tree)
    for required_call in {
        "fcntl.flock",
        "tempfile.mkstemp",
        "os.fsync",
        "os.replace",
    }:
        if required_call not in store_calls:
            fail(f"missing context-store capability: {required_call}")

    migration_tree = trees["context_migration.py"]
    migration_calls = called_names(migration_tree)
    forbidden_mutations = {
        name
        for name in migration_calls
        if name.split(".")[-1] in {"unlink", "rmtree", "remove"}
    }
    if forbidden_mutations:
        fail(
            "legacy migration must be read-only: "
            + ",".join(sorted(forbidden_mutations))
        )

    print("v4_package_files=OK")
    print("v4_contract_versions=OK")
    print("canonical_context_atomic_locking=OK")
    print("canonical_context_secret_and_raw_bound=OK")
    print("legacy_migration_read_only=OK")
    print("intent_keyword_classifier_scan=OK")
    print("external_side_effect_import_scan=OK")
    print("checked_files={}".format(",".join(checked)))
    print("result=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
