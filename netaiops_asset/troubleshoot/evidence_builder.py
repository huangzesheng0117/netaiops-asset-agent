# -*- coding: utf-8 -*-
"""
Evidence builder for V2 troubleshooting.

This module aggregates:
- CMDB/device identity evidence
- Prometheus guarded query evidence
- Netmiko confirmed execution audit evidence

Safety:
- It does not execute device CLI.
- It may execute Prometheus read-only query through guarded service.
- Netmiko evidence is read from existing audit files only.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

from netaiops_asset.device_identity.resolver import DeviceIdentityResolver
from netaiops_asset.observability.prometheus_query import GuardedPrometheusQueryService


DEFAULT_NETMIKO_AUDIT_DIR = os.getenv(
    "NETAIOPS_NETMIKO_EXEC_AUDIT_DIR",
    "/var/lib/netaiops-asset-agent/data/v2_netmiko_exec_audit",
)


class EvidenceBuilder:
    def __init__(
        self,
        identity_resolver: Optional[DeviceIdentityResolver] = None,
        prometheus_service: Optional[GuardedPrometheusQueryService] = None,
        netmiko_audit_dir: str = DEFAULT_NETMIKO_AUDIT_DIR,
    ) -> None:
        self.identity_resolver = identity_resolver or DeviceIdentityResolver()
        self.prometheus_service = prometheus_service or GuardedPrometheusQueryService()
        self.netmiko_audit_dir = netmiko_audit_dir

    def build_identity_evidence(self, keyword: str) -> Dict[str, Any]:
        identity = self.identity_resolver.resolve(keyword, probe_prometheus=True)

        hostname = identity.get("hostname")
        mgmt_ip = identity.get("mgmt_ip")
        status = identity.get("status")

        if hostname or mgmt_ip:
            summary = "CMDB解析到设备：hostname={}，mgmt_ip={}，状态={}".format(
                hostname or "-",
                mgmt_ip or "-",
                status,
            )
            ev_status = "ok" if status in ("ok", "partial") else "failed"
        else:
            summary = "未能从CMDB/Netmiko解析到明确设备身份"
            ev_status = "no_data"

        return {
            "source": "cmdb_netmiko_prometheus_identity",
            "evidence_type": "device_identity",
            "status": ev_status,
            "title": "设备身份解析",
            "summary": summary,
            "payload": identity,
        }

    def build_prometheus_up_evidence(self, identity: Dict[str, Any]) -> Dict[str, Any]:
        plan = self.prometheus_service.plan_device_up_query(identity)
        if not plan.get("ok"):
            return {
                "source": "prometheus_mcp",
                "evidence_type": "prometheus_up",
                "status": "skipped",
                "title": "Prometheus up状态",
                "summary": "缺少mgmt_ip，跳过Prometheus up查询",
                "payload": {
                    "plan": plan,
                },
            }

        result = self.prometheus_service.execute_instant(plan["query"])
        content = result.get("result") if isinstance(result, dict) else None

        series_count = None
        sample = []
        if isinstance(content, dict):
            values = content.get("result")
            if isinstance(values, list):
                series_count = len(values)
                sample = values[:5]

        if result.get("ok"):
            summary = "Prometheus查询成功：query={}，series_count={}".format(
                plan.get("query"),
                series_count,
            )
            status = "ok"
        elif result.get("status") == "rejected":
            summary = "Prometheus查询被Guard拒绝：query={}".format(plan.get("query"))
            status = "rejected"
        else:
            summary = "Prometheus查询失败：query={}，error={}".format(
                plan.get("query"),
                result.get("error"),
            )
            status = "failed"

        return {
            "source": "prometheus_mcp",
            "evidence_type": "prometheus_up",
            "status": status,
            "title": "Prometheus up状态",
            "summary": summary,
            "payload": {
                "plan": plan,
                "query_result": result,
                "series_count": series_count,
                "sample": sample,
            },
        }

    def build_latest_netmiko_audit_evidence(self) -> Dict[str, Any]:
        audit = self.find_latest_executed_netmiko_audit()
        if not audit:
            return {
                "source": "netmiko_mcp_audit",
                "evidence_type": "netmiko_confirmed_execution",
                "status": "no_data",
                "title": "Netmiko只读取证审计",
                "summary": "未找到已执行成功的Netmiko审计文件",
                "payload": {},
            }

        plan = audit.get("plan") or {}
        guard = plan.get("guard") or {}

        summary = "读取最近一次Netmiko审计：device={}，command={}，status={}，guard_status={}".format(
            plan.get("device_name"),
            plan.get("command"),
            audit.get("status"),
            guard.get("status"),
        )

        return {
            "source": "netmiko_mcp_audit",
            "evidence_type": "netmiko_confirmed_execution",
            "status": "ok" if audit.get("status") == "executed" else "failed",
            "title": "Netmiko只读取证审计",
            "summary": summary,
            "payload": audit,
        }

    def find_latest_executed_netmiko_audit(self) -> Optional[Dict[str, Any]]:
        pattern = os.path.join(self.netmiko_audit_dir, "*.json")
        paths = sorted(glob.glob(pattern), reverse=True)

        fallback = None
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["_audit_file"] = path
                if fallback is None:
                    fallback = data
                if data.get("status") == "executed":
                    return data
            except Exception:
                continue

        return fallback

    def build_summary(self, evidences: List[Dict[str, Any]]) -> str:
        lines = []
        lines.append("本轮排障证据汇总：")

        for idx, ev in enumerate(evidences, 1):
            lines.append(
                "{}. [{}] {}：{}".format(
                    idx,
                    ev.get("status"),
                    ev.get("title"),
                    ev.get("summary"),
                )
            )

        ok_count = sum(1 for ev in evidences if ev.get("status") == "ok")
        failed_count = sum(1 for ev in evidences if ev.get("status") in ("failed", "rejected"))
        no_data_count = sum(1 for ev in evidences if ev.get("status") in ("no_data", "skipped"))

        lines.append(
            "证据统计：ok={}，failed/rejected={}，no_data/skipped={}".format(
                ok_count,
                failed_count,
                no_data_count,
            )
        )

        return "\n".join(lines)
