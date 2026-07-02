#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_FILE = Path(sys.argv[1]).resolve()
REPORT_OUT = Path(sys.argv[2])
APP_DIR = APP_FILE.parent
REQUIRED_ACTIONS = {"general_chat", "advice_analysis"}

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

CANDIDATES = [
    'Explain what a network device management IP is. Do not generate commands.',
    'Explain what a dual data center architecture is. Do not generate commands.',
    'Explain the difference between OSPF Full and 2-Way states. Do not generate commands.',
    'Explain the purpose of SNMP in network monitoring. Do not generate commands.',
    'Explain what F5 LTM virtual server does. Do not generate commands.',
    'Explain the difference between shadow mode and takeover mode. Do not generate commands.',
    'Explain why V2 fallback should be preserved during migration. Do not generate commands.',
    'Explain what route-return takeover means. Do not generate commands.',
    'Should we verify service health after changing app.py? Provide operational advice only. Do not generate commands.',
    'Should we prepare rollback steps before restarting a production service? Provide operational advice only. Do not generate commands.',
    'Should we collect evidence before changing a network device? Provide operational advice only. Do not generate commands.',
    'Should we check Git status before staging a release? Provide operational advice only. Do not generate commands.',
    'Should a script with here-doc content be tested with a real dry-run? Provide operational advice only. Do not generate commands.',
    'Should standby network device maintenance isolate traffic first? Provide operational advice only. Do not generate commands.',
    'Should link flap analysis check historical alarms first? Provide operational advice only. Do not generate commands.',
]


