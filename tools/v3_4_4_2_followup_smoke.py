#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

APP_DIR = Path(sys.argv[1]).resolve()
REPORT_OUT = Path(sys.argv[2]).resolve()
SERVICE = sys.argv[3]
BASE_URL = sys.argv[4].rstrip("/")
APP_FILE = APP_DIR / "app.py"


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    for name in ("model_dump", "as_dict", "dict"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                data = method()
                if isinstance(data, dict):
                    return dict(data)
            except Exception:
                pass
    try:
        data = vars(value)
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        return {}


def normalize_action(value: Any) -> str:
    if value in (None, ""):
        return ""
    enum_value = getattr(value, "value", None)
    if enum_value not in (None, ""):
        return str(enum_value)
    text = str(value).strip()
    if text.startswith("IntentAction."):
        return text.split(".", 1)[1]
    return text


def load_service_env() -> dict[str, str]:
    pid = subprocess.check_output(
        [
            "systemctl",
            "show",
            SERVICE,
            "-p",
            "MainPID",
            "--value",
        ],
        text=True,
    ).strip()
    env: dict[str, str] = {}
    if pid and pid != "0":
        for raw in (
            Path("/proc") / pid / "environ"
        ).read_bytes().split(b"\0"):
            if b"=" not in raw:
                continue
            key, value = raw.split(b"=", 1)
            env[
                key.decode(errors="replace")
            ] = value.decode(errors="replace")
    return env


def import_app(path: Path):
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))
    spec = importlib.util.spec_from_file_location(
        "netaiops_asset_agent_v3442_fix1_smoke",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def post_chat(payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        BASE_URL + "/api/v1/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(
            request,
            timeout=150,
        ) as response:
            body = response.read().decode(
                "utf-8",
                errors="replace",
            )
            status_code = response.status
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"_raw_body_prefix": body[:10000]}
        return {
            "ok": True,
            "status_code": status_code,
            "elapsed_seconds": round(
                time.time() - started,
                3,
            ),
            "response": parsed,
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "http_error": exc.code,
            "elapsed_seconds": round(
                time.time() - started,
                3,
            ),
            "body_prefix": body[:10000],
        }
    except Exception as exc:
        return {
            "ok": False,
            "exception": repr(exc),
            "elapsed_seconds": round(
                time.time() - started,
                3,
            ),
        }


