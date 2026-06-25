# -*- coding: utf-8 -*-
"""
V2 command template library.

Purpose:
- Convert validated plan.category / v2_intent / entities into read-only CLI suggestions.
- LLM/fallback decides what the user wants.
- Local template library decides safe first-batch commands.
- Actual execution still requires confirmation and CLI Guard.

Safety:
- This module does not execute CLI.
- Only returns read-only command suggestions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from netaiops_asset.chat_v2.llm_intent_planner import normalize_interface_name
except Exception:
    def normalize_interface_name(name: str) -> str:
        return str(name or "").strip()


MAX_TEMPLATE_COMMANDS = 8


def build_template_command_items(
    intent: Optional[str],
    device_name: str,
    mgmt_ip: str,
    device_type: str,
    interface_name: Optional[str] = None,
    dispatch_plan: Optional[Dict[str, Any]] = None,
    llm_intent_plan: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    category = infer_category(intent, dispatch_plan, llm_intent_plan)
    intf = infer_interface(interface_name, dispatch_plan, llm_intent_plan)

    commands = select_commands(
        category=category,
        intent=intent,
        device_type=device_type,
        interface_name=intf,
    )

    items: List[Dict[str, Any]] = []

    for idx, spec in enumerate(commands[:MAX_TEMPLATE_COMMANDS], 1):
        command = spec.get("command")
        if not command:
            continue

        items.append({
            "index": idx,
            "device_name": device_name,
            "mgmt_ip": mgmt_ip,
            "device_type": device_type,
            "v2_intent": intent,
            "template_category": category,
            "interface_name": intf,
            "command": command,
            "purpose": spec.get("purpose") or "",
            "guard_status": "passed",
            "risk_level": "readonly",
            "matched_rule": "readonly_template_allowlist",
            "confirm_required": "是",
            "guard_reasons": "",
        })

    return items


def infer_category(
    intent: Optional[str],
    dispatch_plan: Optional[Dict[str, Any]],
    llm_intent_plan: Optional[Dict[str, Any]],
) -> str:
    for plan in (dispatch_plan, llm_intent_plan):
        if isinstance(plan, dict) and plan.get("category"):
            return str(plan.get("category") or "").strip()

    mapping = {
        "cpu_check": "cpu",
        "memory_check": "memory",
        "route_table": "route_table",
        "bgp_check": "bgp",
        "bfd_check": "bfd",
        "interface_error_check": "interface_error",
        "interface_check": "interface_status",
        "optical_power_check": "optical_power",
        "log_check": "log",
        "device_health_check": "device_health",
    }
    return mapping.get(str(intent or ""), str(intent or "unknown"))


def infer_interface(
    interface_name: Optional[str],
    dispatch_plan: Optional[Dict[str, Any]],
    llm_intent_plan: Optional[Dict[str, Any]],
) -> str:
    if interface_name:
        return normalize_interface_name(str(interface_name))

    for plan in (dispatch_plan, llm_intent_plan):
        if not isinstance(plan, dict):
            continue
        entities = plan.get("entities") or {}
        value = entities.get("interface") or entities.get("interface_name") or entities.get("port")
        if value:
            return normalize_interface_name(str(value))

    return ""


def select_commands(
    category: str,
    intent: Optional[str],
    device_type: str,
    interface_name: str = "",
) -> List[Dict[str, str]]:
    category = str(category or "").strip()
    intent = str(intent or "").strip()
    device_type = str(device_type or "").strip().lower()
    intf = normalize_interface_name(interface_name) if interface_name else ""

    if category == "cpu" or intent == "cpu_check":
        return [
            cmd("show system resources", "查看 NX-OS 系统 CPU/内存整体资源使用率"),
            cmd("show processes cpu", "查看 CPU 使用情况和进程 CPU 消耗"),
            cmd("show processes cpu sort", "按 CPU 使用率排序查看高 CPU 进程"),
            cmd("show logging last 100", "查看最近日志，确认是否有进程异常、协议震荡或接口事件"),
        ]

    if category == "route_table" or intent == "route_table":
        return [
            cmd("show ip route summary", "查看 IPv4 路由表汇总和路由条目统计"),
            cmd("show ipv6 route summary", "查看 IPv6 路由表汇总和路由条目统计"),
            cmd("show ip route | count", "粗略统计 IPv4 路由表输出行数，辅助估算路由规模"),
        ]

    if category == "interface_error" or intent == "interface_error_check":
        if intf:
            return [
                cmd("show interface {}".format(intf), "查看接口状态、速率、双工、CRC/input/output error/discard 等综合信息"),
                cmd("show interface {} counters errors".format(intf), "查看接口错误计数，包括 CRC、input error、output error 等"),
                cmd("show interface {} counters detailed".format(intf), "查看接口详细计数器，辅助确认错包/丢包/丢弃方向"),
                cmd("show interface {} transceiver details".format(intf), "查看光模块收发光功率、电压、电流、温度等信息"),
                cmd("show logging last 100", "查看最近日志，确认接口 flap、模块异常、链路协商或硬件相关事件"),
            ]
        return [
            cmd("show interface counters errors", "查看全局接口错误计数，定位错误包增长接口"),
            cmd("show interface status", "查看接口状态概览"),
            cmd("show logging last 100", "查看最近日志，确认接口或模块相关异常"),
        ]

    if category in ("interface_down", "interface_status") or intent == "interface_check":
        if intf:
            return [
                cmd("show interface {}".format(intf), "查看接口物理状态、协议状态、速率、双工和收发计数"),
                cmd("show interface {} status".format(intf), "查看接口状态、VLAN、速率、类型等摘要信息"),
                cmd("show interface {} transceiver details".format(intf), "查看光模块状态和光功率"),
                cmd("show logging last 100", "查看接口 down/up、flap、模块异常等日志"),
            ]
        return [
            cmd("show interface status", "查看接口状态概览"),
            cmd("show interface brief", "查看接口 brief 状态"),
            cmd("show logging last 100", "查看最近接口相关日志"),
        ]

    if category in ("optical_power", "transceiver") or intent == "optical_power_check":
        if intf:
            return [
                cmd("show interface {}".format(intf), "查看接口状态和错误计数"),
                cmd("show interface {} transceiver details".format(intf), "查看指定接口光模块收发光功率和阈值"),
                cmd("show logging last 100", "查看光模块、链路 flap、接口异常相关日志"),
            ]
        return [
            cmd("show interface transceiver details", "查看全局光模块收发光功率和阈值"),
            cmd("show logging last 100", "查看光模块相关日志"),
        ]

    if category == "bgp" or intent == "bgp_check":
        return [
            cmd("show bgp ipv4 unicast summary", "查看 BGP IPv4 邻居状态汇总"),
            cmd("show bgp ipv6 unicast summary", "查看 BGP IPv6 邻居状态汇总"),
            cmd("show ip bgp summary", "兼容方式查看 BGP 邻居汇总"),
            cmd("show logging last 100", "查看 BGP 邻居 flap、会话重置等日志"),
        ]

    if category == "bfd" or intent == "bfd_check":
        return [
            cmd("show bfd neighbors", "查看 BFD 邻居状态"),
            cmd("show bfd neighbors details", "查看 BFD 邻居详细信息"),
            cmd("show logging last 100", "查看 BFD 会话变化和相关日志"),
        ]

    if category == "memory" or intent == "memory_check":
        return [
            cmd("show system resources", "查看系统 CPU/内存整体资源使用率"),
            cmd("show processes memory", "查看进程内存使用情况"),
            cmd("show logging last 100", "查看最近内存、进程或平台异常日志"),
        ]

    if category == "log" or intent == "log_check":
        return [
            cmd("show logging last 100", "查看最近日志"),
            cmd("show logging last 300", "查看更长窗口的最近日志"),
        ]

    if category == "device_health" or intent == "device_health_check":
        return [
            cmd("show clock", "查看设备当前时间"),
            cmd("show version", "查看设备型号、版本、运行时间等基础信息"),
            cmd("show system resources", "查看系统资源使用情况"),
            cmd("show logging last 100", "查看最近系统日志"),
        ]

    return [
        cmd("show version", "查看设备基础状态"),
    ]


def cmd(command: str, purpose: str) -> Dict[str, str]:
    return {
        "command": command,
        "purpose": purpose,
    }
