from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from netaiops_asset.cmdb.field_map import (
    CMDB_FIELDS,
    DEFAULT_FIELDS,
    DETAIL_FIELDS,
    FIELD_ALIASES,
    QUERY_FILTER_FIELDS,
    field_labels,
    normalize_fields,
)
from netaiops_asset.llm.client import LLMClient


ALLOWED_TOOLS = {"query_cmdb_devices", "query_cmdb_device_detail", "clarify"}


def _allowed_filters() -> set[str]:
    allowed = {
        "search",
        "mgmt_ip",
        "mgmt_ip__in",
    }

    for field in QUERY_FILTER_FIELDS:
        allowed.add(field)
        allowed.add(f"{field}__icontains")

    return allowed


ALLOWED_FILTERS = _allowed_filters()


def _field_catalog_text() -> str:
    parts = []
    for f in CMDB_FIELDS:
        name = f.get("name")
        cn = f.get("cn_name", name)
        desc = f.get("description", "")
        parts.append(f"- {name}: {cn}；{desc}")
    return "\n".join(parts)


def _alias_catalog_text() -> str:
    groups: dict[str, list[str]] = defaultdict(list)
    for alias, field in FIELD_ALIASES.items():
        groups[str(field)].append(str(alias))

    lines = []
    for field in sorted(groups):
        aliases = sorted(set(groups[field]))[:12]
        lines.append(f"- {field}: {', '.join(aliases)}")
    return "\n".join(lines)


def _filter_catalog_text() -> str:
    lines = []
    for field in sorted(QUERY_FILTER_FIELDS):
        if field == "mgmt_ip":
            lines.append(f"- {field}: 精确匹配管理IP，例如 {{\"mgmt_ip\": \"10.189.250.8\"}}")
        else:
            lines.append(f"- {field}__icontains: 模糊匹配 {field}")
    lines.append("- search: 当字段不确定但用户给出了明确值时，可作为兜底全文搜索")
    return "\n".join(lines)


