# -*- coding: utf-8 -*-
"""
Confirmed Netmiko execution service.

This module implements the V2 safety flow:

1. Validate command with CLI Guard.
2. Reject blocked/review commands before MCP call.
3. Require explicit human confirmation.
4. Execute only read-only command through Netmiko MCP.
5. Save structured audit record.

Safety:
- This module never calls Netmiko config tool.
- This module only calls send_command_and_get_output after guard=passed and confirm_execute=YES.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional

from netaiops_asset.mcp.netmiko_client import NetmikoMcpClient
from netaiops_asset.netmiko.cli_guard import CliReadOnlyGuard


DEFAULT_AUDIT_DIR = os.getenv(
    "NETAIOPS_NETMIKO_EXEC_AUDIT_DIR",
    "/var/lib/netaiops-asset-agent/data/v2_netmiko_exec_audit",
)


@dataclass
class NetmikoCommandPlan:
    plan_id: str
    device_name: str
    command: str
    platform: Optional[str]
    device_type: Optional[str]
    guard: Dict[str, Any]
    confirm_required: bool
    confirmed: bool
    status: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NetmikoExecutionResult:
    execution_id: str
    plan: Dict[str, Any]
    ok: bool
    status: str
    output: str
    output_preview: str
    error: Optional[str]
    audit_path: Optional[str]
    executed_at: str
    confirmed_by: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConfirmedNetmikoExecutor:
    def __init__(
        self,
        netmiko_client: Optional[NetmikoMcpClient] = None,
        guard: Optional[CliReadOnlyGuard] = None,
        audit_dir: str = DEFAULT_AUDIT_DIR,
        max_output_chars: int = 200000,
    ) -> None:
        self.netmiko_client = netmiko_client or NetmikoMcpClient()
        self.guard = guard or CliReadOnlyGuard()
        self.audit_dir = audit_dir
        self.max_output_chars = max_output_chars

    def build_plan(
        self,
        device_name: str,
        command: str,
        platform: Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        plan_id = str(uuid.uuid4())
        guard_result = self.guard.validate(
            command=command,
            platform=platform,
            device_type=device_type,
        ).to_dict()

        if not device_name:
            guard_result = dict(guard_result)
            guard_result.setdefault("reasons", [])
            guard_result["reasons"].append("device_name is required")
            guard_result["status"] = "blocked"
            guard_result["passed"] = False
            status = "rejected"
            message = "device_name is required"
        elif guard_result.get("status") == "passed":
            status = "pending_confirmation"
            message = "Command passed guard and requires human confirmation before execution"
        elif guard_result.get("status") == "review":
            status = "review_required"
            message = "Command requires special manual review and will not be executed by this flow"
        else:
            status = "rejected"
            message = "Command rejected by CLI guard"

        plan = NetmikoCommandPlan(
            plan_id=plan_id,
            device_name=device_name,
            command=command,
            platform=platform,
            device_type=device_type,
            guard=guard_result,
            confirm_required=True,
            confirmed=False,
            status=status,
            message=message,
        )
        return plan.to_dict()

    def execute_confirmed(
        self,
        device_name: str,
        command: str,
        platform: Optional[str] = None,
        device_type: Optional[str] = None,
        confirm_execute: str = "",
        confirmed_by: Optional[str] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        execution_id = str(uuid.uuid4())
        executed_at = datetime.now().isoformat()

        plan = self.build_plan(
            device_name=device_name,
            command=command,
            platform=platform,
            device_type=device_type,
        )

        guard = plan.get("guard") or {}
        guard_status = guard.get("status")

        if guard_status != "passed":
            result = NetmikoExecutionResult(
                execution_id=execution_id,
                plan=plan,
                ok=False,
                status=plan.get("status") or "rejected",
                output="",
                output_preview="",
                error=plan.get("message"),
                audit_path=None,
                executed_at=executed_at,
                confirmed_by=confirmed_by,
            )
            return self._with_audit(result)

        if confirm_execute != "YES":
            result = NetmikoExecutionResult(
                execution_id=execution_id,
                plan=plan,
                ok=False,
                status="pending_confirmation",
                output="",
                output_preview="",
                error='confirmation required: pass confirm_execute="YES"',
                audit_path=None,
                executed_at=executed_at,
                confirmed_by=confirmed_by,
            )
            return self._with_audit(result)

        plan["confirmed"] = True
        plan["status"] = "confirmed"

        try:
            tool_result = self.netmiko_client.send_command_after_guard(
                name=device_name,
                command=command,
                guard_status="passed",
                confirmed=True,
                timeout=timeout,
            )

            output = tool_result.content_text or ""
            if len(output) > self.max_output_chars:
                output = output[: self.max_output_chars] + "\n...[TRUNCATED]"

            result = NetmikoExecutionResult(
                execution_id=execution_id,
                plan=plan,
                ok=tool_result.ok,
                status="executed" if tool_result.ok else "failed",
                output=output,
                output_preview=output[:4000],
                error=tool_result.error,
                audit_path=None,
                executed_at=executed_at,
                confirmed_by=confirmed_by,
            )
            return self._with_audit(result)

        except Exception as exc:
            result = NetmikoExecutionResult(
                execution_id=execution_id,
                plan=plan,
                ok=False,
                status="failed",
                output="",
                output_preview="",
                error=repr(exc),
                audit_path=None,
                executed_at=executed_at,
                confirmed_by=confirmed_by,
            )
            return self._with_audit(result)

    def _with_audit(self, result: NetmikoExecutionResult) -> Dict[str, Any]:
        data = result.to_dict()

        try:
            os.makedirs(self.audit_dir, exist_ok=True)
            filename = "{}_{}.json".format(
                datetime.now().strftime("%Y%m%d_%H%M%S"),
                result.execution_id,
            )
            path = os.path.join(self.audit_dir, filename)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            data["audit_path"] = path

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as exc:
            data["audit_path"] = None
            data["audit_error"] = repr(exc)

        return data
