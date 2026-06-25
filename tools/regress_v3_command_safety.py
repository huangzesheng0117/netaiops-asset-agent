# -*- coding: utf-8 -*-
"""
Regression tests for V3 command_splitter and safety_guard.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.command_splitter import split_commands
from netaiops_asset.chat_v3.safety_guard import check_commands, check_single_command


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def assert_equal(left: Any, right: Any, message: str) -> None:
    if left != right:
        raise AssertionError(f"{message}: left={left!r}, right={right!r}")


def test_multiline_show_commands() -> None:
    text = """
    show clock
    show version
    show logging last 100
    """
    result = split_commands(text)
    assert_equal(result.commands, ["show clock", "show version", "show logging last 100"], "multiline split mismatch")


def test_same_line_multiple_show_commands() -> None:
    text = "我再给你一批命令，执行后分析：show clock show version show logging last 100"
    result = split_commands(text)
    assert_equal(result.commands, ["show clock", "show version", "show logging last 100"], "inline show split mismatch")


def test_markdown_numbered_list() -> None:
    text = """
    ```bash
    1. show clock
    2. show platform
    3）show redundancy
    ```
    """
    result = split_commands(text)
    assert_equal(result.commands, ["show clock", "show platform", "show redundancy"], "numbered list split mismatch")


def test_semicolon_split() -> None:
    result = split_commands("show clock; show version; display current-configuration")
    assert_equal(result.commands, ["show clock", "show version", "display current-configuration"], "semicolon split mismatch")


def test_prompt_prefix_split() -> None:
    result = split_commands("[root@device:/Common:Active] config # tmsh show ltm virtual")
    assert_equal(result.commands, ["tmsh show ltm virtual"], "prompt prefix split mismatch")


def test_deduplicate() -> None:
    result = split_commands("show clock\nshow clock\nSHOW CLOCK")
    assert_equal(result.commands, ["show clock"], "deduplicate mismatch")


def test_readonly_allowed() -> None:
    commands: List[str] = [
        "show clock",
        "display interface brief",
        "ping 10.1.1.1",
        "traceroute 10.1.1.1",
        "terminal length 0",
        "screen-length 0 temporary",
        "tmsh show ltm virtual",
        "tmsh list ltm virtual",
        "get system status",
        "diagnose hardware sysinfo memory",
        "execute ping 10.1.1.1",
    ]
    result = check_commands(commands)
    assert_true(result.allowed, result.as_dict())


def test_dangerous_blocked() -> None:
    commands: List[str] = [
        "reload",
        "configure terminal",
        "delete flash:test.bin",
        "clear counters",
        "copy running-config startup-config",
        "execute reboot",
        "diagnose debug enable",
    ]

    for command in commands:
        check = check_single_command(command)
        assert_true(not check.allowed, f"dangerous command should be blocked: {command}")
        assert_true(check.reason, "blocked command should have reason")


def test_shell_operator_blocked() -> None:
    unsafe = [
        "show clock; reload",
        "show version && reload",
        "show version > /tmp/a",
        "tmsh show sys version | bash",
    ]

    for command in unsafe:
        check = check_single_command(command)
        assert_true(not check.allowed, f"shell operator command should be blocked: {command}")


def test_safe_pipe_allowed() -> None:
    safe = [
        "show logging | include ERROR",
        "display logbuffer | include DOWN",
    ]

    for command in safe:
        check = check_single_command(command)
        assert_true(check.allowed, f"safe include pipe should be allowed: {command}")


def test_too_many_commands() -> None:
    commands = [f"show clock {idx}" for idx in range(25)]
    result = check_commands(commands, max_commands=20)
    assert_true(not result.allowed, "too many commands should be blocked")
    assert_true(result.reason.startswith("too_many_commands"), result.reason)


def main() -> None:
    tests = [
        test_multiline_show_commands,
        test_same_line_multiple_show_commands,
        test_markdown_numbered_list,
        test_semicolon_split,
        test_prompt_prefix_split,
        test_deduplicate,
        test_readonly_allowed,
        test_dangerous_blocked,
        test_shell_operator_blocked,
        test_safe_pipe_allowed,
        test_too_many_commands,
    ]

    for test in tests:
        test()
        print(test.__name__ + "=OK")

    print("regress_v3_command_safety=OK")


if __name__ == "__main__":
    main()
