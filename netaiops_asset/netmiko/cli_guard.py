# -*- coding: utf-8 -*-
"""
CLI read-only guard for Netmiko MCP execution.

This guard is intentionally conservative.

Status meanings:
- passed: command is clearly read-only and can enter human confirmation stage.
- review: command may be read-only but is sensitive/high-output/high-risk; do not execute automatically.
- blocked: command is configuration/change/reboot/save/delete/debug-dangerous; must not execute.

Safety boundary:
- This module only validates text commands.
- It does NOT execute any device command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


STATUS_PASSED = "passed"
STATUS_REVIEW = "review"
STATUS_BLOCKED = "blocked"


PLATFORM_ALIASES = {
    "cisco_xe": "cisco",
    "cisco_ios": "cisco",
    "cisco_nxos": "cisco",
    "cisco_asa": "cisco",
    "nxos": "cisco",
    "ios": "cisco",
    "iosxe": "cisco",
    "huawei": "huawei",
    "huawei_vrp": "huawei",
    "hp_comware": "h3c",
    "h3c": "h3c",
    "fortinet": "fortigate",
    "fortigate": "fortigate",
    "fortios": "fortigate",
    "f5": "f5",
    "f5_tmsh": "f5",
    "hillstone": "hillstone",
}


GENERIC_DANGEROUS_PREFIXES = [
    "configure",
    "conf ",
    "config ",
    "system-view",
    "edit ",
    "set ",
    "unset ",
    "delete ",
    "remove ",
    "rename ",
    "commit",
    "save",
    "write",
    "copy ",
    "reload",
    "reboot",
    "restart",
    "shutdown",
    "no shutdown",
    "clear ",
    "reset ",
    "format ",
    "erase ",
    "install ",
    "upgrade ",
    "request system",
    "request chassis",
    "run util",
    "tmsh modify",
    "tmsh create",
    "tmsh delete",
    "tmsh save",
    "tmsh load",
    "tmsh run util",
    "execute ",
]


DANGEROUS_CONTAINS = [
    " commit",
    " reload",
    " reboot",
    " shutdown",
    " no shutdown",
    " save",
    " delete",
    " factory-reset",
    " format",
    " erase",
    " enable password",
    " password ",
]


SUSPICIOUS_TOKENS = [
    ";",
    "&&",
    "||",
    "`",
    "$(",
    ">",
    "<",
]


SENSITIVE_PATTERNS = [
    r"^show\s+running-config\b",
    r"^show\s+startup-config\b",
    r"^show\s+tech\b",
    r"^show\s+tech-support\b",
    r"^display\s+current-configuration\b",
    r"^display\s+saved-configuration\b",
    r"^display\s+diagnostic-information\b",
    r"^show\s+full-configuration\b",
    r"^show\s+configuration\b",
    r"^tmsh\s+list\s+auth\b",
    r"^list\s+auth\b",
]


HIGH_RISK_DEBUG_PATTERNS = [
    r"^debug\b",
    r"^undebug\b",
    r"^diagnose\s+debug\b",
    r"^diag\s+debug\b",
    r"^terminal\s+monitor\b",
    r"^monitor\s+capture\b",
]


REVIEW_PREFIXES = [
    "ping",
    "traceroute",
    "tracert",
    "telnet",
    "ssh ",
]


ALLOWED_FILTERS_AFTER_PIPE = [
    "include",
    "exclude",
    "begin",
    "section",
    "count",
    "grep",
    "match",
    "find",
    "display",
    "no-more",
    "json",
    "i ",
    "e ",
]


PLATFORM_ALLOWED_PREFIXES = {
    "generic": [
        "show ",
        "display ",
        "get ",
        "diagnose ",
        "diag ",
        "tmsh show ",
        "tmsh list ",
        "list ",
    ],
    "cisco": [
        "show ",
    ],
    "huawei": [
        "display ",
    ],
    "h3c": [
        "display ",
    ],
    "fortigate": [
        "get ",
        "diagnose ",
        "diag ",
        "show ",
    ],
    "f5": [
        "show ",
        "list ",
        "tmsh show ",
        "tmsh list ",
    ],
    "hillstone": [
        "show ",
        "get ",
    ],
}


@dataclass
class CliGuardResult:
    status: str
    passed: bool
    risk_level: str
    platform: str
    original_command: str
    normalized_command: str
    reasons: List[str]
    matched_rule: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CliReadOnlyGuard:
    def normalize_platform(self, platform: Optional[str] = None, device_type: Optional[str] = None) -> str:
        raw = str(platform or device_type or "generic").strip().lower()
        if not raw:
            return "generic"
        return PLATFORM_ALIASES.get(raw, raw)

    def normalize_command(self, command: str) -> str:
        text = str(command or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def validate(
        self,
        command: str,
        platform: Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> CliGuardResult:
        normalized = self.normalize_command(command)
        lowered = normalized.lower()
        normalized_platform = self.normalize_platform(platform=platform, device_type=device_type)
        reasons: List[str] = []

        if not normalized:
            return self._blocked(normalized_platform, command, normalized, ["command is empty"], "empty")

        if "\n" in str(command or "") or "\r" in str(command or ""):
            return self._blocked(normalized_platform, command, normalized, ["multi-line command is not allowed"], "multi_line")

        pipe_error = self._validate_pipe_usage(lowered)
        if pipe_error:
            return self._blocked(normalized_platform, command, normalized, [pipe_error], "unsafe_pipe")

        for token in SUSPICIOUS_TOKENS:
            if token in lowered:
                return self._blocked(
                    normalized_platform,
                    command,
                    normalized,
                    ["command contains suspicious token: {}".format(token)],
                    "suspicious_token",
                )

        for pattern in HIGH_RISK_DEBUG_PATTERNS:
            if re.search(pattern, lowered):
                return self._review(
                    normalized_platform,
                    command,
                    normalized,
                    ["debug/capture/terminal-monitor command requires manual special handling"],
                    "high_risk_debug",
                )

        for pattern in SENSITIVE_PATTERNS:
            if re.search(pattern, lowered):
                return self._review(
                    normalized_platform,
                    command,
                    normalized,
                    ["sensitive or high-output read-only command requires special approval"],
                    "sensitive_readonly",
                )

        for prefix in REVIEW_PREFIXES:
            if lowered == prefix.strip() or lowered.startswith(prefix):
                return self._review(
                    normalized_platform,
                    command,
                    normalized,
                    ["active probing command requires special approval"],
                    "active_probe",
                )

        for prefix in GENERIC_DANGEROUS_PREFIXES:
            if lowered == prefix.strip() or lowered.startswith(prefix):
                return self._blocked(
                    normalized_platform,
                    command,
                    normalized,
                    ["configuration/change command is not allowed"],
                    "dangerous_prefix",
                )

        for item in DANGEROUS_CONTAINS:
            if item in " " + lowered + " ":
                return self._blocked(
                    normalized_platform,
                    command,
                    normalized,
                    ["command contains dangerous keyword: {}".format(item.strip())],
                    "dangerous_contains",
                )

        allowed_prefixes = PLATFORM_ALLOWED_PREFIXES.get(normalized_platform, PLATFORM_ALLOWED_PREFIXES["generic"])
        if not any(lowered == p.strip() or lowered.startswith(p) for p in allowed_prefixes):
            return self._review(
                normalized_platform,
                command,
                normalized,
                ["command prefix is not in allowlist for platform {}".format(normalized_platform)],
                "unknown_prefix",
            )

        if normalized_platform == "fortigate" and lowered.startswith("show "):
            return self._review(
                normalized_platform,
                command,
                normalized,
                ["FortiGate show may expose configuration; require special approval"],
                "fortigate_show_config_sensitive",
            )

        return CliGuardResult(
            status=STATUS_PASSED,
            passed=True,
            risk_level="readonly",
            platform=normalized_platform,
            original_command=str(command or ""),
            normalized_command=normalized,
            reasons=[],
            matched_rule="readonly_allowlist",
        )

    def _validate_pipe_usage(self, lowered: str) -> Optional[str]:
        if "|" not in lowered:
            return None

        parts = [p.strip() for p in lowered.split("|")]
        if not parts[0]:
            return "empty command before pipe"

        for part in parts[1:]:
            if not part:
                return "empty filter after pipe"
            if not any(part == f.strip() or part.startswith(f) for f in ALLOWED_FILTERS_AFTER_PIPE):
                return "unsupported pipe filter: {}".format(part[:50])

        return None

    def _blocked(self, platform: str, original: str, normalized: str, reasons: List[str], rule: str) -> CliGuardResult:
        return CliGuardResult(
            status=STATUS_BLOCKED,
            passed=False,
            risk_level="dangerous",
            platform=platform,
            original_command=str(original or ""),
            normalized_command=normalized,
            reasons=reasons,
            matched_rule=rule,
        )

    def _review(self, platform: str, original: str, normalized: str, reasons: List[str], rule: str) -> CliGuardResult:
        return CliGuardResult(
            status=STATUS_REVIEW,
            passed=False,
            risk_level="review_required",
            platform=platform,
            original_command=str(original or ""),
            normalized_command=normalized,
            reasons=reasons,
            matched_rule=rule,
        )


def validate_cli_command(
    command: str,
    platform: Optional[str] = None,
    device_type: Optional[str] = None,
) -> Dict[str, Any]:
    return CliReadOnlyGuard().validate(command, platform=platform, device_type=device_type).to_dict()
