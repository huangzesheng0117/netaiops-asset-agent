# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.response_generator import (
    build_frontend_response,
    generate_v3_response,
)


def assert_case(name: str, condition: bool, payload: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{name} failed: {payload}")
    print(f"{name}=OK")


def gate() -> Dict[str, Any]:
    return {
        "enabled": True,
        "eligible": True,
        "takeover": True,
        "reason": "eligible",
        "effective_confidence": 0.95,
    }


def env_presence_report() -> Dict[str, Any]:
    keys = [
        "NETAIOPS_LLM_API_KEY",
        "NETAIOPS_LLM_BASE_URL",
        "NETAIOPS_LLM_MODEL",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
    ]
    return {
        "present_keys": [key for key in keys if os.getenv(key)],
        "api_key_length": len(os.getenv("NETAIOPS_LLM_API_KEY") or ""),
        "base_url_present": bool(os.getenv("NETAIOPS_LLM_BASE_URL")),
        "model_present": bool(os.getenv("NETAIOPS_LLM_MODEL")),
    }


def quality_check(answer: str, *, min_len: int = 40) -> Dict[str, Any]:
    answer = str(answer or "").strip()
    lowered = answer.lower()
    issues: List[str] = []

    if len(answer) < min_len:
        issues.append("too_short")
    if answer.startswith("{") and answer.endswith("}"):
        issues.append("looks_like_json")
    if "```json" in lowered:
        issues.append("contains_json_fence")
    if "执行以下命令" in answer or "reload" in lowered or "reboot" in lowered:
        issues.append("contains_execution_or_danger_hint")
    if "无法回答" in answer and len(answer) < 80:
        issues.append("generic_unable_to_answer")

    return {
        "ok": not issues,
        "issues": issues,
        "length": len(answer),
        "prefix": answer[:260],
    }


def main() -> int:
    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    env_report = env_presence_report()
    print("llm_env_presence=", json.dumps(env_report, ensure_ascii=False))

    if not os.getenv("NETAIOPS_LLM_API_KEY"):
        raise SystemExit("ERROR: NETAIOPS_LLM_API_KEY is still empty after env loading")

    cases = [
        {
            "name": "general_chat_live_llm",
            "question": "只做文本解释，不要查询设备、不生成命令：解释一下 Cisco StackWise Virtual 是什么，以及它主要解决什么问题。",
            "plan": {
                "action": "general_chat",
                "handler_key": "general_chat",
                "response_mode": "chat",
                "reason": "内部判断：用户要通用概念解释，不需要查询设备。",
                "confidence": 0.95,
                "effective_confidence": 0.95,
            },
            "min_len": 60,
        },
        {
            "name": "advice_analysis_live_llm",
            "question": "是否建议在重启 standby 网络设备前先隔离流量？只给运维建议，不要生成命令。",
            "plan": {
                "action": "advice_analysis",
                "handler_key": "advice_analysis",
                "response_mode": "advice",
                "reason": "内部判断：用户需要维护操作建议，不需要执行设备命令。",
                "confidence": 0.95,
                "effective_confidence": 0.95,
            },
            "min_len": 60,
        },
    ]

    results: List[Dict[str, Any]] = []

    for item in cases:
        print(f"===== live_llm_case={item['name']} =====")
        started = time.time()
        generated = generate_v3_response(
            question=item["question"],
            conversation_id=f"v3-3-13-fix-{item['name']}",
            plan=item["plan"],
            gate=gate(),
            allow_live_llm=True,
            llm_timeout=90,
        )
        elapsed = round(time.time() - started, 3)
        data = generated.as_dict()
        qc = quality_check(data.get("answer") or "", min_len=int(item["min_len"]))

        response = None
        response_error = ""
        try:
            response = build_frontend_response(
                question=item["question"],
                conversation_id=f"v3-3-13-fix-{item['name']}",
                generated=generated,
            )
        except Exception as exc:
            response_error = repr(exc)

        row = {
            "name": item["name"],
            "elapsed_seconds": elapsed,
            "generated": data,
            "quality": qc,
            "frontend_response_keys": sorted(response.keys()) if isinstance(response, dict) else [],
            "frontend_status": response.get("status") if isinstance(response, dict) else None,
            "frontend_planner_source": response.get("planner_source") if isinstance(response, dict) else None,
            "frontend_answer_prefix": str(response.get("answer") or "")[:260] if isinstance(response, dict) else "",
            "response_error": response_error,
        }
        print(json.dumps(row, ensure_ascii=False, indent=2))

        assert_case(f"{item['name']}_generated_ready", data.get("ready") is True, data)
        assert_case(f"{item['name']}_generated_source_llm", data.get("source") == "llm", data)
        assert_case(f"{item['name']}_llm_status_ok", data.get("llm_status") == "ok", data)
        assert_case(f"{item['name']}_quality_ok", qc.get("ok") is True, qc)
        assert_case(
            f"{item['name']}_frontend_contract_ok",
            isinstance(response, dict) and response.get("planner_source") == "v3_response_generator",
            response or response_error,
        )

        results.append(row)

    out = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "env_presence": env_report,
        "case_count": len(cases),
        "all_ready": all(row["generated"].get("ready") is True for row in results),
        "all_quality_ok": all(row["quality"].get("ok") is True for row in results),
        "results": results,
        "notes": [
            "This script calls the real local LLM through response_generator.",
            "It does not call the chat endpoint.",
            "It does not restart service or modify app.py.",
        ],
    }

    output_path = report_dir / "v3_3_response_generator_live_llm_regression.json"
    output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(f"json_report={output_path}")
    print("smoke_v3_response_generator_live_llm=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
