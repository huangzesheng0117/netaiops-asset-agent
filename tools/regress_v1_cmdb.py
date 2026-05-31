#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from typing import Any

import requests


class RegressError(RuntimeError):
    pass


def ok(msg: str) -> None:
    print(f"[PASS] {msg}")


def fail(msg: str) -> None:
    raise RegressError(msg)


def get_json(base: str, path: str, timeout: int = 20) -> dict[str, Any]:
    url = base.rstrip("/") + path
    r = requests.get(url, timeout=timeout)
    if r.status_code >= 400:
        fail(f"GET {path} returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception as exc:
        fail(f"GET {path} returned non-json: {exc}")
    return {}


def post_json(base: str, path: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    url = base.rstrip("/") + path
    r = requests.post(url, json=payload, timeout=timeout)
    if r.status_code >= 400:
        fail(f"POST {path} returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception as exc:
        fail(f"POST {path} returned non-json: {exc}")
    return {}


def delete_json(base: str, path: str, timeout: int = 20) -> dict[str, Any]:
    url = base.rstrip("/") + path
    r = requests.delete(url, timeout=timeout)
    if r.status_code >= 400:
        fail(f"DELETE {path} returned HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def assert_status_ok(data: dict[str, Any], label: str) -> None:
    if data.get("status") != "ok":
        fail(f"{label} status not ok: {data}")
    ok(label)


def main() -> int:
    parser = argparse.ArgumentParser(description="Regress V1 CMDB asset query platform")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--known-ip", default="10.189.250.8")
    parser.add_argument("--idc", default="SH16")
    parser.add_argument("--rack", default="H03")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    known_ip = args.known_ip
    idc = args.idc
    rack = args.rack

    health = get_json(base, "/health")
    assert_status_ok(health, "health")

    selfcheck = get_json(base, "/api/v1/selfcheck")
    if selfcheck.get("status") not in ("ok", "warn"):
        fail(f"selfcheck bad status: {selfcheck}")
    checks = selfcheck.get("checks", {})
    if not checks.get("token_configured") or not checks.get("cmdb_api_reachable"):
        fail(f"selfcheck failed critical checks: {checks}")
    ok("selfcheck critical checks")

    catalog = get_json(base, "/api/v1/tools/catalog")
    assert_status_ok(catalog, "tools catalog")
    if int(catalog.get("count") or 0) < 3:
        fail(f"tools catalog count too small: {catalog}")
    ok("tools catalog count")

    tool_query = post_json(base, "/api/v1/tools/cmdb/query", {
        "user": "regress",
        "filters": {"IDC__icontains": idc, "rack__icontains": rack},
        "fields": ["host_name", "mgmt_ip", "sn", "device_spec", "status", "IDC", "server_room", "rack"],
        "page_size": 5,
    })
    assert_status_ok(tool_query, "tool query cmdb devices")
    if int(tool_query.get("count") or 0) <= 0:
        fail(f"tool query returned no rows for {idc}/{rack}: {tool_query}")
    ok("tool query returned rows")

    tool_detail = post_json(base, "/api/v1/tools/cmdb/detail", {
        "user": "regress",
        "keyword": known_ip,
        "fields": ["host_name", "mgmt_ip", "sn", "device_spec", "status", "IDC", "server_room", "rack"],
    })
    assert_status_ok(tool_detail, "tool detail")
    if int(tool_detail.get("returned") or 0) <= 0:
        fail(f"tool detail returned no rows for {known_ip}: {tool_detail}")
    ok("tool detail returned rows")

    chat_room = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "limit": 10,
        "question": f"{idc}机房的{rack}机柜有哪些设备？",
    })
    assert_status_ok(chat_room, "chat SHxx room/rack")
    filters = chat_room.get("parsed", {}).get("filters", {})
    bad_room = filters.get("server_room__icontains") in ("H16", "H08", "H03")
    if bad_room and idc.upper().startswith("SH"):
        fail(f"SHxx was mis-parsed as server_room: {filters}")
    if int(chat_room.get("count") or 0) <= 0:
        fail(f"chat room/rack returned no rows: {chat_room}")
    ok("chat SHxx room/rack parse and result")

    chat_ip = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "limit": 5,
        "question": f"{known_ip}是哪台设备，主机名、序列号、型号、状态、IDC、机房、机架是什么？",
    })
    assert_status_ok(chat_ip, "chat IP detail")
    if int(chat_ip.get("returned") or 0) <= 0:
        fail(f"chat IP returned no rows: {chat_ip}")
    ok("chat IP returned rows")

    create = post_json(base, "/api/v1/conversations", {
        "user": "regress",
        "title": "regress-temp-conversation",
    })
    assert_status_ok(create, "create conversation")
    cid = create.get("conversation", {}).get("conversation_id")
    if not cid:
        fail(f"conversation_id missing: {create}")

    chat_conv = post_json(base, "/api/v1/chat", {
        "user": "regress",
        "limit": 3,
        "conversation_id": cid,
        "question": f"{known_ip}是哪台设备？",
    })
    assert_status_ok(chat_conv, "append chat to conversation")
    if chat_conv.get("conversation_id") != cid:
        fail(f"conversation_id mismatch: {chat_conv.get('conversation_id')} != {cid}")

    conv = get_json(base, f"/api/v1/conversations/{cid}")
    assert_status_ok(conv, "get conversation")
    if len(conv.get("conversation", {}).get("turns", [])) < 1:
        fail(f"conversation turns missing: {conv}")
    ok("conversation turn persisted")

    conv_list = get_json(base, "/api/v1/conversations?limit=5")
    assert_status_ok(conv_list, "list conversations")

    deleted = delete_json(base, f"/api/v1/conversations/{cid}")
    if not deleted.get("deleted"):
        fail(f"delete conversation failed: {deleted}")
    ok("delete conversation")

    xlsx_url = (
        f"{base}/api/v1/cmdb/devices/export.xlsx"
        f"?mgmt_ip={known_ip}&fields=host_name,mgmt_ip,sn,device_spec,status,IDC,server_room,rack&pageSize=20"
    )
    r = requests.get(xlsx_url, timeout=30)
    if r.status_code != 200:
        fail(f"xlsx export HTTP {r.status_code}: {r.text[:300]}")
    if not r.content.startswith(b"PK"):
        fail("xlsx export does not look like xlsx zip content")
    ok("xlsx export")

    print("[DONE] V1 CMDB regression passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RegressError as exc:
        print(f"[FAIL] {exc}")
        raise SystemExit(1)
