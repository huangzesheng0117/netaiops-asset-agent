#!/usr/bin/env python3
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import requests


CHECKS: list[tuple[str, bool, str]] = []


def add(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))


def file_mode(path: Path) -> str:
    try:
        return oct(stat.S_IMODE(path.stat().st_mode))
    except Exception:
        return "missing"


def get_tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or tool.get("tool_name") or tool.get("id") or "")
    return str(tool)


def main() -> int:
    cfg = Path("/etc/netaiops-asset-agent/config.yaml")
    env = Path("/etc/netaiops-asset-agent/asset-agent.env")
    app_dir = Path("/opt/netaiops-asset-agent")
    data_dir = Path("/var/lib/netaiops-asset-agent/data")

    add("config_exists", cfg.exists(), str(cfg))
    add("config_mode_640_or_stricter", file_mode(cfg) in {"0o600", "0o640"}, f"{cfg} mode={file_mode(cfg)}")

    add("env_exists", env.exists(), str(env))
    add("env_mode_640_or_stricter", file_mode(env) in {"0o600", "0o640"}, f"{env} mode={file_mode(env)}")

    try:
        llm_cfg = requests.get("http://127.0.0.1:18081/api/v1/llm/config", timeout=20).json()
        text = str(llm_cfg)
        add(
            "llm_config_no_key_value",
            "sk-" not in text and "Bearer" not in text,
            "api key should not be returned by /api/v1/llm/config",
        )
        add(
            "llm_config_key_masked",
            bool(llm_cfg.get("llm", {}).get("api_key_configured")),
            "only boolean api_key_configured should be exposed",
        )
    except Exception as exc:
        add("llm_config_probe", False, str(exc))

    try:
        catalog = requests.get("http://127.0.0.1:18081/api/v1/tools/catalog", timeout=20).json()
        tools = catalog.get("items", [])

        allowed_tools = {
            "query_cmdb_devices",
            "query_cmdb_device_detail",
            "query_cmdb_devices_by_ips",
        }

        dangerous_name_keywords = {
            "write",
            "delete",
            "update",
            "modify",
            "create",
            "remove",
            "config",
            "push",
            "apply",
            "commit",
            "send_command",
            "exec",
        }

        names = [get_tool_name(tool) for tool in tools]
        names = [name for name in names if name]

        unknown = [name for name in names if name not in allowed_tools]
        dangerous = [
            name for name in names
            if any(keyword in name.lower() for keyword in dangerous_name_keywords)
        ]

        add(
            "tool_catalog_readonly_allowlist",
            not unknown and not dangerous,
            f"tools={names}, unknown={unknown}, dangerous={dangerous}",
        )
    except Exception as exc:
        add("tool_catalog_check", False, str(exc))

    add("data_dir_exists", data_dir.exists(), str(data_dir))
    add(
        "data_dir_not_world_writable",
        data_dir.exists() and not bool(data_dir.stat().st_mode & stat.S_IWOTH),
        f"{data_dir} mode={file_mode(data_dir)}",
    )

    forbidden_paths = [
        app_dir / ".env",
        app_dir / "asset-agent.env",
    ]
    add(
        "no_env_file_inside_repo",
        not any(p.exists() for p in forbidden_paths),
        "secret env file should stay under /etc/netaiops-asset-agent",
    )

    print("========== V1 Security Check ==========")
    failed = 0
    for name, ok, detail in CHECKS:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name} {detail}")
        if not ok:
            failed += 1

    print()
    if failed:
        print(f"[FAIL] security check failed: {failed}")
        return 1

    print("[DONE] V1 security check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
