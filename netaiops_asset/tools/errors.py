from __future__ import annotations

import traceback
from typing import Any


DEFAULT_SUGGESTIONS = {
    "CMDB_TIMEOUT": "CMDB 接口请求超时，请稍后重试，或缩小查询条件。",
    "CMDB_HTTP_ERROR": "CMDB 接口返回异常，请检查 Token、网络访问策略或接口状态。",
    "CMDB_EMPTY_TOKEN": "CMDB Token 未配置，请检查环境变量 NETAIOPS_CMDB_API_TOKEN。",
    "CMDB_EMPTY_BASE_URL": "CMDB API Base URL 未配置，请检查 config.yaml。",
    "TOOL_BAD_REQUEST": "工具入参不合法，请检查查询条件和字段名称。",
    "TOOL_EXCEPTION": "工具执行异常，请查看服务日志。",
    "NO_RESULT": "未查询到结果，请确认查询条件是否正确。",
}


def standard_error(
    error_code: str,
    message: str,
    suggestion: str | None = None,
    detail: Any | None = None,
    http_status: int | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "suggestion": suggestion or DEFAULT_SUGGESTIONS.get(error_code, "请查看日志或联系平台维护人员。"),
        "http_status": http_status,
        "request_id": request_id,
        "detail": detail,
        "count": 0,
        "returned": 0,
        "items": [],
    }


def exception_error(
    exc: Exception,
    error_code: str = "TOOL_EXCEPTION",
    message: str = "工具执行异常",
    include_traceback: bool = False,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
    }
    if include_traceback:
        detail["traceback"] = traceback.format_exc()

    return standard_error(
        error_code=error_code,
        message=message,
        detail=detail,
    )


def normalize_tool_result(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    if result.get("status") == "ok":
        result.setdefault("tool_name", tool_name)
        result.setdefault("error_code", None)
        result.setdefault("message", "")
        result.setdefault("suggestion", "")
        result.setdefault("count", result.get("count", 0))
        result.setdefault("returned", result.get("returned", len(result.get("items", []) or [])))
        result.setdefault("items", result.get("items", []))
        return result

    message = result.get("message") or "工具执行失败"
    http_status = result.get("http_status")

    if "token" in message.lower():
        code = "CMDB_EMPTY_TOKEN"
    elif "base_url" in message.lower():
        code = "CMDB_EMPTY_BASE_URL"
    elif http_status:
        code = "CMDB_HTTP_ERROR"
    else:
        code = "TOOL_EXCEPTION"

    err = standard_error(
        error_code=code,
        message=message,
        detail=result,
        http_status=http_status,
    )
    err["tool_name"] = tool_name
    return err
