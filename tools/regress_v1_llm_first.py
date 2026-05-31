#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

import requests


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"[PASS] {msg}")


def post_json(base: str, path: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    r = requests.post(base.rstrip("/") + path, json=payload, timeout=timeout)
    if r.status_code >= 400:
        fail(f"POST {path} HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def get_json(base: str, path: str, timeout: int = 120) -> dict[str, Any]:
    r = requests.get(base.rstrip("/") + path, timeout=timeout)
    if r.status_code >= 400:
        fail(f"GET {path} HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    health = get_json(base, "/health")
    if health.get("status") != "ok":
        fail(f"health not ok: {health}")
    ok("health")

    # 不传 planner_mode，验证默认也走 LLM。
    q1 = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "debug": True,
        "limit": 5,
        "question": "请帮我查一下SH16机房H03机柜里的网络设备，返回主机名、管理IP、序列号、设备型号和状态",
    })

    if q1.get("status") != "ok":
        fail(f"default chat failed: {q1}")

    if q1.get("planner_source") != "llm_tool_planner":
        fail(f"default chat should use llm_tool_planner, got {q1.get('planner_source')}: {q1}")

    if int(q1.get("returned") or 0) <= 0:
        fail(f"default llm chat returned no rows: {q1}")

    ok("default chat uses LLM planner")

    # 验证失败案例：完整主机名不应只按 IDC=SH8 查询。
    q2 = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "debug": True,
        "limit": 20,
        "question": "设备SH8-H05-INT-CON-SW01的管理IP是多少？",
    })

    if q2.get("planner_source") != "llm_tool_planner":
        fail(f"hostname query should use llm_tool_planner, got {q2.get('planner_source')}: {q2}")

    parsed = q2.get("parsed") or {}
    filters = parsed.get("filters") or {}

    host_filter = filters.get("host_name__icontains")
    detail_keyword = parsed.get("keyword")

    if not host_filter and not detail_keyword:
        fail(f"hostname query did not produce host_name filter or keyword: {parsed}")

    if filters.get("IDC__icontains") == "SH8" and not host_filter:
        fail(f"hostname query still mis-parsed as only IDC=SH8: {parsed}")

    ok("hostname query no longer falls back to IDC-only rule parse")

    print("[DONE] V1 LLM-first regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
