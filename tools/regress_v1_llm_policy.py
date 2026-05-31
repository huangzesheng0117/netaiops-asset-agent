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

    compare = post_json(base, "/api/v1/llm/compare", {
        "user": "regress",
        "planner_mode": "llm",
        "question": "请帮我查一下SH16机房H03机柜里的网络设备，返回主机名、管理IP、序列号、设备型号和状态",
    })

    if compare.get("status") != "ok":
        fail(f"compare failed: {compare}")

    selected = compare.get("selected_parsed") or {}
    if selected.get("intent") != "query_devices":
        fail(f"compare selected intent unexpected: {selected}")

    filters = selected.get("filters") or {}
    if filters.get("IDC__icontains") != "SH16" or filters.get("rack__icontains") != "H03":
        fail(f"compare filters unexpected: {filters}")

    if compare.get("planner_source") != "llm_tool_planner":
        fail(f"compare did not select llm planner: {compare.get('planner_source')}")

    ok("llm compare forced mode")

    chat_llm = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "planner_mode": "llm",
        "debug": True,
        "limit": 5,
        "question": "请帮我查一下SH16机房H03机柜里的网络设备，返回主机名、管理IP、序列号、设备型号和状态",
    })

    if chat_llm.get("status") != "ok":
        fail(f"chat forced llm failed: {chat_llm}")

    if chat_llm.get("planner_source") != "llm_tool_planner":
        fail(f"chat did not use llm planner: {chat_llm.get('planner_source')}")

    if int(chat_llm.get("returned") or 0) <= 0:
        fail(f"chat forced llm returned no rows: {chat_llm}")

    ok("chat forced llm planner")

    chat_rule = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "planner_mode": "auto",
        "debug": True,
        "limit": 5,
        "question": "10.189.250.8是哪台设备，主机名、序列号、型号、状态、IDC、机房、机架是什么？",
    })

    if chat_rule.get("status") != "ok":
        fail(f"chat exact ip failed: {chat_rule}")

    if chat_rule.get("planner_source") != "rule_parser":
        fail(f"exact IP should keep rule parser, got: {chat_rule.get('planner_source')}")

    if int(chat_rule.get("returned") or 0) <= 0:
        fail(f"chat exact ip returned no rows: {chat_rule}")

    ok("exact IP keeps rule parser")

    print("[DONE] V1 LLM planner policy regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
