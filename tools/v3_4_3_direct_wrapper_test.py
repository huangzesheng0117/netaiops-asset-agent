#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_FILE = Path(sys.argv[1]).resolve()
PREFLIGHT_REPORT = Path(sys.argv[2])
REPORT_OUT = Path(sys.argv[3])
APP_DIR = APP_FILE.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

POLLUTED_SOURCE = "local_context_nested:polluted_nested_candidate"


def load_service_env() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        pid = subprocess.check_output(
            ["systemctl", "show", "netaiops-asset-agent.service", "-p", "MainPID", "--value"],
            text=True,
        ).strip()
        if pid and pid != "0":
            for item in (Path("/proc") / pid / "environ").read_bytes().split(b"\0"):
                if b"=" in item:
                    key, value = item.split(b"=", 1)
                    env[key.decode(errors="replace")] = value.decode(errors="replace")
    except Exception:
        pass
    return env


def import_app(path: Path):
    spec = importlib.util.spec_from_file_location("netaiops_asset_agent_app_direct_r6", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_audit_rows() -> list[dict[str, Any]]:
    audit_dir = Path(
        os.getenv(
            "NETAIOPS_V3_TAKEOVER_AUDIT_DIR",
            "/var/lib/netaiops-asset-agent/data/v3_takeover_audit",
        )
    )
    files = (
        sorted(
            audit_dir.glob("takeover_*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:3]
        if audit_dir.exists()
        else []
    )
    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            record["_file"] = str(path)
            record["_line"] = line_number
            rows.append(record)
    return rows


for key, value in load_service_env().items():
    if key.startswith("NETAIOPS_"):
        os.environ.setdefault(key, value)

preflight = json.loads(PREFLIGHT_REPORT.read_text(encoding="utf-8"))
selected = preflight.get("selected") or {}
user = (preflight.get("users") or [""])[0]

before_rows = load_audit_rows()
before_keys = {(row.get("_file"), row.get("_line")) for row in before_rows}

app = import_app(APP_FILE)
wrapper = getattr(app, "_v3_apply_chat_canary_takeover")

report: dict[str, Any] = {
    "cases": [],
    "selected_actions": sorted(selected),
    "polluted_source": POLLUTED_SOURCE,
}

for action in ("general_chat", "advice_analysis"):
    item = selected[action]
    question = item["question"]
    conversation_id = item["conversation_id"] + "-direct-r6"

    response = {
        "status": "ok",
        "answer": "V2 fallback answer for direct wrapper test.",
        "message": "V2 fallback answer for direct wrapper test.",
        "planner_source": "direct_wrapper_test",
        "question": question,
        "conversation_id": conversation_id,
        "items": [],
        "columns": [],
        "field_labels": {},
        "count": 0,
        "returned": 0,
    }
    local_context = {
        "question": question,
        "user": user,
        "conversation_id": conversation_id,
        "payload": {
            "question": question,
            "user": user,
            "conversation_id": conversation_id,
        },
        # Regression case from the previous general_chat failure: this object has
        # plan/decision-shaped fields but no usable Arbiter action. It must be
        # rejected and must not prevent _v3_shadow_build() from rebuilding.
        "polluted_nested_candidate": {
            "plan": {"steps": [], "note": "no action"},
            "decision": {"reason": "missing action"},
        },
    }

    try:
        result = wrapper(
            response=response,
            local_context=local_context,
            route_label="direct_wrapper_test_r6",
        )
        case = {
            "expected_action": action,
            "conversation_id": conversation_id,
            "status": result.get("status") if isinstance(result, dict) else None,
            "planner_source": result.get("planner_source") if isinstance(result, dict) else None,
            "v3_takeover": result.get("v3_takeover") if isinstance(result, dict) else None,
            "v3_takeover_action": result.get("v3_takeover_action") if isinstance(result, dict) else None,
            "v3_takeover_reason": result.get("v3_takeover_reason") if isinstance(result, dict) else None,
            "v3_takeover_source": result.get("v3_takeover_source") if isinstance(result, dict) else None,
            "v3_takeover_error": result.get("v3_takeover_error") if isinstance(result, dict) else None,
            "v3_audit_error": result.get("v3_audit_error") if isinstance(result, dict) else None,
            "v3_arbiter_state_source": result.get("v3_arbiter_state_source") if isinstance(result, dict) else None,
            "answer_len": len(str(result.get("answer") or result.get("message") or "")) if isinstance(result, dict) else 0,
            "reused_v2_fallback_answer": (
                str(result.get("answer") or "") == "V2 fallback answer for direct wrapper test."
                if isinstance(result, dict)
                else None
            ),
            "keys": sorted(result.keys())[:100] if isinstance(result, dict) else [],
        }
    except Exception as exc:
        case = {
            "expected_action": action,
            "conversation_id": conversation_id,
            "exception": repr(exc),
        }
    report["cases"].append(case)

after_rows = load_audit_rows()
new_rows = [
    row
    for row in after_rows
    if (row.get("_file"), row.get("_line")) not in before_keys
]
conversation_ids = {
    case.get("conversation_id")
    for case in report["cases"]
    if case.get("conversation_id")
}
matched_rows = [
    row
    for row in after_rows
    if row.get("conversation_id") in conversation_ids
]
report["audit_new_rows"] = new_rows[-80:]
report["audit_matched_rows"] = matched_rows[-80:]

REPORT_OUT.write_text(
    json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

failures: list[dict[str, Any]] = []
for case in report["cases"]:
    if case.get("exception") or case.get("v3_takeover_error") or case.get("v3_audit_error"):
        failures.append(case)
    elif case.get("v3_takeover") is not True:
        failures.append(case)
    elif case.get("v3_takeover_action") != case.get("expected_action"):
        failures.append(case)
    elif case.get("v3_takeover_reason") != "v3_4_3_preflight_arbiter_rebuild_text_advice_convergence":
        failures.append(case)
    elif case.get("v3_takeover_source") != "llm":
        failures.append(case)
    elif case.get("v3_arbiter_state_source") != "rebuilt_in_takeover_wrapper":
        failures.append(case)
    elif case.get("reused_v2_fallback_answer") is not False:
        failures.append(case)

for conversation_id in sorted(conversation_ids):
    taken_rows = [
        row
        for row in matched_rows
        if row.get("conversation_id") == conversation_id
        and row.get("version") == "v3.4.3-r6"
        and row.get("taken") is True
        and row.get("reason") == "taken"
        and row.get("generator_source") == "llm"
        and row.get("generator_reason") == "llm_answer_generated"
    ]
    if not taken_rows:
        failures.append(
            {
                "conversation_id": conversation_id,
                "audit_failure": "missing_taken_llm_generated_v3_4_3_r6_audit",
                "matched_rows": [
                    row
                    for row in matched_rows
                    if row.get("conversation_id") == conversation_id
                ],
            }
        )
        continue

    if not any(
        int(row.get("arbiter_state_rejected_count") or 0) >= 1
        and any(
            item.get("source") == POLLUTED_SOURCE
            and item.get("reason") == "arbiter_action_missing"
            for item in (row.get("arbiter_state_rejected_candidates") or [])
            if isinstance(item, dict)
        )
        for row in taken_rows
    ):
        failures.append(
            {
                "conversation_id": conversation_id,
                "audit_failure": "polluted_nested_state_was_not_rejected_before_rebuild",
                "matched_rows": taken_rows,
            }
        )

if failures:
    raise SystemExit(
        "ERROR: direct wrapper test failed: "
        + json.dumps(failures, ensure_ascii=False)
    )

print("v3_4_3_r6_direct_wrapper_test=OK")
print("v3_4_3_r6_direct_wrapper_source_llm=OK")
print("v3_4_3_r6_direct_wrapper_rejects_invalid_nested_state=OK")
print("v3_4_3_r6_direct_wrapper_rebuilds_after_rejection=OK")
print("v3_4_3_r6_direct_wrapper_audit_taken=OK")