def _system_prompt() -> str:
    prompt = """
你是 NetAIOps 网络资产查询平台的 Tool Planner。
你的任务：把用户的中文问题转换为严格 JSON 工具调用计划。

总原则：
1. 只能输出 JSON 对象，不要输出 Markdown，不要解释。
2. 你不是事实来源，不能编造设备信息。
3. 你只负责把用户问题转换成后端工具调用参数，真实数据由 CMDB Tool 查询。
4. 只能选择三个 tool_name：
   - query_cmdb_devices：按任意 CMDB 字段查询设备，或者用一个字段值反查其他字段
   - query_cmdb_device_detail：按管理IP精确查询单台设备详情
   - clarify：用户没有给出任何可查询条件，需要追问
5. 不能生成 SQL、Linux命令、URL、Python代码。
6. 只允许使用白名单 filters key。
7. 用户给出 CMDB 任意字段的信息时，都应尽量用该字段去查其他字段，而不是让用户重新补充条件。
8. 如果用户给出完整主机名，如 SH8-H05-INT-CON-SW01、WG88-SW-H19-1，应使用 host_name__icontains。
9. 如果用户给出设备序列号，如 FDO24130P9S，应使用 sn__icontains。
10. 如果用户给出 EM码，如 EM06027，应使用 server_ID__icontains。
11. 如果用户给出管理IP，应优先使用 query_cmdb_device_detail，keyword 填 IP。
12. 如果用户问“某字段是多少”，fields 里必须包含被询问字段，同时建议包含用于定位的字段。
13. SH8、SH16、万国88 这类通常是 IDC；G03、H03 这类通常是 rack/机柜；203、404 这类通常是 server_room/机房。
14. “G排机柜 / G排 / G列”应解析为 rack__icontains=G；“H排机柜 / H排 / H列”应解析为 rack__icontains=H。
15. “生产网 / 骨干网 / 带外网 / 管理网 / 测试网 / 下电”属于环境或用途类描述，优先解析为 env__icontains。
16. 用户同时给出 IDC、机柜排、环境时，必须同时保留所有过滤条件，不要只保留 IDC 和机柜。
14. 如果同时出现 IDC 和完整主机名，完整主机名优先，不要只按 IDC 查询。
15. 如果字段名不确定但给出的值很明确，可以使用 search 兜底。

CMDB 字段清单：
__FIELD_CATALOG__

字段别名：
__ALIAS_CATALOG__

允许使用的 filters：
__FILTER_CATALOG__

输出 JSON 格式必须为：
{
  "tool_name": "query_cmdb_devices",
  "confidence": 0.95,
  "reason": "简短说明",
  "arguments": {
    "filters": {},
    "fields": [],
    "page_size": 20,
    "keyword": ""
  },
  "clarify_message": ""
}

示例1：
用户：设备SH8-H05-INT-CON-SW01的管理IP是多少？
输出：
{
  "tool_name": "query_cmdb_devices",
  "confidence": 0.98,
  "reason": "用户按完整主机名查询管理IP",
  "arguments": {
    "filters": {"host_name__icontains": "SH8-H05-INT-CON-SW01"},
    "fields": ["host_name", "mgmt_ip"],
    "page_size": 20,
    "keyword": ""
  },
  "clarify_message": ""
}

示例2：
用户：EM码为EM06027的设备主机名是什么？
输出：
{
  "tool_name": "query_cmdb_devices",
  "confidence": 0.98,
  "reason": "用户按EM码查询主机名",
  "arguments": {
    "filters": {"server_ID__icontains": "EM06027"},
    "fields": ["server_ID", "host_name"],
    "page_size": 20,
    "keyword": ""
  },
  "clarify_message": ""
}

示例3：
用户：设备序列号为FDO24130P9S的设备，主机名和管理IP分别是多少？
输出：
{
  "tool_name": "query_cmdb_devices",
  "confidence": 0.98,
  "reason": "用户按设备序列号查询主机名和管理IP",
  "arguments": {
    "filters": {"sn__icontains": "FDO24130P9S"},
    "fields": ["host_name", "mgmt_ip", "sn"],
    "page_size": 20,
    "keyword": ""
  },
  "clarify_message": ""
}

示例4：
用户：WG88-SW-H19-1这台设备的用途是什么？操作系统是什么？
输出：
{
  "tool_name": "query_cmdb_devices",
  "confidence": 0.98,
  "reason": "用户按主机名查询用途和操作系统",
  "arguments": {
    "filters": {"host_name__icontains": "WG88-SW-H19-1"},
    "fields": ["host_name", "comment", "os_version"],
    "page_size": 20,
    "keyword": ""
  },
  "clarify_message": ""
}

示例5：
用户：10.189.250.8是哪台设备？
输出：
{
  "tool_name": "query_cmdb_device_detail",
  "confidence": 0.98,
  "reason": "用户按管理IP反查单台设备",
  "arguments": {
    "filters": {},
    "fields": ["host_name", "mgmt_ip", "sn", "device_spec", "status", "IDC", "server_room", "rack"],
    "page_size": 20,
    "keyword": "10.189.250.8"
  },
  "clarify_message": ""
}

示例6：
用户：SH8机房G排机柜，生产网的设备有哪些？
输出：
{
  "tool_name": "query_cmdb_devices",
  "confidence": 0.98,
  "reason": "用户按IDC、机柜排和环境查询设备清单",
  "arguments": {
    "filters": {"IDC__icontains": "SH8", "rack__icontains": "G", "env__icontains": "生产"},
    "fields": ["host_name", "mgmt_ip", "ci_type", "env", "IDC", "rack"],
    "page_size": 20,
    "keyword": ""
  },
  "clarify_message": ""
}

""".strip()

    return (
        prompt
        .replace("__FIELD_CATALOG__", _field_catalog_text())
        .replace("__ALIAS_CATALOG__", _alias_catalog_text())
        .replace("__FILTER_CATALOG__", _filter_catalog_text())
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"```$", "", raw).strip()

    try:
        return json.loads(raw)
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start:end + 1])

    raise ValueError("LLM output does not contain valid JSON object")


def _sanitize_filters(filters: Any) -> dict[str, Any]:
    if not isinstance(filters, dict):
        return {}

    result: dict[str, Any] = {}
    for k, v in filters.items():
        key = str(k).strip()
        if key not in ALLOWED_FILTERS:
            continue
        if v in (None, ""):
            continue
        if isinstance(v, (str, int, float, bool)):
            result[key] = str(v).strip()
    return result


