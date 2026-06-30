#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
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


def post_json(url: str, payload: dict[str, Any], timeout: int) -> tuple[int, dict[str, Any], str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
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


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatBot V3.3-16 request-context canary takeover smoke")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--shadow-dir", default="/var/lib/netaiops-asset-agent/data/v3_intent_shadow")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    shadow_dir = Path(args.shadow_dir)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = now_tag()
    tests = [
        {
            "name": "allowed_canary_general_chat_negative_query_phrase",
            "user": "v3_3_16_takeover",
            "conversation_id": f"v3-3-16-takeover-{ts}-allowed-general-chat",
            "message": "只做文本解释，不要查询设备、不生成命令：解释一下 Cisco StackWise Virtual 是什么，以及它主要解决什么问题。",
            "expect_takeover": True,
            "expect_action": "general_chat",
        },
        {
            "name": "allowed_canary_advice_analysis",
            "user": "v3_3_16_takeover",
            "conversation_id": f"v3-3-16-takeover-{ts}-allowed-advice-analysis",
            "message": "是否建议在重启 standby 网络设备前先隔离流量？只给运维建议，不要生成命令。",
            "expect_takeover": True,
            "expect_action": "advice_analysis",
        },
        {
            "name": "blocked_wrong_user",
            "user": "normal_user",
            "conversation_id": f"v3-3-16-takeover-{ts}-blocked-user",
            "message": "只做文本解释，不要查询设备、不生成命令：解释一下 Cisco StackWise Virtual 是什么，以及它主要解决什么问题。",
            "expect_takeover": False,
        },
        {
            "name": "blocked_wrong_prefix",
            "user": "v3_3_16_takeover",
            "conversation_id": f"normal-prefix-{ts}-blocked-prefix",
            "message": "只做文本解释，不要查询设备、不生成命令：解释一下 Cisco StackWise Virtual 是什么，以及它主要解决什么问题。",
            "expect_takeover": False,
        },
        {
            "name": "blocked_missing_cmdb",
            "user": "v3_3_16_takeover",
            "conversation_id": f"v3-3-16-takeover-{ts}-missing-cmdb",
            "message": "查一下 V3-3-16-TAKEOVER-NONEXIST-DEVICE-001 的管理 IP 和设备类型",
            "expect_takeover": False,
        },
    ]

    shadow_file = today_shadow_file(shadow_dir)
    before_lines = count_lines(shadow_file)
    api_results: list[dict[str, Any]] = []

    for index, test in enumerate(tests, start=1):
        payload = {
            "user": test["user"],
            "conversation_id": test["conversation_id"],
            "message": test["message"],
            "question": test["message"],
        }
        payload_path = report_dir / f"payload_{index}_{test['name']}.json"
        response_path = report_dir / f"response_{index}_{test['name']}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        status, data, raw_text = post_json(f"{base_url}/api/v1/chat", payload, args.timeout)
        response_path.write_text(raw_text, encoding="utf-8")

        answer = str((data.get("answer") or data.get("message") or "") if isinstance(data, dict) else "")
        item = {
            "index": index,
            "name": test["name"],
            "conversation_id": test["conversation_id"],
            "user": test["user"],
            "http_status": status,
            "planner_source": data.get("planner_source") if isinstance(data, dict) else None,
            "v3_takeover": data.get("v3_takeover") if isinstance(data, dict) else None,
            "v3_takeover_mode": data.get("v3_takeover_mode") if isinstance(data, dict) else None,
            "v3_takeover_action": data.get("v3_takeover_action") if isinstance(data, dict) else None,
            "v3_takeover_source": data.get("v3_takeover_source") if isinstance(data, dict) else None,
            "v3_takeover_route_label": data.get("v3_takeover_route_label") if isinstance(data, dict) else None,
            "v3_takeover_error": data.get("v3_takeover_error") if isinstance(data, dict) else None,
            "answer_len": len(answer),
            "answer_prefix": answer[:260],
            "response_conversation_id": data.get("conversation_id") if isinstance(data, dict) else None,
            "response_path": str(response_path),
        }
        print(json.dumps(item, ensure_ascii=False))
        api_results.append(item)

        if status != 200:
            raise SystemExit(f"ERROR: API test {test['name']} returned HTTP {status}")

        actual_takeover = item["planner_source"] == "v3_response_generator" and item["v3_takeover"] is True
        expected_takeover = bool(test["expect_takeover"])
        if actual_takeover != expected_takeover:
            raise SystemExit(
                f"ERROR: takeover expectation mismatch for {test['name']}: expected={expected_takeover}, actual={actual_takeover}, item={item}"
            )
        if expected_takeover:
            if item["v3_takeover_mode"] != "canary":
                raise SystemExit(f"ERROR: expected canary takeover mode for {test['name']}: {item}")
            if item["v3_takeover_source"] != "llm":
                raise SystemExit(f"ERROR: expected llm takeover source for {test['name']}: {item}")
            expected_action = test.get("expect_action")
            if expected_action and item["v3_takeover_action"] != expected_action:
                raise SystemExit(f"ERROR: unexpected takeover action for {test['name']}: {item}")
            if item["answer_len"] < 40:
                raise SystemExit(f"ERROR: canary takeover answer too short for {test['name']}: {item}")
            if item["v3_takeover_error"]:
                raise SystemExit(f"ERROR: unexpected takeover error for {test['name']}: {item}")

        time.sleep(1)

    time.sleep(3)
    after_lines = count_lines(shadow_file)

    report = {
        "version": "v3.3.16",
        "purpose": "request-context canary real takeover smoke v2",
        "shadow_file": str(shadow_file),
        "before_shadow_lines": before_lines,
        "after_shadow_lines": after_lines,
        "api_results": api_results,
        "passed": True,
    }
    (report_dir / "v3_3_16_request_context_takeover_canary_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print("v3_3_16_request_context_allowed_general_chat=OK")
    print("v3_3_16_request_context_allowed_advice_analysis=OK")
    print("v3_3_16_request_context_blocked_user=OK")
    print("v3_3_16_request_context_blocked_prefix=OK")
    print("v3_3_16_request_context_blocked_missing_cmdb=OK")
    print("v3_3_16_request_context_takeover_summary=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
