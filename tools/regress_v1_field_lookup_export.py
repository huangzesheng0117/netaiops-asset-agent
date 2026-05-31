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


def assert_xlsx(base: str, url: str, label: str) -> None:
    r = requests.get(base.rstrip("/") + url, timeout=120)
    if r.status_code != 200:
        fail(f"{label} export HTTP {r.status_code}: {r.text[:500]}")
    if not r.content.startswith(b"PK"):
        fail(f"{label} export is not xlsx zip")
    ok(label)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    health = get_json(base, "/health")
    if health.get("status") != "ok":
        fail(f"health not ok: {health}")
    ok("health")

    em_parse = post_json(base, "/api/v1/llm/parse", {
        "user": "regress",
        "question": "EM码为EM06027的设备主机名是什么？",
    })
    parsed = em_parse.get("parsed") or {}
    filters = parsed.get("filters") or {}
    if parsed.get("intent") != "query_devices":
        fail(f"EM parse intent unexpected: {parsed}")
    if "server_ID__icontains" not in filters and "search" not in filters:
        fail(f"EM parse did not use server_ID__icontains or search: {parsed}")
    ok("EM field parsed as query condition")

    sn_chat = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "debug": True,
        "limit": 5,
        "question": "设备序列号为FDO24130P9S的设备，主机名和管理IP分别是多少？",
    })
    if sn_chat.get("status") != "ok":
        fail(f"SN chat failed: {sn_chat}")
    if int(sn_chat.get("returned") or 0) <= 0:
        fail(f"SN chat returned no rows: {sn_chat}")
    ok("SN lookup returned rows")

    host_chat = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "debug": True,
        "limit": 5,
        "question": "WG88-SW-H19-1这台设备的用途是什么？操作系统是什么？",
    })
    if host_chat.get("status") != "ok":
        fail(f"hostname chat failed: {host_chat}")
    if int(host_chat.get("returned") or 0) <= 0:
        fail(f"hostname chat returned no rows: {host_chat}")
    ok("hostname lookup returned rows")

    assert_xlsx(
        base,
        "/api/v1/cmdb/devices/export.xlsx?sn__icontains=FDO24130P9S&fields=host_name,mgmt_ip,sn&pageSize=20",
        "direct xlsx export by sn__icontains",
    )

    create = post_json(base, "/api/v1/conversations", {
        "user": "regress",
        "title": "regress-export-fix",
    })
    cid = create.get("conversation", {}).get("conversation_id")
    if not cid:
        fail(f"conversation_id missing: {create}")

    q = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "conversation_id": cid,
        "limit": 5,
        "question": "设备序列号为FDO24130P9S的设备，主机名和管理IP分别是多少？",
    })
    if q.get("status") != "ok" or int(q.get("returned") or 0) <= 0:
        fail(f"conversation query failed: {q}")

    action = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "conversation_id": cid,
        "question": "导出刚才结果Excel",
    })
    export_url = action.get("export_url")
    if action.get("status") != "ok" or not export_url:
        fail(f"conversation export action failed: {action}")
    assert_xlsx(base, export_url, "conversation export xlsx")

    requests.delete(base + f"/api/v1/conversations/{cid}", timeout=60)

    print("[DONE] V1 generic field lookup and export regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
