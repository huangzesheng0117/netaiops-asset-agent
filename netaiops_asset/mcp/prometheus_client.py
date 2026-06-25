# -*- coding: utf-8 -*-
"""Prometheus MCP client wrapper."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from .client import McpClient, McpClientError, McpToolResult


DEFAULT_PROMETHEUS_MCP_SSE_URL = os.getenv(
    "NETAIOPS_PROMETHEUS_MCP_SSE_URL",
    "http://10.191.97.137:10001/sse",
)

DEFAULT_PROMETHEUS_DIRECT_URL = os.getenv(
    "NETAIOPS_PROMETHEUS_DIRECT_URL",
    "http://10.191.96.43:9090",
)


class PrometheusMcpClient:
    def __init__(
        self,
        sse_url: Optional[str] = None,
        direct_prometheus_url: Optional[str] = None,
    ) -> None:
        self.sse_url = sse_url or DEFAULT_PROMETHEUS_MCP_SSE_URL
        self.direct_prometheus_url = direct_prometheus_url or DEFAULT_PROMETHEUS_DIRECT_URL

    def list_tools(self):
        with McpClient("prometheus_mcp", self.sse_url) as client:
            return client.list_tools()

    def health_check(self) -> McpToolResult:
        with McpClient("prometheus_mcp", self.sse_url) as client:
            return client.call_tool("health_check", {}, timeout=20)

    def list_metrics(
        self,
        limit: int = 50,
        offset: int = 0,
        filter_pattern: Optional[str] = None,
        refresh_cache: bool = False,
    ) -> McpToolResult:
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))

        args: Dict[str, Any] = {
            "limit": safe_limit,
            "offset": safe_offset,
            "refresh_cache": bool(refresh_cache),
        }
        if filter_pattern:
            args["filter_pattern"] = filter_pattern

        with McpClient("prometheus_mcp", self.sse_url, request_timeout=40) as client:
            return client.call_tool("list_metrics", args, timeout=40)

    def execute_query(
        self,
        query: str,
        query_time: Optional[str] = None,
        timeout: int = 35,
    ) -> McpToolResult:
        if not query or not str(query).strip():
            raise McpClientError("PromQL query is required")

        args: Dict[str, Any] = {"query": query}
        if query_time:
            args["time"] = query_time

        with McpClient("prometheus_mcp", self.sse_url, request_timeout=timeout) as client:
            return client.call_tool("execute_query", args, timeout=timeout)

    def execute_range_query(
        self,
        query: str,
        start: str,
        end: str,
        step: str,
        timeout: int = 45,
    ) -> McpToolResult:
        if not query or not start or not end or not step:
            raise McpClientError("query, start, end and step are required")

        args = {
            "query": query,
            "start": start,
            "end": end,
            "step": step,
        }

        with McpClient("prometheus_mcp", self.sse_url, request_timeout=timeout) as client:
            return client.call_tool("execute_range_query", args, timeout=timeout)

    def get_targets_via_direct_prometheus(self, state: str = "active", timeout: int = 10) -> Dict[str, Any]:
        base = self.direct_prometheus_url.rstrip("/")
        url = base + "/api/v1/targets?" + urllib.parse.urlencode({"state": state})

        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(5 * 1024 * 1024).decode("utf-8", errors="replace")
                data = json.loads(body)
                active = data.get("data", {}).get("activeTargets", [])
                dropped = data.get("data", {}).get("droppedTargets", [])
                return {
                    "ok": True,
                    "status": resp.status,
                    "active_targets_count": len(active),
                    "dropped_targets_count": len(dropped),
                    "raw": data,
                }
        except Exception as exc:
            return {
                "ok": False,
                "error": repr(exc),
            }
