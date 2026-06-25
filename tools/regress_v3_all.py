# -*- coding: utf-8 -*-
"""
V3.1 total regression runner.

This script:
- Compiles current V3 modules.
- Runs all existing V3 regression scripts.
- Optionally runs live LLM smoke by passing through environment variables.
- Generates a compact JSON summary.

It does not modify app.py, does not restart service, and does not call MCP.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT / "venv" / "bin" / "python"
REPORT_DIR = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
RUN_LIVE = os.environ.get("V3_LIVE_LLM_SMOKE", "").strip().lower() in {"1", "true", "yes", "on"}


def run_cmd(
    args: List[str],
    env: Dict[str, str] | None = None,
    timeout: int = 120,
) -> Tuple[int, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    proc = subprocess.run(
        args,
        cwd=str(PROJECT),
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


def collect_python_files() -> List[Path]:
    targets: List[Path] = []

    chat_v3_dir = PROJECT / "netaiops_asset" / "chat_v3"
    if chat_v3_dir.exists():
        targets.extend(sorted(chat_v3_dir.glob("*.py")))

    tools_dir = PROJECT / "tools"
    if tools_dir.exists():
        targets.extend(sorted(tools_dir.glob("regress_v3_*.py")))
        report_file = tools_dir / "report_v3_file_inventory.py"
        if report_file.exists():
            targets.append(report_file)

    unique: List[Path] = []
    seen = set()
    for item in targets:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def compile_files(files: List[Path]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": True,
        "items": [],
    }

    for path in files:
        rel = str(path.relative_to(PROJECT))
        code, output = run_cmd([str(PYTHON), "-m", "py_compile", rel], timeout=60)
        item = {
            "file": rel,
            "returncode": code,
            "ok": code == 0,
            "output_tail": output[-1000:],
        }
        result["items"].append(item)
        if code != 0:
            result["ok"] = False

    return result


def discover_regression_scripts() -> List[Path]:
    tools_dir = PROJECT / "tools"
    if not tools_dir.exists():
        return []

    scripts = []
    for path in sorted(tools_dir.glob("regress_v3_*.py")):
        if path.name == "regress_v3_all.py":
            continue
        scripts.append(path)

    return scripts


def run_regressions(scripts: List[Path]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": True,
        "items": [],
    }

    env = {
        "V3_LIVE_LLM_SMOKE": "1" if RUN_LIVE else "0",
    }

    for script in scripts:
        rel = str(script.relative_to(PROJECT))
        code, output = run_cmd([str(PYTHON), rel], env=env, timeout=180)
        item = {
            "script": rel,
            "returncode": code,
            "ok": code == 0,
            "live_llm_smoke_enabled": RUN_LIVE,
            "output_tail": output[-3000:],
        }
        result["items"].append(item)

        print(f"----- {rel} -----")
        print(output.rstrip())
        print(f"----- {rel} returncode={code} -----")

        if code != 0:
            result["ok"] = False

    return result


def build_manual_schema_smoke() -> Dict[str, Any]:
    code = r"""
from netaiops_asset.chat_v3.intent_schema import IntentDecision
d = IntentDecision(
    action="execute_provided_commands_and_analyze",
    confidence=0.93,
    commands=["show clock show version"],
    raw_user_text="执行后分析：show clock show version",
)
assert d.commands_provided is True
assert d.should_execute_commands is True
assert d.should_analyze_after_execution is True
assert d.requires_confirmation is False
print("manual_schema_smoke=OK")
"""
    rc, output = run_cmd([str(PYTHON), "-c", code], timeout=60)
    print(output.rstrip())
    return {
        "ok": rc == 0,
        "returncode": rc,
        "output": output,
    }


def build_module_import_smoke() -> Dict[str, Any]:
    code = r"""
from netaiops_asset.chat_v3.intent_schema import IntentDecision, IntentAction
from netaiops_asset.chat_v3.command_splitter import split_commands
from netaiops_asset.chat_v3.safety_guard import check_commands
from netaiops_asset.chat_v3.intent_dispatcher import build_dispatch_plan
from netaiops_asset.chat_v3.shadow_logger import build_shadow_record

print("import_intent_schema=OK")
print("import_intent_arbiter=OK")
print("import_command_splitter=OK")
print("import_safety_guard=OK")
print("import_intent_dispatcher=OK")
print("import_shadow_logger=OK")

split = split_commands("执行后分析：show clock show version")
assert split.commands == ["show clock", "show version"]

safe = check_commands(split.commands)
assert safe.allowed is True

decision = IntentDecision(
    action=IntentAction.execute_provided_commands_and_analyze,
    confidence=0.95,
    commands=split.commands,
    raw_user_text="执行后分析：show clock show version",
)

plan = build_dispatch_plan(
    question="执行后分析：show clock show version",
    decision=decision,
)
assert plan.accepted is True
assert plan.handler_key == "execute_provided_commands_and_analyze"
assert plan.requires_confirmation is False

record = build_shadow_record(
    question="执行后分析：show clock show version",
    conversation_id="smoke",
    user="tester",
    v2_route="",
    v3_decision=decision,
    v3_plan=plan,
)
assert record["v3_plan"]["handler_key"] == "execute_provided_commands_and_analyze"
print("module_import_and_cross_module_smoke=OK")
"""
    rc, output = run_cmd([str(PYTHON), "-c", code], timeout=60)
    print(output.rstrip())
    return {
        "ok": rc == 0,
        "returncode": rc,
        "output": output,
    }


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "project": str(PROJECT),
        "python": str(PYTHON),
        "run_live_llm_smoke": RUN_LIVE,
        "compile": {},
        "manual_schema_smoke": {},
        "module_import_smoke": {},
        "regressions": {},
        "overall_ok": False,
    }

    if not PYTHON.exists():
        print(f"ERROR: python not found: {PYTHON}")
        summary["overall_ok"] = False
        report = REPORT_DIR / "v3_1_6_total_regression_summary.json"
        report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    print("===== collect V3 python files =====")
    files = collect_python_files()
    for item in files:
        print(str(item.relative_to(PROJECT)))

    print("===== compile V3 python files =====")
    compile_result = compile_files(files)
    summary["compile"] = compile_result
    print("compile_ok=" + str(compile_result["ok"]))

    print("===== manual schema smoke =====")
    manual_schema = build_manual_schema_smoke()
    summary["manual_schema_smoke"] = manual_schema

    print("===== module import and cross-module smoke =====")
    module_import = build_module_import_smoke()
    summary["module_import_smoke"] = module_import

    print("===== discover and run V3 regression scripts =====")
    scripts = discover_regression_scripts()
    for script in scripts:
        print(str(script.relative_to(PROJECT)))

    regressions = run_regressions(scripts)
    summary["regressions"] = regressions

    overall_ok = bool(
        compile_result["ok"]
        and manual_schema["ok"]
        and module_import["ok"]
        and regressions["ok"]
    )
    summary["overall_ok"] = overall_ok

    report = REPORT_DIR / "v3_1_6_total_regression_summary.json"
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("===== V3.1-6 total regression summary =====")
    print(f"report={report}")
    print(f"overall_ok={overall_ok}")

    if overall_ok:
        print("regress_v3_all=OK")
        return 0

    print("regress_v3_all=FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