def _sanitize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(plan.get("tool_name") or "").strip()
    if tool_name not in ALLOWED_TOOLS:
        tool_name = "clarify"

    args = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}

    if tool_name == "query_cmdb_device_detail":
        normalized_fields = normalize_fields(args.get("fields"), DETAIL_FIELDS)
    else:
        normalized_fields = normalize_fields(args.get("fields"), DEFAULT_FIELDS)

    try:
        page_size = int(args.get("page_size") or 20)
    except Exception:
        page_size = 20
    page_size = max(1, min(page_size, 100))

    try:
        confidence = float(plan.get("confidence") or 0)
    except Exception:
        confidence = 0.0

    sanitized = {
        "tool_name": tool_name,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": str(plan.get("reason") or "")[:300],
        "arguments": {
            "filters": _sanitize_filters(args.get("filters")),
            "fields": normalized_fields,
            "page_size": page_size,
            "keyword": str(args.get("keyword") or "").strip(),
        },
        "clarify_message": str(plan.get("clarify_message") or "").strip(),
    }

    if sanitized["tool_name"] == "query_cmdb_devices" and not sanitized["arguments"]["filters"]:
        sanitized["tool_name"] = "clarify"
        sanitized["clarify_message"] = sanitized["clarify_message"] or "请补充查询条件，例如管理IP、主机名、设备序列号、EM码、IDC、机房、机柜、厂商或型号。"

    if sanitized["tool_name"] == "query_cmdb_device_detail" and not sanitized["arguments"]["keyword"]:
        sanitized["tool_name"] = "clarify"
        sanitized["clarify_message"] = sanitized["clarify_message"] or "请补充要查询的管理IP。"

    if sanitized["tool_name"] == "clarify" and not sanitized["clarify_message"]:
        sanitized["clarify_message"] = "请补充查询条件，例如管理IP、主机名、设备序列号、EM码、IDC、机房、机柜、厂商或型号。"

    return sanitized


def plan_with_llm(question: str, rule_parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    client = LLMClient()

    messages = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "rule_parsed_reference": rule_parsed or {},
                    "field_labels": field_labels(),
                },
                ensure_ascii=False,
            ),
        },
    ]

    llm_result = client.chat(messages)

    if llm_result.get("status") != "ok":
        return {
            "status": "error",
            "error_code": llm_result.get("error_code", "LLM_ERROR"),
            "message": llm_result.get("message", "LLM planning failed"),
            "llm_result": llm_result,
        }

    try:
        raw_plan = _extract_json_object(llm_result.get("content", ""))
        plan = _sanitize_plan(raw_plan)
        return {
            "status": "ok",
            "plan": plan,
            "raw_content": llm_result.get("content", ""),
            "llm": {
                "model": llm_result.get("model"),
                "latency_ms": llm_result.get("latency_ms"),
                "usage": llm_result.get("usage"),
                "headers": llm_result.get("headers"),
                "base_url_used": llm_result.get("base_url_used"),
                "retry_attempts": llm_result.get("retry_attempts"),
                "fallback_index": llm_result.get("fallback_index"),
            },
        }
    except Exception as exc:
        return {
            "status": "error",
            "error_code": "LLM_PLAN_PARSE_ERROR",
            "message": f"LLM plan parse failed: {type(exc).__name__}: {exc}",
            "raw_content": llm_result.get("content", ""),
            "llm": {
                "model": llm_result.get("model"),
                "latency_ms": llm_result.get("latency_ms"),
                "usage": llm_result.get("usage"),
                "headers": llm_result.get("headers"),
                "base_url_used": llm_result.get("base_url_used"),
            },
        }


def apply_llm_plan(plan_result: dict[str, Any]) -> dict[str, Any] | None:
    if plan_result.get("status") != "ok":
        return None

    plan = plan_result.get("plan") or {}
    tool_name = plan.get("tool_name")
    args = plan.get("arguments") or {}

    if tool_name == "query_cmdb_device_detail":
        return {
            "intent": "query_device_detail",
            "keyword": args.get("keyword", ""),
            "fields": args.get("fields", []),
            "reason": "llm_tool_plan",
            "llm_plan": plan,
        }

    if tool_name == "query_cmdb_devices":
        return {
            "intent": "query_devices",
            "filters": args.get("filters", {}),
            "fields": args.get("fields", []),
            "reason": "llm_tool_plan",
            "llm_plan": plan,
        }

    if tool_name == "clarify":
        return {
            "intent": "clarify",
            "message": plan.get("clarify_message") or "请补充查询条件。",
            "fields": args.get("fields", []),
            "reason": "llm_tool_plan_clarify",
            "llm_plan": plan,
        }

    return None