def norm_action(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        enum_value = getattr(value, "value", None)
        if enum_value not in (None, ""):
            return str(enum_value)
    except Exception:
        pass
    text = str(value).strip()
    if text.startswith("IntentAction."):
        return text.split(".", 1)[1]
    return text


def as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        from dataclasses import asdict as dc_asdict, is_dataclass
        if is_dataclass(value):
            data = dc_asdict(value)
            if isinstance(data, dict):
                return dict(data)
    except Exception:
        pass
    for method_name in ("model_dump", "dict", "as_dict"):
        try:
            method = getattr(value, method_name, None)
            if callable(method):
                data = method()
                if isinstance(data, dict):
                    return dict(data)
        except Exception:
            pass
    try:
        data = vars(value)
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    return {}


def extract_plan_decision(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = state.get("v3_plan") or state.get("plan") or {}
    decision = state.get("v3_decision") or state.get("decision") or {}
    return as_dict(plan), as_dict(decision)


def extract_action(plan: dict[str, Any], decision: dict[str, Any]) -> tuple[str, str]:
    for source_name, source in (("decision", decision), ("plan", plan)):
        for key in ("action", "handler_key"):
            action = norm_action(source.get(key))
            if action:
                return action, source_name
    return "", ""


def extract_confidence(plan: dict[str, Any], decision: dict[str, Any]) -> float:
    for source in (decision, plan):
        for key in ("effective_confidence", "confidence"):
            try:
                value = source.get(key)
                if value not in (None, ""):
                    return float(value)
            except Exception:
                pass
        metadata = source.get("metadata")
        if isinstance(metadata, dict):
            for key in ("effective_confidence", "confidence"):
                try:
                    value = metadata.get(key)
                    if value not in (None, ""):
                        return float(value)
                except Exception:
                    pass
    return 0.0


def load_service_env() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        pid = subprocess.check_output(
            ["systemctl", "show", "netaiops-asset-agent.service", "-p", "MainPID", "--value"],
            text=True,
        ).strip()
        if pid and pid != "0":
            raw_items = (Path("/proc") / pid / "environ").read_bytes().split(b"\0")
            for item in raw_items:
                if b"=" not in item:
                    continue
                key, value = item.split(b"=", 1)
                env[key.decode(errors="replace")] = value.decode(errors="replace")
    except Exception:
        pass
    return env


def import_app_module(app_file: Path):
    spec = importlib.util.spec_from_file_location("netaiops_asset_agent_app_preflight", app_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build import spec for {app_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def call_shadow_builder(builder, question: str, user: str, conversation_id: str) -> dict[str, Any]:
    payload = {"user": user, "conversation_id": conversation_id, "question": question}
    try:
        state = builder(question, user=user, conversation_id=conversation_id, payload=payload)
    except TypeError:
        try:
            state = builder(question, user=user, conversation_id=conversation_id)
        except TypeError:
            state = builder(question)
    return as_dict(state)


def safe_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [safe_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        enum_value = getattr(value, "value", None)
        if enum_value not in (None, ""):
            return str(enum_value)
    except Exception:
        pass
    return str(value)


def main() -> int:
    service_env = load_service_env()
    for key, value in service_env.items():
        if key.startswith("NETAIOPS_"):
            os.environ.setdefault(key, value)

    enabled = str(os.getenv("NETAIOPS_V3_TAKEOVER_ENABLED", "0")).lower() in {"1", "true", "yes", "on", "enabled"}
    users = [item.strip() for item in os.getenv("NETAIOPS_V3_TAKEOVER_ALLOWED_USERS", "").split(",") if item.strip()]
    prefixes = [item.strip() for item in os.getenv("NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX", "").split(",") if item.strip()]
    allowed_actions = {item.strip() for item in os.getenv("NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS", "general_chat,advice_analysis").split(",") if item.strip()}
    min_confidence = float(os.getenv("NETAIOPS_V3_TAKEOVER_MIN_CONFIDENCE", "0.70"))

    report: dict[str, Any] = {
        "app_file": str(APP_FILE),
        "app_dir_added_to_sys_path": str(APP_DIR) in sys.path,
        "enabled": enabled,
        "users": users,
        "prefixes": prefixes,
        "allowed_actions": sorted(allowed_actions),
        "min_confidence": min_confidence,
        "candidates": [],
        "selected": {},
    }

    if not enabled or not users or not prefixes:
        report["error"] = "takeover trigger env incomplete"
        REPORT_OUT.write_text(json.dumps(safe_jsonable(report), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        raise SystemExit("ERROR: takeover trigger env incomplete")

    module = import_app_module(APP_FILE)
    builder = getattr(module, "_v3_shadow_build", None)
    if not callable(builder):
        report["error"] = "_v3_shadow_build missing"
        REPORT_OUT.write_text(json.dumps(safe_jsonable(report), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        raise SystemExit("ERROR: _v3_shadow_build missing")

    report["shadow_builder_signature"] = str(inspect.signature(builder))
    user = users[0]
    prefix = prefixes[0]
    selected: dict[str, dict[str, Any]] = {}

    for idx, question in enumerate(CANDIDATES, start=1):
        conversation_id = f"{prefix}v343-r6-preflight-{idx}"
        item: dict[str, Any] = {
            "candidate_id": idx,
            "question": question,
            "conversation_id": conversation_id,
        }
        try:
            state = call_shadow_builder(builder, question, user, conversation_id)
            plan, decision = extract_plan_decision(state)
            action, action_source = extract_action(plan, decision)
            confidence = extract_confidence(plan, decision)
            item.update({
                "error": str(state.get("error") or ""),
                "action": action,
                "action_source": action_source,
                "confidence": confidence,
                "plan_keys": sorted(str(k) for k in plan.keys())[:80],
                "decision_keys": sorted(str(k) for k in decision.keys())[:80],
            })
            if not item["error"] and action in REQUIRED_ACTIONS and action in allowed_actions and confidence >= min_confidence:
                current = selected.get(action)
                if current is None or float(item["confidence"]) > float(current.get("confidence", 0.0)):
                    selected[action] = dict(item)
        except Exception as exc:
            item["exception"] = repr(exc)
        report["candidates"].append(item)
        if REQUIRED_ACTIONS.issubset(selected):
            break

    report["selected"] = selected
    report["selected_actions"] = sorted(selected)
    missing = sorted(REQUIRED_ACTIONS.difference(selected))
    report["missing_required_actions"] = missing
    REPORT_OUT.write_text(json.dumps(safe_jsonable(report), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(safe_jsonable(report), ensure_ascii=False, indent=2, sort_keys=True))
    if missing:
        raise SystemExit("ERROR: Arbiter preflight did not find required actions before modifying app.py: " + ",".join(missing))
    print("v3_4_3_arbiter_preflight=OK")
    print("v3_4_3_arbiter_preflight_general_chat=OK")
    print("v3_4_3_arbiter_preflight_advice_analysis=OK")
    print("v3_4_3_preflight_sys_path_import=OK")
    print("v3_4_3_preflight_enum_action_normalization=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
