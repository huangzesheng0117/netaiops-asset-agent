# -*- coding: utf-8 -*-
"""
Device identity resolver.

Goal:
- Link user input to CMDB device identity.
- Link CMDB hostname / mgmt_ip to Netmiko MCP device name.
- Build Prometheus label candidates.
- Optionally probe Prometheus up{ip="..."} to discover real labels.

Safety:
- This module only reads CMDB / Netmiko device list / Prometheus metrics.
- It does NOT execute any network device CLI command.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from netaiops_asset.cmdb.field_map import DETAIL_FIELDS, normalize_fields
from netaiops_asset.tools.cmdb_tools import tool_query_cmdb_device_detail
from netaiops_asset.mcp.netmiko_client import NetmikoMcpClient
from netaiops_asset.mcp.prometheus_client import PrometheusMcpClient


IDENTITY_FIELDS = normalize_fields(
    [
        "host_name",
        "mgmt_ip",
        "sn",
        "device_spec",
        "status",
        "IDC",
        "server_room",
        "rack",
        "manufacturer",
        "vendor",
        "brand",
        "device_type",
        "use",
        "purpose",
        "env",
        "environment",
        "asset_code",
        "em_code",
    ],
    DETAIL_FIELDS,
)


@dataclass
class DeviceIdentityResult:
    status: str
    keyword: str
    keyword_type: str
    cmdb_count: int
    cmdb_items: List[Dict[str, Any]]
    selected_cmdb: Optional[Dict[str, Any]]
    hostname: Optional[str]
    mgmt_ip: Optional[str]
    netmiko_match: Optional[Dict[str, Any]]
    netmiko_match_reason: Optional[str]
    prometheus_label_candidates: Dict[str, List[str]]
    prometheus_up_probe: Optional[Dict[str, Any]]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeviceIdentityResolver:
    def __init__(
        self,
        netmiko_client: Optional[NetmikoMcpClient] = None,
        prometheus_client: Optional[PrometheusMcpClient] = None,
    ) -> None:
        self.netmiko_client = netmiko_client or NetmikoMcpClient()
        self.prometheus_client = prometheus_client or PrometheusMcpClient()

    @staticmethod
    def detect_keyword_type(keyword: str) -> str:
        text = str(keyword or "").strip()
        if not text:
            return "empty"

        try:
            ipaddress.ip_address(text)
            return "ip"
        except Exception:
            pass

        if re.match(r"^[A-Za-z0-9_.:-]+$", text):
            return "name_or_code"

        return "text"

    @staticmethod
    def _norm(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _lower(value: Any) -> str:
        return str(value or "").strip().lower()

    def query_cmdb(self, keyword: str) -> Dict[str, Any]:
        return tool_query_cmdb_device_detail(keyword=keyword, fields=IDENTITY_FIELDS)

    def select_cmdb_item(self, keyword: str, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not items:
            return None

        keyword_l = self._lower(keyword)

        for item in items:
            if self._lower(item.get("mgmt_ip")) == keyword_l:
                return item

        for item in items:
            if self._lower(item.get("host_name")) == keyword_l:
                return item

        for item in items:
            if self._lower(item.get("sn")) == keyword_l:
                return item

        return items[0]

    def find_netmiko_match(
        self,
        keyword: str,
        hostname: Optional[str],
        mgmt_ip: Optional[str],
        devices: List[Dict[str, Any]],
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        keyword_l = self._lower(keyword)
        hostname_l = self._lower(hostname)
        mgmt_ip_l = self._lower(mgmt_ip)

        for dev in devices:
            if self._lower(dev.get("name")) == hostname_l and hostname_l:
                return dev, "cmdb.host_name == netmiko.name"

        for dev in devices:
            if self._lower(dev.get("hostname")) == mgmt_ip_l and mgmt_ip_l:
                return dev, "cmdb.mgmt_ip == netmiko.hostname"

        for dev in devices:
            if self._lower(dev.get("name")) == keyword_l and keyword_l:
                return dev, "keyword == netmiko.name"

        for dev in devices:
            if self._lower(dev.get("hostname")) == keyword_l and keyword_l:
                return dev, "keyword == netmiko.hostname"

        return None, None

    def build_prometheus_label_candidates(
        self,
        keyword: str,
        hostname: Optional[str],
        mgmt_ip: Optional[str],
        netmiko_match: Optional[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        candidates: Dict[str, List[str]] = {}

        def add(label: str, value: Any) -> None:
            text = self._norm(value)
            if not text:
                return
            candidates.setdefault(label, [])
            if text not in candidates[label]:
                candidates[label].append(text)

        def is_ip_literal(value: Any) -> bool:
            text = self._norm(value)
            if not text:
                return False
            try:
                ipaddress.ip_address(text)
                return True
            except Exception:
                return False

        def add_ip(label: str, value: Any) -> None:
            if is_ip_literal(value):
                add(label, value)

        def add_name(value: Any) -> None:
            text = self._norm(value)
            if not text:
                return
            if is_ip_literal(text):
                return
            add("hostname", text)
            add("host_name", text)
            add("host", text)
            add("name", text)
            add("device", text)
            add("sysName", text)

        # IP-like labels must only receive real IP literals.
        add_ip("ip", mgmt_ip)
        add_ip("instance", mgmt_ip)
        add_ip("ip", keyword)
        add_ip("instance", keyword)

        # Name-like labels must not receive IP literals.
        add_name(hostname)
        add_name(keyword)

        if netmiko_match:
            # In this Netmiko inventory, "hostname" is the login address/IP,
            # while "name" is the device name.
            add_ip("ip", netmiko_match.get("hostname"))
            add_ip("instance", netmiko_match.get("hostname"))
            add_name(netmiko_match.get("name"))

        return candidates

    def probe_prometheus_up(self, mgmt_ip: Optional[str]) -> Optional[Dict[str, Any]]:
        ip_text = self._norm(mgmt_ip)
        if not ip_text:
            return None

        promql = 'up{ip="' + ip_text.replace('"', '\\"') + '"}'

        try:
            result = self.prometheus_client.execute_query(promql, timeout=25)
            content = result.content_json
            if isinstance(content, dict):
                vector = content.get("result") or []
                labels = []
                for item in vector[:10]:
                    metric = item.get("metric") if isinstance(item, dict) else None
                    if isinstance(metric, dict):
                        labels.append(metric)
                return {
                    "ok": result.ok,
                    "is_error": result.is_error,
                    "query": promql,
                    "result_type": content.get("resultType"),
                    "series_count": len(vector) if isinstance(vector, list) else None,
                    "sample_labels": labels,
                    "error": result.error,
                }

            return {
                "ok": result.ok,
                "is_error": result.is_error,
                "query": promql,
                "raw_text_preview": result.content_text[:1000],
                "error": result.error,
            }

        except Exception as exc:
            return {
                "ok": False,
                "query": promql,
                "error": repr(exc),
            }

    def resolve(self, keyword: str, probe_prometheus: bool = True) -> Dict[str, Any]:
        keyword = self._norm(keyword)
        keyword_type = self.detect_keyword_type(keyword)
        warnings: List[str] = []

        if not keyword:
            return DeviceIdentityResult(
                status="error",
                keyword=keyword,
                keyword_type=keyword_type,
                cmdb_count=0,
                cmdb_items=[],
                selected_cmdb=None,
                hostname=None,
                mgmt_ip=None,
                netmiko_match=None,
                netmiko_match_reason=None,
                prometheus_label_candidates={},
                prometheus_up_probe=None,
                warnings=["keyword is empty"],
            ).to_dict()

        cmdb_result = self.query_cmdb(keyword)
        cmdb_items = cmdb_result.get("items") or []
        if not isinstance(cmdb_items, list):
            cmdb_items = []

        if cmdb_result.get("status") != "ok":
            warnings.append("CMDB query status is not ok: {}".format(cmdb_result.get("error_code") or cmdb_result.get("message")))

        selected = self.select_cmdb_item(keyword, cmdb_items)
        hostname = self._norm(selected.get("host_name")) if isinstance(selected, dict) else None
        mgmt_ip = self._norm(selected.get("mgmt_ip")) if isinstance(selected, dict) else None

        devices: List[Dict[str, Any]] = []
        netmiko_match = None
        netmiko_reason = None

        try:
            devices = self.netmiko_client.list_devices()
            netmiko_match, netmiko_reason = self.find_netmiko_match(keyword, hostname, mgmt_ip, devices)
            if not netmiko_match:
                warnings.append("No matching Netmiko device found")
        except Exception as exc:
            warnings.append("Netmiko device list failed: {}".format(repr(exc)))

        label_candidates = self.build_prometheus_label_candidates(keyword, hostname, mgmt_ip, netmiko_match)

        prometheus_probe = None
        if probe_prometheus:
            prometheus_probe = self.probe_prometheus_up(mgmt_ip)
            if prometheus_probe and not prometheus_probe.get("ok"):
                warnings.append("Prometheus up probe failed or returned no ok result")

        status = "ok"
        if not selected and not netmiko_match:
            status = "not_found"
        elif warnings:
            status = "partial"

        return DeviceIdentityResult(
            status=status,
            keyword=keyword,
            keyword_type=keyword_type,
            cmdb_count=int(cmdb_result.get("count") or len(cmdb_items)),
            cmdb_items=cmdb_items,
            selected_cmdb=selected,
            hostname=hostname,
            mgmt_ip=mgmt_ip,
            netmiko_match=netmiko_match,
            netmiko_match_reason=netmiko_reason,
            prometheus_label_candidates=label_candidates,
            prometheus_up_probe=prometheus_probe,
            warnings=warnings,
        ).to_dict()
