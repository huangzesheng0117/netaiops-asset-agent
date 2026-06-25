# -*- coding: utf-8 -*-
"""
V2 conversation context manager.

Goal:
- Persist structured V2 conversation context across turns.
- Avoid relying only on raw prompt history.
- Support future follow-up understanding:
  - 上述
  - 刚才
  - 以上三点
  - 继续
  - 这个设备
  - 这些结果

Safety:
- This module only reads/writes local JSON context.
- It does not execute commands.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


CONTEXT_DIR = os.getenv(
    "NETAIOPS_V2_CONTEXT_DIR",
    "/var/lib/netaiops-asset-agent/data/v2_conversation_context",
)

MAX_RECENT_TURNS = int(os.getenv("NETAIOPS_V2_CONTEXT_MAX_TURNS", "30"))
MAX_COMMANDS = int(os.getenv("NETAIOPS_V2_CONTEXT_MAX_COMMANDS", "30"))
MAX_EXECUTIONS = int(os.getenv("NETAIOPS_V2_CONTEXT_MAX_EXECUTIONS", "50"))
MAX_SUMMARY_CHARS = int(os.getenv("NETAIOPS_V2_CONTEXT_MAX_SUMMARY_CHARS", "12000"))


def _safe_user(user: Optional[str]) -> str:
    text = str(user or "anonymous").strip()
    text = re.sub(r"[^A-Za-z0-9_.@-]+", "_", text)
    return text or "anonymous"


def _safe_id(value: Optional[str]) -> str:
    text = str(value or "").strip()
    text = text.replace("/", "_").replace("..", "_")
    return text or str(uuid.uuid4())


def context_file_path(conversation_id: Optional[str] = None, user: Optional[str] = None) -> str:
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    if conversation_id:
        return os.path.join(CONTEXT_DIR, "conversation_{}.json".format(_safe_id(conversation_id)))
    return os.path.join(CONTEXT_DIR, "latest_user_{}.json".format(_safe_user(user)))


def load_v2_context(conversation_id: Optional[str] = None, user: Optional[str] = None) -> Optional[Dict[str, Any]]:
    paths = []
    if conversation_id:
        paths.append(context_file_path(conversation_id=conversation_id))
    paths.append(context_file_path(user=user))

    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue

    return None


def new_context(conversation_id: Optional[str], user: Optional[str]) -> Dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "context_id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "user": user,
        "created_at": now,
        "updated_at": now,
        "current_device": None,
        "current_topic": None,
        "current_intent": None,
        "active_focus": None,
        "last_prometheus_evidence": None,
        "last_command_suggestions": [],
        "last_executions": [],
        "last_analysis": None,
        "last_bulk_analysis": None,
        "last_followup_analysis": None,
        "open_questions": [],
        "resolved_findings": [],
        "rolling_summary": "",
        "recent_turns": [],
        "context_stats": {},
    }


def save_v2_context(context: Dict[str, Any], conversation_id: Optional[str] = None, user: Optional[str] = None) -> Dict[str, str]:
    os.makedirs(CONTEXT_DIR, exist_ok=True)

    paths = []
    if conversation_id:
        paths.append(context_file_path(conversation_id=conversation_id))
    paths.append(context_file_path(user=user))

    saved = {}
    for path in paths:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(context, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        saved[path] = "ok"

    return saved


def save_v2_context_from_response(
    conversation_id: Optional[str],
    user: Optional[str],
    question: str,
    response: Dict[str, Any],
) -> Dict[str, Any]:
    if not response:
        return {}

    planner_source = response.get("planner_source")
    parsed = response.get("parsed") or {}

    if planner_source not in ("v2_chat_router", "v2_execution_confirmation", "v2_followup_analysis"):
        return {}

    context = load_v2_context(conversation_id=conversation_id, user=user)
    if not context:
        context = new_context(conversation_id=conversation_id, user=user)

    context["conversation_id"] = conversation_id or context.get("conversation_id")
    context["user"] = user or context.get("user")
    context["updated_at"] = datetime.now().isoformat()

    _update_device_and_topic(context, response)
    _update_prometheus_evidence(context, response)
    _update_command_suggestions(context, response)
    _update_executions_and_analysis(context, response)
    _update_followup_analysis(context, response)
    _append_recent_turn(context, question, response)
    _update_context_insights(context, question, response)
    _enforce_context_limits(context)
    _refresh_rolling_summary(context)

    save_v2_context(context, conversation_id=conversation_id, user=user)

    return context


def _update_device_and_topic(context: Dict[str, Any], response: Dict[str, Any]) -> None:
    parsed = response.get("parsed") or {}
    v2 = response.get("v2") or {}
    identity = v2.get("identity") or {}

    device_name = parsed.get("device_name") or identity.get("hostname")
    mgmt_ip = parsed.get("mgmt_ip") or identity.get("mgmt_ip")
    device_type = parsed.get("device_type")

    netmiko_match = identity.get("netmiko_match") if isinstance(identity, dict) else None
    if not device_name and isinstance(netmiko_match, dict):
        device_name = netmiko_match.get("name")
    if not device_type and isinstance(netmiko_match, dict):
        device_type = netmiko_match.get("device_type")

    if device_name or mgmt_ip:
        current_device = context.get("current_device") or {}
        current_device.update({
            "device_name": device_name or current_device.get("device_name"),
            "hostname": identity.get("hostname") or current_device.get("hostname"),
            "mgmt_ip": mgmt_ip or current_device.get("mgmt_ip"),
            "device_type": device_type or current_device.get("device_type"),
            "netmiko_device_name": device_name or current_device.get("netmiko_device_name"),
            "identity_status": identity.get("status") or current_device.get("identity_status"),
            "updated_at": datetime.now().isoformat(),
        })
        context["current_device"] = current_device

    v2_intent = parsed.get("v2_intent")
    intent = parsed.get("intent")

    if v2_intent:
        context["current_topic"] = _topic_from_intent(v2_intent)
        context["current_intent"] = v2_intent
        context["last_action_intent"] = v2_intent
    elif intent and str(intent).startswith("v2_"):
        # Do not let confirmation/follow-up actions overwrite the current
        # troubleshooting intent, e.g. keep current_intent=cpu_check instead
        # of replacing it with v2_execute_all_confirmation.
        context["last_action_intent"] = intent


def _update_prometheus_evidence(context: Dict[str, Any], response: Dict[str, Any]) -> None:
    v2 = response.get("v2") or {}
    evidence = v2.get("prometheus_evidence")

    if evidence:
        context["last_prometheus_evidence"] = {
            "updated_at": datetime.now().isoformat(),
            "evidence": evidence,
        }


def _update_command_suggestions(context: Dict[str, Any], response: Dict[str, Any]) -> None:
    if response.get("planner_source") != "v2_chat_router":
        return

    items = response.get("items") or []
    commands = []

    for pos, item in enumerate(items, 1):
        command = item.get("command")
        if not command:
            continue

        commands.append({
            "index": item.get("index") or pos,
            "device_name": item.get("device_name"),
            "mgmt_ip": item.get("mgmt_ip"),
            "device_type": item.get("device_type"),
            "command": command,
            "purpose": item.get("purpose"),
            "guard_status": item.get("guard_status"),
            "risk_level": item.get("risk_level"),
            "confirm_required": item.get("confirm_required"),
            "guard_reasons": item.get("guard_reasons"),
        })

    if commands:
        context["last_command_suggestions"] = commands[:MAX_COMMANDS]


def _update_executions_and_analysis(context: Dict[str, Any], response: Dict[str, Any]) -> None:
    if response.get("planner_source") != "v2_execution_confirmation":
        return

    items = response.get("items") or []
    executions = []

    for item in items:
        command = item.get("command")
        if not command:
            continue

        # Only real execution results should enter last_executions.
        # Pending confirmation items contain command/index but have no
        # execution_status/ok/audit_path/output_preview and must not be treated
        # as already executed.
        if (
            item.get("execution_status") is None
            and item.get("ok") is None
            and not item.get("audit_path")
            and not item.get("output_preview")
        ):
            continue

        executions.append({
            "index": item.get("index"),
            "device_name": item.get("device_name"),
            "device_type": item.get("device_type"),
            "command": command,
            "execution_status": item.get("execution_status"),
            "ok": item.get("ok"),
            "audit_path": item.get("audit_path"),
            "analysis_status": item.get("analysis_status"),
            "analysis_summary": item.get("analysis_summary"),
            "output_preview": _truncate(item.get("output_preview"), 2000),
            "updated_at": datetime.now().isoformat(),
        })

    if executions:
        old = context.get("last_executions") or []
        history = context.get("execution_history") or []
        context["execution_history"] = (history + old + executions)[-(MAX_EXECUTIONS * 10):]
        context["last_executions"] = executions[-MAX_EXECUTIONS:]

    v2 = response.get("v2") or {}

    if v2.get("analysis"):
        context["last_analysis"] = {
            "updated_at": datetime.now().isoformat(),
            "analysis": v2.get("analysis"),
        }

    if v2.get("analyses"):
        context["last_bulk_analysis"] = {
            "updated_at": datetime.now().isoformat(),
            "analyses": v2.get("analyses"),
            "counts": v2.get("counts"),
        }



def _update_followup_analysis(context: Dict[str, Any], response: Dict[str, Any]) -> None:
    if response.get("planner_source") != "v2_followup_analysis":
        return

    v2 = response.get("v2") or {}
    context["last_followup_analysis"] = {
        "updated_at": datetime.now().isoformat(),
        "question": response.get("question"),
        "facts": v2.get("facts") or [],
        "conclusion": v2.get("conclusion"),
        "next_steps": v2.get("next_steps") or [],
        "answer_summary": _summarize_answer(response.get("answer")),
    }


def _append_recent_turn(context: Dict[str, Any], question: str, response: Dict[str, Any]) -> None:
    turn = {
        "time": datetime.now().isoformat(),
        "question": _truncate(question, 1000),
        "planner_source": response.get("planner_source"),
        "status": response.get("status"),
        "parsed": response.get("parsed"),
        "answer_summary": _summarize_answer(response.get("answer")),
        "count": response.get("count"),
        "returned": response.get("returned"),
    }

    turns = context.get("recent_turns") or []
    turns.append(turn)
    context["recent_turns"] = turns[-MAX_RECENT_TURNS:]



def _update_context_insights(context: Dict[str, Any], question: str, response: Dict[str, Any]) -> None:
    planner_source = response.get("planner_source")
    parsed = response.get("parsed") or {}
    v2 = response.get("v2") or {}

    device = context.get("current_device") or {}
    topic = context.get("current_topic")
    intent = context.get("current_intent")

    if device or topic or intent:
        context["active_focus"] = {
            "device_name": device.get("device_name"),
            "mgmt_ip": device.get("mgmt_ip"),
            "device_type": device.get("device_type"),
            "topic": topic,
            "intent": intent,
            "last_action_intent": context.get("last_action_intent"),
            "updated_at": datetime.now().isoformat(),
        }

    if planner_source == "v2_chat_router":
        v2_intent = parsed.get("v2_intent")
        evidence = v2.get("prometheus_evidence") or {}

        if v2_intent == "cpu_check":
            _append_unique_insight(context, "open_questions", "如 CPU 当前值不高，需要查看告警时间前后的 Prometheus CPU 历史趋势。")
            _append_unique_insight(context, "open_questions", "需要结合 show processes cpu sort 判断是否存在异常高 CPU 进程。")
            _append_unique_insight(context, "open_questions", "需要结合日志判断是否存在协议震荡、接口 flap 或进程异常。")

        if evidence.get("status") == "ok":
            matched = evidence.get("matched") or {}
            _append_unique_insight(
                context,
                "resolved_findings",
                "Prometheus 当前指标已命中：{}，当前值={}。".format(
                    matched.get("query") or "-",
                    matched.get("sample_value") or "-",
                ),
            )

    elif planner_source == "v2_execution_confirmation":
        status = response.get("status")
        items = response.get("items") or []
        if status in ("ok", "partial") and items:
            ok_count = sum(1 for x in items if x.get("ok") is True)
            _append_unique_insight(
                context,
                "resolved_findings",
                "已执行只读命令 {} 条，其中成功 {} 条。".format(len(items), ok_count),
            )

        analyses = v2.get("analyses") or []
        for item in analyses:
            analysis = item.get("analysis") or {}
            summary = analysis.get("summary")
            if summary:
                _append_unique_insight(
                    context,
                    "resolved_findings",
                    "命令 {} 的分析摘要：{}".format(item.get("command") or "-", summary),
                )
            for step in analysis.get("next_steps") or []:
                _append_unique_insight(context, "open_questions", step)

        analysis = v2.get("analysis") or {}
        if analysis.get("summary"):
            _append_unique_insight(context, "resolved_findings", analysis.get("summary"))
        for step in analysis.get("next_steps") or []:
            _append_unique_insight(context, "open_questions", step)

    elif planner_source == "v2_followup_analysis":
        conclusion = v2.get("conclusion")
        if conclusion:
            _append_unique_insight(context, "resolved_findings", conclusion)

        for fact in v2.get("facts") or []:
            if "不支持" in str(fact) or "命中" in str(fact) or "成功" in str(fact):
                _append_unique_insight(context, "resolved_findings", fact)

        for step in v2.get("next_steps") or []:
            _append_unique_insight(context, "open_questions", step)


def _append_unique_insight(context: Dict[str, Any], key: str, value: Any, limit: int = 30) -> None:
    text = str(value or "").strip()
    if not text:
        return

    text = _truncate(text, 600)

    items = context.get(key) or []
    if text in items:
        return

    items.append(text)
    context[key] = items[-limit:]


def _enforce_context_limits(context: Dict[str, Any]) -> None:
    context["recent_turns"] = (context.get("recent_turns") or [])[-MAX_RECENT_TURNS:]
    context["last_command_suggestions"] = (context.get("last_command_suggestions") or [])[-MAX_COMMANDS:]
    context["last_executions"] = (context.get("last_executions") or [])[-MAX_EXECUTIONS:]
    context["open_questions"] = (context.get("open_questions") or [])[-30:]
    context["resolved_findings"] = (context.get("resolved_findings") or [])[-30:]

    for item in context.get("recent_turns") or []:
        if item.get("question") is not None:
            item["question"] = _truncate(item.get("question"), 1000)
        if item.get("answer_summary") is not None:
            item["answer_summary"] = _truncate(item.get("answer_summary"), 1200)

    for item in context.get("last_executions") or []:
        if item.get("output_preview") is not None:
            item["output_preview"] = _truncate(item.get("output_preview"), 2000)
        if item.get("analysis_summary") is not None:
            item["analysis_summary"] = _truncate(item.get("analysis_summary"), 800)

    context["context_stats"] = {
        "recent_turns_count": len(context.get("recent_turns") or []),
        "last_command_suggestions_count": len(context.get("last_command_suggestions") or []),
        "last_executions_count": len(context.get("last_executions") or []),
        "open_questions_count": len(context.get("open_questions") or []),
        "resolved_findings_count": len(context.get("resolved_findings") or []),
        "rolling_summary_chars": len(context.get("rolling_summary") or ""),
        "max_recent_turns": MAX_RECENT_TURNS,
        "max_summary_chars": MAX_SUMMARY_CHARS,
        "updated_at": datetime.now().isoformat(),
    }



def _refresh_rolling_summary(context: Dict[str, Any]) -> None:
    lines = []

    dev = context.get("current_device") or {}
    if dev:
        lines.append("当前设备：device_name={}，mgmt_ip={}，device_type={}。".format(
            dev.get("device_name") or "-",
            dev.get("mgmt_ip") or "-",
            dev.get("device_type") or "-",
        ))

    focus = context.get("active_focus") or {}
    if focus:
        lines.append("当前焦点：topic={}，intent={}，last_action_intent={}。".format(
            focus.get("topic") or "-",
            focus.get("intent") or "-",
            focus.get("last_action_intent") or "-",
        ))

    if context.get("current_topic"):
        lines.append("当前排障主题：{}。".format(context.get("current_topic")))

    prom = context.get("last_prometheus_evidence") or {}
    evidence = prom.get("evidence") or {}
    if evidence:
        lines.append("最近 Prometheus 证据：status={}，summary={}。".format(
            evidence.get("status"),
            evidence.get("summary") or "-",
        ))

    cmds = context.get("last_command_suggestions") or []
    if cmds:
        lines.append("最近建议命令：{}。".format(
            "；".join("{}:{}".format(x.get("index") or i + 1, x.get("command")) for i, x in enumerate(cmds[:10]))
        ))

    execs = context.get("last_executions") or []
    if execs:
        latest = execs[-10:]
        ok_count = sum(1 for x in execs if x.get("ok") is True)
        lines.append("最近执行命令：共 {} 条，成功 {} 条；{}。".format(
            len(execs),
            ok_count,
            "；".join("{}:{}:{}".format(x.get("index"), x.get("command"), x.get("execution_status")) for x in latest),
        ))

    analysis = context.get("last_analysis") or {}
    if analysis.get("analysis"):
        a = analysis.get("analysis") or {}
        lines.append("最近单命令分析：{}。".format(a.get("summary") or "-"))

    bulk = context.get("last_bulk_analysis") or {}
    if bulk.get("analyses"):
        lines.append("最近批量分析：共 {} 条分析。".format(len(bulk.get("analyses") or [])))

    followup = context.get("last_followup_analysis") or {}
    if followup.get("conclusion"):
        lines.append("最近追问分析：{}。".format(followup.get("conclusion")))

    resolved = context.get("resolved_findings") or []
    if resolved:
        lines.append("已确认发现：{}。".format("；".join(resolved[-8:])))

    open_questions = context.get("open_questions") or []
    if open_questions:
        lines.append("待继续确认：{}。".format("；".join(open_questions[-8:])))

    turns = context.get("recent_turns") or []
    if turns:
        lines.append("最近对话轮次：{}，保留上限：{}。".format(len(turns), MAX_RECENT_TURNS))

    summary = "\\n".join(lines)
    context["rolling_summary"] = summary[-MAX_SUMMARY_CHARS:]

    stats = context.get("context_stats") or {}
    stats.update({
        "recent_turns_count": len(context.get("recent_turns") or []),
        "last_command_suggestions_count": len(context.get("last_command_suggestions") or []),
        "last_executions_count": len(context.get("last_executions") or []),
        "open_questions_count": len(context.get("open_questions") or []),
        "resolved_findings_count": len(context.get("resolved_findings") or []),
        "rolling_summary_chars": len(context.get("rolling_summary") or ""),
        "max_recent_turns": MAX_RECENT_TURNS,
        "max_summary_chars": MAX_SUMMARY_CHARS,
        "updated_at": datetime.now().isoformat(),
    })
    context["context_stats"] = stats


def _topic_from_intent(intent: str) -> str:
    if intent == "interface_error_check":
        return "interface_error"
    mapping = {
        "cpu_check": "cpu",
        "route_table": "route_table",
        "interface_error_check": "interface_error",
        "interface_check": "interface",
        "bgp_check": "bgp",
        "bfd_check": "bfd",
    }
    return mapping.get(str(intent or ""), str(intent or ""))


def _summarize_answer(answer: Any) -> str:
    text = str(answer or "").strip()
    if not text:
        return ""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines:
        return _truncate(text, 800)
    return _truncate(" / ".join(lines[:8]), 1200)


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[TRUNCATED]"


def get_context_debug(conversation_id: Optional[str] = None, user: Optional[str] = None) -> Dict[str, Any]:
    context = load_v2_context(conversation_id=conversation_id, user=user)
    if not context:
        return {
            "exists": False,
            "conversation_id": conversation_id,
            "user": user,
        }

    return {
        "exists": True,
        "context_file": context_file_path(conversation_id=conversation_id) if conversation_id else context_file_path(user=user),
        "context": context,
    }


# ===== Batch59 context isolation patch =====
# 原则：
# 1. conversation_id 存在时，只读取 conversation_xxx.json，不再 fallback 到 latest_user_xxx.json。
# 2. latest_user 只作为“无 conversation_id”的兜底，不作为新会话基底。
# 3. 保存上下文前，按 current_device/current_topic/current_intent 过滤 last_executions / last_bulk_analysis。
# 4. 非 CPU 主题不允许携带 Prometheus CPU 证据参与后续分析。

_ORIG_BATCH59_SAVE_V2_CONTEXT = save_v2_context


def _batch59_norm_topic(value):
    text = str(value or "").strip()
    mapping = {
        "log": "log_check",
        "log_check": "log_check",
        "cpu": "cpu_check",
        "cpu_check": "cpu_check",
        "interface_error": "interface_error_check",
        "interface_error_check": "interface_error_check",
        "interface": "interface_check",
        "interface_check": "interface_check",
        "bgp": "bgp_check",
        "bgp_check": "bgp_check",
        "route": "route_table",
        "route_table": "route_table",
        "optical_power": "optical_power_check",
        "optical_power_check": "optical_power_check",
        "memory": "memory_check",
        "memory_check": "memory_check",
    }
    return mapping.get(text, text)


def _batch59_cmd_topic(command):
    c = str(command or "").lower()
    if "show logging" in c or "display log" in c or "display trapbuffer" in c:
        return "log_check"
    if "show system resources" in c or "show processes cpu" in c or "display cpu-usage" in c:
        return "cpu_check"
    if "show interface" in c and ("counter" in c or "transceiver" in c or "ethernet" in c or "eth" in c):
        return "interface_error_check"
    if "bgp" in c:
        return "bgp_check"
    if "route" in c:
        return "route_table"
    if "transceiver" in c or "optical" in c:
        return "optical_power_check"
    if "memory" in c:
        return "memory_check"
    return ""


def _batch59_topic_match(current_topic, item_topic, command):
    ct = _batch59_norm_topic(current_topic)
    it = _batch59_norm_topic(item_topic) or _batch59_cmd_topic(command)

    if not ct:
        return True

    if ct == it:
        return True

    # 日志命令可作为多数排障主题的辅助证据，但 log_check 只允许日志证据。
    if it == "log_check" and ct in (
        "interface_error_check",
        "interface_check",
        "bgp_check",
        "route_table",
        "cpu_check",
        "memory_check",
        "optical_power_check",
    ):
        return True

    return False


def _batch59_device_match(context, item):
    current_device = context.get("current_device") or {}
    cur_name = str(
        current_device.get("device_name")
        or current_device.get("hostname")
        or current_device.get("netmiko_device_name")
        or ""
    ).strip()
    cur_ip = str(current_device.get("mgmt_ip") or "").strip()

    item_name = str(
        item.get("device_name")
        or item.get("hostname")
        or item.get("netmiko_device_name")
        or ""
    ).strip()
    item_ip = str(item.get("mgmt_ip") or "").strip()

    # 有明确设备名但不一致，拒绝。
    if cur_name and item_name and cur_name != item_name:
        return False

    # 有明确管理 IP 但不一致，拒绝。
    if cur_ip and item_ip and cur_ip != item_ip:
        return False

    return True


def _batch59_filter_execution_items(context, items):
    current_topic = _batch59_norm_topic(context.get("current_intent") or context.get("current_topic"))
    result = []

    for item in items or []:
        if not isinstance(item, dict):
            continue

        command = item.get("command") or ""
        item_topic = item.get("v2_intent") or item.get("template_category") or item.get("category") or _batch59_cmd_topic(command)

        if not _batch59_device_match(context, item):
            continue

        if not _batch59_topic_match(current_topic, item_topic, command):
            continue

        result.append(item)

    return result[-MAX_EXECUTIONS:]


def _batch59_filter_bulk_analysis(context, bulk):
    if not isinstance(bulk, dict):
        return None

    analyses = bulk.get("analyses") or []
    if not isinstance(analyses, list):
        return None

    filtered = []
    for item in analyses:
        if not isinstance(item, dict):
            continue
        command = item.get("command") or item.get("cmd") or ""
        wrapper = {
            "command": command,
            "device_name": item.get("device_name"),
            "mgmt_ip": item.get("mgmt_ip"),
            "v2_intent": item.get("v2_intent"),
            "template_category": item.get("template_category"),
            "category": item.get("category"),
        }
        if _batch59_device_match(context, wrapper) and _batch59_topic_match(
            context.get("current_intent") or context.get("current_topic"),
            wrapper.get("v2_intent") or wrapper.get("template_category") or wrapper.get("category"),
            command,
        ):
            filtered.append(item)

    if not filtered:
        return None

    copied = dict(bulk)
    copied["analyses"] = filtered
    copied["analysis_count"] = len(filtered)
    return copied


def _batch59_sanitize_context(context):
    if not isinstance(context, dict):
        return context

    context["last_executions"] = _batch59_filter_execution_items(context, context.get("last_executions") or [])

    bulk = _batch59_filter_bulk_analysis(context, context.get("last_bulk_analysis"))
    context["last_bulk_analysis"] = bulk

    # 非 CPU 主题不保留 Prometheus CPU 证据，避免日志/接口追问里出现其他设备 CPU。
    topic = _batch59_norm_topic(context.get("current_intent") or context.get("current_topic"))
    prom = context.get("last_prometheus_evidence")
    if isinstance(prom, dict):
        metric_type = str(prom.get("metric_type") or prom.get("type") or "").lower()
        summary = str(prom.get("summary") or "").lower()
        if topic != "cpu_check" and ("cpu" in metric_type or "cpu" in summary or "cpmcputotal" in summary):
            context["last_prometheus_evidence"] = None

    # 如果当前主题是 log_check，open_questions/resolved_findings 里不保留 CPU 专项遗留。
    if topic == "log_check":
        for key in ("open_questions", "resolved_findings"):
            values = []
            for value in context.get(key) or []:
                text = str(value or "")
                if "CPU" in text or "cpu" in text or "system resources" in text:
                    continue
                values.append(value)
            context[key] = values[-30:]

    return context


def load_v2_context(conversation_id=None, user=None):
    paths = []

    # 关键修复：只要有 conversation_id，就只读该会话文件；不存在则返回 None。
    # 绝不从 latest_user 继承旧证据作为新会话基底。
    if conversation_id:
        path = context_file_path(conversation_id=conversation_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return _batch59_sanitize_context(json.load(f))
            except Exception:
                return None
        return None

    if user:
        paths.append(context_file_path(user=user))

    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _batch59_sanitize_context(json.load(f))
        except Exception:
            continue

    return None


def save_v2_context(context, conversation_id=None, user=None):
    context = _batch59_sanitize_context(context or {})
    return _ORIG_BATCH59_SAVE_V2_CONTEXT(context, conversation_id=conversation_id, user=user)
