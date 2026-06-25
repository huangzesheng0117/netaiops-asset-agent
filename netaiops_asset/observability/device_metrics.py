# -*- coding: utf-8 -*-
"""
Device metric probes for V2 chat router.

Safety:
- Prometheus read-only query only.
- No device CLI execution.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from netaiops_asset.mcp.prometheus_client import PrometheusMcpClient
from netaiops_asset.observability.prometheus_query import GuardedPrometheusQueryService


CPU_QUERY_TEMPLATES = [
    'avg(cpmCPUTotal5minRev{ip="%s"})',
    'avg(cpmCPUTotal5min{ip="%s"})',
    'avg(cpmCPUTotal1minRev{ip="%s"})',
    'avg(cpmCPUTotal1min{ip="%s"})',
    'avg(hrProcessorLoad{ip="%s"})',
    'avg(cpu_usage{ip="%s"})',
    'avg(cpu_utilization{ip="%s"})',
    'avg(device_cpu_usage{ip="%s"})',
    'avg(system_cpu_usage{ip="%s"})',
    'avg(sysCpuUsage{ip="%s"})',
]


class DeviceMetricProbe:
    def __init__(
        self,
        prometheus_client: Optional[PrometheusMcpClient] = None,
        query_service: Optional[GuardedPrometheusQueryService] = None,
    ) -> None:
        self.prometheus_client = prometheus_client or PrometheusMcpClient()
        self.query_service = query_service or GuardedPrometheusQueryService(
            prometheus_client=self.prometheus_client
        )

    def probe_cpu(self, identity: Dict[str, Any]) -> Dict[str, Any]:
        mgmt_ip = str(identity.get("mgmt_ip") or "").strip()
        hostname = str(identity.get("hostname") or "").strip()

        result: Dict[str, Any] = {
            "status": "skipped",
            "metric_type": "cpu",
            "mgmt_ip": mgmt_ip,
            "hostname": hostname,
            "matched": None,
            "attempts": [],
            "metric_hints": [],
            "summary": "",
        }

        if not mgmt_ip:
            result["summary"] = "缺少管理 IP，跳过 Prometheus CPU 查询。"
            return result

        safe_ip = mgmt_ip.replace('"', '\\"')

        for template in CPU_QUERY_TEMPLATES:
            query = template % safe_ip
            one = self.query_service.execute_instant(query)
            compact = self._compact_query_result(query, one)
            result["attempts"].append(compact)

            if compact.get("has_data"):
                result["status"] = "ok"
                result["matched"] = compact
                result["summary"] = "Prometheus 当前 CPU 查询命中：query={}，value={}".format(
                    query,
                    compact.get("sample_value"),
                )
                return result

        result["status"] = "no_data"
        result["metric_hints"] = self.list_cpu_metric_hints()
        result["summary"] = "未从内置 CPU 指标候选中查询到当前 CPU 数据；可能需要补充现网 CPU 指标名映射。"
        return result

    def probe_up(self, identity: Dict[str, Any]) -> Dict[str, Any]:
        mgmt_ip = str(identity.get("mgmt_ip") or "").strip()
        if not mgmt_ip:
            return {
                "status": "skipped",
                "metric_type": "up",
                "summary": "缺少管理 IP，跳过 up 查询。",
            }

        safe_ip = mgmt_ip.replace('"', '\\"')
        query = 'up{ip="' + safe_ip + '"}'
        data = self.query_service.execute_instant(query)
        return self._compact_query_result(query, data)

    def list_cpu_metric_hints(self) -> List[str]:
        hints: List[str] = []
        patterns = [
            "cpu",
            "CPU",
            "processor",
            "Processor",
            "cpmCPU",
            "hrProcessor",
        ]

        for pattern in patterns:
            try:
                result = self.prometheus_client.list_metrics(limit=30, filter_pattern=pattern)
                content = result.content_json
                if isinstance(content, dict):
                    metrics = content.get("metrics") or []
                    for name in metrics:
                        if name not in hints:
                            hints.append(name)
            except Exception:
                continue

            if len(hints) >= 50:
                break

        return hints[:50]

    def _compact_query_result(self, query: str, data: Dict[str, Any]) -> Dict[str, Any]:
        compact: Dict[str, Any] = {
            "query": query,
            "ok": bool(data.get("ok")),
            "status": data.get("status"),
            "error": data.get("error"),
            "has_data": False,
            "result_type": None,
            "series_count": 0,
            "sample_value": None,
            "sample_metric": None,
        }

        result = data.get("result")
        if not isinstance(result, dict):
            return compact

        compact["result_type"] = result.get("resultType")
        vector = result.get("result")
        if not isinstance(vector, list):
            return compact

        compact["series_count"] = len(vector)

        if not vector:
            return compact

        sample = vector[0]
        if not isinstance(sample, dict):
            return compact

        metric = sample.get("metric")
        value = sample.get("value")

        compact["sample_metric"] = metric if isinstance(metric, dict) else None

        if isinstance(value, list) and len(value) >= 2:
            compact["sample_value"] = value[1]
            compact["has_data"] = True

        return compact


def format_cpu_evidence_for_answer(evidence: Optional[Dict[str, Any]]) -> List[str]:
    if not evidence:
        return []

    status = evidence.get("status")
    lines = []

    lines.append("Prometheus 当前 CPU 证据：")

    if status == "ok":
        matched = evidence.get("matched") or {}
        lines.append("已命中 CPU 指标。")
        lines.append("PromQL：{}".format(matched.get("query")))
        lines.append("当前值：{}".format(matched.get("sample_value")))
        if matched.get("sample_metric"):
            lines.append("样例标签：{}".format(matched.get("sample_metric")))
    elif status == "no_data":
        lines.append("未从内置 CPU 指标候选中查询到当前 CPU 数据。")
        hints = evidence.get("metric_hints") or []
        if hints:
            lines.append("Prometheus 中疑似 CPU 指标候选：{}".format(", ".join(hints[:15])))
        lines.append("后续建议补充现网 CPU 指标名映射，或继续通过 Netmiko 命令取证。")
    elif status == "skipped":
        lines.append(evidence.get("summary") or "跳过 CPU 查询。")
    else:
        lines.append(evidence.get("summary") or "CPU 查询状态未知。")

    return lines
