#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def now_tag() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def today_shadow_file(shadow_dir: Path) -> Path:
    return shadow_dir / f"shadow_{dt.datetime.now().strftime('%Y%m%d')}.jsonl"


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def load_shadow_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                item["_line_no"] = line_no
                records.append(item)
        except Exception as exc:
            records.append({"_line_no": line_no, "_parse_error": repr(exc), "_raw_prefix": line[:300]})
    return records


def post_json(url: str, payload: dict[str, Any], timeout: int) -> tuple[int, dict[str, Any], str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text)
            except Exception:
                data = {"_json_parse_failed": True, "_body_prefix": text[:500]}
            return int(resp.status), data, text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except Exception:
            data = {"_json_parse_failed": True, "_body_prefix": text[:500]}
        return int(exc.code), data, text


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def summarize_shadow(record: dict[str, Any]) -> dict[str, Any]:
    extra = as_dict(record.get("extra"))
    plan = as_dict(record.get("v3_plan"))
    decision = as_dict(record.get("v3_decision"))
    runtime_gate = as_dict(extra.get("takeover_gate_runtime"))
    if_enabled_gate = as_dict(extra.get("takeover_gate_if_enabled"))
    readiness_before = as_dict(extra.get("takeover_response_readiness_if_enabled"))
    generator = as_dict(extra.get("response_generator_runtime"))
    readiness_after = as_dict(extra.get("takeover_response_readiness_after_generator"))

    errors = {
        key: extra.get(key)
        for key in [
            "plan_decision_normalization_error",
            "takeover_gate_error",
            "takeover_response_readiness_error",
            "response_generator_error",
        ]
        if extra.get(key)
    }

    action = plan.get("action") or decision.get("action")
    candidate_after_generator = (
        bool(generator.get("ready") is True)
        and bool(readiness_after.get("ready") is True)
        and not errors
    )

    return {
        "line_no": record.get("_line_no"),
        "conversation_id": record.get("conversation_id"),
        "v2_route": record.get("v2_route"),
        "v3_action": action,
        "v3_handler_key": plan.get("handler_key"),
        "v3_plan_is_dict": isinstance(record.get("v3_plan"), dict),
        "v3_decision_is_dict": isinstance(record.get("v3_decision"), dict),
        "runtime_gate_enabled": runtime_gate.get("enabled"),
        "runtime_gate_takeover": runtime_gate.get("takeover"),
        "runtime_gate_reason": runtime_gate.get("reason"),
        "if_enabled_gate_action": if_enabled_gate.get("action"),
        "if_enabled_gate_eligible": if_enabled_gate.get("eligible"),
        "if_enabled_gate_takeover": if_enabled_gate.get("takeover"),
        "if_enabled_gate_reason": if_enabled_gate.get("reason"),
        "readiness_before_ready": readiness_before.get("ready"),
        "readiness_before_reason": readiness_before.get("reason"),
        "generator_ready": generator.get("ready"),
        "generator_source": generator.get("source"),
        "generator_llm_status": generator.get("llm_status"),
        "generator_reason": generator.get("reason"),
        "generator_answer_len": len(str(generator.get("answer") or "")),
        "readiness_after_ready": readiness_after.get("ready"),
        "readiness_after_reason": readiness_after.get("reason"),
        "candidate_after_generator": candidate_after_generator,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatBot V3.3-15 takeover preflight guardrails")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--shadow-dir", default="/var/lib/netaiops-asset-agent/data/v3_intent_shadow")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    shadow_dir = Path(args.shadow_dir)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    tests = [
        {
            "name": "general_chat",
            "message": "只做文本解释，不要查询设备、不生成命令：解释一下 Cisco StackWise Virtual 是什么，以及它主要解决什么问题。",
            "expect_generator_ready": True,
            "expect_generator_source": "llm",
        },
        {
            "name": "advice_analysis",
            "message": "是否建议在重启 standby 网络设备前先隔离流量？只给运维建议，不要生成命令。",
            "expect_generator_ready": True,
            "expect_generator_source": "llm",
        },
        {
            "name": "need_clarification",
            "message": "这个设备现在怎么办？",
            "expect_generator_ready": None,
            "expect_generator_source": None,
        },
        {
            "name": "cmdb_query_nonexistent",
            "message": "查一下 V3-3-15-PREFLIGHT-NONEXIST-DEVICE-001 的管理 IP 和设备类型",
            "expect_generator_ready": False,
            "expect_generator_reason": "missing_cmdb_items",
        },
        {
            "name": "command_safety",
            "message": "只分析不要执行：show interface status 这个命令通常用于 Cisco 设备查看什么信息？",
            "expect_generator_ready": True,
            "expect_generator_source": "llm",
        },
    ]

    shadow_file = today_shadow_file(shadow_dir)
    before_lines = count_lines(shadow_file)
    ts = now_tag()
    api_results: list[dict[str, Any]] = []

    for index, test in enumerate(tests, start=1):
        conversation_id = f"v3-3-15-preflight-{ts}-{index}-{test['name']}"
        payload = {
            "user": "v3_3_15_preflight",
            "conversation_id": conversation_id,
            "message": test["message"],
            "question": test["message"],
        }
        payload_path = report_dir / f"payload_{index}_{test['name']}.json"
        response_path = report_dir / f"response_{index}_{test['name']}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        status, data, text = post_json(f"{base_url}/api/v1/chat", payload, args.timeout)
        response_path.write_text(text, encoding="utf-8")

        result = {
            "index": index,
            "name": test["name"],
            "conversation_id": conversation_id,
            "http_status": status,
            "response_path": str(response_path),
            "response_keys": sorted(data.keys()) if isinstance(data, dict) else [],
            "planner_source": data.get("planner_source") if isinstance(data, dict) else None,
            "status": data.get("status") if isinstance(data, dict) else None,
            "answer_prefix": str((data.get("answer") or data.get("message") or "") if isinstance(data, dict) else "")[:300],
        }
        print(json.dumps(result, ensure_ascii=False))
        api_results.append(result)

        if status != 200:
            raise SystemExit(f"ERROR: API test {test['name']} returned HTTP {status}")
        if result["planner_source"] in {"v3_takeover", "v3_response_generator"}:
            raise SystemExit(f"ERROR: V3 real takeover leaked into API response while disabled: {result}")

        time.sleep(1)

    time.sleep(3)
    after_lines = count_lines(shadow_file)

    if after_lines < before_lines + len(tests):
        raise SystemExit(
            f"ERROR: shadow file did not grow enough: before={before_lines}, after={after_lines}, tests={len(tests)}"
        )

    records = load_shadow_records(shadow_file)
    shadow_by_conv = {record.get("conversation_id"): record for record in records if record.get("conversation_id")}
    shadow_summaries: list[dict[str, Any]] = []

    for test, api in zip(tests, api_results):
        record = shadow_by_conv.get(api["conversation_id"])
        if not record:
            raise SystemExit(f"ERROR: missing shadow record for {api['conversation_id']}")

        summary = summarize_shadow(record)
        summary["test_name"] = test["name"]
        shadow_summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False))

        if summary["errors"]:
            raise SystemExit(f"ERROR: shadow errors exist for {test['name']}: {summary['errors']}")
        if summary["runtime_gate_enabled"] is not False:
            raise SystemExit(f"ERROR: runtime gate must remain disabled for {test['name']}: {summary}")
        if summary["runtime_gate_takeover"] is not False:
            raise SystemExit(f"ERROR: runtime takeover must remain false for {test['name']}: {summary}")
        if not summary["v3_plan_is_dict"] or not summary["v3_decision_is_dict"]:
            raise SystemExit(f"ERROR: plan/decision not normalized dict for {test['name']}: {summary}")

        expected_ready = test.get("expect_generator_ready")
        if expected_ready is not None and summary["generator_ready"] is not expected_ready:
            raise SystemExit(f"ERROR: unexpected generator_ready for {test['name']}: {summary}")

        expected_source = test.get("expect_generator_source")
        if expected_source is not None and summary["generator_source"] != expected_source:
            raise SystemExit(f"ERROR: unexpected generator_source for {test['name']}: {summary}")

        expected_reason = test.get("expect_generator_reason")
        if expected_reason is not None and summary["generator_reason"] != expected_reason:
            raise SystemExit(f"ERROR: unexpected generator_reason for {test['name']}: {summary}")

    candidate_count = sum(1 for item in shadow_summaries if item.get("candidate_after_generator"))
    llm_ready_count = sum(1 for item in shadow_summaries if item.get("generator_ready") is True and item.get("generator_source") == "llm")

    final_report = {
        "version": "v3.3.15",
        "purpose": "takeover preflight guardrails while real takeover remains disabled",
        "base_url": base_url,
        "shadow_file": str(shadow_file),
        "before_lines": before_lines,
        "after_lines": after_lines,
        "api_results": api_results,
        "shadow_summaries": shadow_summaries,
        "candidate_after_generator_count": candidate_count,
        "llm_ready_count": llm_ready_count,
        "real_takeover_leaked": False,
        "passed": True,
    }

    (report_dir / "v3_3_15_takeover_preflight_report.json").write_text(
        json.dumps(final_report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print("v3_3_15_preflight_api=OK")
    print("v3_3_15_no_real_takeover=OK")
    print("v3_3_15_candidate_after_generator_count=", candidate_count)
    print("v3_3_15_llm_ready_count=", llm_ready_count)
    print("v3_3_15_preflight_summary=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
