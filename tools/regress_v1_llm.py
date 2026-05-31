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


def get_json(base: str, path: str, timeout: int = 120) -> dict[str, Any]:
    r = requests.get(base.rstrip("/") + path, timeout=timeout)
    if r.status_code >= 400:
        fail(f"GET {path} HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def post_json(base: str, path: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    r = requests.post(base.rstrip("/") + path, json=payload, timeout=timeout)
    if r.status_code >= 400:
        fail(f"POST {path} HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    cfg = get_json(base, "/api/v1/llm/config")
    if cfg.get("status") != "ok":
        fail(f"llm config status bad: {cfg}")
    llm = cfg.get("llm", {})
    if not llm.get("enabled"):
        fail(f"llm not enabled: {llm}")
    if not llm.get("api_key_configured"):
        fail(f"llm api key not configured: {llm}")
    ok("llm config")

    models = get_json(base, "/api/v1/llm/models")
    if models.get("status") == "ok":
        ok("llm models")
    else:
        print(f"[WARN] llm models endpoint not ok: {models.get('error_code')} {models.get('message')}")

    probe = get_json(base, "/api/v1/llm/probe")
    if probe.get("status") != "ok":
        fail(f"llm probe failed: {probe}")
    ok("llm probe")

    parse = post_json(base, "/api/v1/llm/parse", {
        "user": "regress",
        "question": "请帮我查一下SH16机房H03机柜里的网络设备，返回主机名、管理IP、序列号、设备型号和状态",
    })

    if parse.get("status") != "ok":
        fail(f"llm parse failed: {parse}")

    parsed = parse.get("parsed") or {}
    if parsed.get("intent") != "query_devices":
        fail(f"llm parsed intent unexpected: {parsed}")

    filters = parsed.get("filters") or {}
    if filters.get("IDC__icontains") != "SH16" or filters.get("rack__icontains") != "H03":
        fail(f"llm filters unexpected: {filters}")

    ok("llm parse to tool plan")

    chat = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "limit": 5,
        "question": "请帮我查一下SH16机房H03机柜里的网络设备，返回主机名、管理IP、序列号、设备型号和状态",
    })

    if chat.get("status") != "ok":
        fail(f"chat failed: {chat}")

    if int(chat.get("returned") or 0) <= 0:
        fail(f"chat returned no rows: {chat}")

    ok("chat returns CMDB data")
    print("[DONE] V1 LLM regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
