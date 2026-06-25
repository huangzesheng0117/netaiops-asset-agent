# -*- coding: utf-8 -*-
"""
V2 follow-up analysis router.

Purpose:
- Answer follow-up questions based on V2 conversation context.
- Avoid falling back to V1 CMDB when user says:
  - 结合以上三点
  - 根据刚才结果
  - 当前是否真是 CPU 问题
  - 下一步查什么
  - 这些结果说明什么

Safety:
- This module only reads saved context.
- It does not execute device CLI.
- It does not call Netmiko MCP.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from netaiops_asset.chat_v2.context import load_v2_context
from netaiops_asset.chat_v2.execution_response_enricher import is_execution_result_followup_question_text, build_interface_error_followup_answer


FOLLOWUP_HINTS = [
    "根据命令执行结果",
    "根据执行结果",
    "根据命令结果",
    "命令执行结果",
    "命令结果",
    "执行结果",
    "分析原因",
    "判断原因",
    "原因是什么",
    "给出原因",
    "总结",
    "总结一下",
    "总结目前",
    "总结当前",
    "排查到的结论",
    "排查结论",
    "当前结论",
    "目前结论",
    "最终结论",
    "结论是什么",
    "到目前为止",
    "结合以上",
    "结合上面",
    "结合上述",
    "以上三点",
    "以上结果",
    "上述结果",
    "上述分析",
    "刚才结果",
    "刚才的结果",
    "刚才分析",
    "刚才的分析",
    "这些结果",
    "这些信息",
    "这些证据",
    "说明什么",
    "能说明",
    "是否说明",
    "是否真",
    "是不是",
    "是不是CPU",
    "是不是 CPU",
    "更准确的结论",
    "明确的结论",
    "综合判断",
    "综合分析",
    "下一步",
    "还需要查",
    "继续分析",
    "继续判断",
    "如果CPU不高",
    "如果 CPU 不高",
]


def try_handle_v2_followup_analysis(
    question: str,
    user: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    q = str(question or "").strip()
    if not q:
        return None

    context = load_v2_context(conversation_id=conversation_id, user=user)
    if not context:
        return None

    if not is_followup_question(q):
        return None

    if not has_usable_context(context):
        return _chat_response(
            status="need_more_evidence",
            question=q,
            answer=(
                "我识别到这是一个基于上下文的追问，但当前会话里还没有足够的 V2 执行结果或分析证据。"
                "请先生成排查命令并确认执行，或补充设备和问题现象。"
            ),
            parsed={
                "intent": "v2_followup_analysis",
                "reason": "context_exists_but_no_usable_evidence",
            },
            v2={
                "context_used": True,
                "context_summary": compact_context(context),
            },
        )

    answer, facts, conclusion, next_steps = build_followup_answer(q, context)

    return _chat_response(
        status="ok",
        question=q,
        answer=answer,
        parsed={
            "intent": "v2_followup_analysis",
            "reason": "answered_from_v2_conversation_context",
            "device_name": ((context.get("current_device") or {}).get("device_name")),
            "mgmt_ip": ((context.get("current_device") or {}).get("mgmt_ip")),
            "device_type": ((context.get("current_device") or {}).get("device_type")),
            "current_topic": context.get("current_topic"),
            "current_intent": context.get("current_intent"),
        },
        v2={
            "context_used": True,
            "context_summary": compact_context(context),
            "facts": facts,
            "conclusion": conclusion,
            "next_steps": next_steps,
        },
    )


def is_followup_question(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False

    # execution_priority_patch:
    # 执行命令请求必须先进入 v2_execution_confirmation，不能被 follow-up 抢走。
    if is_v2_execution_request_question(text):
        return False

    if is_execution_result_followup_question_text(text):
        return True

    if any(h in text for h in FOLLOWUP_HINTS):
        return True

    # Summary/conclusion style follow-ups should be answered from saved V2
    # context, even if the text also contains CPU/device keywords.
    # Example: 总结一下目前这个设备CPU排查到的结论
    if ("总结" in text or "结论" in text) and any(
        x in text for x in ["设备", "CPU", "cpu", "排查", "结果", "目前", "当前"]
    ):
        return True

    if re.search(r"(那|那么|所以|因此).*(结论|下一步|还需要|是否|是不是|说明)", text):
        return True

    return False


def has_usable_context(context: Dict[str, Any]) -> bool:
    if context.get("last_executions"):
        return True
    if context.get("last_analysis"):
        return True
    if context.get("last_bulk_analysis"):
        return True
    if context.get("last_prometheus_evidence"):
        return True
    return False


def build_followup_answer(question: str, context: Dict[str, Any]) -> tuple[str, List[str], str, List[str]]:
    current_intent = context.get("current_intent")
    current_topic = context.get("current_topic")
    if current_intent == "interface_error_check" or current_topic == "interface_error":
        return build_interface_error_followup_answer(question, context)

    device = context.get("current_device") or {}
    topic = context.get("current_topic") or "-"
    intent = context.get("current_intent") or "-"

    facts = collect_context_facts(context)
    conclusion = build_conclusion(question, context)
    next_steps = build_next_steps(question, context)

    lines: List[str] = []
    lines.append("我会沿用上一轮 V2 会话上下文继续分析。")
    lines.append("当前设备：{}，管理IP：{}，设备类型：{}，当前主题：{}。".format(
        device.get("device_name") or "-",
        device.get("mgmt_ip") or "-",
        device.get("device_type") or "-",
        topic,
    ))

    lines.append("")
    lines.append("可引用的上下文证据：")
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
    lines.append("说明：本轮回答来自已保存的 V2 上下文，不会重新执行设备命令。")

    return "\n".join(lines), facts, conclusion, next_steps


def collect_context_facts(context: Dict[str, Any]) -> List[str]:
    facts: List[str] = []

    prom = context.get("last_prometheus_evidence") or {}
    evidence = prom.get("evidence") or {}
    if evidence:
        if evidence.get("status") == "ok":
            matched = evidence.get("matched") or {}
            facts.append("Prometheus 当前 CPU 证据命中：{}，当前值={}。".format(
                matched.get("query") or "-",
                matched.get("sample_value") or "-",
            ))
        else:
            facts.append("Prometheus 证据状态：{}，{}。".format(
                evidence.get("status"),
                evidence.get("summary") or "-",
            ))

    executions = context.get("last_executions") or []
    if executions:
        ok_count = sum(1 for x in executions if x.get("ok") is True)
        facts.append("最近已执行只读命令 {} 条，其中成功 {} 条。".format(len(executions), ok_count))
        for item in executions[-8:]:
            facts.append("已执行命令：{}，状态={}，分析摘要={}。".format(
                item.get("command"),
                item.get("execution_status"),
                item.get("analysis_summary") or "-",
            ))

    analysis = context.get("last_analysis") or {}
    if analysis.get("analysis"):
        a = analysis.get("analysis") or {}
        facts.append("最近单命令分析结论：{}。".format(a.get("summary") or "-"))

    bulk = context.get("last_bulk_analysis") or {}
    analyses = bulk.get("analyses") or []
    if analyses:
        facts.append("最近批量分析包含 {} 条命令分析。".format(len(analyses)))
        for item in analyses[:8]:
            a = item.get("analysis") or {}
            facts.append("第 {} 条 {}：{}。".format(
                item.get("index"),
                item.get("command"),
                a.get("summary") or "-",
            ))

    if not facts:
        summary = context.get("rolling_summary")
        if summary:
            facts.append("上下文摘要：{}".format(summary))

    return dedupe(facts)


def build_conclusion(question: str, context: Dict[str, Any]) -> str:
    topic = context.get("current_topic")
    cpu_used = extract_cpu_used_from_context(context)
    prom_cpu = extract_prom_cpu_from_context(context)

    q = str(question or "")

    if topic == "cpu":
        parts = []

        if prom_cpu is not None:
            parts.append("Prometheus 当前 CPU 值约为 {}。".format(prom_cpu))

        if cpu_used is not None:
            parts.append("设备 CLI system resources 解析到整体 CPU used≈{}%。".format(cpu_used))

        if prom_cpu is not None and cpu_used is not None:
            if prom_cpu < 50 and cpu_used < 50:
                parts.append("两类证据都偏低，当前证据不支持“设备当前整体 CPU 高负载”这个判断。")
            elif prom_cpu >= 80 or cpu_used >= 80:
                parts.append("至少一类证据显示 CPU 偏高，需要继续定位高 CPU 进程和触发原因。")
            else:
                parts.append("CPU 处于中等或不一致状态，需要结合历史趋势判断是否为瞬时波动。")
        elif prom_cpu is not None:
            if prom_cpu < 50:
                parts.append("仅从 Prometheus 当前值看，CPU 不高；但还需要结合 CLI 或历史趋势确认。")
            else:
                parts.append("Prometheus 当前值提示 CPU 有压力，需要结合 CLI 进程信息确认。")
        elif cpu_used is not None:
            if cpu_used < 50:
                parts.append("仅从 CLI 当前输出看，CPU 不高；但还需要结合历史趋势确认是否曾经异常。")
            else:
                parts.append("CLI 当前输出提示 CPU 有压力，需要继续定位高 CPU 进程。")
        else:
            parts.append("当前上下文没有解析到明确 CPU 数值，只能基于已执行命令和输出摘要做保守判断。")

        if "是不是" in q or "是否" in q or "真" in q:
            parts.append("因此，当前更合理的结论是：暂不能把问题直接定性为设备 CPU 当前高负载，应继续检查历史趋势、日志和业务相关链路。")

        return "".join(parts)

    return "当前上下文主题为 {}。基于已保存证据，可以继续沿用该设备和主题分析，但该主题的专项结论规则还需要后续增强。".format(topic or "-")


def build_next_steps(question: str, context: Dict[str, Any]) -> List[str]:
    topic = context.get("current_topic")
    steps: List[str] = []

    if topic == "cpu":
        steps.extend([
            "查询 Prometheus 最近 30～60 分钟 CPU 趋势，判断当前低 CPU 是否只是恢复后的状态。",
            "结合告警发生时间点查看 CPU 峰值，而不是只看当前瞬时值。",
            "继续分析 show processes cpu / show processes cpu sort 输出，确认是否存在异常进程。",
            "结合 show logging last 100 输出，查看是否存在协议震荡、接口 flap、模块异常或进程异常日志。",
            "如果 CPU 证据不支持异常，应转向接口错误、链路拥塞、BGP/路由震荡、对端业务异常等方向。",
        ])
    else:
        steps.extend([
            "继续补充该主题的历史指标趋势。",
            "结合最近已执行命令输出，确认是否存在明确异常证据。",
            "如证据不足，生成下一批只读命令并通过确认流程执行。",
        ])

    q = str(question or "")
    if "历史趋势" in q or "Prometheus" in q or "prometheus" in q:
        steps.insert(0, "可以继续查 Prometheus 历史趋势；建议优先查告警时间前后窗口，而不是只看当前值。")

    return dedupe(steps)


def extract_prom_cpu_from_context(context: Dict[str, Any]) -> Optional[float]:
    prom = context.get("last_prometheus_evidence") or {}
    evidence = prom.get("evidence") or {}
    matched = evidence.get("matched") or {}

    value = matched.get("sample_value")
    try:
        return float(value)
    except Exception:
        return None


def extract_cpu_used_from_context(context: Dict[str, Any]) -> Optional[float]:
    bulk = context.get("last_bulk_analysis") or {}
    analyses = bulk.get("analyses") or []

    for item in analyses:
        analysis = item.get("analysis") or {}
        metrics = analysis.get("metrics") or {}
        cpu_total = metrics.get("cpu_total") or {}
        if cpu_total.get("used") is not None:
            try:
                return float(cpu_total.get("used"))
            except Exception:
                pass

    single = context.get("last_analysis") or {}
    analysis = single.get("analysis") or {}
    metrics = analysis.get("metrics") or {}
    cpu_total = metrics.get("cpu_total") or {}

    if cpu_total.get("used") is not None:
        try:
            return float(cpu_total.get("used"))
        except Exception:
            return None

    return None


def compact_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "conversation_id": context.get("conversation_id"),
        "current_device": context.get("current_device"),
        "current_topic": context.get("current_topic"),
        "current_intent": context.get("current_intent"),
        "last_action_intent": context.get("last_action_intent"),
        "last_command_suggestions_count": len(context.get("last_command_suggestions") or []),
        "last_executions_count": len(context.get("last_executions") or []),
        "has_last_analysis": bool(context.get("last_analysis")),
        "has_last_bulk_analysis": bool(context.get("last_bulk_analysis")),
        "rolling_summary": context.get("rolling_summary"),
    }


def _chat_response(
    status: str,
    question: str,
    answer: str,
    parsed: Dict[str, Any],
    v2: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "question": question,
        "parsed": parsed,
        "llm_plan": None,
        "planner_source": "v2_followup_analysis",
        "planner_diagnostics": None,
        "answer": answer,
        "columns": [],
        "field_labels": {},
        "count": 0,
        "returned": 0,
        "items": [],
        "v2": v2 or {},
    }


def dedupe(items: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
from netaiops_asset.chat_v2.execution_response_enricher import is_v2_execution_request_question


# ===== Batch58 semantic-route forced follow-up =====

def try_handle_v2_followup_analysis_forced(question: str, user=None, conversation_id=None):
    """
    Force follow-up analysis after LLM/Dispatcher has already decided route=v2_followup_analysis.

    This bypasses local semantic hint matching. Local code only reads context and builds answer.
    """
    try:
        context = load_v2_context(conversation_id=conversation_id, user=user)
    except Exception as exc:
        return {
            "status": "need_clarification",
            "planner_source": "v2_followup_analysis",
            "parsed": {
                "reason": "context_load_failed",
                "error": repr(exc),
            },
            "answer": "我识别到这是基于上下文的追问分析，但读取上下文失败，请重新指定设备或重新生成排查命令。",
            "columns": [],
            "field_labels": {},
            "count": 0,
            "returned": 0,
            "items": [],
        }

    if not context:
        return {
            "status": "need_clarification",
            "planner_source": "v2_followup_analysis",
            "parsed": {
                "reason": "context_missing",
            },
            "answer": "我识别到这是基于上下文的追问分析，但当前会话上下文为空，请重新指定设备或重新生成排查命令。",
            "columns": [],
            "field_labels": {},
            "count": 0,
            "returned": 0,
            "items": [],
        }

    answer, facts, conclusion, next_steps = build_followup_answer(question, context)

    return {
        "status": "ok",
        "planner_source": "v2_followup_analysis",
        "parsed": {
            "intent": "v2_followup_analysis",
            "reason": "semantic_route_forced",
            "current_device": context.get("current_device"),
            "current_topic": context.get("current_topic"),
            "current_intent": context.get("current_intent"),
        },
        "answer": answer,
        "columns": [],
        "field_labels": {},
        "count": 0,
        "returned": 0,
        "items": [],
        "v2": {
            "facts": facts,
            "conclusion": conclusion,
            "next_steps": next_steps,
        },
    }


# ===== Batch59 followup evidence isolation patch =====

_PRE_BATCH59_BUILD_FOLLOWUP_ANSWER = build_followup_answer


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
    return ""


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

    if cur_name and item_name and cur_name != item_name:
        return False
    if cur_ip and item_ip and cur_ip != item_ip:
        return False
    return True


def _batch59_topic_match(context, item):
    current_topic = _batch59_norm_topic(context.get("current_intent") or context.get("current_topic"))
    command = item.get("command") or ""
    item_topic = _batch59_norm_topic(
        item.get("v2_intent") or item.get("template_category") or item.get("category") or _batch59_cmd_topic(command)
    )

    if not current_topic:
        return True

    if current_topic == item_topic:
        return True

    if item_topic == "log_check" and current_topic in (
        "interface_error_check",
        "interface_check",
        "bgp_check",
        "route_table",
        "cpu_check",
    ):
        return True

    return False


def _batch59_filtered_context(context):
    ctx = dict(context or {})

    executions = []
    for item in ctx.get("last_executions") or []:
        if not isinstance(item, dict):
            continue
        if _batch59_device_match(ctx, item) and _batch59_topic_match(ctx, item):
            executions.append(item)
    ctx["last_executions"] = executions

    # 批量分析也必须过滤；无法判断匹配时直接丢弃，避免旧 CPU 结论污染。
    bulk = ctx.get("last_bulk_analysis")
    if isinstance(bulk, dict):
        analyses = []
        for item in bulk.get("analyses") or []:
            if not isinstance(item, dict):
                continue
            wrapper = {
                "command": item.get("command") or item.get("cmd") or "",
                "device_name": item.get("device_name"),
                "mgmt_ip": item.get("mgmt_ip"),
                "v2_intent": item.get("v2_intent"),
                "template_category": item.get("template_category"),
                "category": item.get("category"),
            }
            if _batch59_device_match(ctx, wrapper) and _batch59_topic_match(ctx, wrapper):
                analyses.append(item)
        if analyses:
            new_bulk = dict(bulk)
            new_bulk["analyses"] = analyses
            new_bulk["analysis_count"] = len(analyses)
            ctx["last_bulk_analysis"] = new_bulk
        else:
            ctx["last_bulk_analysis"] = None

    topic = _batch59_norm_topic(ctx.get("current_intent") or ctx.get("current_topic"))
    prom = ctx.get("last_prometheus_evidence")
    if topic != "cpu_check" and isinstance(prom, dict):
        text = str(prom).lower()
        if "cpu" in text or "cpmcputotal" in text:
            ctx["last_prometheus_evidence"] = None

    return ctx


def _batch59_build_log_followup_answer(question, context):
    ctx = _batch59_filtered_context(context)
    device = ctx.get("current_device") or {}
    executions = ctx.get("last_executions") or []

    facts = []
    next_steps = []

    lines = []
    lines.append("我会基于当前会话中与该设备、该主题匹配的日志证据继续分析。")
    lines.append("当前设备：{}，管理IP：{}，设备类型：{}，当前主题：{}。".format(
        device.get("device_name") or "-",
        device.get("mgmt_ip") or "-",
        device.get("device_type") or "-",
        ctx.get("current_topic") or "-",
    ))
    lines.append("")

    log_execs = [
        x for x in executions
        if "show logging" in str(x.get("command") or "").lower()
        or "display log" in str(x.get("command") or "").lower()
        or "display trapbuffer" in str(x.get("command") or "").lower()
    ]

    if not log_execs:
        conclusion = (
            "当前会话还没有与该设备、log_check 主题匹配的日志命令执行结果。"
            "因此不能引用旧会话或其他设备的证据来判断日志是否异常。"
        )
        facts.append("未找到当前会话、当前设备、log_check 主题匹配的日志执行结果。")
        next_steps.extend([
            "先执行上一轮建议的 show logging last 100 / show logging last 300 日志命令。",
            "执行完成后再基于当前批次日志输出判断是否存在异常。",
        ])
    else:
        ok_count = sum(1 for x in log_execs if x.get("ok") is True)
        facts.append("当前会话匹配到日志执行命令 {} 条，其中成功 {} 条。".format(len(log_execs), ok_count))
        for item in log_execs:
            facts.append("已执行日志命令：{}，状态={}，摘要={}。".format(
                item.get("command") or "-",
                item.get("execution_status") or item.get("status") or "-",
                item.get("analysis_summary") or "命令已返回输出",
            ))
        conclusion = (
            "当前只引用与该设备、log_check 主题匹配的日志命令结果。"
            "如需判断是否存在异常，需要进一步解析日志输出中的 error/fail/down/flap/reset/module/auth 等关键事件。"
        )
        next_steps.extend([
            "按日志时间戳与故障/告警时间对齐。",
            "重点确认是否存在接口 down/up、flap、模块异常、协议邻居重置、认证失败、进程异常或 reload 事件。",
            "如现有日志窗口不足，扩大日志窗口后再执行日志取证。",
        ])

    lines.append("可引用的当前主题证据：")
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
    lines.append("说明：本轮回答只使用当前会话、当前设备、当前日志主题匹配的证据，不会复用其他设备或其他主题的历史执行结果。")

    return "\n".join(lines), facts, conclusion, next_steps


def build_followup_answer(question, context):
    ctx = _batch59_filtered_context(context)
    topic = _batch59_norm_topic(ctx.get("current_intent") or ctx.get("current_topic"))

    if topic == "log_check":
        return _batch59_build_log_followup_answer(question, ctx)

    return _PRE_BATCH59_BUILD_FOLLOWUP_ANSWER(question, ctx)


# ===== Batch61 LLM evidence follow-up patch =====
# 主路径：当前会话执行结果 -> audit_path 原始输出 -> 本地 LLM 分析。
# 不再用本地模板替代分析。

from netaiops_asset.chat_v2.llm_evidence_analyzer import analyze_evidence_with_llm

_PRE_BATCH61_BUILD_FOLLOWUP_ANSWER = build_followup_answer


def build_followup_answer(question, context):
    result = analyze_evidence_with_llm(question=question, context=context)

    if result.get("ok"):
        return (
            result.get("answer") or "",
            result.get("facts") or [],
            result.get("conclusion") or "",
            result.get("next_steps") or [],
        )

    # LLM 分析失败时必须显式报错，不允许本地模板伪装成分析完成。
    answer = result.get("answer") or "LLM 分析失败：未知错误。"
    facts = [
        "已进入 Batch61 LLM 证据分析链路。",
        "但未能完成本地 LLM 分析。",
        "错误信息：{}".format(result.get("error")),
    ]
    conclusion = "本轮未完成基于原始输出的 LLM 分析。"
    next_steps = [
        "检查 audit_path 是否存在并包含原始 output。",
        "检查本地 LLM 网关、API KEY、模型权限和上下文长度限制。",
    ]
    return answer, facts, conclusion, next_steps
