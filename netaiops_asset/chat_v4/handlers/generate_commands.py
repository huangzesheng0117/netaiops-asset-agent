# -*- coding: utf-8 -*-
"""Deterministic V4.3-1 command-generation handler.

The handler consumes a structured CommandGenerationSpec selected by the LLM
Intent Arbiter. It resolves device identity read-only, builds commands from a
local platform catalog, normalizes them through the shared splitter, and applies
both V3 and Netmiko read-only guards. It never executes CLI and never creates or
consumes pending commands.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from netaiops_asset.chat_v3.command_splitter import split_command_list
from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v3.safety_guard import check_commands
from netaiops_asset.chat_v4.handlers.base import HandlerOutcome, HandlerRequest
from netaiops_asset.device_identity.resolver import DeviceIdentityResolver
from netaiops_asset.netmiko.cli_guard import CliReadOnlyGuard


def _cmd(command: str, purpose: str) -> Dict[str, str]:
    return {"command": command, "purpose": purpose}


def _platform_family(device_type: str) -> str:
    value = str(device_type or "").strip().lower()
    if any(token in value for token in ("nxos", "ios", "cisco", "asa")):
        return "cisco"
    if "huawei" in value or "vrp" in value:
        return "huawei"
    if "h3c" in value or "comware" in value:
        return "h3c"
    if "forti" in value:
        return "fortigate"
    if "f5" in value or "tmsh" in value or "bigip" in value:
        return "f5"
    if "hillstone" in value:
        return "hillstone"
    return "generic"


def _interface_or_default(interface_name: str) -> str:
    return str(interface_name or "").strip()


def build_command_specs(
    *,
    category: str,
    platform: str,
    interface_name: str = "",
) -> list[Dict[str, str]]:
    """Return deterministic read-only command specifications."""

    category = str(category or "device_health").strip().lower()
    platform = _platform_family(platform)
    intf = _interface_or_default(interface_name)

    if platform in {"huawei", "h3c"}:
        if category == "cpu":
            return [
                _cmd("display cpu-usage", "查看 CPU 使用情况"),
                _cmd("display process cpu", "查看进程 CPU 使用情况"),
                _cmd("display logbuffer", "查看最近系统日志"),
            ]
        if category == "memory":
            return [
                _cmd("display memory-usage", "查看内存使用情况"),
                _cmd("display process memory", "查看进程内存使用情况"),
                _cmd("display logbuffer", "查看最近系统日志"),
            ]
        if category == "route_table":
            return [
                _cmd("display ip routing-table", "查看 IPv4 路由表"),
                _cmd("display ipv6 routing-table", "查看 IPv6 路由表"),
            ]
        if category == "bgp":
            return [
                _cmd("display bgp peer", "查看 BGP 邻居状态"),
                _cmd("display bgp routing-table", "查看 BGP 路由表摘要"),
            ]
        if category == "bfd":
            return [
                _cmd("display bfd session all", "查看 BFD 会话状态"),
                _cmd("display logbuffer", "查看 BFD 相关日志"),
            ]
        if category in {"interface_status", "interface_error", "optical_power"}:
            if intf:
                commands = [
                    _cmd(f"display interface {intf}", "查看接口状态和计数器"),
                ]
                if category == "optical_power":
                    commands.append(
                        _cmd(
                            f"display transceiver interface {intf} verbose",
                            "查看接口光模块和光功率",
                        )
                    )
                commands.append(_cmd("display logbuffer", "查看接口相关日志"))
                return commands
            return [
                _cmd("display interface brief", "查看接口状态概览"),
                _cmd("display logbuffer", "查看接口相关日志"),
            ]
        if category == "log":
            return [_cmd("display logbuffer", "查看最近系统日志")]
        return [
            _cmd("display clock", "查看设备时间"),
            _cmd("display version", "查看设备版本和运行时间"),
            _cmd("display cpu-usage", "查看 CPU 使用情况"),
            _cmd("display memory-usage", "查看内存使用情况"),
            _cmd("display logbuffer", "查看最近系统日志"),
        ]

    if platform == "fortigate":
        if category == "cpu":
            return [
                _cmd("get system performance status", "查看系统性能和 CPU"),
                _cmd("diagnose sys top-summary", "查看高资源消耗进程摘要"),
                _cmd("get system status", "查看系统状态"),
            ]
        if category == "memory":
            return [
                _cmd("get system performance status", "查看系统性能和内存"),
                _cmd("diagnose sys top-summary", "查看高资源消耗进程摘要"),
            ]
        if category == "route_table":
            return [_cmd("get router info routing-table all", "查看路由表")]
        if category == "bgp":
            return [_cmd("get router info bgp summary", "查看 BGP 邻居摘要")]
        if category in {"interface_status", "interface_error", "optical_power"}:
            if intf:
                return [
                    _cmd(
                        f"diagnose hardware deviceinfo nic {intf}",
                        "查看接口硬件状态和计数",
                    ),
                    _cmd("get system interface physical", "查看物理接口状态"),
                ]
            return [_cmd("get system interface physical", "查看物理接口状态")]
        return [
            _cmd("get system status", "查看系统版本和状态"),
            _cmd("get system performance status", "查看系统性能"),
            _cmd("get system interface physical", "查看物理接口状态"),
        ]

    if platform == "f5":
        if category == "route_table":
            return [_cmd("tmsh show net route", "查看路由状态")]
        if category in {"interface_status", "interface_error", "optical_power"}:
            return [
                _cmd("tmsh show net interface", "查看接口状态和计数"),
                _cmd("tmsh show sys performance system", "查看系统性能"),
            ]
        if category == "log":
            return [_cmd("tmsh show sys log", "查看系统日志摘要")]
        return [
            _cmd("tmsh show sys version", "查看系统版本"),
            _cmd("tmsh show sys performance system", "查看系统性能"),
            _cmd("tmsh show net interface", "查看接口状态"),
            _cmd("tmsh show ltm virtual", "查看虚拟服务器状态"),
        ]

    # Cisco, Hillstone and generic devices use conservative show-only commands.
    if category == "cpu":
        return [
            _cmd("show system resources", "查看系统 CPU/内存整体资源"),
            _cmd("show processes cpu", "查看进程 CPU 使用情况"),
            _cmd("show logging last 100", "查看最近系统日志"),
        ]
    if category == "memory":
        return [
            _cmd("show system resources", "查看系统资源"),
            _cmd("show processes memory", "查看进程内存使用情况"),
            _cmd("show logging last 100", "查看最近系统日志"),
        ]
    if category == "route_table":
        return [
            _cmd("show ip route summary", "查看 IPv4 路由表汇总"),
            _cmd("show ipv6 route summary", "查看 IPv6 路由表汇总"),
        ]
    if category == "bgp":
        return [
            _cmd("show bgp ipv4 unicast summary", "查看 BGP IPv4 邻居摘要"),
            _cmd("show bgp ipv6 unicast summary", "查看 BGP IPv6 邻居摘要"),
            _cmd("show logging last 100", "查看 BGP 相关日志"),
        ]
    if category == "bfd":
        return [
            _cmd("show bfd neighbors", "查看 BFD 邻居状态"),
            _cmd("show bfd neighbors details", "查看 BFD 邻居详情"),
        ]
    if category in {"interface_status", "interface_error", "optical_power"}:
        if intf:
            result = [
                _cmd(f"show interface {intf}", "查看接口状态和计数"),
            ]
            if category == "interface_error":
                result.append(
                    _cmd(
                        f"show interface {intf} counters errors",
                        "查看接口错误计数",
                    )
                )
            if category == "optical_power":
                result.append(
                    _cmd(
                        f"show interface {intf} transceiver details",
                        "查看接口光模块和光功率",
                    )
                )
            result.append(_cmd("show logging last 100", "查看接口相关日志"))
            return result
        return [
            _cmd("show interface status", "查看接口状态概览"),
            _cmd("show interface counters errors", "查看接口错误计数"),
            _cmd("show logging last 100", "查看接口相关日志"),
        ]
    if category == "log":
        return [_cmd("show logging last 100", "查看最近系统日志")]
    return [
        _cmd("show clock", "查看设备时间"),
        _cmd("show version", "查看设备版本和运行时间"),
        _cmd("show system resources", "查看系统资源"),
        _cmd("show logging last 100", "查看最近系统日志"),
    ]


class GenerateCommandsHandler:
    """Generate and validate commands without executing or creating pending."""

    action = IntentAction.generate_commands
    handler_key = "generate_commands"

    def __init__(
        self,
        *,
        resolver_factory: Callable[[], Any] = DeviceIdentityResolver,
        catalog: Callable[..., list[Dict[str, str]]] = build_command_specs,
        splitter: Callable[..., Any] = split_command_list,
        safety_checker: Callable[..., Any] = check_commands,
        guard_factory: Callable[[], Any] = CliReadOnlyGuard,
    ) -> None:
        self.resolver_factory = resolver_factory
        self.catalog = catalog
        self.splitter = splitter
        self.safety_checker = safety_checker
        self.guard_factory = guard_factory

    @staticmethod
    def _context_device(request: HandlerRequest) -> Dict[str, Any]:
        value = request.canonical_context.device_context
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _keyword(request: HandlerRequest) -> str:
        device = GenerateCommandsHandler._context_device(request)
        for value in (
            request.decision.device_hint,
            device.get("host_name"),
            device.get("hostname"),
            device.get("device_name"),
            device.get("mgmt_ip"),
        ):
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _identity_device_context(identity: Dict[str, Any]) -> Dict[str, Any]:
        selected = identity.get("selected_cmdb")
        selected = dict(selected) if isinstance(selected, dict) else {}
        netmiko = identity.get("netmiko_match")
        netmiko = dict(netmiko) if isinstance(netmiko, dict) else {}
        result = {
            "host_name": identity.get("hostname") or selected.get("host_name"),
            "mgmt_ip": identity.get("mgmt_ip") or selected.get("mgmt_ip"),
            "device_name": netmiko.get("name") or identity.get("hostname"),
            "device_type": netmiko.get("device_type") or selected.get("device_type"),
            "device_spec": selected.get("device_spec"),
            "manufacturer": selected.get("manufacturer"),
            "IDC": selected.get("IDC"),
            "server_room": selected.get("server_room"),
            "rack": selected.get("rack"),
        }
        return {key: value for key, value in result.items() if value not in (None, "")}

    def handle(self, request: HandlerRequest) -> HandlerOutcome:
        if request.decision.action != self.action:
            return HandlerOutcome.failure(
                action=request.decision.action,
                handler_key=self.handler_key,
                detail=(
                    "handler action mismatch: expected generate_commands, got "
                    f"{request.decision.action.value}"
                ),
            )

        keyword = self._keyword(request)
        if not keyword:
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail="generate_commands requires a structured device hint or context device",
                metadata={"side_effect_started": False, "execution_started": False},
            )

        resolver = self.resolver_factory()
        identity = resolver.resolve(keyword, probe_prometheus=False)
        if not isinstance(identity, dict):
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail="device resolver returned a non-dict result",
                metadata={"side_effect_started": False, "execution_started": False},
            )
        if identity.get("status") == "not_found":
            return HandlerOutcome.success(
                action=self.action,
                handler_key=self.handler_key,
                answer="没有在 CMDB 或 Netmiko 只读清单中找到该设备，因此未生成命令。",
                status="not_found",
                source="deterministic_command_catalog",
                items=[],
                columns=[],
                field_labels={},
                metadata={
                    "command_source": "system_generated",
                    "requires_confirmation": True,
                    "execution_started": False,
                    "pending_created": False,
                    "side_effect_started": False,
                    "identity_status": "not_found",
                    "context_topic": "generate_commands",
                },
            )
        if identity.get("status") not in {"ok", "partial"}:
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail="device resolver failed: " + "; ".join(identity.get("warnings") or []),
                metadata={"side_effect_started": False, "execution_started": False},
            )

        device_context = self._identity_device_context(identity)
        device_type = str(device_context.get("device_type") or "generic")
        device_name = str(
            device_context.get("device_name")
            or device_context.get("host_name")
            or keyword
        )
        mgmt_ip = str(device_context.get("mgmt_ip") or "")
        spec = request.decision.command_generation
        category = str(spec.category or "device_health").strip().lower()
        max_commands = min(max(int(spec.max_commands), 1), 8)
        raw_specs = self.catalog(
            category=category,
            platform=device_type,
            interface_name=spec.interface,
        )
        raw_commands = [
            str(item.get("command") or "").strip()
            for item in raw_specs
            if isinstance(item, dict) and str(item.get("command") or "").strip()
        ]
        split = self.splitter(raw_commands, max_commands=max_commands)
        commands = list(split.commands)
        if not commands:
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail="deterministic command catalog produced no commands",
                metadata={"side_effect_started": False, "execution_started": False},
            )

        safety = self.safety_checker(commands, max_commands=max_commands)
        guard = self.guard_factory()
        guard_results = [
            guard.validate(
                command,
                platform=device_type,
                device_type=device_type,
            ).to_dict()
            for command in commands
        ]
        guard_failed = [
            item for item in guard_results if item.get("status") != "passed"
        ]
        if not safety.allowed or safety.blocked_commands or guard_failed:
            return HandlerOutcome.failure(
                action=self.action,
                handler_key=self.handler_key,
                detail="generated commands did not pass the complete read-only safety contract",
                metadata={
                    "command_source": "system_generated",
                    "requires_confirmation": True,
                    "execution_started": False,
                    "pending_created": False,
                    "side_effect_started": False,
                    "splitter": split.as_dict(),
                    "safety": safety.as_dict(),
                    "cli_guard": guard_results,
                },
            )

        purposes = {
            str(item.get("command") or "").strip(): str(item.get("purpose") or "")
            for item in raw_specs
            if isinstance(item, dict)
        }
        items: list[Dict[str, Any]] = []
        for index, (command, guard_result) in enumerate(
            zip(commands, guard_results),
            1,
        ):
            items.append(
                {
                    "index": index,
                    "device_name": device_name,
                    "mgmt_ip": mgmt_ip,
                    "device_type": device_type,
                    "category": category,
                    "interface_name": str(spec.interface or ""),
                    "command": command,
                    "purpose": purposes.get(command, "只读排障取证"),
                    "guard_status": guard_result.get("status"),
                    "risk_level": guard_result.get("risk_level"),
                    "matched_rule": guard_result.get("matched_rule"),
                    "guard_reasons": "；".join(guard_result.get("reasons") or []),
                    "command_source": "system_generated",
                    "requires_confirmation": True,
                    "confirm_required": "是",
                    "execution_started": False,
                    "pending_created": False,
                }
            )

        answer = (
            f"已为设备 {device_name} 生成 {len(items)} 条只读排障命令。"
            "本批仅生成并完成安全预检，没有执行设备命令，也没有创建待确认记录；"
            "系统生成命令后续必须经过明确确认才能进入执行阶段。"
        )
        columns = [
            "device_name",
            "mgmt_ip",
            "device_type",
            "command",
            "purpose",
            "guard_status",
            "risk_level",
            "command_source",
            "confirm_required",
        ]
        labels = {
            "device_name": "设备名称",
            "mgmt_ip": "管理IP",
            "device_type": "设备类型",
            "command": "建议只读命令",
            "purpose": "用途",
            "guard_status": "安全校验状态",
            "risk_level": "风险级别",
            "command_source": "命令来源",
            "confirm_required": "需要确认",
        }
        return HandlerOutcome.success(
            action=self.action,
            handler_key=self.handler_key,
            answer=answer,
            status="confirmation_required",
            source="deterministic_command_catalog",
            items=items,
            columns=columns,
            field_labels=labels,
            metadata={
                "command_source": "system_generated",
                "requires_confirmation": True,
                "execution_started": False,
                "pending_created": False,
                "side_effect_started": False,
                "identity_status": identity.get("status"),
                "splitter": split.as_dict(),
                "safety": safety.as_dict(),
                "cli_guard": guard_results,
                "context_topic": category,
                "device_context": device_context,
            },
        )
