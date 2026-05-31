from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from netaiops_asset.agent.conversation_store import get_conversation


FILTER_TO_QUERY = {
    "search": "search",
    "IDC__icontains": "IDC",
    "server_room__icontains": "server_room",
    "rack__icontains": "rack",
    "host_name__icontains": "host_name",
    "mgmt_ip": "mgmt_ip",
    "mgmt_ip__in": "mgmt_ip__in",
    "sn__icontains": "sn",
    "ci_type__icontains": "ci_type",
    "manufacturer__icontains": "manufacturer",
    "band__icontains": "band",
    "device_spec__icontains": "device_spec",
    "os_version__icontains": "os_version",
    "env": "env",
    "status__icontains": "status",
    "tag__icontains": "tag",
    "maintenance_manufacturer__icontains": "maintenance_manufacturer",
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def detect_conversation_action(question: str) -> str | None:
    q = _compact(question)

    export_keywords = [
        "导出",
        "下载",
        "生成excel",
        "生成xlsx",
        "excel",
        "xlsx",
        "表格",
    ]

    result_keywords = [
        "刚才",
        "上次",
        "上一条",
        "当前结果",
        "这个结果",
        "查询结果",
        "结果",
        "这批",
        "这些",
    ]

    if any(k in q for k in export_keywords):
        if any(k in q for k in result_keywords):
            return "export_last_result"

        # 用户只说“生成Excel / 导出Excel”时，也按最近一次结果处理。
        if q in {"生成excel", "导出excel", "下载excel", "生成xlsx", "导出xlsx", "下载xlsx"}:
            return "export_last_result"

    return None


def _latest_query_turn(conversation_id: str | None) -> dict[str, Any] | None:
    if not conversation_id:
        return None

    conv = get_conversation(conversation_id)
    if not conv:
        return None

    turns = conv.get("turns", [])
    if not isinstance(turns, list):
        return None

    for turn in reversed(turns):
        response = turn.get("response") or {}
        parsed = response.get("parsed") or {}

        if response.get("status") != "ok":
            continue

        if response.get("action") in {"export_last_result"}:
            continue

        if parsed.get("intent") in {"query_devices", "query_device_detail"}:
            if int(response.get("count") or 0) > 0:
                return turn

    return None


def _build_export_params_from_response(response: dict[str, Any]) -> dict[str, Any]:
    parsed = response.get("parsed") or {}
    params: dict[str, Any] = {}

    if parsed.get("intent") == "query_device_detail":
        keyword = str(parsed.get("keyword") or "").strip()
        if keyword:
            if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", keyword):
                params["mgmt_ip"] = keyword
            else:
                params["search"] = keyword

    filters = parsed.get("filters") or {}
    if isinstance(filters, dict):
        for key, value in filters.items():
            query_key = FILTER_TO_QUERY.get(str(key))
            if query_key and value not in (None, ""):
                params[query_key] = str(value)

    fields = response.get("columns") or parsed.get("fields") or []
    if isinstance(fields, list) and fields:
        params["fields"] = ",".join(str(x) for x in fields if str(x).strip())
    elif isinstance(fields, str) and fields.strip():
        params["fields"] = fields.strip()

    try:
        count = int(response.get("count") or response.get("returned") or 20)
    except Exception:
        count = 20

    params["pageSize"] = str(max(1, min(count, 500)))
    return params


def handle_conversation_action(question: str, conversation_id: str | None) -> dict[str, Any] | None:
    action = detect_conversation_action(question)
    if not action:
        return None

    if action == "export_last_result":
        turn = _latest_query_turn(conversation_id)
        if not turn:
            return {
                "status": "need_clarification",
                "action": "export_last_result",
                "answer": "当前对话里还没有可导出的查询结果。请先完成一次设备查询，再说“导出刚才结果 Excel”。",
                "items": [],
                "columns": [],
                "count": 0,
                "returned": 0,
                "export_url": None,
                "export_params": {},
            }

        response = turn.get("response") or {}
        params = _build_export_params_from_response(response)
        query = urlencode(params, doseq=False)
        export_url = "/api/v1/cmdb/devices/export.xlsx"
        if query:
            export_url = f"{export_url}?{query}"

        title = turn.get("question") or "上一次查询"
        count = int(response.get("count") or 0)
        returned = int(response.get("returned") or 0)

        return {
            "status": "ok",
            "action": "export_last_result",
            "answer": (
                f"已根据上一次查询结果生成 Excel 下载链接。\n"
                f"原查询：{title}\n"
                f"匹配总数：{count}，上次页面返回：{returned}。\n"
                f"单次 Excel 最多导出前 500 条。"
            ),
            "items": [],
            "columns": [],
            "field_labels": {},
            "count": count,
            "returned": 0,
            "export_url": export_url,
            "export_params": params,
            "source_turn_id": turn.get("turn_id"),
        }

    return None
