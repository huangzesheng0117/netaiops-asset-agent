# -*- coding: utf-8 -*-
"""
Conversation-style confirmation for V2 command execution.

Supported flows:
1. Confirm one command:
   确认执行第1条命令 YES

2. Context-following execution intent without YES:
   将你上述给出的命令在设备上执行，然后根据命令的结果给出分析

3. Confirm all passed commands:
   确认执行全部命令 YES
   执行上述全部命令 YES

Safety:
- Requires explicit YES for real execution.
- Only executes commands whose saved guard_status is passed.
- Uses ConfirmedNetmikoExecutor, so CLI Guard is checked again.
- review/blocked commands are skipped.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from netaiops_asset.netmiko.executor import ConfirmedNetmikoExecutor
from netaiops_asset.troubleshoot.output_analyzer import analyze_command_output, format_analysis_for_answer


PENDING_DIR = os.getenv(
    "NETAIOPS_V2_PENDING_COMMAND_DIR",
    "/var/lib/netaiops-asset-agent/data/v2_pending_commands",
)

MAX_BATCH_EXECUTE = int(os.getenv("NETAIOPS_V2_MAX_BATCH_EXECUTE", "20"))


CN_NUM = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


SINGLE_CONFIRM_RE = re.compile(
    r"(确认执行|执行)\s*第?\s*([0-9]+|[一二三四五六七八九十])\s*条",
    re.IGNORECASE,
)

BULK_CONFIRM_RE = re.compile(
    r"(确认执行|执行|运行|下发).*(全部|所有|上述|上面|刚才|这些|前面).*(命令)?",
    re.IGNORECASE,
)

CONTEXT_EXEC_HINTS = [
    "上述命令",
    "上面命令",
    "刚才命令",
    "这些命令",
    "你上述给出的命令",
    "你上面给出的命令",
    "上面的命令",
    "上述给出的命令",
    "给出的命令",
    "执行并分析",
    "执行一下",
    "在设备上执行",
    "根据命令的结果",
    "根据执行结果",
    "命令结果给出分析",
]


def _safe_user(user: Optional[str]) -> str:
    text = str(user or "anonymous").strip()
    text = re.sub(r"[^A-Za-z0-9_.@-]+", "_", text)
    return text or "anonymous"


def _safe_id(value: Optional[str]) -> str:
    text = str(value or "").strip()
    text = text.replace("/", "_").replace("..", "_")
    return text or str(uuid.uuid4())


def _pending_path_for_conversation(conversation_id: str) -> str:
    return os.path.join(PENDING_DIR, "conversation_{}.json".format(_safe_id(conversation_id)))


def _pending_path_for_user(user: Optional[str]) -> str:
    return os.path.join(PENDING_DIR, "latest_user_{}.json".format(_safe_user(user)))


def parse_confirmation_index(question: str) -> Optional[int]:
    text = str(question or "").strip()
    m = SINGLE_CONFIRM_RE.search(text)
    if not m:
        return None

    raw = m.group(2)
    if raw.isdigit():
        return int(raw)

    return CN_NUM.get(raw)


def is_bulk_context_request(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False

    if BULK_CONFIRM_RE.search(text):
        return True

    if any(x in text for x in CONTEXT_EXEC_HINTS):
        if "执行" in text or "运行" in text or "分析" in text or "结果" in text:
            return True

    return False


def has_explicit_yes(question: str) -> bool:
    return bool(re.search(r"\bYES\b", str(question or ""), re.IGNORECASE))


def store_pending_commands(
    conversation_id: Optional[str],
    user: Optional[str],
    question: str,
    response: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    items = response.get("items") or []
    if not items:
        return None

    pending_items: List[Dict[str, Any]] = []
    for idx, item in enumerate(items, 1):
        copied = dict(item)
        copied["index"] = idx
        pending_items.append(copied)

    if not pending_items:
        return None

    os.makedirs(PENDING_DIR, exist_ok=True)

    data = {
        "pending_id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "user": user,
        "source_question": question,
        "created_at": datetime.now().isoformat(),
        "items": pending_items,
        "answer": response.get("answer"),
        "parsed": response.get("parsed"),
    }

    paths = []

    if conversation_id:
        paths.append(_pending_path_for_conversation(conversation_id))

    paths.append(_pending_path_for_user(user))

    for path in paths:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    return data


def load_pending_commands(conversation_id: Optional[str], user: Optional[str]) -> Optional[Dict[str, Any]]:
    paths = []
    if conversation_id:
        paths.append(_pending_path_for_conversation(conversation_id))
    paths.append(_pending_path_for_user(user))

    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue

    return None


def try_handle_v2_execution_confirmation(
    question: str,
    user: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    index = parse_confirmation_index(question)
    bulk_request = is_bulk_context_request(question)

    if index is None and not bulk_request:
        return None

    pending = load_pending_commands(conversation_id=conversation_id, user=user)

    if not pending:
        return _chat_response(
            status="not_found",
            answer="没有找到上一轮待确认的 V2 命令建议。请先提问生成命令建议，例如：某设备 CPU 利用率该怎么排查？",
            items=[],
            parsed={
                "intent": "v2_execute_confirmation",
                "index": index,
                "bulk": bool(bulk_request),
                "reason": "pending_commands_not_found",
            },
        )

    if bulk_request and index is None:
        return _handle_bulk_confirmation(
            question=question,
            user=user,
            pending=pending,
        )

    return _handle_single_confirmation(
        question=question,
        user=user,
        pending=pending,
        index=index,
    )


def _handle_single_confirmation(
    question: str,
    user: Optional[str],
    pending: Dict[str, Any],
    index: Optional[int],
) -> Dict[str, Any]:
    items = pending.get("items") or []
    selected = None
    for item in items:
        if int(item.get("index") or 0) == int(index or 0):
            selected = item
            break

    if not selected:
        return _chat_response(
            status="not_found",
            answer="上一轮待确认命令中没有第 {} 条，请重新确认命令序号。".format(index),
            items=[],
            parsed={
                "intent": "v2_execute_confirmation",
                "index": index,
                "reason": "index_not_found",
            },
            v2={
                "pending": _pending_preview(pending),
            },
        )

    if selected.get("guard_status") != "passed":
        return _chat_response(
            status="rejected",
            answer="第 {} 条命令未通过只读校验，当前状态为 {}，不会执行。".format(
                index,
                selected.get("guard_status"),
            ),
            items=[selected],
            parsed={
                "intent": "v2_execute_confirmation",
                "index": index,
                "reason": "guard_status_not_passed",
            },
            v2={
                "pending": _pending_preview(pending),
                "selected": selected,
            },
        )

    if not has_explicit_yes(question):
        return _chat_response(
            status="pending_confirmation",
            answer=(
                "第 {idx} 条命令可以执行，但为避免误操作，需要显式确认。\n"
                "请发送：确认执行第{idx}条命令 YES"
            ).format(idx=index),
            items=[selected],
            parsed={
                "intent": "v2_execute_confirmation",
                "index": index,
                "reason": "missing_explicit_yes",
            },
            v2={
                "pending": _pending_preview(pending),
                "selected": selected,
                "execute_policy": {
                    "requires_yes": True,
                    "auto_execute": False,
                },
            },
        )

    result_item, result, analysis = _execute_one_selected(index=index, selected=selected, user=user)

    if result.get("ok"):
        analysis_text = format_analysis_for_answer(analysis) if analysis else ""
        answer = (
            "已确认执行第 {idx} 条只读命令。\n"
            "设备：{device}\n"
            "命令：{command}\n"
            "执行状态：{status}\n\n"
            "{analysis}\n\n"
            "原始输出预览：\n{output}"
        ).format(
            idx=index,
            device=selected.get("device_name"),
            command=selected.get("command"),
            status=result.get("status"),
            analysis=analysis_text,
            output=(result.get("output_preview") or "").strip()[:3000],
        )
        status = "ok"
    else:
        answer = (
            "第 {idx} 条命令执行失败或被拒绝。\n"
            "设备：{device}\n"
            "命令：{command}\n"
            "状态：{status}\n"
            "错误：{error}"
        ).format(
            idx=index,
            device=selected.get("device_name"),
            command=selected.get("command"),
            status=result.get("status"),
            error=result.get("error") or result.get("audit_error") or "-",
        )
        status = "failed"

    return _chat_response(
        status=status,
        answer=answer,
        items=[result_item],
        parsed={
            "intent": "v2_execute_confirmation",
            "index": index,
            "device_name": selected.get("device_name"),
            "device_type": selected.get("device_type"),
            "command": selected.get("command"),
        },
        v2={
            "pending": _pending_preview(pending),
            "selected": selected,
            "execution_result": result,
            "analysis": analysis,
        },
    )


def _handle_bulk_confirmation(
    question: str,
    user: Optional[str],
    pending: Dict[str, Any],
) -> Dict[str, Any]:
    items = pending.get("items") or []

    passed_items = [x for x in items if x.get("guard_status") == "passed"]
    review_items = [x for x in items if x.get("guard_status") == "review"]
    blocked_items = [x for x in items if x.get("guard_status") == "blocked"]

    if not passed_items:
        return _chat_response(
            status="rejected",
            answer="上一轮命令中没有可执行的 passed 命令。review/blocked 命令不会被批量执行。",
            items=items,
            parsed={
                "intent": "v2_execute_all_confirmation",
                "bulk": True,
                "reason": "no_passed_commands",
            },
            v2={
                "pending": _pending_preview(pending),
                "counts": _counts(passed_items, review_items, blocked_items),
            },
        )

    if len(passed_items) > MAX_BATCH_EXECUTE:
        return _chat_response(
            status="rejected",
            answer="上一轮 passed 命令数量为 {}，超过批量执行上限 {}，请改为确认执行指定序号。".format(
                len(passed_items),
                MAX_BATCH_EXECUTE,
            ),
            items=passed_items,
            parsed={
                "intent": "v2_execute_all_confirmation",
                "bulk": True,
                "reason": "too_many_commands",
            },
            v2={
                "pending": _pending_preview(pending),
                "counts": _counts(passed_items, review_items, blocked_items),
                "max_batch_execute": MAX_BATCH_EXECUTE,
            },
        )

    if not has_explicit_yes(question):
        answer = build_bulk_pending_answer(pending, passed_items, review_items, blocked_items)
        return _chat_response(
            status="pending_confirmation",
            answer=answer,
            items=passed_items,
            parsed={
                "intent": "v2_execute_all_confirmation",
                "bulk": True,
                "reason": "missing_explicit_yes",
            },
            v2={
                "pending": _pending_preview(pending),
                "counts": _counts(passed_items, review_items, blocked_items),
                "execute_policy": {
                    "requires_yes": True,
                    "auto_execute": False,
                    "confirm_text": "确认执行全部命令 YES",
                },
            },
        )

    executed_items: List[Dict[str, Any]] = []
    analyses: List[Dict[str, Any]] = []

    for item in passed_items:
        idx = int(item.get("index") or 0)
        result_item, result, analysis = _execute_one_selected(index=idx, selected=item, user=user)
        executed_items.append(result_item)
        if analysis:
            analyses.append({
                "index": idx,
                "command": item.get("command"),
                "analysis": analysis,
            })

    ok_count = sum(1 for x in executed_items if x.get("ok") is True)
    failed_count = len(executed_items) - ok_count

    status = "ok" if failed_count == 0 else "partial"
    answer = build_bulk_executed_answer(
        pending=pending,
        executed_items=executed_items,
        analyses=analyses,
        review_items=review_items,
        blocked_items=blocked_items,
    )

    return _chat_response(
        status=status,
        answer=answer,
        items=executed_items,
        parsed={
            "intent": "v2_execute_all_confirmation",
            "bulk": True,
            "executed_count": len(executed_items),
            "ok_count": ok_count,
            "failed_count": failed_count,
        },
        v2={
            "pending": _pending_preview(pending),
            "counts": {
                "passed": len(passed_items),
                "review": len(review_items),
                "blocked": len(blocked_items),
                "executed": len(executed_items),
                "ok": ok_count,
                "failed": failed_count,
            },
            "executions": executed_items,
            "analyses": analyses,
        },
    )


def _execute_one_selected(index: int, selected: Dict[str, Any], user: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    executor = ConfirmedNetmikoExecutor()

    device_name = selected.get("device_name") or ""
    device_type = selected.get("device_type") or None
    command = selected.get("command") or ""

    result = executor.execute_confirmed(
        device_name=device_name,
        command=command,
        device_type=device_type,
        confirm_execute="YES",
        confirmed_by=user,
        timeout=120,
    )

    analysis = None
    if result.get("ok"):
        analysis = analyze_command_output(
            command=command,
            output=result.get("output") or result.get("output_preview") or "",
            device_name=device_name,
            device_type=device_type,
        )

    result_item = {
        "index": index,
        "device_name": device_name,
        "device_type": device_type or "",
        "command": command,
        "execution_status": result.get("status"),
        "ok": result.get("ok"),
        "audit_path": result.get("audit_path"),
        "audit_error": result.get("audit_error"),
        "output_preview": result.get("output_preview"),
        "analysis_summary": analysis.get("summary") if analysis else "",
        "analysis_status": analysis.get("status") if analysis else "",
    }

    return result_item, result, analysis


def build_bulk_pending_answer(
    pending: Dict[str, Any],
    passed_items: List[Dict[str, Any]],
    review_items: List[Dict[str, Any]],
    blocked_items: List[Dict[str, Any]],
) -> str:
    lines = []
    lines.append("我识别到你想执行上一轮生成的命令并根据结果分析。")
    lines.append("为避免误操作，本轮不会自动执行。")
    lines.append("")
    lines.append("上一轮可批量执行的 passed 命令共 {} 条：".format(len(passed_items)))

    for item in passed_items:
        lines.append("{}. {}  # {}".format(
            item.get("index"),
            item.get("command"),
            item.get("purpose") or "-",
        ))

    if review_items:
        lines.append("")
        lines.append("review 命令 {} 条，不会被批量执行。".format(len(review_items)))

    if blocked_items:
        lines.append("blocked 命令 {} 条，不会被批量执行。".format(len(blocked_items)))

    lines.append("")
    lines.append("如确认执行全部 passed 命令，请发送：")
    lines.append("确认执行全部命令 YES")
    lines.append("")
    lines.append("也可以只执行某一条，例如：确认执行第1条命令 YES")

    return "\n".join(lines)


def build_bulk_executed_answer(
    pending: Dict[str, Any],
    executed_items: List[Dict[str, Any]],
    analyses: List[Dict[str, Any]],
    review_items: List[Dict[str, Any]],
    blocked_items: List[Dict[str, Any]],
) -> str:
    ok_count = sum(1 for x in executed_items if x.get("ok") is True)
    failed_count = len(executed_items) - ok_count

    lines = []
    lines.append("已确认批量执行上一轮 passed 只读命令。")
    lines.append("执行统计：total={}，ok={}，failed={}。".format(len(executed_items), ok_count, failed_count))

    if review_items or blocked_items:
        lines.append("未执行：review={}，blocked={}。".format(len(review_items), len(blocked_items)))

    lines.append("")
    lines.append("命令执行结果摘要：")

    for item in executed_items:
        lines.append("{}. {}：{}，ok={}".format(
            item.get("index"),
            item.get("command"),
            item.get("execution_status"),
            item.get("ok"),
        ))

    lines.append("")
    lines.append("综合分析：")
    lines.extend(build_combined_analysis_lines(analyses))

    lines.append("")
    lines.append("分命令关键分析：")
    for item in analyses:
        idx = item.get("index")
        command = item.get("command")
        analysis = item.get("analysis") or {}
        lines.append("")
        lines.append("第 {} 条：{}".format(idx, command))
        lines.append(analysis.get("summary") or "-")
        facts = analysis.get("facts") or []
        for fact_idx, fact in enumerate(facts[:5], 1):
            lines.append("  {}. {}".format(fact_idx, fact))

    lines.append("")
    lines.append("建议下一步：")
    next_steps = []
    for item in analyses:
        analysis = item.get("analysis") or {}
        for step in analysis.get("next_steps") or []:
            if step not in next_steps:
                next_steps.append(step)

    if not next_steps:
        next_steps.append("结合业务现象继续补充 Prometheus 历史趋势、日志和协议状态证据。")

    for idx, step in enumerate(next_steps[:8], 1):
        lines.append("{}. {}".format(idx, step))

    return "\n".join(lines)


def build_combined_analysis_lines(analyses: List[Dict[str, Any]]) -> List[str]:
    lines = []

    cpu_status = None
    resource_analysis = None

    for item in analyses:
        analysis = item.get("analysis") or {}
        if analysis.get("analysis_type") == "nxos_system_resources":
            resource_analysis = analysis
            metrics = analysis.get("metrics") or {}
            cpu_total = metrics.get("cpu_total") or {}
            if cpu_total:
                cpu_status = cpu_total

    if cpu_status:
        used = cpu_status.get("used")
        idle = cpu_status.get("idle")
        lines.append("系统资源命令显示整体 CPU used≈{}%，idle≈{}%。".format(used, idle))
        try:
            used_f = float(used)
            if used_f >= 80:
                lines.append("当前证据支持 CPU 处于较高负载状态，需要优先定位高 CPU 进程。")
            elif used_f >= 50:
                lines.append("当前 CPU 使用率中等，需要结合进程排序和历史趋势判断是否异常。")
            else:
                lines.append("当前整体 CPU 使用率不高，暂不支持设备整体 CPU 高负载的判断。")
        except Exception:
            pass
    else:
        lines.append("本轮未能从 system resources 输出中解析出明确 CPU 总体使用率。")

    if resource_analysis:
        metrics = resource_analysis.get("metrics") or {}
        memory = metrics.get("memory") or {}
        if memory:
            lines.append("内存使用率约 {}%，内存状态需结合平台阈值判断。".format(memory.get("used_pct")))

    generic_count = sum(1 for item in analyses if (item.get("analysis") or {}).get("analysis_type") in ("generic", "generic_cpu"))
    if generic_count:
        lines.append("部分命令已返回输出，但当前仍以通用分析为主，后续可继续细化对应命令的解析器。")

    return lines


def _pending_preview(pending: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pending_id": pending.get("pending_id"),
        "conversation_id": pending.get("conversation_id"),
        "user": pending.get("user"),
        "source_question": pending.get("source_question"),
        "created_at": pending.get("created_at"),
        "item_count": len(pending.get("items") or []),
    }


def _counts(passed_items: List[Dict[str, Any]], review_items: List[Dict[str, Any]], blocked_items: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "passed": len(passed_items),
        "review": len(review_items),
        "blocked": len(blocked_items),
    }


def _chat_response(
    status: str,
    answer: str,
    items: List[Dict[str, Any]],
    parsed: Dict[str, Any],
    v2: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "question": "",
        "parsed": parsed,
        "llm_plan": None,
        "planner_source": "v2_execution_confirmation",
        "planner_diagnostics": None,
        "answer": answer,
        "columns": [
            "index",
            "device_name",
            "device_type",
            "command",
            "execution_status",
            "ok",
            "audit_path",
            "audit_error",
            "analysis_status",
            "analysis_summary",
            "output_preview",
        ],
        "field_labels": {
            "index": "序号",
            "device_name": "Netmiko设备名",
            "device_type": "设备类型",
            "command": "命令",
            "execution_status": "执行状态",
            "ok": "是否成功",
            "audit_path": "审计文件",
            "audit_error": "审计错误",
            "analysis_status": "分析状态",
            "analysis_summary": "分析摘要",
            "output_preview": "输出预览",
        },
        "count": len(items),
        "returned": len(items),
        "items": items,
        "v2": v2 or {},
    }
