# -*- coding: utf-8 -*-
"""
Command output analyzer for V2.

This module provides lightweight, deterministic analysis for common
read-only command outputs. It does not call external LLM services yet.

Safety:
- It only analyzes text output.
- It does not execute commands.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def analyze_command_output(
    command: str,
    output: str,
    device_name: Optional[str] = None,
    device_type: Optional[str] = None,
) -> Dict[str, Any]:
    command_l = str(command or "").strip().lower()
    output_text = str(output or "")

    if "show system resources" in command_l:
        return analyze_nxos_system_resources(output_text, device_name=device_name, device_type=device_type)

    if "show processes cpu" in command_l:
        return analyze_generic_cpu_output(output_text, command=command, device_name=device_name, device_type=device_type)

    if "show clock" in command_l:
        return {
            "status": "ok",
            "analysis_type": "clock",
            "summary": "命令已成功返回设备时间，可用于确认设备命令通道可用和时间同步状态。",
            "facts": [
                "设备返回时间信息：{}".format(output_text.strip().splitlines()[0] if output_text.strip() else "-")
            ],
            "judgement": "当前证据只说明设备可登录、命令可执行、设备有时间输出，不代表业务或性能状态正常。",
            "next_steps": [
                "如需继续排障，应根据问题类型执行对应只读命令，例如 CPU、接口、BGP、路由表等。",
            ],
        }

    return analyze_generic_output(output_text, command=command, device_name=device_name, device_type=device_type)


def analyze_nxos_system_resources(
    output: str,
    device_name: Optional[str] = None,
    device_type: Optional[str] = None,
) -> Dict[str, Any]:
    text = str(output or "")
    facts: List[str] = []
    next_steps: List[str] = []
    warnings: List[str] = []

    load_avg = None
    processes = None
    cpu_total = None
    memory = None
    memory_status = None

    m = re.search(r"Load average:\s*1 minute:\s*([0-9.]+)\s*5 minutes:\s*([0-9.]+)\s*15 minutes:\s*([0-9.]+)", text)
    if m:
        load_avg = {
            "1m": float(m.group(1)),
            "5m": float(m.group(2)),
            "15m": float(m.group(3)),
        }
        facts.append("Load average：1m={1m}，5m={5m}，15m={15m}".format(**load_avg))

    m = re.search(r"Processes\s*:\s*([0-9]+)\s+total,\s*([0-9]+)\s+running", text)
    if m:
        processes = {
            "total": int(m.group(1)),
            "running": int(m.group(2)),
        }
        facts.append("进程数：total={}，running={}".format(processes["total"], processes["running"]))

    m = re.search(r"CPU states\s*:\s*([0-9.]+)%\s*user,\s*([0-9.]+)%\s*kernel,\s*([0-9.]+)%\s*idle", text)
    if m:
        user = float(m.group(1))
        kernel = float(m.group(2))
        idle = float(m.group(3))
        used = round(user + kernel, 2)
        cpu_total = {
            "user": user,
            "kernel": kernel,
            "idle": idle,
            "used": used,
        }
        facts.append("整体 CPU：user={:.2f}%，kernel={:.2f}%，idle={:.2f}%，used≈{:.2f}%".format(user, kernel, idle, used))

    m = re.search(r"Memory usage:\s*([0-9]+)K\s+total,\s*([0-9]+)K\s+used,\s*([0-9]+)K\s+free", text)
    if m:
        total = int(m.group(1))
        used = int(m.group(2))
        free = int(m.group(3))
        used_pct = round((used / total) * 100, 2) if total else None
        memory = {
            "total_k": total,
            "used_k": used,
            "free_k": free,
            "used_pct": used_pct,
        }
        facts.append("内存：total={}K，used={}K，free={}K，used≈{}%".format(total, used, free, used_pct))

    m = re.search(r"Current memory status:\s*([A-Za-z0-9_-]+)", text)
    if m:
        memory_status = m.group(1)
        facts.append("内存状态：{}".format(memory_status))

    hot_cpus = []
    for cpu_id, user, kernel, idle in re.findall(
        r"CPU([0-9]+)\s+states\s*:\s*([0-9.]+)%\s*user,\s*([0-9.]+)%\s*kernel,\s*([0-9.]+)%\s*idle",
        text,
    ):
        used = float(user) + float(kernel)
        if used >= 50:
            hot_cpus.append({
                "cpu": int(cpu_id),
                "used": round(used, 2),
                "user": float(user),
                "kernel": float(kernel),
                "idle": float(idle),
            })

    if hot_cpus:
        facts.append("存在局部 CPU 核心使用率偏高：{}".format(
            ", ".join("CPU{cpu}≈{used}%".format(**x) for x in hot_cpus[:8])
        ))

    status = "ok"
    judgement_parts = []

    if cpu_total:
        if cpu_total["used"] >= 80:
            status = "warning"
            judgement_parts.append("整体 CPU 使用率较高，当前证据支持 CPU 压力偏大的判断。")
            next_steps.extend([
                "继续执行 show processes cpu sort，定位高 CPU 进程。",
                "结合 show logging last 100 查看是否存在协议震荡、接口异常或进程告警。",
            ])
        elif cpu_total["used"] >= 50:
            status = "attention"
            judgement_parts.append("整体 CPU 使用率中等，需要结合进程排序和历史指标判断是否异常。")
            next_steps.extend([
                "继续执行 show processes cpu sort，确认是否有单进程异常。",
                "查询 Prometheus 中该设备最近 30 分钟 CPU 趋势，判断是瞬时还是持续。",
            ])
        else:
            judgement_parts.append("整体 CPU 使用率较低，当前命令输出不支持设备整体 CPU 高负载。")
            next_steps.extend([
                "如仍怀疑 CPU 异常，建议查询最近 30 分钟 Prometheus CPU 趋势。",
                "如存在业务异常，继续结合接口、BGP、日志等方向排查。",
            ])

    if hot_cpus and status == "ok":
        status = "attention"
        judgement_parts.append("虽然整体 CPU 不高，但存在局部核心使用率偏高，需要结合进程排序确认是否为正常调度。")

    if memory:
        if memory["used_pct"] is not None and memory["used_pct"] >= 85:
            status = "warning"
            judgement_parts.append("内存使用率较高，需要关注是否存在内存压力。")
            next_steps.append("继续查看内存相关日志或系统资源趋势。")
        elif memory_status and memory_status.upper() == "OK":
            judgement_parts.append("内存状态为 OK，当前未见明显内存压力。")

    if not facts:
        status = "unknown"
        warnings.append("未能从输出中解析到标准 NX-OS system resources 字段。")
        judgement_parts.append("命令有输出，但当前分析器无法识别关键字段。")
        next_steps.append("请人工查看原始输出，或补充适配该平台输出格式。")

    if not next_steps:
        next_steps.append("根据现象继续执行下一批只读命令，补充日志、进程和历史指标证据。")

    return {
        "status": status,
        "analysis_type": "nxos_system_resources",
        "device_name": device_name,
        "device_type": device_type,
        "metrics": {
            "load_average": load_avg,
            "processes": processes,
            "cpu_total": cpu_total,
            "memory": memory,
            "memory_status": memory_status,
            "hot_cpus": hot_cpus,
        },
        "summary": "；".join(judgement_parts) if judgement_parts else "已解析系统资源输出。",
        "facts": facts,
        "judgement": "；".join(judgement_parts) if judgement_parts else "当前证据不足以形成明确判断。",
        "next_steps": dedupe(next_steps),
        "warnings": warnings,
    }


def analyze_generic_cpu_output(
    output: str,
    command: str,
    device_name: Optional[str] = None,
    device_type: Optional[str] = None,
) -> Dict[str, Any]:
    text = str(output or "")
    facts = []

    if text.strip():
        facts.append("命令 {} 已返回输出，输出长度约 {} 字符。".format(command, len(text)))
    else:
        facts.append("命令 {} 未返回有效输出。".format(command))

    next_steps = [
        "结合 show system resources 或平台等价命令确认整体 CPU/内存状态。",
        "结合日志命令查看异常时间点是否存在协议、接口或进程告警。",
    ]

    return {
        "status": "unknown",
        "analysis_type": "generic_cpu",
        "device_name": device_name,
        "device_type": device_type,
        "summary": "当前命令输出已返回，但尚未做精细字段解析。",
        "facts": facts,
        "judgement": "需要结合原始输出和后续命令进一步判断。",
        "next_steps": next_steps,
        "warnings": [],
    }


def analyze_generic_output(
    output: str,
    command: str,
    device_name: Optional[str] = None,
    device_type: Optional[str] = None,
) -> Dict[str, Any]:
    text = str(output or "")
    has_output = bool(text.strip())

    return {
        "status": "ok" if has_output else "no_data",
        "analysis_type": "generic",
        "device_name": device_name,
        "device_type": device_type,
        "summary": "命令已返回输出。" if has_output else "命令未返回有效输出。",
        "facts": [
            "命令：{}".format(command),
            "输出长度：{} 字符".format(len(text)),
        ],
        "judgement": "当前仅完成通用输出确认，尚未针对该命令类型做专项分析。",
        "next_steps": [
            "根据问题类型继续补充相关只读命令或 Prometheus 指标证据。",
        ],
        "warnings": [],
    }


def format_analysis_for_answer(analysis: Dict[str, Any]) -> str:
    lines = []

    lines.append("初步分析：")
    lines.append(analysis.get("summary") or "-")

    facts = analysis.get("facts") or []
    if facts:
        lines.append("")
        lines.append("关键证据：")
        for idx, item in enumerate(facts[:8], 1):
            lines.append("{}. {}".format(idx, item))

    judgement = analysis.get("judgement")
    if judgement:
        lines.append("")
        lines.append("判断：")
        lines.append(str(judgement))

    next_steps = analysis.get("next_steps") or []
    if next_steps:
        lines.append("")
        lines.append("建议下一步：")
        for idx, item in enumerate(next_steps[:6], 1):
            lines.append("{}. {}".format(idx, item))

    warnings = analysis.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("注意：")
        for idx, item in enumerate(warnings[:4], 1):
            lines.append("{}. {}".format(idx, item))

    return "\n".join(lines)


def dedupe(items: List[str]) -> List[str]:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
