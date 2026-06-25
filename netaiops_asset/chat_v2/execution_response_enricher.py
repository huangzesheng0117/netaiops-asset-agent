# -*- coding: utf-8 -*-
"""
V2 execution response enricher.

Purpose:
- Normalize one-step confirmation phrases.
- Enrich execution confirmation response by category.
- Provide interface_error specific execution/follow-up analysis.
- Prevent CPU/system resources fallback text from appearing in interface_error analysis.

Safety:
- Does not execute CLI.
- Only rewrites/augments response text after confirmation.py has executed commands.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


EXECUTE_WORDS = [
    "执行",
    "跑一下",
    "运行",
]

BULK_WORDS = [
    "全部",
    "这批",
    "这些",
    "上述",
    "上面",
    "刚才",
    "上一轮",
    "所有",
]

FOLLOWUP_RESULT_HINTS = [
    "根据命令执行结果",
    "根据执行结果",
    "根据命令结果",
    "根据刚才命令",
    "结合命令执行结果",
    "结合执行结果",
    "结合上述执行结果",
    "结合刚才结果",
    "基于执行结果",
    "命令的执行结果",
    "命令执行结果",
    "命令结果",
    "执行结果",
    "分析原因",
    "判断原因",
    "原因是什么",
    "什么原因",
    "给出原因",
    "给出结论",
    "分析一下问题",
    "分析一下原因",
    "这些结果说明什么",
    "这些结果能说明什么",
]


def normalize_execution_confirmation_question(question: str) -> str:
    q = str(question or "").strip()
    if not q:
        return q

    upper = q.upper()
    if "YES" not in upper:
        return q

    # Keep existing explicit command-index confirmation.
    m = re.search(r"第\s*(\d+)\s*条", q)
    if m and any(w in q for w in EXECUTE_WORDS):
        return "确认执行第{}条命令 YES".format(m.group(1))

    if any(w in q for w in EXECUTE_WORDS) and any(w in q for w in BULK_WORDS):
        return "确认执行全部命令 YES"

    if "确认" in q and "命令" in q and any(w in q for w in BULK_WORDS):
        return "确认执行全部命令 YES"

    return q


def is_execution_result_followup_question_text(question: str) -> bool:
    q = str(question or "").strip()
    if not q:
        return False

    if any(h in q for h in FOLLOWUP_RESULT_HINTS):
        return True

    if ("结果" in q or "命令" in q) and any(x in q for x in ["分析", "原因", "结论", "判断", "说明"]):
        return True

    return False


def enrich_v2_execution_response(
    response: Optional[Dict[str, Any]],
    question: str = "",
    user: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(response, dict):
        return response

    if response.get("planner_source") != "v2_execution_confirmation":
        return response

    if response.get("status") not in ("ok", "partial"):
        return response

    items = response.get("items") or []
    if not items:
        return response

    category = infer_category_from_items(items)
    if category != "interface_error":
        return response

    answer, facts, conclusion, next_steps = build_interface_error_execution_answer(items)

    response["answer"] = answer
    v2 = response.get("v2") or {}
    v2["category_analysis"] = {
        "category": "interface_error",
        "facts": facts,
        "conclusion": conclusion,
        "next_steps": next_steps,
        "source": "execution_response_enricher",
    }
    response["v2"] = v2

    # Also stamp each item with category-aware summary to avoid CPU residue in context.
    for item in items:
        if isinstance(item, dict):
            item["analysis_status"] = item.get("analysis_status") or "category_summary"
            if not item.get("analysis_summary") or "CPU" in str(item.get("analysis_summary")) or "system resources" in str(item.get("analysis_summary")):
                item["analysis_summary"] = "接口错包/错误计数取证命令已执行，需结合接口错误计数、光模块信息和日志判断原因。"

    return response


def build_interface_error_followup_answer(question: str, context: Dict[str, Any]) -> Tuple[str, List[str], str, List[str]]:
    executions = context.get("last_executions") or []
    facts, conclusion, next_steps = analyze_interface_error_executions(executions)

    device = context.get("current_device") or {}
    lines: List[str] = []
    lines.append("我会基于上一轮已执行命令结果继续分析接口错包增长问题。")
    lines.append("当前设备：{}，管理IP：{}，设备类型：{}。".format(
        device.get("device_name") or "-",
        device.get("mgmt_ip") or "-",
        device.get("device_type") or "-",
    ))

    intf = infer_interface_from_executions(executions)
    if intf:
        lines.append("当前接口：{}。".format(intf))

    lines.append("")
    lines.append("可引用的执行结果证据：")
    for idx, fact in enumerate(facts[:12], 1):
        lines.append("{}. {}".format(idx, fact))

    lines.append("")
    lines.append("综合结论：")
    lines.append(conclusion)

    lines.append("")
    lines.append("建议下一步：")
    for idx, step in enumerate(next_steps[:8], 1):
        lines.append("{}. {}".format(idx, step))

    lines.append("")
    lines.append("说明：本轮回答来自已保存的 V2 命令执行结果，不会重新生成命令，也不会重新执行设备命令。")

    return "\n".join(lines), facts, conclusion, next_steps


def build_interface_error_execution_answer(items: List[Dict[str, Any]]) -> Tuple[str, List[str], str, List[str]]:
    facts, conclusion, next_steps = analyze_interface_error_executions(items)

    total = len(items)
    ok_count = sum(1 for x in items if x.get("ok") is True)
    failed_count = total - ok_count

    lines: List[str] = []
    lines.append("已确认批量执行上一轮 passed 只读命令。")
    lines.append("执行统计：total={}，ok={}，failed={}。".format(total, ok_count, failed_count))
    lines.append("")
    lines.append("命令执行结果摘要：")

    for item in items:
        lines.append("{}. {}: {}, ok={}".format(
            item.get("index") or "-",
            item.get("command") or "-",
            item.get("execution_status") or item.get("status") or "-",
            item.get("ok"),
        ))

    lines.append("")
    lines.append("综合分析：")
    lines.append(conclusion)

    lines.append("")
    lines.append("分命令关键分析：")
    for idx, fact in enumerate(facts[:12], 1):
        lines.append("{}. {}".format(idx, fact))

    lines.append("")
    lines.append("建议下一步：")
    for idx, step in enumerate(next_steps[:8], 1):
        lines.append("{}. {}".format(idx, step))

    lines.append("")
    lines.append("说明：本轮为接口错包/错误计数专项分析，已按接口取证结果生成结论。")

    return "\n".join(lines), facts, conclusion, next_steps


def analyze_interface_error_executions(items: List[Dict[str, Any]]) -> Tuple[List[str], str, List[str]]:
    facts: List[str] = []
    next_steps: List[str] = []

    intf = infer_interface_from_executions(items)
    if intf:
        facts.append("本轮排查对象接口为 {}。".format(intf))

    ok_count = sum(1 for x in items if x.get("ok") is True)
    facts.append("最近接口错包取证命令共 {} 条，成功 {} 条。".format(len(items), ok_count))

    combined_outputs = []
    for item in items:
        cmd = str(item.get("command") or "")
        output = read_output_from_item(item)
        if output:
            combined_outputs.append((cmd, output))

        if cmd:
            facts.append("已执行命令：{}，状态={}，ok={}。".format(
                cmd,
                item.get("execution_status") or item.get("status") or "-",
                item.get("ok"),
            ))

    parsed = parse_interface_error_outputs(combined_outputs)

    if parsed.get("error_counters"):
        facts.extend(parsed["error_counters"][:8])

    if parsed.get("transceiver"):
        facts.extend(parsed["transceiver"][:6])

    if parsed.get("log_hints"):
        facts.extend(parsed["log_hints"][:6])

    if parsed.get("interface_state"):
        facts.extend(parsed["interface_state"][:4])

    # Conservative conclusion. We only make strong claims when output has clear counters/logs.
    if parsed.get("error_counters"):
        conclusion = (
            "当前已获得接口错误计数相关输出。请重点关注 CRC/input error/output error/discard 是否持续增加；"
            "如果错误计数集中在 CRC/input error，优先怀疑物理链路、光模块、尾纤或对端端口；"
            "如果 discard/drop 增长更明显，则还需要结合拥塞、队列、策略或对端发送情况继续判断。"
        )
    else:
        conclusion = (
            "本轮接口错包相关命令均已返回，但当前通用解析未提取到明确错误计数数值。"
            "因此不能直接给出单一根因；需要结合原始接口输出中的 CRC/input/output error/discard 计数、"
            "光模块收发光功率以及日志中的 flap/模块异常事件综合判断。"
        )

    if parsed.get("transceiver"):
        conclusion += " 同时本轮包含光模块信息，应检查收发光功率是否接近阈值或存在异常告警。"

    if parsed.get("log_hints"):
        conclusion += " 最近日志中存在接口/链路相关线索，需要结合告警时间点确认是否同步发生。"

    next_steps.extend([
        "对比告警前后接口错误计数差值，确认 CRC/input error/output error/discard 哪类计数在增长。",
        "检查 show interface 与 counters errors 输出中错误计数集中在哪个方向，区分物理层问题和拥塞/丢弃问题。",
        "结合 transceiver details 判断收发光功率是否低、是否接近阈值、是否存在模块异常。",
        "结合 show logging last 100 查看接口 flap、模块插拔、链路协商、对端异常等日志。",
        "如 CRC/input error 持续增长，优先检查光模块、尾纤、跳纤、对端端口和中间链路。",
        "如 discard/drop 持续增长，继续检查接口拥塞、队列丢弃、QoS/策略、对端流量突增。",
    ])

    return dedupe(facts), conclusion, dedupe(next_steps)


def parse_interface_error_outputs(command_outputs: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    result = {
        "error_counters": [],
        "transceiver": [],
        "log_hints": [],
        "interface_state": [],
    }

    for cmd, output in command_outputs:
        text = output or ""
        lower = text.lower()

        if "show interface" in cmd.lower():
            state_lines = []
            for line in text.splitlines():
                l = line.strip()
                if not l:
                    continue
                if re.search(r"\bis (up|down|admin.*down)\b", l, re.I):
                    state_lines.append(l)
                elif any(k in l.lower() for k in ["input error", "output error", "crc", "discard", "drop", "collisions", "runts", "giants"]):
                    state_lines.append(l)
            if state_lines:
                result["interface_state"].append("接口状态/计数输出摘要：{}".format(" | ".join(state_lines[:5])))

        if "counters errors" in cmd.lower() or "counters detailed" in cmd.lower():
            counter_lines = []
            for line in text.splitlines():
                l = line.strip()
                if not l:
                    continue
                if any(k in l.lower() for k in ["crc", "error", "discard", "drop", "timeout", "collision", "runt", "giant"]):
                    counter_lines.append(l)
            if counter_lines:
                result["error_counters"].append("错误计数输出摘要：{}".format(" | ".join(counter_lines[:8])))

        if "transceiver" in cmd.lower():
            txrx_lines = []
            for line in text.splitlines():
                l = line.strip()
                if not l:
                    continue
                if any(k in l.lower() for k in ["dbm", "power", "temperature", "voltage", "current", "alarm", "warning", "threshold"]):
                    txrx_lines.append(l)
            if txrx_lines:
                result["transceiver"].append("光模块输出摘要：{}".format(" | ".join(txrx_lines[:8])))

        if "logging" in cmd.lower():
            log_lines = []
            for line in text.splitlines():
                l = line.strip()
                if not l:
                    continue
                if any(k in l.lower() for k in ["ethernet", "eth", "interface", "link", "flap", "down", "up", "transceiver", "module", "crc", "error"]):
                    log_lines.append(l)
            if log_lines:
                result["log_hints"].append("日志相关线索：{}".format(" | ".join(log_lines[:8])))

    return result


def infer_category_from_items(items: List[Dict[str, Any]]) -> str:
    for item in items:
        for key in ("template_category", "category"):
            value = item.get(key)
            if value:
                return str(value)
        v2_intent = item.get("v2_intent")
        if v2_intent == "interface_error_check":
            return "interface_error"
    return ""


def infer_interface_from_executions(items: List[Dict[str, Any]]) -> str:
    for item in items:
        value = item.get("interface_name")
        if value:
            return str(value)
        cmd = str(item.get("command") or "")
        m = re.search(r"\b(Ethernet\d+(?:/\d+){1,4}|Eth\d+(?:/\d+){1,4}|eth\d+(?:/\d+){1,4})\b", cmd, re.I)
        if m:
            raw = m.group(1)
            if raw.lower().startswith("eth") and not raw.lower().startswith("ethernet"):
                return "Ethernet" + raw[3:]
            return raw
    return ""


def read_output_from_item(item: Dict[str, Any]) -> str:
    for key in ("output", "output_preview", "raw_output"):
        if item.get(key):
            return str(item.get(key) or "")

    audit_path = item.get("audit_path")
    if audit_path and os.path.exists(str(audit_path)):
        try:
            with open(str(audit_path), "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("output", "output_preview", "raw_output", "result"):
                if data.get(key):
                    return str(data.get(key) or "")
            nested = data.get("response") or data.get("data") or {}
            if isinstance(nested, dict):
                for key in ("output", "output_preview", "raw_output", "result"):
                    if nested.get(key):
                        return str(nested.get(key) or "")
        except Exception:
            return ""

    return ""


def dedupe(items: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


# ===== Batch57 fix append: command-based interface_error detection and failed-output handling =====

def _batch57_is_interface_error_command(command: str) -> bool:
    c = str(command or "").lower()
    if "show interface" not in c:
        return False
    if "counters errors" in c or "counters detailed" in c or "transceiver" in c:
        return True
    if "ethernet" in c or "eth" in c:
        return True
    return False


def _batch57_is_transport_error_text(text: str) -> bool:
    lower = str(text or "").lower()
    return (
        "error executing tool" in lower
        or "error reading ssh protocol banner" in lower
        or "paramiko sshexception" in lower
        or "connection creation" in lower
        or "timed out" in lower
        or "authentication failed" in lower
    )


def infer_category_from_items(items: List[Dict[str, Any]]) -> str:
    for item in items:
        for key in ("template_category", "category"):
            value = item.get(key)
            if value:
                return str(value)
        v2_intent = item.get("v2_intent")
        if v2_intent == "interface_error_check":
            return "interface_error"

    commands = [str(x.get("command") or "") for x in items if isinstance(x, dict)]
    if any(_batch57_is_interface_error_command(c) for c in commands):
        return "interface_error"

    return ""


def _batch57_select_relevant_interface_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not items:
        return []

    relevant = []
    for item in items:
        cmd = str(item.get("command") or "")
        if _batch57_is_interface_error_command(cmd) or "show logging" in cmd.lower():
            relevant.append(item)

    # 优先使用最近一组接口相关命令，避免混入旧 CPU 上下文。
    if len(relevant) > 8:
        relevant = relevant[-8:]

    return relevant or items[-8:]


def read_output_from_item(item: Dict[str, Any]) -> str:
    if item.get("ok") is False:
        for key in ("output", "output_preview", "raw_output"):
            if item.get(key):
                return str(item.get(key) or "")
        return ""

    for key in ("output", "output_preview", "raw_output"):
        if item.get(key):
            return str(item.get(key) or "")

    audit_path = item.get("audit_path")
    if audit_path and os.path.exists(str(audit_path)):
        try:
            with open(str(audit_path), "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("output", "output_preview", "raw_output", "result"):
                if data.get(key):
                    return str(data.get(key) or "")
            nested = data.get("response") or data.get("data") or {}
            if isinstance(nested, dict):
                for key in ("output", "output_preview", "raw_output", "result"):
                    if nested.get(key):
                        return str(nested.get(key) or "")
        except Exception:
            return ""

    return ""


def analyze_interface_error_executions(items: List[Dict[str, Any]]) -> Tuple[List[str], str, List[str]]:
    items = _batch57_select_relevant_interface_items(items)

    facts: List[str] = []
    next_steps: List[str] = []

    intf = infer_interface_from_executions(items)
    if intf:
        facts.append("本轮排查对象接口为 {}。".format(intf))

    total = len(items)
    ok_count = sum(1 for x in items if x.get("ok") is True)
    failed_count = total - ok_count
    facts.append("最近接口错包取证命令共 {} 条，成功 {} 条，失败 {} 条。".format(total, ok_count, failed_count))

    transport_errors = []
    combined_outputs = []

    for item in items:
        cmd = str(item.get("command") or "")
        output = read_output_from_item(item)
        status = item.get("execution_status") or item.get("status") or "-"
        ok = item.get("ok")

        if cmd:
            facts.append("已执行命令：{}，状态={}，ok={}。".format(cmd, status, ok))

        if output and _batch57_is_transport_error_text(output):
            short = " ".join(str(output).split())[:300]
            transport_errors.append("{}：{}".format(cmd or "-", short))
            continue

        if output and ok is True:
            combined_outputs.append((cmd, output))

    if transport_errors:
        facts.append("存在连接层失败：{}。".format("；".join(transport_errors[:5])))

    parsed = parse_interface_error_outputs(combined_outputs)

    if parsed.get("error_counters"):
        facts.extend(parsed["error_counters"][:8])
    if parsed.get("transceiver"):
        facts.extend(parsed["transceiver"][:6])
    if parsed.get("log_hints"):
        facts.extend(parsed["log_hints"][:6])
    if parsed.get("interface_state"):
        facts.extend(parsed["interface_state"][:4])

    if ok_count == 0:
        conclusion = (
            "本轮接口错包相关命令均未成功连接设备，失败点在 SSH/Netmiko 连接建立阶段，"
            "典型错误为 Error reading SSH protocol banner。当前没有拿到设备真实接口输出，"
            "因此不能基于设备证据判断错包增长根因。应先排查 MCP/Netmiko 到设备的 SSH 连通性、"
            "设备 VTY/SSH 资源、登录协议、凭据、AAA 或中间网络访问问题。"
        )
    elif transport_errors and not parsed.get("error_counters"):
        conclusion = (
            "本轮部分接口错包命令连接失败，且当前未解析到明确 CRC/input error/output error/discard 数值。"
            "现阶段只能确认取证链路不完整，不能直接判断错包增长根因。"
        )
    elif parsed.get("error_counters"):
        conclusion = (
            "当前已获得接口错误计数相关输出。请重点关注 CRC/input error/output error/discard 是否持续增加；"
            "如果错误计数集中在 CRC/input error，优先怀疑物理链路、光模块、尾纤或对端端口；"
            "如果 discard/drop 增长更明显，则还需要结合拥塞、队列、策略或对端发送情况继续判断。"
        )
    else:
        conclusion = (
            "本轮接口错包相关命令已有部分返回，但当前未提取到明确错误计数。"
            "需要结合原始接口输出中的 CRC/input/output error/discard、光模块功率和日志事件继续判断。"
        )

    next_steps.extend([
        "先确认 Netmiko/MCP 到设备 10.189.250.80 的 SSH 连接是否稳定，重点关注 SSH banner 读取失败。",
        "在设备侧查看当前 VTY/SSH 会话占用、AAA/登录失败记录以及是否存在连接数限制。",
        "待连接恢复后，重新执行 show interface、counters errors、counters detailed、transceiver details 和 logging 命令。",
        "如果成功拿到输出，再对比 CRC/input error/output error/discard 的计数和增量。",
        "如 CRC/input error 持续增长，优先检查光模块、尾纤、跳纤、对端端口和中间链路。",
        "如 discard/drop 持续增长，继续检查接口拥塞、队列丢弃、QoS/策略、对端流量突增。",
    ])

    return dedupe(facts), conclusion, dedupe(next_steps)


def build_interface_error_execution_answer(items: List[Dict[str, Any]]) -> Tuple[str, List[str], str, List[str]]:
    facts, conclusion, next_steps = analyze_interface_error_executions(items)

    total = len(items)
    ok_count = sum(1 for x in items if x.get("ok") is True)
    failed_count = total - ok_count

    lines: List[str] = []
    lines.append("已确认批量执行上一轮 passed 只读命令。")
    lines.append("执行统计：total={}，ok={}，failed={}。".format(total, ok_count, failed_count))
    lines.append("")
    lines.append("命令执行结果摘要：")

    for item in items:
        lines.append("{}. {}: {}, ok={}".format(
            item.get("index") or "-",
            item.get("command") or "-",
            item.get("execution_status") or item.get("status") or "-",
            item.get("ok"),
        ))

    lines.append("")
    lines.append("综合分析：")
    lines.append(conclusion)

    lines.append("")
    lines.append("分命令关键分析：")
    for idx, fact in enumerate(facts[:12], 1):
        lines.append("{}. {}".format(idx, fact))

    lines.append("")
    lines.append("建议下一步：")
    for idx, step in enumerate(next_steps[:8], 1):
        lines.append("{}. {}".format(idx, step))

    lines.append("")
    lines.append("说明：本轮为接口错包/错误计数专项分析，已按接口取证结果生成结论。")

    return "\\n".join(lines), facts, conclusion, next_steps


def build_interface_error_followup_answer(question: str, context: Dict[str, Any]) -> Tuple[str, List[str], str, List[str]]:
    executions = _batch57_select_relevant_interface_items(context.get("last_executions") or [])
    facts, conclusion, next_steps = analyze_interface_error_executions(executions)

    device = context.get("current_device") or {}
    lines: List[str] = []
    lines.append("我会基于上一轮已执行命令结果继续分析接口错包增长问题。")
    lines.append("当前设备：{}，管理IP：{}，设备类型：{}。".format(
        device.get("device_name") or "-",
        device.get("mgmt_ip") or "-",
        device.get("device_type") or "-",
    ))

    intf = infer_interface_from_executions(executions)
    if intf:
        lines.append("当前接口：{}。".format(intf))

    lines.append("")
    lines.append("可引用的执行结果证据：")
    for idx, fact in enumerate(facts[:12], 1):
        lines.append("{}. {}".format(idx, fact))

    lines.append("")
    lines.append("综合结论：")
    lines.append(conclusion)

    lines.append("")
    lines.append("建议下一步：")
    for idx, step in enumerate(next_steps[:8], 1):
        lines.append("{}. {}".format(idx, step))

    lines.append("")
    lines.append("说明：本轮回答来自已保存的 V2 命令执行结果，不会重新生成命令，也不会重新执行设备命令。")

    return "\\n".join(lines), facts, conclusion, next_steps

# ===== Batch57 execution priority patch =====

def is_v2_execution_request_question(question: str) -> bool:
    """
    Detect user intent to execute previously suggested commands.

    True examples:
    - 将你上述给出的命令在设备上执行，然后根据命令的结果给出分析
    - 执行这批命令 YES
    - 执行上述命令 YES
    - 确认执行全部命令 YES
    - 确认执行第1条命令 YES

    False examples:
    - 根据命令的执行结果，分析一下接口错包增长的原因
    - 根据执行结果给出结论
    """
    q = str(question or "").strip()
    if not q:
        return False

    explicit_execute_patterns = [
        r"确认\s*执行\s*第\s*\d+\s*条\s*命令",
        r"确认\s*执行\s*(全部|这批|这些|上述|上面|刚才|上一轮|所有)?\s*命令",
        r"(执行|运行|跑一下)\s*(全部|这批|这些|上述|上面|刚才|上一轮|所有)\s*命令",
        r"(将|把).{0,40}(上述|上面|这批|这些|刚才|上一轮|全部|所有).{0,40}命令.{0,40}(执行|运行|跑一下)",
        r"(将|把).{0,40}命令.{0,40}(执行|运行|跑一下)",
    ]

    for pat in explicit_execute_patterns:
        if re.search(pat, q, re.I):
            return True

    upper = q.upper()
    if "YES" in upper and "执行" in q and ("命令" in q or re.search(r"第\s*\d+\s*条", q)):
        return True

    return False


# ===== Batch59 log_check execution analysis patch =====

_PRE_BATCH59_ENRICH_V2_EXECUTION_RESPONSE = enrich_v2_execution_response


def _batch59_is_log_execution(items):
    if not items:
        return False

    for item in items:
        for key in ("template_category", "category", "v2_intent"):
            val = str(item.get(key) or "").lower()
            if val in ("log", "log_check"):
                return True

    commands = [str(x.get("command") or "").lower() for x in items if isinstance(x, dict)]
    return bool(commands) and all(("show logging" in c or "display log" in c or "display trapbuffer" in c) for c in commands)


def _batch59_read_item_output(item):
    for key in ("output", "output_preview", "raw_output"):
        if item.get(key):
            return str(item.get(key) or "")

    audit_path = item.get("audit_path")
    if audit_path and os.path.exists(str(audit_path)):
        try:
            with open(str(audit_path), "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("output", "output_preview", "raw_output", "result"):
                if data.get(key):
                    return str(data.get(key) or "")
            nested = data.get("response") or data.get("data") or {}
            if isinstance(nested, dict):
                for key in ("output", "output_preview", "raw_output", "result"):
                    if nested.get(key):
                        return str(nested.get(key) or "")
        except Exception:
            return ""

    return ""


def _batch59_analyze_log_outputs(items):
    facts = []
    abnormal = []
    next_steps = []

    total = len(items)
    ok_count = sum(1 for x in items if x.get("ok") is True)
    failed_count = total - ok_count

    facts.append("本轮日志取证命令共 {} 条，成功 {} 条，失败 {} 条。".format(total, ok_count, failed_count))

    keywords = [
        "error", "err", "fail", "failed", "failure", "exception", "traceback",
        "down", "up", "flap", "reset", "reload", "crash", "core", "timeout",
        "denied", "invalid", "auth", "aaa", "ssh", "link", "lineproto",
        "bgp", "ospf", "isis", "bfd", "stp", "loop", "storm",
        "transceiver", "module", "power", "temperature", "crc", "discard", "drop",
        "错误", "失败", "异常", "重启", "超时", "认证", "链路", "模块", "光模块",
    ]

    for item in items:
        cmd = item.get("command") or "-"
        status = item.get("execution_status") or item.get("status") or "-"
        ok = item.get("ok")
        facts.append("已执行命令：{}，状态={}，ok={}。".format(cmd, status, ok))

        output = _batch59_read_item_output(item)
        if not output:
            continue

        matched = []
        for line in output.splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            line_l = line_s.lower()
            if any(k in line_l for k in keywords):
                matched.append(line_s)
            if len(matched) >= 12:
                break

        if matched:
            abnormal.append({
                "command": cmd,
                "matched_lines": matched,
            })

    if abnormal:
        facts.append("日志输出中命中异常/事件关键字的命令数：{}。".format(len(abnormal)))
        for item in abnormal[:4]:
            facts.append("{} 命中日志样例：{}".format(item["command"], " | ".join(item["matched_lines"][:4])))
        conclusion = (
            "本轮日志命令已返回输出，并命中部分异常/事件关键字。"
            "需要结合日志时间戳、接口/协议/模块/认证等关键字进一步判断是否与当前故障现象相关。"
        )
    elif ok_count > 0:
        conclusion = (
            "本轮日志命令已成功返回，但在当前通用日志关键字扫描中未发现明显 error/fail/down/flap/reset/module/auth 等异常线索。"
            "这不代表设备一定没有问题，只能说明最近日志窗口内未被当前规则提取到明确异常。"
        )
    else:
        conclusion = (
            "本轮日志命令未成功返回，当前没有可用于判断日志异常的设备输出。"
            "需要先确认 Netmiko/MCP 到设备的只读命令执行链路。"
        )

    next_steps.extend([
        "优先按日志时间戳与故障/告警时间对齐，确认异常是否发生在同一时间窗口。",
        "重点查看日志中是否存在接口 down/up、flap、模块异常、协议邻居重置、AAA/SSH 失败、进程崩溃或 reload 事件。",
        "如日志窗口过短，可扩大到 show logging last 500 或结合设备保存的历史日志文件继续取证。",
        "如日志无明显异常，再结合对应问题类型补充接口、协议、资源或 Prometheus 历史指标证据。",
    ])

    return facts, conclusion, next_steps


def _batch59_build_log_execution_answer(items):
    facts, conclusion, next_steps = _batch59_analyze_log_outputs(items)
    total = len(items)
    ok_count = sum(1 for x in items if x.get("ok") is True)
    failed_count = total - ok_count

    lines = []
    lines.append("已确认批量执行上一轮 passed 日志只读命令。")
    lines.append("执行统计：total={}，ok={}，failed={}。".format(total, ok_count, failed_count))
    lines.append("")
    lines.append("命令执行结果摘要：")
    for item in items:
        lines.append("{}. {}：{}，ok={}".format(
            item.get("index") or "-",
            item.get("command") or "-",
            item.get("execution_status") or item.get("status") or "-",
            item.get("ok"),
        ))
    lines.append("")
    lines.append("综合分析：")
    lines.append(conclusion)
    lines.append("")
    lines.append("日志关键证据：")
    for idx, fact in enumerate(facts[:12], 1):
        lines.append("{}. {}".format(idx, fact))
    lines.append("")
    lines.append("建议下一步：")
    for idx, step in enumerate(next_steps[:8], 1):
        lines.append("{}. {}".format(idx, step))
    lines.append("")
    lines.append("说明：本轮为日志取证专项分析，只基于日志命令输出判断，不会套用资源类分析模板。")

    return "\n".join(lines), facts, conclusion, next_steps


def enrich_v2_execution_response(response, question="", user=None, conversation_id=None):
    if isinstance(response, dict) and response.get("planner_source") == "v2_execution_confirmation":
        items = response.get("items") or []
        if _batch59_is_log_execution(items):
            answer, facts, conclusion, next_steps = _batch59_build_log_execution_answer(items)
            response["answer"] = answer
            response.setdefault("v2", {})
            response["v2"]["category_analysis"] = {
                "category": "log",
                "facts": facts,
                "conclusion": conclusion,
                "next_steps": next_steps,
                "source": "batch59_log_execution_analysis",
            }
            for item in items:
                if isinstance(item, dict):
                    item["analysis_status"] = item.get("analysis_status") or "log_summary"
                    item["analysis_summary"] = item.get("analysis_summary") or "日志命令已返回输出，需结合异常关键字和时间窗口判断。"
            return response

    return _PRE_BATCH59_ENRICH_V2_EXECUTION_RESPONSE(
        response,
        question=question,
        user=user,
        conversation_id=conversation_id,
    )


# ===== Batch61 LLM evidence execution patch =====
# 执行确认响应主路径：items.audit_path 原始输出 -> 本地 LLM 分析 -> 前端返回 LLM 分析结果。

from netaiops_asset.chat_v2.llm_evidence_analyzer import analyze_evidence_with_llm as _batch61_analyze_evidence_with_llm

_PRE_BATCH61_ENRICH_V2_EXECUTION_RESPONSE = enrich_v2_execution_response


def enrich_v2_execution_response(response, question="", user=None, conversation_id=None):
    if isinstance(response, dict) and response.get("planner_source") == "v2_execution_confirmation":
        items = response.get("items") or []
        if items:
            context = {
                "current_device": {},
                "current_topic": "",
                "current_intent": "",
            }

            # 从第一条结果推断设备与主题。
            first = items[0] if isinstance(items[0], dict) else {}
            if isinstance(first, dict):
                context["current_device"] = {
                    "device_name": first.get("device_name") or first.get("hostname") or first.get("netmiko_device_name"),
                    "hostname": first.get("hostname") or first.get("device_name"),
                    "mgmt_ip": first.get("mgmt_ip"),
                    "device_type": first.get("device_type"),
                    "netmiko_device_name": first.get("netmiko_device_name") or first.get("device_name"),
                }
                context["current_topic"] = first.get("template_category") or first.get("category") or first.get("v2_intent") or ""
                context["current_intent"] = first.get("v2_intent") or first.get("template_category") or first.get("category") or ""

            llm_question = question or "请根据本轮 MCP/Netmiko 命令原始输出分析当前设备是否存在异常。"
            result = _batch61_analyze_evidence_with_llm(
                question=llm_question,
                context=context,
                items=items,
            )

            response.setdefault("v2", {})
            response["v2"]["llm_evidence_analysis"] = {
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "facts": result.get("facts") or [],
                "conclusion": result.get("conclusion"),
                "evidence_count": ((result.get("evidence_bundle") or {}).get("evidence_count")),
                "has_raw_output": ((result.get("evidence_bundle") or {}).get("has_raw_output")),
            }

            if result.get("ok"):
                response["answer"] = result.get("answer") or response.get("answer") or ""
                for item in items:
                    if isinstance(item, dict):
                        item["analysis_status"] = "llm_evidence_analyzed"
                        item["analysis_summary"] = "已读取 MCP/Netmiko 原始输出并交由本地 LLM 分析。"
                return response

            # 失败时明确报错，不用旧模板兜底伪装。
            response["answer"] = result.get("answer") or "LLM 分析失败：未知错误。"
            for item in items:
                if isinstance(item, dict):
                    item["analysis_status"] = "llm_evidence_analysis_failed"
                    item["analysis_summary"] = "LLM 原始输出分析失败：{}".format(result.get("error"))
            return response

    return _PRE_BATCH61_ENRICH_V2_EXECUTION_RESPONSE(
        response,
        question=question,
        user=user,
        conversation_id=conversation_id,
    )
