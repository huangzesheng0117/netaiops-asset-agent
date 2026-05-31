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


def assert_rows_are_production_g(rows: list[dict[str, Any]]) -> None:
    if not rows:
        fail("no rows returned")

    bad = []
    for row in rows:
        env = str(row.get("env") or "")
        rack = str(row.get("rack") or "")
        idc = str(row.get("IDC") or "")
        if "生产" not in env or "G" not in rack.upper() or "SH8" not in idc.upper():
            bad.append(row)

    if bad:
        fail(f"found rows not matching SH8 + rack G + env production: {bad[:5]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    health = get_json(base, "/health")
    if health.get("status") != "ok":
        fail(f"health not ok: {health}")
    ok("health")

    direct = post_json(base, "/api/v1/tools/cmdb/query", {
        "user": "regress",
        "filters": {
            "IDC__icontains": "SH8",
            "rack__icontains": "G",
            "env__icontains": "生产"
        },
        "fields": ["host_name", "mgmt_ip", "ci_type", "env", "IDC", "rack"],
        "page_size": 20
    })

    if direct.get("status") != "ok":
        fail(f"direct tool query failed: {direct}")
    assert_rows_are_production_g(direct.get("items") or [])
    ok("direct CMDB tool local filter")

    chat = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "debug": True,
        "limit": 20,
        "question": "SH8机房G排机柜，生产网的设备有哪些？"
    })

    if chat.get("status") != "ok":
        fail(f"chat query failed: {chat}")

    parsed = chat.get("parsed") or {}
    filters = parsed.get("filters") or {}
    if filters.get("env__icontains") not in {"生产", "生产网"}:
        fail(f"LLM did not keep production env filter: {parsed}")

    assert_rows_are_production_g(chat.get("items") or [])
    ok("chat query local filter")

    print("[DONE] V1 local filter regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
