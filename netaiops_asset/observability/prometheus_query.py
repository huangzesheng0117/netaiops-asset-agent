# -*- coding: utf-8 -*-
"""Guarded Prometheus query service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from netaiops_asset.mcp.prometheus_client import PrometheusMcpClient
from netaiops_asset.observability.promql_guard import PromqlGuard


class GuardedPrometheusQueryService:
    def __init__(
        self,
        prometheus_client: Optional[PrometheusMcpClient] = None,
        guard: Optional[PromqlGuard] = None,
    ) -> None:
        self.prometheus_client = prometheus_client or PrometheusMcpClient()
        self.guard = guard or PromqlGuard()

    def execute_instant(self, query: str, query_time: Optional[str] = None) -> Dict[str, Any]:
        guard_result = self.guard.validate_instant_query(query)
        if not guard_result.passed:
            return {
                "ok": False,
                "status": "rejected",
                "guard": guard_result.to_dict(),
                "result": None,
                "error": "PromQL rejected by guard",
            }

        result = self.prometheus_client.execute_query(query, query_time=query_time)
        return {
            "ok": result.ok,
            "status": "ok" if result.ok else "failed",
            "guard": guard_result.to_dict(),
            "result": result.content_json,
            "raw_text_preview": result.content_text[:2000],
            "error": result.error,
        }

    def execute_range(self, query: str, start: str, end: str, step: str) -> Dict[str, Any]:
        guard_result = self.guard.validate_range_query(query, start=start, end=end, step=step)
        if not guard_result.passed:
            return {
                "ok": False,
                "status": "rejected",
                "guard": guard_result.to_dict(),
                "result": None,
                "error": "PromQL range query rejected by guard",
            }

        result = self.prometheus_client.execute_range_query(query, start=start, end=end, step=step)
        return {
            "ok": result.ok,
            "status": "ok" if result.ok else "failed",
            "guard": guard_result.to_dict(),
            "result": result.content_json,
            "raw_text_preview": result.content_text[:2000],
            "error": result.error,
        }

    @staticmethod
    def plan_device_up_query(device_identity: Dict[str, Any]) -> Dict[str, Any]:
        mgmt_ip = str(device_identity.get("mgmt_ip") or "").strip()
        if not mgmt_ip:
            return {
                "ok": False,
                "error": "mgmt_ip is missing",
                "query": None,
                "purpose": "查询设备 up 状态",
            }

        safe_ip = mgmt_ip.replace('"', '\\"')
        return {
            "ok": True,
            "query": 'up{ip="' + safe_ip + '"}',
            "purpose": "查询设备 Prometheus up 状态",
            "labels": {
                "ip": mgmt_ip,
            },
        }

    @staticmethod
    def plan_count_up_query() -> Dict[str, Any]:
        return {
            "ok": True,
            "query": "count(up)",
            "purpose": "统计当前 Prometheus up 序列数量",
            "labels": {},
        }
