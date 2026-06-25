# -*- coding: utf-8 -*-
"""
V2 chat router.

Purpose:
- Intercept troubleshooting-style user questions before the old CMDB-only flow.
- Resolve device identity through CMDB + Netmiko inventory.
- Generate read-only Netmiko command suggestions.
- Validate every suggested command with CLI Guard.
- Return suggestions to frontend through the existing chat response format.

Safety:
- This module does NOT execute any network device command.
- It only generates and validates command suggestions.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, List, Optional

from netaiops_asset.device_identity.resolver import DeviceIdentityResolver
from netaiops_asset.netmiko.cli_guard import CliReadOnlyGuard
from netaiops_asset.observability.device_metrics import DeviceMetricProbe, format_cpu_evidence_for_answer
from netaiops_asset.chat_v2.context import load_v2_context
from netaiops_asset.chat_v2.llm_intent_planner import plan_v2_intent, v2_intent_from_plan, keyword_from_plan, interface_from_plan, extract_interface_from_text, is_v2_plan, is_cmdb_only_plan
from netaiops_asset.chat_v2.plan_dispatcher import validate_and_dispatch_plan
from netaiops_asset.chat_v2.command_templates import build_template_command_items


DEVICE_NAME_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


ROUTE_KEYWORDS = [
    "路由",
    "路由表",
    "route",
    "routing",
    "fib",
    "rib",
]

CPU_KEYWORDS = [
    "cpu",
    "CPU",
    "利用率",
    "负载",
    "system resources",
    "processes cpu",
]

INTERFACE_KEYWORDS = [
    "接口",
    "端口",
    "interface",
    "端口状态",
    "链路",
]

BGP_KEYWORDS = [
    "bgp",
    "BGP",
    "邻居",
    "peer",
]

BFD_KEYWORDS = [
    "bfd",
    "BFD",
]

TROUBLESHOOT_HINTS = [
    "多少条",
    "当前",
    "排查",
    "检查",
    "查看",
    "状态",
    "怎么查",
    "哪些命令",
    "命令",
    "故障",
    "异常",
    "down",
    "Down",
    "DOWN",
]


def try_handle_v2_chat(question: str, user: Optional[str] = None, conversation_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    q = str(question or "").strip()
    if not q:
        return None

    context = load_v2_context(conversation_id=conversation_id, user=user)

    llm_intent_plan = plan_v2_intent(q, context=context, user=user)
    dispatch_plan = validate_and_dispatch_plan(llm_intent_plan, question=q, context=context)

    # Plan Validator + Action Dispatcher:
    # - CMDB-only questions fall back to V1 planner.
    # - V2 troubleshoot questions continue inside this router.
    # - followup/execute plans are handled by app-level routers before this function.
    if dispatch_plan.get("route") == "v1_cmdb":
        return None

    if dispatch_plan.get("route") == "need_clarification" and dispatch_plan.get("status") == "need_clarification":
        return {
            "status": "need_clarification",
            "question": q,
            "parsed": {
                "intent": "v2_plan_dispatch",
                "reason": "plan_dispatch_need_clarification",
                "llm_intent_plan": llm_intent_plan,
                "dispatch_plan": dispatch_plan,
            },
            "llm_plan": None,
            "planner_source": "v2_chat_router",
            "planner_diagnostics": None,
            "answer": "我识别到这是 V2 排障/取证类问题，但当前计划缺少必要信息。请补充设备名、管理 IP 或更明确的问题现象。",
            "columns": [],
            "field_labels": {},
            "count": 0,
            "returned": 0,
            "items": [],
        }

    intent = dispatch_plan.get("v2_intent") or v2_intent_from_plan(llm_intent_plan)

    if not intent:
        intent = detect_v2_intent(q)

    if not intent:
        intent = infer_v2_intent_from_context(q, context)

    if not intent:
        return None

    dispatch_entities = dispatch_plan.get("entities") or {}
    keyword = dispatch_entities.get("device_name") or dispatch_entities.get("mgmt_ip") or keyword_from_plan(llm_intent_plan) or extract_device_keyword(q)
    interface_name = dispatch_entities.get("interface") or interface_from_plan(llm_intent_plan) or extract_interface_from_text(q)
    inherited_context = None

    if not keyword:
        keyword, inherited_context = inherit_device_keyword_from_context(q, context, intent)

    if not keyword:
        return {
            "status": "need_clarification",
            "question": q,
            "parsed": {
                "intent": "v2_troubleshoot",
                "v2_intent": intent,
                "reason": "v2_router_no_device_keyword",
                "context_available": bool(context),
                "llm_intent_plan": llm_intent_plan,
            },
            "llm_plan": None,
            "planner_source": "v2_chat_router",
            "planner_diagnostics": None,
            "answer": "我识别到这是 V2 排障/取证类问题，但没有识别到明确设备名或管理 IP，且当前会话上下文中也没有可继承的设备。请补充设备主机名或管理 IP。",
            "columns": [],
            "field_labels": {},
            "count": 0,
            "returned": 0,
            "items": [],
        }

    resolver = DeviceIdentityResolver()
    identity = resolver.resolve(keyword, probe_prometheus=False)

    hostname = identity.get("hostname")
    mgmt_ip = identity.get("mgmt_ip")
    netmiko_match = identity.get("netmiko_match") or {}
    device_name = netmiko_match.get("name") or hostname or keyword
    device_type = netmiko_match.get("device_type") or infer_device_type(identity)
    platform = device_type or infer_platform(identity)

    if not device_name:
        return {
            "status": "not_found",
            "question": q,
            "parsed": {
                "intent": "v2_troubleshoot",
                "v2_intent": intent,
                "keyword": keyword,
                "reason": "v2_router_device_not_found",
            },
            "llm_plan": None,
            "planner_source": "v2_chat_router",
            "planner_diagnostics": None,
            "answer": "我识别到这是 V2 排障/取证类问题，但没有从 CMDB 或 Netmiko 清单中解析到明确设备，请确认设备名或管理 IP。",
            "columns": [],
            "field_labels": {},
            "count": 0,
            "returned": 0,
            "items": [],
            "v2": {
                "identity": identity,
            },
        }

    prometheus_evidence = None
    if intent == "cpu_check":
        try:
            prometheus_evidence = DeviceMetricProbe().probe_cpu(identity)
        except Exception as exc:
            prometheus_evidence = {
                "status": "failed",
                "metric_type": "cpu",
                "summary": "Prometheus CPU 查询异常：{}".format(repr(exc)),
            }

    commands = build_command_suggestions(intent, platform=platform, question=q)
    guard = CliReadOnlyGuard()

    items: List[Dict[str, Any]] = []
    for item in commands:
        guard_result = guard.validate(
            item["command"],
            platform=platform,
            device_type=device_type,
        ).to_dict()

        items.append({
            "device_name": device_name,
            "mgmt_ip": mgmt_ip or "",
            "device_type": device_type or "",
            "v2_intent": intent,
            "command": item["command"],
            "purpose": item["purpose"],
            "guard_status": guard_result.get("status"),
            "risk_level": guard_result.get("risk_level"),
            "matched_rule": guard_result.get("matched_rule"),
            "confirm_required": "是" if guard_result.get("status") == "passed" else "否",
            "guard_reasons": "；".join(guard_result.get("reasons") or []),
        })

    template_items = build_template_command_items(
        intent=intent,
        device_name=device_name,
        mgmt_ip=mgmt_ip,
        device_type=device_type,
        interface_name=interface_name,
        dispatch_plan=dispatch_plan,
        llm_intent_plan=llm_intent_plan,
    )
    if template_items:
        items = template_items

    answer = build_v2_answer(
        question=q,
        intent=intent,
        keyword=keyword,
        hostname=hostname,
        mgmt_ip=mgmt_ip,
        device_name=device_name,
        device_type=device_type,
        items=items,
        prometheus_evidence=prometheus_evidence,
    )

    if interface_name:
        answer = "接口解析结果：{}。\n{}".format(interface_name, answer)

    if llm_intent_plan:
        answer = (
            "LLM-first 识别结果：action={action}，category={category}，confidence={confidence}。\n"
            "Plan Dispatcher：route={route}，status={status}，degraded={degraded}。\n"
            "{answer}"
        ).format(
            action=llm_intent_plan.get("action"),
            category=llm_intent_plan.get("category"),
            confidence=llm_intent_plan.get("confidence"),
            route=dispatch_plan.get("route"),
            status=dispatch_plan.get("status"),
            degraded=dispatch_plan.get("degraded"),
            answer=answer,
        )

    if inherited_context:
        answer = (
            "已继承上一轮 V2 上下文：当前设备={device}，管理IP={ip}，排障主题={topic}。\n"
            "{answer}"
        ).format(
            device=inherited_context.get("device_name") or "-",
            ip=inherited_context.get("mgmt_ip") or "-",
            topic=inherited_context.get("current_topic") or "-",
            answer=answer,
        )

    return {
        "status": "ok",
        "question": q,
        "parsed": {
            "intent": "v2_troubleshoot",
            "v2_intent": intent,
            "keyword": keyword,
            "hostname": hostname,
            "mgmt_ip": mgmt_ip,
            "device_name": device_name,
            "device_type": device_type,
            "reason": "v2_chat_router",
            "context_inherited": bool(inherited_context),
            "inherited_context": inherited_context,
            "llm_intent_plan": llm_intent_plan,
            "dispatch_plan": dispatch_plan,
            "interface_name": interface_name,
        },
        "llm_plan": None,
        "planner_source": "v2_chat_router",
        "planner_diagnostics": None,
        "answer": answer,
        "columns": [
            "device_name",
            "mgmt_ip",
            "device_type",
            "command",
            "purpose",
            "guard_status",
            "risk_level",
            "confirm_required",
            "guard_reasons",
        ],
        "field_labels": {
            "device_name": "Netmiko设备名",
            "mgmt_ip": "管理IP",
            "device_type": "设备类型",
            "command": "建议只读命令",
            "purpose": "用途",
            "guard_status": "安全校验状态",
            "risk_level": "风险级别",
            "confirm_required": "是否需要确认",
            "guard_reasons": "校验原因",
        },
        "count": len(items),
        "returned": len(items),
        "items": items,
        "v2": {
            "identity": identity,
            "prometheus_evidence": prometheus_evidence,
            "execution_policy": {
                "auto_execute": False,
                "message": "V2 先生成建议命令并补充只读监控证据，不自动执行设备 CLI。后续需通过确认执行流程调用 Netmiko MCP。",
            },
        },
    }


def detect_v2_intent(question: str) -> Optional[str]:
    q = question
    q_lower = q.lower()

    if not any(h in q for h in TROUBLESHOOT_HINTS) and not any(h.lower() in q_lower for h in TROUBLESHOOT_HINTS):
        return None

    if any(k in q for k in ROUTE_KEYWORDS) or any(k.lower() in q_lower for k in ROUTE_KEYWORDS):
        return "route_table"

    if any(k in q for k in CPU_KEYWORDS) or any(k.lower() in q_lower for k in CPU_KEYWORDS):
        return "cpu_check"

    if any(k in q for k in BGP_KEYWORDS) or any(k.lower() in q_lower for k in BGP_KEYWORDS):
        return "bgp_check"

    if any(k in q for k in BFD_KEYWORDS) or any(k.lower() in q_lower for k in BFD_KEYWORDS):
        return "bfd_check"

    if any(k in q for k in INTERFACE_KEYWORDS) or any(k.lower() in q_lower for k in INTERFACE_KEYWORDS):
        return "interface_check"

    return None


def extract_device_keyword(question: str) -> Optional[str]:
    for m in IP_RE.findall(question):
        if is_ip(m):
            return m

    candidates = DEVICE_NAME_RE.findall(question)
    if not candidates:
        return None

    cleaned = []
    for item in candidates:
        token = str(item or "").strip().strip("-_.")
        if token.count("-") < 2:
            continue
        cleaned.append(token)

    if not cleaned:
        return None

    # Prefer the longest hostname-like token. Do not use \b here because
    # Python treats Chinese characters as word characters, causing
    # "SH8-G03-DCI-BN-SW01的..." to be truncated to "SH8-G03-DCI-BN".
    cleaned = sorted(cleaned, key=len, reverse=True)
    return cleaned[0]



CONTEXT_REFERENCE_HINTS = [
    "这个设备",
    "当前设备",
    "该设备",
    "这台设备",
    "上面这台",
    "刚才这台",
    "上一台",
    "这个",
    "上述",
    "上面",
    "刚才",
    "继续",
    "接着",
    "下一步",
    "这些结果",
    "以上结果",
    "以上三点",
    "刚才的结果",
    "刚才的分析",
    "上述结果",
    "上述分析",
]


def has_context_reference(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    return any(hint in text for hint in CONTEXT_REFERENCE_HINTS)


def infer_v2_intent_from_context(question: str, context: Optional[Dict[str, Any]]) -> Optional[str]:
    if not context:
        return None

    text = str(question or "").strip()
    if not text:
        return None

    if not has_context_reference(text):
        return None

    current_intent = context.get("current_intent")
    current_topic = context.get("current_topic")

    if current_intent in ("cpu_check", "route_table", "interface_check", "bgp_check", "bfd_check"):
        if any(x in text for x in ["命令", "排查", "继续", "下一步", "怎么查", "查什么", "查看"]):
            return current_intent

    if current_topic == "cpu":
        return "cpu_check"
    if current_topic == "route_table":
        return "route_table"
    if current_topic == "interface":
        return "interface_check"
    if current_topic == "bgp":
        return "bgp_check"
    if current_topic == "bfd":
        return "bfd_check"

    return None


def inherit_device_keyword_from_context(
    question: str,
    context: Optional[Dict[str, Any]],
    intent: Optional[str],
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    if not context:
        return None, None

    if not intent:
        return None, None

    text = str(question or "").strip()

    # Allow inheritance when user explicitly refers to the previous/current device,
    # or when the new question has a clear V2 intent but omits a device.
    if not has_context_reference(text) and not intent:
        return None, None

    current_device = context.get("current_device") or {}
    if not current_device:
        return None, None

    keyword = (
        current_device.get("device_name")
        or current_device.get("hostname")
        or current_device.get("netmiko_device_name")
        or current_device.get("mgmt_ip")
    )

    if not keyword:
        return None, None

    inherited = {
        "device_name": current_device.get("device_name"),
        "hostname": current_device.get("hostname"),
        "mgmt_ip": current_device.get("mgmt_ip"),
        "device_type": current_device.get("device_type"),
        "current_topic": context.get("current_topic"),
        "current_intent": context.get("current_intent"),
        "reason": "inherited_from_v2_conversation_context",
    }

    return keyword, inherited


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(str(value))
        return True
    except Exception:
        return False


def infer_device_type(identity: Dict[str, Any]) -> str:
    selected = identity.get("selected_cmdb") or {}
    netmiko_match = identity.get("netmiko_match") or {}

    dt = str(netmiko_match.get("device_type") or "").strip()
    if dt:
        return dt

    os_version = str(selected.get("os_version") or "").lower()
    manufacturer = str(selected.get("manufacturer") or selected.get("vendor") or "").lower()
    spec = str(selected.get("device_spec") or "").lower()

    if "nxos" in os_version or "n9k" in spec or "nexus" in spec:
        return "cisco_nxos"

    if "cisco" in manufacturer:
        return "cisco_xe"

    if "forti" in manufacturer:
        return "fortigate"

    if "f5" in manufacturer:
        return "f5"

    if "huawei" in manufacturer or "华为" in manufacturer:
        return "huawei"

    if "h3c" in manufacturer or "新华三" in manufacturer:
        return "h3c"

    return "generic"


def infer_platform(identity: Dict[str, Any]) -> str:
    return infer_device_type(identity)


def build_command_suggestions(intent: str, platform: str, question: str) -> List[Dict[str, str]]:
    p = str(platform or "").lower()

    if intent == "route_table":
        if "nxos" in p or "cisco" in p:
            return [
                {"command": "show ip route summary", "purpose": "查看 IPv4 路由表汇总和路由条目统计"},
                {"command": "show ipv6 route summary", "purpose": "查看 IPv6 路由表汇总和路由条目统计"},
                {"command": "show ip route | count", "purpose": "粗略统计 IPv4 路由表输出行数，辅助估算路由规模"},
            ]
        if "huawei" in p or "h3c" in p:
            return [
                {"command": "display ip routing-table statistics", "purpose": "查看 IPv4 路由表统计"},
                {"command": "display ipv6 routing-table statistics", "purpose": "查看 IPv6 路由表统计"},
            ]
        if "forti" in p:
            return [
                {"command": "get router info routing-table database", "purpose": "查看 FortiGate 路由数据库"},
                {"command": "get router info routing-table all", "purpose": "查看完整路由表"},
            ]
        return [
            {"command": "show ip route summary", "purpose": "查看路由表汇总"},
        ]

    if intent == "cpu_check":
        if "nxos" in p or "cisco" in p:
            return [
                {"command": "show system resources", "purpose": "查看系统 CPU/内存整体资源使用率"},
                {"command": "show processes cpu", "purpose": "查看 CPU 使用情况"},
                {"command": "show processes cpu sort", "purpose": "按 CPU 使用率排序查看进程"},
                {"command": "show logging last 100", "purpose": "查看最近日志，辅助判断 CPU 异常是否伴随进程/协议事件"},
            ]
        if "huawei" in p or "h3c" in p:
            return [
                {"command": "display cpu-usage", "purpose": "查看 CPU 使用率"},
                {"command": "display memory", "purpose": "查看内存使用情况"},
                {"command": "display logbuffer", "purpose": "查看近期日志"},
            ]
        if "forti" in p:
            return [
                {"command": "get system performance status", "purpose": "查看系统性能和 CPU 使用率"},
                {"command": "diagnose sys top-summary", "purpose": "查看进程级资源占用摘要"},
            ]
        if "f5" in p:
            return [
                {"command": "tmsh show sys performance", "purpose": "查看 F5 系统性能"},
                {"command": "tmsh show sys cpu", "purpose": "查看 F5 CPU 状态"},
            ]
        return [
            {"command": "show system resources", "purpose": "查看系统资源"},
            {"command": "show processes cpu", "purpose": "查看 CPU 使用率"},
        ]

    if intent == "interface_check":
        if "nxos" in p or "cisco" in p:
            return [
                {"command": "show interface status", "purpose": "查看接口状态"},
                {"command": "show interface counters errors", "purpose": "查看接口错误计数"},
                {"command": "show interface transceiver details", "purpose": "查看光模块信息"},
            ]
        if "huawei" in p or "h3c" in p:
            return [
                {"command": "display interface brief", "purpose": "查看接口摘要"},
                {"command": "display interface", "purpose": "查看接口详细状态"},
            ]
        return [
            {"command": "show interface", "purpose": "查看接口状态"},
        ]

    if intent == "bgp_check":
        if "nxos" in p or "cisco" in p:
            return [
                {"command": "show bgp summary", "purpose": "查看 BGP 邻居摘要"},
                {"command": "show bgp sessions", "purpose": "查看 BGP 会话状态"},
            ]
        if "huawei" in p or "h3c" in p:
            return [
                {"command": "display bgp peer", "purpose": "查看 BGP 邻居状态"},
            ]
        return [
            {"command": "show bgp summary", "purpose": "查看 BGP 邻居摘要"},
        ]

    if intent == "bfd_check":
        if "nxos" in p or "cisco" in p:
            return [
                {"command": "show bfd neighbors", "purpose": "查看 BFD 邻居状态"},
                {"command": "show bfd neighbors details", "purpose": "查看 BFD 邻居详细状态"},
            ]
        if "huawei" in p or "h3c" in p:
            return [
                {"command": "display bfd session all", "purpose": "查看 BFD 会话状态"},
            ]
        return [
            {"command": "show bfd neighbors", "purpose": "查看 BFD 邻居状态"},
        ]

    return [
        {"command": "show version", "purpose": "查看设备基础状态"},
    ]


def build_v2_answer(
    question: str,
    intent: str,
    keyword: str,
    hostname: Optional[str],
    mgmt_ip: Optional[str],
    device_name: str,
    device_type: Optional[str],
    items: List[Dict[str, Any]],
    prometheus_evidence: Optional[Dict[str, Any]] = None,
) -> str:
    passed_count = sum(1 for x in items if x.get("guard_status") == "passed")
    review_count = sum(1 for x in items if x.get("guard_status") == "review")
    blocked_count = sum(1 for x in items if x.get("guard_status") == "blocked")

    intent_name = {
        "route_table": "路由表取证",
        "cpu_check": "CPU 利用率排查",
        "interface_check": "接口状态排查",
        "bgp_check": "BGP 邻居排查",
        "bfd_check": "BFD 会话排查",
    }.get(intent, intent)

    lines = [
        "已进入 V2 排障取证流程：{}。".format(intent_name),
        "设备解析结果：输入={}，CMDB主机名={}，管理IP={}，Netmiko设备名={}，设备类型={}。".format(
            keyword,
            hostname or "-",
            mgmt_ip or "-",
            device_name or "-",
            device_type or "-",
        ),
        "本轮只生成并校验 Netmiko 只读命令建议，不会自动登录设备执行。",
        "命令校验统计：passed={}，review={}，blocked={}。".format(passed_count, review_count, blocked_count),
    ]

    if intent == "cpu_check":
        lines.extend(format_cpu_evidence_for_answer(prometheus_evidence))

    lines.append("后续需要进入确认执行流程后，才会调用 Netmiko MCP 执行 passed 命令。")

    return "\n".join(lines)