def load_audit_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    files = sorted(
        root.glob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:8]
    for path in files:
        for line_number, line in enumerate(
            path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                row["_file"] = str(path)
                row["_line"] = line_number
                rows.append(row)
    return rows


def shadow_action(state: Any) -> tuple[str, float, dict[str, Any]]:
    state_dict = as_dict(state)
    plan = as_dict(
        state_dict.get("v3_plan")
        or state_dict.get("plan")
    )
    decision = as_dict(
        state_dict.get("v3_decision")
        or state_dict.get("decision")
    )
    action = ""
    for source in (decision, plan):
        for key in ("action", "handler_key"):
            action = normalize_action(source.get(key))
            if action:
                break
        if action:
            break
    confidence = 0.0
    for source in (decision, plan):
        for key in (
            "effective_confidence",
            "confidence",
        ):
            try:
                if source.get(key) not in (None, ""):
                    confidence = float(source.get(key))
                    break
            except Exception:
                pass
        if confidence:
            break
    return action, confidence, {
        "state": state_dict,
        "plan": plan,
        "decision": decision,
    }


def valid_answer(response: dict[str, Any]) -> bool:
    if not isinstance(response, dict):
        return False
    status = str(response.get("status") or "").strip().lower()
    if status in {"error", "failed", "failure", "exception"}:
        return False
    return bool(
        str(
            response.get("answer")
            or response.get("message")
            or ""
        ).strip()
    )


def main() -> int:
    env = load_service_env()
    for key, value in env.items():
        if key.startswith("NETAIOPS_"):
            os.environ[key] = value

    users = [
        item.strip()
        for item in env.get(
            "NETAIOPS_V3_TAKEOVER_ALLOWED_USERS",
            "",
        ).split(",")
        if item.strip()
    ]
    prefixes = [
        item.strip()
        for item in env.get(
            "NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX",
            "",
        ).split(",")
        if item.strip()
    ]
    if not users or not prefixes:
        raise SystemExit(
            "ERROR: canary user/prefix environment is incomplete"
        )

    user = users[0]
    prefix = prefixes[0]
    audit_dir = Path(
        env.get(
            "NETAIOPS_V3_TAKEOVER_AUDIT_DIR",
            "/var/lib/netaiops-asset-agent/data/v3_takeover_audit",
        )
    )

    app = import_app(APP_FILE)
    shadow_builder = getattr(app, "_v3_shadow_build")

    from netaiops_asset.chat_v3.followup_context import (
        build_followup_context,
        delete_followup_context,
    )

    first_questions = [
        (
            "请从CMDB中查询设备名称为 "
            "V3442-FIX1-NONEXISTENT-DEVICE 的资产，"
            "只返回查询结果，不生成或执行命令。"
        ),
        (
            "请查询CMDB中管理IP为 192.0.2.254 的设备信息，"
            "只做资产查询，不生成或执行命令。"
        ),
        (
            "请列出CMDB中机房字段为 "
            "V3442-FIX1-NONEXISTENT-IDC 的设备，"
            "不要生成或执行命令。"
        ),
        (
            "请解释网络设备维护前为什么必须确认当前主备状态和"
            "实际流量路径，只做解释，不生成或执行命令。"
        ),
    ]
    followup_questions = [
        (
            "只基于上一轮已经返回的内容继续分析："
            "其中最容易被忽略的风险或原因是什么？"
            "不要重新查询CMDB，不要生成或执行命令。"
        ),
        (
            "根据上一轮已有结果继续分析最可能的原因，"
            "不要获取任何新数据，也不要生成或执行命令。"
        ),
        (
            "请仅分析上一轮已有证据能够支持的结论，"
            "不要重新查询、不要执行命令。"
        ),
    ]

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    attempts: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None

    for index, first_question in enumerate(
        first_questions,
        start=1,
    ):
        conversation_id = (
            f"{prefix}v3442-fix1-api-{timestamp}-{index}"
        )
        delete_followup_context(conversation_id)

        first_result = post_chat(
            {
                "question": first_question,
                "user": user,
                "conversation_id": conversation_id,
            }
        )
        time.sleep(2)
        first_response = first_result.get("response")
        first_response = (
            first_response
            if isinstance(first_response, dict)
            else {}
        )
        first_context = build_followup_context(
            conversation_id=conversation_id,
            user=user,
        )
        audit_rows = [
            row
            for row in load_audit_rows(audit_dir)
            if (
                row.get("original_conversation_id")
                or row.get("conversation_id")
            )
            == conversation_id
        ]

        attempt = {
            "conversation_id": conversation_id,
            "first_question": first_question,
            "first_result": first_result,
            "first_context": first_context,
            "audit_rows_after_first": audit_rows,
        }
        attempts.append(attempt)

        non_taken = first_response.get("v3_takeover") is not True
        recorded = (
            first_response.get(
                "v3_followup_context_recorded"
            )
            is True
        )
        source_ok = (
            first_response.get(
                "v3_followup_context_record_source"
            )
            == "v2_or_non_taken_return"
        )
        context_ok = (
            first_context.get("available") is True
            and int(first_context.get("turn_count") or 0) >= 1
        )
        audit_ok = any(
            row.get("version") == "v3.4.4-2-fix1"
            and row.get("taken") is False
            and row.get("context_recorded") is True
            and row.get("context_record_source")
            == "v2_or_non_taken_return"
            for row in audit_rows
        )

        if (
            first_result.get("ok") is True
            and valid_answer(first_response)
            and non_taken
            and recorded
            and source_ok
            and context_ok
            and audit_ok
        ):
            selected = attempt
            break

        delete_followup_context(conversation_id)

    if selected is None:
        REPORT_OUT.write_text(
            json.dumps(
                {
                    "attempts": attempts,
                    "error": (
                        "no valid non-taken first return was "
                        "persisted"
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )
        raise SystemExit(
            "ERROR: no valid non-taken first return was persisted"
        )

    conversation_id = selected["conversation_id"]
    followup_candidates = []
    chosen_followup = None
    min_confidence = float(
        env.get(
            "NETAIOPS_V3_TAKEOVER_MIN_CONFIDENCE",
            "0.70",
        )
        or 0.70
    )

    for followup_question in followup_questions:
        state = shadow_builder(
            question=followup_question,
            user=user,
            conversation_id=conversation_id,
            payload={
                "question": followup_question,
                "user": user,
                "conversation_id": conversation_id,
            },
        )
        action, confidence, details = shadow_action(state)
        candidate = {
            "question": followup_question,
            "action": action,
            "confidence": confidence,
            "details": details,
        }
        followup_candidates.append(candidate)
        if (
            action == "analyze_existing_evidence"
            and confidence >= min_confidence
        ):
            chosen_followup = candidate
            break

    if chosen_followup is None:
        delete_followup_context(conversation_id)
        REPORT_OUT.write_text(
            json.dumps(
                {
                    "attempts": attempts,
                    "selected": selected,
                    "followup_candidates": followup_candidates,
                    "error": (
                        "Arbiter did not select "
                        "analyze_existing_evidence"
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )
        raise SystemExit(
            "ERROR: Arbiter did not select "
            "analyze_existing_evidence"
        )

    before_followup_turn_count = int(
        selected["first_context"].get("turn_count") or 0
    )
    followup_result = post_chat(
        {
            "question": chosen_followup["question"],
            "user": user,
            "conversation_id": conversation_id,
        }
    )
    time.sleep(3)
    followup_response = followup_result.get("response")
    followup_response = (
        followup_response
        if isinstance(followup_response, dict)
        else {}
    )
    context_after_followup = build_followup_context(
        conversation_id=conversation_id,
        user=user,
    )
    audit_rows_final = [
        row
        for row in load_audit_rows(audit_dir)
        if (
            row.get("original_conversation_id")
            or row.get("conversation_id")
        )
        == conversation_id
    ]

    report = {
        "user": user,
        "prefix": prefix,
        "selected": selected,
        "followup_candidates": followup_candidates,
        "chosen_followup": chosen_followup,
        "followup_result": followup_result,
        "context_after_followup": context_after_followup,
        "audit_rows_final": audit_rows_final,
    }
    REPORT_OUT.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
    )

    failures = []
    if followup_result.get("ok") is not True:
        failures.append("followup_http_failed")
    if followup_response.get("v3_takeover") is not True:
        failures.append("followup_not_taken")
    if followup_response.get("v3_takeover_action") != (
        "analyze_existing_evidence"
    ):
        failures.append("followup_wrong_action")
    if followup_response.get("v3_takeover_source") != "llm":
        failures.append("followup_wrong_source")
    if followup_response.get(
        "v3_original_conversation_id"
    ) != conversation_id:
        failures.append("original_conversation_id_not_preserved")
    if followup_response.get(
        "v3_followup_context_available"
    ) is not True:
        failures.append("followup_context_not_available")
    if followup_response.get(
        "v3_followup_context_record_source"
    ) != "v3_taken_return":
        failures.append("taken_return_not_recorded")
    if int(context_after_followup.get("turn_count") or 0) < (
        before_followup_turn_count + 1
    ):
        failures.append("followup_turn_not_appended")

    taken_audit = [
        row
        for row in audit_rows_final
        if row.get("version") == "v3.4.4-2-fix1"
        and row.get("action") == "analyze_existing_evidence"
        and row.get("taken") is True
        and row.get("reason") == "taken"
        and row.get("generator_source") == "llm"
        and row.get("context_recorded") is True
        and row.get("context_record_source") == "v3_taken_return"
    ]
    if not taken_audit:
        failures.append("taken_audit_missing")

    delete_followup_context(conversation_id)

    if failures:
        raise SystemExit(
            "ERROR: V3.4-4-2 fix1 API smoke failed: "
            + json.dumps(failures, ensure_ascii=False)
        )

    print("v3_4_4_2_fix1_non_taken_api_persisted=OK")
    print("v3_4_4_2_fix1_arbiter_followup_selected=OK")
    print("v3_4_4_2_fix1_followup_api_takeover=OK")
    print("v3_4_4_2_fix1_followup_audit_taken=OK")
    print("v3_4_4_2_fix1_original_conversation_id=OK")
    print("result=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
