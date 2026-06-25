# -*- coding: utf-8 -*-
"""
V3.1 file inventory reporter.

This script generates:
- V3 module file list
- line counts
- sha256 checksums
- git status snapshot
- service status snapshot
- shadow directory status

It does not modify business code and does not restart service.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT = Path(__file__).resolve().parents[1]
REPORT_DIR = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
SHADOW_DIR = Path("/var/lib/netaiops-asset-agent/data/v3_intent_shadow")


def run_cmd(args: List[str], timeout: int = 60) -> Tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(PROJECT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except UnicodeDecodeError:
        return len(path.read_bytes().splitlines())


def collect_inventory_files() -> List[Path]:
    files: List[Path] = []

    chat_v3 = PROJECT / "netaiops_asset" / "chat_v3"
    if chat_v3.exists():
        files.extend(sorted(chat_v3.glob("*.py")))

    tools = PROJECT / "tools"
    if tools.exists():
        files.extend(sorted(tools.glob("regress_v3_*.py")))
        report_script = tools / "report_v3_file_inventory.py"
        if report_script.exists():
            files.append(report_script)

    unique: List[Path] = []
    seen = set()
    for item in files:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def build_file_inventory(files: List[Path]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in files:
        rel = str(path.relative_to(PROJECT))
        stat = path.stat()
        items.append(
            {
                "path": rel,
                "size_bytes": stat.st_size,
                "line_count": line_count(path),
                "sha256": sha256_file(path),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(stat.st_mtime)),
            }
        )
    return items


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    files = collect_inventory_files()
    inventory = build_file_inventory(files)

    git_status_rc, git_status = run_cmd(["git", "-c", f"safe.directory={PROJECT}", "status", "--short"])
    git_head_rc, git_head = run_cmd(["git", "-c", f"safe.directory={PROJECT}", "rev-parse", "--short", "HEAD"])
    git_branch_rc, git_branch = run_cmd(["git", "-c", f"safe.directory={PROJECT}", "branch", "--show-current"])

    service_rc, service_output = run_cmd(
        ["systemctl", "status", "netaiops-asset-agent.service", "--no-pager", "-l"],
        timeout=60,
    )

    shadow_status = {
        "path": str(SHADOW_DIR),
        "exists": SHADOW_DIR.exists(),
        "items": [],
    }

    if SHADOW_DIR.exists():
        shadow_status["items"] = [
            {
                "name": item.name,
                "size_bytes": item.stat().st_size,
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(item.stat().st_mtime)),
            }
            for item in sorted(SHADOW_DIR.iterdir())
            if item.is_file()
        ]

    payload = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "project": str(PROJECT),
        "inventory": inventory,
        "git": {
            "status_returncode": git_status_rc,
            "status_short": git_status,
            "head_returncode": git_head_rc,
            "head": git_head.strip(),
            "branch_returncode": git_branch_rc,
            "branch": git_branch.strip(),
        },
        "service": {
            "returncode": service_rc,
            "output_tail": service_output[-4000:],
        },
        "shadow_dir": shadow_status,
    }

    json_report = REPORT_DIR / "v3_1_6_file_inventory.json"
    txt_report = REPORT_DIR / "v3_1_6_file_inventory.txt"

    json_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: List[str] = []
    lines.append("===== V3.1 file inventory =====")
    lines.append(f"created_at={payload['created_at']}")
    lines.append(f"project={PROJECT}")
    lines.append("")
    lines.append("===== files =====")
    for item in inventory:
        lines.append(
            f"{item['path']} | lines={item['line_count']} | bytes={item['size_bytes']} | sha256={item['sha256']}"
        )
    lines.append("")
    lines.append("===== git =====")
    lines.append(f"branch={payload['git']['branch']}")
    lines.append(f"head={payload['git']['head']}")
    lines.append("status_short:")
    lines.append(payload["git"]["status_short"].rstrip())
    lines.append("")
    lines.append("===== shadow_dir =====")
    lines.append(f"path={shadow_status['path']}")
    lines.append(f"exists={shadow_status['exists']}")
    for item in shadow_status["items"]:
        lines.append(f"{item['name']} | bytes={item['size_bytes']} | mtime={item['mtime']}")
    lines.append("")
    lines.append("===== service status tail =====")
    lines.append(payload["service"]["output_tail"].rstrip())

    txt_report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"json_report={json_report}")
    print(f"txt_report={txt_report}")
    print("v3_file_inventory=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
