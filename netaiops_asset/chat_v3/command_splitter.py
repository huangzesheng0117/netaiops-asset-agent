# -*- coding: utf-8 -*-
"""
V3 command splitter.

Responsibilities:
- Normalize pasted command text.
- Split commands from multiline, semicolon, numbered list, markdown code fence,
  and same-line multi-command input.
- Do not decide business intent.
- Do not decide whether a command is safe.

This module is deterministic and can be used after LLM Intent Arbiter returns
commands or when backend needs to repair command formatting.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Iterable, List


COMMAND_START_RE = re.compile(
    r"(?i)(?<![\w./-])("
    r"show\b|"
    r"display\b|"
    r"ping\b|"
    r"traceroute\b|"
    r"tracert\b|"
    r"terminal\s+length\b|"
    r"terminal\s+pager\b|"
    r"screen-length\b|"
    r"get\b|"
    r"diagnose\b|"
    r"execute\s+ping\b|"
    r"execute\s+traceroute\b|"
    r"tmsh\s+(?:show|list)\b|"
    r"list\b"
    r")"
)

FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$")
NUMBER_PREFIX_RE = re.compile(r"^\s*(?:\d+[\.\)、)]|[-*•]+)\s*")
PROMPT_PREFIX_RE = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?"
    r"(?:[A-Za-z0-9_.@:/()~-]+)?"
    r"(?:#|>|\\$)\s+"
)


@dataclass
class SplitCommandResult:
    commands: List[str] = field(default_factory=list)
    raw_count: int = 0
    deduplicated_count: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "commands": self.commands,
            "raw_count": self.raw_count,
            "deduplicated_count": self.deduplicated_count,
            "notes": self.notes,
        }


def normalize_command_text(text: str) -> str:
    if text is None:
        return ""

    value = str(text)
    replacements = {
        "\ufeff": "",
        "\u200b": "",
        "\u00a0": " ",
        "\r\n": "\n",
        "\r": "\n",
        "\t": " ",
        "；": ";",
        "：": ":",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "｜": "|",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"[ ]{2,}", " ", value)
    return value.strip()


def strip_wrapping_quotes(command: str) -> str:
    value = command.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        value = value[1:-1].strip()
    return value


def strip_prompt_and_prefix(line: str) -> str:
    value = line.strip()
    value = NUMBER_PREFIX_RE.sub("", value).strip()
    value = PROMPT_PREFIX_RE.sub("", value).strip()

    # Common natural-language prefixes before code snippets.
    for sep in [":", "："]:
        if sep in value:
            left, right = value.rsplit(sep, 1)
            if COMMAND_START_RE.search(right) and not COMMAND_START_RE.search(left):
                value = right.strip()

    return strip_wrapping_quotes(value)


def remove_markdown_fences(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if FENCE_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def split_semicolon_aware(line: str) -> List[str]:
    value = line.strip()
    if not value:
        return []

    if ";" not in value:
        return [value]

    try:
        lexer = shlex.shlex(value, posix=True)
        lexer.whitespace = ";"
        lexer.whitespace_split = True
        lexer.commenters = ""
        return [part.strip() for part in lexer if part.strip()]
    except Exception:
        return [part.strip() for part in value.split(";") if part.strip()]


def split_inline_commands(line: str) -> List[str]:
    value = strip_prompt_and_prefix(line)
    if not value:
        return []

    starts = [match.start() for match in COMMAND_START_RE.finditer(value)]
    if not starts:
        return [value]

    # If natural language appears before the first command, discard it.
    segments: List[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(value)
        segment = value[start:end].strip(" ,;，。")
        if segment:
            segments.append(segment)

    return segments


def deduplicate_keep_order(commands: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []

    for command in commands:
        value = normalize_single_command(command)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)

    return result


def normalize_single_command(command: str) -> str:
    value = normalize_command_text(command)
    value = strip_prompt_and_prefix(value)
    value = strip_wrapping_quotes(value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip(" ,;，。")
    return value


def split_commands(text: str, max_commands: int = 200) -> SplitCommandResult:
    normalized = remove_markdown_fences(normalize_command_text(text))
    notes: List[str] = []
    raw_commands: List[str] = []

    if not normalized:
        return SplitCommandResult(commands=[], raw_count=0, deduplicated_count=0, notes=["empty_input"])

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        for semicolon_part in split_semicolon_aware(line):
            inline_parts = split_inline_commands(semicolon_part)
            raw_commands.extend(inline_parts)

    commands = deduplicate_keep_order(raw_commands)

    if len(commands) > max_commands:
        notes.append(f"truncated_to_max_commands:{max_commands}")
        commands = commands[:max_commands]

    if len(raw_commands) != len(commands):
        notes.append("deduplicated_or_normalized")

    return SplitCommandResult(
        commands=commands,
        raw_count=len(raw_commands),
        deduplicated_count=len(commands),
        notes=notes,
    )


def split_command_list(values: Iterable[str], max_commands: int = 200) -> SplitCommandResult:
    merged = "\n".join(str(item) for item in values if str(item).strip())
    return split_commands(merged, max_commands=max_commands)
