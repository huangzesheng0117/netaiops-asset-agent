# -*- coding: utf-8 -*-
"""Netmiko MCP client wrapper."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .client import McpClient, McpClientError, McpToolResult
from netaiops_asset.netmiko.cli_guard import CliReadOnlyGuard


DEFAULT_NETMIKO_MCP_SSE_URL = os.getenv(
    "NETAIOPS_NETMIKO_MCP_SSE_URL",
    "http://10.191.97.137:10000/sse",
)

ALLOWED_NETMIKO_TOOLS = {
    "get_network_device_list",
    "send_command_and_get_output",
}

BLOCKED_NETMIKO_TOOLS = {
    "set_config_commands_and_commit_or_save",
}


class NetmikoMcpClient:
    def __init__(self, sse_url: Optional[str] = None) -> None:
        self.sse_url = sse_url or DEFAULT_NETMIKO_MCP_SSE_URL

    def list_tools(self) -> List[Dict[str, Any]]:
        with McpClient("netmiko_mcp", self.sse_url) as client:
            return client.list_tools()

    def list_devices(self) -> List[Dict[str, Any]]:
        with McpClient("netmiko_mcp", self.sse_url, request_timeout=35) as client:
            result = client.call_tool("get_network_device_list", {}, timeout=35)

        if not result.ok:
            raise McpClientError("get_network_device_list failed: {}".format(result.error))

        if not isinstance(result.content_json, list):
            raise McpClientError("get_network_device_list returned non-list content")

        return result.content_json

    def validate_command(
        self,
        command: str,
        platform: Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        return CliReadOnlyGuard().validate(
            command,
            platform=platform,
            device_type=device_type,
        ).to_dict()

    def send_command_after_guard(
        self,
        name: str,
        command: str,
        guard_status: str,
        confirmed: bool,
        timeout: int = 60,
    ) -> McpToolResult:
        if guard_status != "passed":
            raise McpClientError("Netmiko command blocked because guard_status is not passed")

        if not confirmed:
            raise McpClientError("Netmiko command blocked because human confirmation is missing")

        if not name or not command:
            raise McpClientError("name and command are required")

        with McpClient("netmiko_mcp", self.sse_url, request_timeout=timeout) as client:
            return client.call_tool(
                "send_command_and_get_output",
                {
                    "name": name,
                    "command": command,
                },
                timeout=timeout,
            )
