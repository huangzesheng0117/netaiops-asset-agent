# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def main() -> int:
    report_dir = Path(os.environ.get("V3_REPORT_DIR", "/tmp"))
    report_dir.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(PROJECT / "tools" / "smoke_v3_response_generator_live_llm.py")]
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=240,
    )

    stdout_path = report_dir / "v3_3_response_generator_live_llm_stdout.txt"
    stdout_path.write_text(proc.stdout, encoding="utf-8")

    summary = {
        "returncode": proc.returncode,
        "stdout_path": str(stdout_path),
        "ok": proc.returncode == 0 and "smoke_v3_response_generator_live_llm=OK" in proc.stdout,
        "contains_success_marker": "smoke_v3_response_generator_live_llm=OK" in proc.stdout,
        "contains_traceback": "Traceback" in proc.stdout,
        "contains_empty_api_key": "LLM_EMPTY_API_KEY" in proc.stdout or "NETAIOPS_LLM_API_KEY is still empty" in proc.stdout,
        "contains_error": "ERROR:" in proc.stdout,
    }

    summary_path = report_dir / "v3_3_response_generator_live_llm_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"summary_report={summary_path}")
    print(f"stdout_report={stdout_path}")

    if not summary["ok"]:
        print("LIVE_LLM_SMOKE_FAILED=1")
        return 1

    print("LIVE_LLM_SMOKE_FAILED=0")
    print("v3_3_response_generator_live_llm=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
