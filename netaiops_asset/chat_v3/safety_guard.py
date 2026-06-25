# -*- coding: utf-8 -*-
"""
V3 command safety guard.

Responsibilities:
- Deterministically block dangerous or non-read-only commands.
- Enforce command count and command length limits.
- Never rely on LLM to bypass safety checks.

This module does not route user intent and does not execute commands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List

from netaiops_asset.chat_v3.command_splitter import normalize_single_command


DEFAULT_MAX_COMMANDS = 20
DEFAULT_MAX_COMMAND_LENGTH = 300


DANGEROUS_PATTERNS = [
    r"^\s*reload\b",
    r"^\s*reboot\b",
    r"^\s*shutdown\b",
    r"^\s*halt\b",
    r"^\s*poweroff\b",
    r"^\s*configure\s+terminal\b",
    r"^\s*conf\s+t\b",
    r"^\s*config\b",
    r"^\s*delete\b",
    r"^\s*erase\b",
    r"^\s*format\b",
    r"^\s*write\s+erase\b",
    r"^\s*write\s+memory\b",
    r"^\s*write\b",
    r"^\s*copy\b",
    r"^\s*move\b",
    r"^\s*rename\b",
    r"^\s*clear\b",
    r"^\s*reset\b",
    r"^\s*commit\b",
    r"^\s*save\b",
    r"^\s*set\b",
    r"^\s*unset\b",
    r"^\s*no\s+\S+",
    r"^\s*install\b",
    r"^\s*upgrade\b",
    r"^\s*request\s+system\s+reboot\b",
    r"^\s*request\s+system\s+power-off\b",
    r"^\s*execute\s+reboot\b",
    r"^\s*execute\s+shutdown\b",
    r"^\s*execute\s+factoryreset\b",
    r"^\s*diagnose\s+debug\s+enable\b",
    r"^\s*diagnose\s+debug\s+reset\b",
]

READONLY_PATTERNS = [
    r"^\s*show\b",
    r"^\s*display\b",
    r"^\s*ping\b",
    r"^\s*traceroute\b",
    r"^\s*tracert\b",
    r"^\s*terminal\s+length\s+\d+\b",
    r"^\s*terminal\s+pager\s+\d+\b",
    r"^\s*screen-length\s+\d+\s+temporary\b",
    r"^\s*get\b",
    r"^\s*diagnose\s+(?!debug\s+enable\b)(?!debug\s+reset\b).+",
    r"^\s*execute\s+ping\b",
    r"^\s*execute\s+traceroute\b",
    r"^\s*tmsh\s+show\b",
    r"^\s*tmsh\s+list\b",
    r"^\s*list\b",
]


@dataclass
class CommandCheck:
    command: str
    allowed: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "command": self.command,
            "allowed": self.allowed,
            "reason": self.reason,
        }


@dataclass
class SafetyCheckResult:
    allowed: bool
    safe_commands: List[str] = field(default_factory=list)
    blocked_commands: List[CommandCheck] = field(default_factory=list)
    checks: List[CommandCheck] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "safe_commands": self.safe_commands,
            "blocked_commands": [item.as_dict() for item in self.blocked_commands],
            "checks": [item.as_dict() for item in self.checks],
            "reason": self.reason,
        }


def _matches_any(command: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if re.search(pattern, command, flags=re.IGNORECASE):
            return True
    return False


def check_single_command(command: str, max_command_length: int = DEFAULT_MAX_COMMAND_LENGTH) -> CommandCheck:
    normalized = normalize_single_command(command)

    if not normalized:
        return CommandCheck(command="", allowed=False, reason="empty_command")

    if len(normalized) > max_command_length:
        return CommandCheck(
            command=normalized,
            allowed=False,
            reason=f"command_too_long:{len(normalized)}>{max_command_length}",
        )

    if "\n" in normalized or "\r" in normalized:
        return CommandCheck(command=normalized, allowed=False, reason="command_contains_newline")

    lowered = normalized.lower()

    # Shell escapes and redirection are not needed for normal read-only device checks.
    if re.search(r"(^|\s)(sudo|su|bash|sh|python|perl|ruby|nc|ncat|telnet|ssh|scp|sftp|curl|wget)\b", lowered):
        return CommandCheck(command=normalized, allowed=False, reason="shell_or_external_program_blocked")

    if re.search(r"(\||&&|\|\||>|<|`|\$\(|;)", normalized):
        # Allow read-only pipe include/exclude/begin on Cisco-like show/display commands.
        if re.search(r"(?i)^\s*(show|display)\b.+\|\s*(include|exclude|begin|count|section)\b", normalized):
            pass
        else:
            return CommandCheck(command=normalized, allowed=False, reason="unsafe_shell_operator_or_redirect")

    if _matches_any(normalized, DANGEROUS_PATTERNS):
        return CommandCheck(command=normalized, allowed=False, reason="dangerous_command_pattern")

    if _matches_any(normalized, READONLY_PATTERNS):
        return CommandCheck(command=normalized, allowed=True, reason="readonly_command_allowed")

    return CommandCheck(command=normalized, allowed=False, reason="unsupported_non_readonly_prefix")


def check_commands(
    commands: Iterable[str],
    max_commands: int = DEFAULT_MAX_COMMANDS,
    max_command_length: int = DEFAULT_MAX_COMMAND_LENGTH,
) -> SafetyCheckResult:
    normalized_commands = [normalize_single_command(item) for item in commands if normalize_single_command(item)]

    if not normalized_commands:
        return SafetyCheckResult(
            allowed=False,
            safe_commands=[],
            blocked_commands=[],
            checks=[],
            reason="no_commands",
        )

    if len(normalized_commands) > max_commands:
        blocked = [
            CommandCheck(command=item, allowed=False, reason=f"too_many_commands:{len(normalized_commands)}>{max_commands}")
            for item in normalized_commands[max_commands:]
        ]
        return SafetyCheckResult(
            allowed=False,
            safe_commands=normalized_commands[:max_commands],
            blocked_commands=blocked,
            checks=blocked,
            reason=f"too_many_commands:{len(normalized_commands)}>{max_commands}",
        )

    checks = [
        check_single_command(item, max_command_length=max_command_length)
        for item in normalized_commands
    ]

    safe_commands = [item.command for item in checks if item.allowed]
    blocked_commands = [item for item in checks if not item.allowed]

    if blocked_commands:
        return SafetyCheckResult(
            allowed=False,
            safe_commands=safe_commands,
            blocked_commands=blocked_commands,
            checks=checks,
            reason="blocked_unsafe_commands",
        )

    return SafetyCheckResult(
        allowed=True,
        safe_commands=safe_commands,
        blocked_commands=[],
        checks=checks,
        reason="all_commands_allowed",
    )
