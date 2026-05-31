from __future__ import annotations

from typing import Any

from netaiops_asset.cmdb.adapter import CMDBAdapter
from netaiops_asset.cmdb.field_map import DETAIL_FIELDS, DEFAULT_FIELDS, field_labels, normalize_fields
from netaiops_asset.tools.errors import exception_error, normalize_tool_result, standard_error


CMDB_TOOL_CATALOG = [
    {
        "name": "query_cmdb_devices",
        "description": "按 IDC、机房、机架、厂商、型号、状态等条件查询基金 CMDB 网络设备。",
        "input_schema": {
            "filters": "dict，例如 {'IDC__icontains':'SH16','rack__icontains':'H03'}",
            "fields": "list[str] 或逗号分隔字符串，可选返回字段",
            "page": "页码，默认 1",
            "page_size": "返回条数，普通查询最大 100",
        },
        "output_schema": {
            "status": "ok/error",
            "count": "总匹配数",
            "returned": "本次返回数",
            "items": "结构化设备列表",
        },
        "safety": "只读查询，不允许写入、修改、删除。",
    },
    {
        "name": "query_cmdb_device_detail",
        "description": "按管理 IP、主机名、序列号或 EM 码查询单台网络设备详情。",
        "input_schema": {
            "keyword": "管理 IP、主机名、序列号或 EM 码",
            "fields": "list[str] 或逗号分隔字符串，可选返回字段",
        },
        "output_schema": {
            "status": "ok/error",
            "count": "总匹配数",
            "returned": "本次返回数",
            "items": "结构化设备详情列表",
        },
        "safety": "只读查询，不允许写入、修改、删除。",
    },
    {
        "name": "query_cmdb_devices_by_ips",
        "description": "按多个管理 IP 批量查询网络设备。",
        "input_schema": {
            "ips": "list[str]，管理 IP 列表",
            "fields": "list[str] 或逗号分隔字符串，可选返回字段",
        },
        "output_schema": {
            "status": "ok/error",
            "count": "总匹配数",
            "returned": "本次返回数",
            "items": "结构化设备列表",
        },
        "safety": "只读查询，不允许写入、修改、删除。",
    },
]


def _safe_page_size(value: int | None, default: int = 20, maximum: int = 100) -> int:
    try:
        n = int(value or default)
    except Exception:
        n = default
    return max(1, min(n, maximum))


def tool_query_cmdb_devices(
    filters: dict[str, Any] | None = None,
    fields: list[str] | str | None = None,
    page: int = 1,
    page_size: int | None = 20,
) -> dict[str, Any]:
    tool_name = "query_cmdb_devices"
    try:
        filters = filters or {}
        if not isinstance(filters, dict):
            return standard_error(
                error_code="TOOL_BAD_REQUEST",
                message="filters must be a dict",
                detail={"filters": filters},
            )

        selected_fields = normalize_fields(fields, DEFAULT_FIELDS)
        adapter = CMDBAdapter()
        result = adapter.query_devices(
            filters=filters,
            fields=selected_fields,
            page=max(1, int(page or 1)),
            page_size=_safe_page_size(page_size, default=20, maximum=100),
        )
        result = normalize_tool_result(result, tool_name)
        result["tool_name"] = tool_name
        result["readonly"] = True
        result["field_labels"] = field_labels()
        return result

    except Exception as exc:
        err = exception_error(exc)
        err["tool_name"] = tool_name
        return err


def tool_query_cmdb_device_detail(
    keyword: str,
    fields: list[str] | str | None = None,
) -> dict[str, Any]:
    tool_name = "query_cmdb_device_detail"
    try:
        keyword = str(keyword or "").strip()
        if not keyword:
            return standard_error(
                error_code="TOOL_BAD_REQUEST",
                message="keyword is required",
            )

        selected_fields = normalize_fields(fields, DETAIL_FIELDS)
        adapter = CMDBAdapter()
        result = adapter.query_device_detail(keyword=keyword, fields=selected_fields)
        result = normalize_tool_result(result, tool_name)
        result["tool_name"] = tool_name
        result["readonly"] = True
        result["field_labels"] = field_labels()
        return result

    except Exception as exc:
        err = exception_error(exc)
        err["tool_name"] = tool_name
        return err


def tool_query_cmdb_devices_by_ips(
    ips: list[str],
    fields: list[str] | str | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    tool_name = "query_cmdb_devices_by_ips"
    try:
        if not isinstance(ips, list):
            return standard_error(
                error_code="TOOL_BAD_REQUEST",
                message="ips must be a list",
                detail={"ips": ips},
            )

        ip_list: list[str] = []
        for ip in ips:
            item = str(ip or "").strip()
            if item and item not in ip_list:
                ip_list.append(item)

        if not ip_list:
            return standard_error(
                error_code="TOOL_BAD_REQUEST",
                message="ips is empty",
            )

        selected_fields = normalize_fields(fields, DEFAULT_FIELDS)
        adapter = CMDBAdapter()
        result = adapter.query_devices(
            filters={"mgmt_ip__in": ",".join(ip_list)},
            fields=selected_fields,
            page=1,
            page_size=_safe_page_size(page_size, default=len(ip_list), maximum=100),
        )
        result = normalize_tool_result(result, tool_name)
        result["tool_name"] = tool_name
        result["readonly"] = True
        result["field_labels"] = field_labels()
        return result

    except Exception as exc:
        err = exception_error(exc)
        err["tool_name"] = tool_name
        return err
