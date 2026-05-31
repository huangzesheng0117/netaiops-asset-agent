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


def get_json(base: str, path: str, timeout: int = 60) -> dict[str, Any]:
    r = requests.get(base.rstrip("/") + path, timeout=timeout)
    if r.status_code >= 400:
        fail(f"GET {path} HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def post_json(base: str, path: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    r = requests.post(base.rstrip("/") + path, json=payload, timeout=timeout)
    if r.status_code >= 400:
        fail(f"POST {path} HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def delete_json(base: str, path: str, timeout: int = 60) -> dict[str, Any]:
    r = requests.delete(base.rstrip("/") + path, timeout=timeout)
    if r.status_code >= 400:
        fail(f"DELETE {path} HTTP {r.status_code}: {r.text[:500]}")
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

    create = post_json(base, "/api/v1/conversations", {
        "user": "regress",
        "title": "regress-export-action",
    })
    cid = create.get("conversation", {}).get("conversation_id")
    if not cid:
        fail(f"conversation_id missing: {create}")
    ok("create conversation")

    query = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "conversation_id": cid,
        "planner_mode": "auto",
        "limit": 5,
        "question": "SH16机房的H03机柜有哪些设备，返回主机名、管理IP、序列号、设备型号和状态",
    })
    if query.get("status") != "ok":
        fail(f"query failed: {query}")
    if int(query.get("returned") or 0) <= 0:
        fail(f"query returned no rows: {query}")
    ok("initial query")

    action = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "conversation_id": cid,
        "question": "把刚才结果生成Excel",
    })
    if action.get("status") != "ok":
        fail(f"export action failed: {action}")
    if action.get("action") != "export_last_result":
        fail(f"unexpected action: {action}")
    export_url = action.get("export_url")
    if not export_url:
        fail(f"export_url missing: {action}")
    ok("export action generated url")

    r = requests.get(base + export_url, timeout=120)
    if r.status_code != 200:
        fail(f"download export HTTP {r.status_code}: {r.text[:500]}")
    if not r.content.startswith(b"PK"):
        fail("export content is not xlsx zip")
    ok("download generated Excel")

    conv = get_json(base, f"/api/v1/conversations/{cid}")
    turns = conv.get("conversation", {}).get("turns", [])
    if len(turns) < 2:
        fail(f"conversation should contain query + action turns: {conv}")
    if not turns[-1].get("response", {}).get("export_url"):
        fail(f"export_url not persisted in conversation: {turns[-1]}")
    ok("conversation persisted export action")

    deleted = delete_json(base, f"/api/v1/conversations/{cid}")
    if not deleted.get("deleted"):
        fail(f"delete conversation failed: {deleted}")
    ok("delete test conversation")

    print("[DONE] V1 conversation actions regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
