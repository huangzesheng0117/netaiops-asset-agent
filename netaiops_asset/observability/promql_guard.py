# -*- coding: utf-8 -*-
"""
PromQL guard for V2.

The guard is intentionally conservative. It does not try to fully parse PromQL.
It only blocks common high-risk patterns and enforces bounded query_range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


HIGH_CARDINALITY_METRICS = {
    "ifHCInOctets",
    "ifHCOutOctets",
    "ifInErrors",
    "ifOutErrors",
    "ifInDiscards",
    "ifOutDiscards",
    "ifOperStatus",
    "ifAdminStatus",
    "ifHighSpeed",
    "ifDescr",
    "ifName",
    "ifAlias",
    "entSensorValue",
    "bgp4PathAttrASPathSegment",
    "bgp4PathAttrIpAddrPrefix",
}

ALLOWED_GLOBAL_QUERIES = {
    "count(up)",
    "sum(up)",
}


@dataclass
class PromqlGuardResult:
    passed: bool
    risk_level: str
    query_type: str
    normalized_query: str
    reasons: List[str]
    limits: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PromqlGuard:
    def __init__(
        self,
        max_query_length: int = 2000,
        max_range_seconds: int = 24 * 3600,
        min_step_seconds: int = 15,
        max_points: int = 1440,
    ) -> None:
        self.max_query_length = max_query_length
        self.max_range_seconds = max_range_seconds
        self.min_step_seconds = min_step_seconds
        self.max_points = max_points

    def validate_instant_query(self, query: str) -> PromqlGuardResult:
        q = self._normalize(query)
        reasons: List[str] = []

        self._validate_common(q, reasons)
        self._validate_high_cardinality(q, reasons)

        passed = not reasons
        return PromqlGuardResult(
            passed=passed,
            risk_level="low" if passed else "blocked",
            query_type="instant",
            normalized_query=q,
            reasons=reasons,
            limits={
                "max_query_length": self.max_query_length,
                "high_cardinality_metrics": sorted(HIGH_CARDINALITY_METRICS),
            },
        )

    def validate_range_query(self, query: str, start: str, end: str, step: str) -> PromqlGuardResult:
        q = self._normalize(query)
        reasons: List[str] = []

        self._validate_common(q, reasons)
        self._validate_high_cardinality(q, reasons)

        start_ts = self._parse_time_to_epoch(start)
        end_ts = self._parse_time_to_epoch(end)
        step_seconds = self._parse_step_seconds(step)

        if start_ts is None:
            reasons.append("invalid start time")
        if end_ts is None:
            reasons.append("invalid end time")
        if step_seconds is None:
            reasons.append("invalid step")

        range_seconds = None
        estimated_points = None

        if start_ts is not None and end_ts is not None:
            range_seconds = int(end_ts - start_ts)
            if range_seconds <= 0:
                reasons.append("end must be greater than start")
            if range_seconds > self.max_range_seconds:
                reasons.append("query range is too large: {}s > {}s".format(range_seconds, self.max_range_seconds))

        if step_seconds is not None:
            if step_seconds < self.min_step_seconds:
                reasons.append("step is too small: {}s < {}s".format(step_seconds, self.min_step_seconds))

        if range_seconds is not None and step_seconds is not None and step_seconds > 0:
            estimated_points = int(range_seconds / step_seconds) + 1
            if estimated_points > self.max_points:
                reasons.append("too many points: {} > {}".format(estimated_points, self.max_points))

        passed = not reasons
        return PromqlGuardResult(
            passed=passed,
            risk_level="low" if passed else "blocked",
            query_type="range",
            normalized_query=q,
            reasons=reasons,
            limits={
                "max_query_length": self.max_query_length,
                "max_range_seconds": self.max_range_seconds,
                "min_step_seconds": self.min_step_seconds,
                "max_points": self.max_points,
                "range_seconds": range_seconds,
                "step_seconds": step_seconds,
                "estimated_points": estimated_points,
            },
        )

    def _normalize(self, query: str) -> str:
        return re.sub(r"\s+", " ", str(query or "").strip())

    def _validate_common(self, query: str, reasons: List[str]) -> None:
        if not query:
            reasons.append("query is empty")
            return

        if len(query) > self.max_query_length:
            reasons.append("query is too long")

        lowered = query.lower()
        suspicious = [
            "http://",
            "https://",
            ";",
            "|",
            "`",
            "$(",
        ]
        for item in suspicious:
            if item in lowered:
                reasons.append("query contains suspicious token: {}".format(item))

    def _validate_high_cardinality(self, query: str, reasons: List[str]) -> None:
        compact = query.replace(" ", "")
        if compact in ALLOWED_GLOBAL_QUERIES:
            return

        for metric in sorted(HIGH_CARDINALITY_METRICS):
            if not re.search(r"(?<![A-Za-z0-9_:])" + re.escape(metric) + r"(?![A-Za-z0-9_:])", query):
                continue

            # Metric with explicit label selector is acceptable for this guard.
            if re.search(re.escape(metric) + r"\s*\{[^}]+\}", query):
                continue

            reasons.append(
                "high-cardinality metric '{}' must include explicit label selector".format(metric)
            )

    def _parse_step_seconds(self, step: str) -> Optional[int]:
        text = str(step or "").strip()
        if not text:
            return None

        if re.match(r"^\d+$", text):
            return int(text)

        m = re.match(r"^(\d+)(s|m|h|d)$", text)
        if not m:
            return None

        value = int(m.group(1))
        unit = m.group(2)

        if unit == "s":
            return value
        if unit == "m":
            return value * 60
        if unit == "h":
            return value * 3600
        if unit == "d":
            return value * 86400

        return None

    def _parse_time_to_epoch(self, value: str) -> Optional[float]:
        text = str(value or "").strip()
        if not text:
            return None

        if re.match(r"^\d+(\.\d+)?$", text):
            return float(text)

        normalized = text.replace("Z", "+00:00")

        try:
            return datetime.fromisoformat(normalized).timestamp()
        except Exception:
            pass

        for fmt in [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M",
        ]:
            try:
                return datetime.strptime(text, fmt).timestamp()
            except Exception:
                continue

        return None
