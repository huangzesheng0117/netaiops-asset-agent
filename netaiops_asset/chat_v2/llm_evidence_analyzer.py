# -*- coding: utf-8 -*-
"""
Batch61: MCP/Netmiko raw output -> local LLM evidence analysis.

原则：
1. 不再用本地模板冒充分析结果。
2. 从 last_executions.audit_path 读取 MCP/Netmiko 原始输出。
3. 将原始输出、命令、设备、主题、用户问题交给本地 LLM。
4. LLM 成功则前端返回 LLM 分析结果。
5. LLM 失败则明确报错，不再返回“需要进一步解析”的占位话。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from netaiops_asset.chat_v2.llm_intent_planner import _load_llm_config, _safe_config


MAX_PER_COMMAND_CHARS = int(os.getenv("NETAIOPS_V2_EVIDENCE_MAX_PER_COMMAND_CHARS", "30000"))
MAX_TOTAL_EVIDENCE_CHARS = int(os.getenv("NETAIOPS_V2_EVIDENCE_MAX_TOTAL_CHARS", "90000"))
LLM_TIMEOUT = int(os.getenv("NETAIOPS_V2_EVIDENCE_LLM_TIMEOUT", "90"))


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _walk_strings(obj: Any, path: str = "") -> List[Tuple[str, str]]:
    values: List[Tuple[str, str]] = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            sub_path = f"{path}.{key}" if path else key
            values.extend(_walk_strings(v, sub_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            values.extend(_walk_strings(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        values.append((path, obj))

    return values


def _extract_output_from_audit(data: Dict[str, Any]) -> Tuple[str, str]:
    """
    尽量从审计 JSON 中提取真实设备输出。
    优先级：
    - output/raw_output/command_output/stdout/result_output
    - response/result/data 里的 output 类字段
    - 包含多行设备输出的最长字符串
    """
    if not isinstance(data, dict):
        return "", "invalid_audit_data"

    direct_keys = [
        "output",
        "raw_output",
        "command_output",
        "stdout",
        "result_output",
        "output_text",
        "full_output",
        "text",
    ]

    for key in direct_keys:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val, key

    nested_roots = ["response", "result", "data", "tool_result", "mcp_result"]
    for root in nested_roots:
        nested = data.get(root)
        if isinstance(nested, dict):
            for key in direct_keys:
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    return val, f"{root}.{key}"

    candidates = []
    for path, value in _walk_strings(data):
        v = value.strip()
        if not v:
            continue

        path_l = path.lower()
        score = len(v)

        if any(k in path_l for k in ("output", "stdout", "result", "raw")):
            score += 100000

        # 网络设备输出通常有多行、冒号、百分号日志、show 命令结果等。
        if "\n" in v:
            score += 50000
        if "%" in v or "Syslog logging" in v or "Interface" in v or "BGP" in v:
            score += 20000

        # 排除明显不是输出的字段。
        if path_l.endswith("command") or path_l.endswith("device_name") or path_l.endswith("mgmt_ip"):
            score -= 100000

        candidates.append((score, path, v))

    if not candidates:
        return "", "no_string_output"

    candidates.sort(reverse=True, key=lambda x: x[0])
    return candidates[0][2], candidates[0][1]


def _item_device_name(item: Dict[str, Any]) -> str:
    return str(
        item.get("device_name")
        or item.get("hostname")
        or item.get("netmiko_device_name")
        or ""
    ).strip()


def _item_mgmt_ip(item: Dict[str, Any]) -> str:
    return str(item.get("mgmt_ip") or "").strip()


def _load_output_for_item(item: Dict[str, Any]) -> Dict[str, Any]:
    command = str(item.get("command") or "").strip()
    audit_path = str(item.get("audit_path") or "").strip()

    output = ""
    output_source = ""
    audit_exists = False
    audit_read_ok = False

    if audit_path:
        p = Path(audit_path)
        audit_exists = p.exists()
        if audit_exists:
            data = _read_json(audit_path)
            if isinstance(data, dict):
                audit_read_ok = True
                output, output_source = _extract_output_from_audit(data)

                # 从审计文件补齐部分字段。
                if not command:
                    command = str(data.get("command") or (data.get("plan") or {}).get("command") or "").strip()

    if not output:
        for key in ("output", "raw_output", "output_preview", "command_output"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                output = val
                output_source = f"context.{key}"
                break

    original_len = len(output or "")
    truncated = False

    if output and len(output) > MAX_PER_COMMAND_CHARS:
        output = output[:MAX_PER_COMMAND_CHARS]
        truncated = True

    return {
        "command": command,
        "device_name": _item_device_name(item),
        "mgmt_ip": _item_mgmt_ip(item),
        "device_type": item.get("device_type"),
        "ok": item.get("ok"),
        "execution_status": item.get("execution_status") or item.get("status"),
        "audit_path": audit_path,
        "audit_exists": audit_exists,
        "audit_read_ok": audit_read_ok,
        "output_source": output_source,
        "output": output or "",
        "output_original_len": original_len,
        "output_used_len": len(output or ""),
        "output_truncated": truncated,
    }


def collect_evidence_from_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    evidences = []
    total = 0

    for item in items or []:
        if not isinstance(item, dict):
            continue

        ev = _load_output_for_item(item)

        if not ev["command"]:
            continue

        if not ev["output"]:
            ev["output_missing_reason"] = "audit_path 无输出，且上下文字段无 output/raw_output/output_preview"

        if total + len(ev["output"]) > MAX_TOTAL_EVIDENCE_CHARS:
            remain = max(0, MAX_TOTAL_EVIDENCE_CHARS - total)
            if remain <= 0:
                ev["output"] = ""
                ev["output_used_len"] = 0
                ev["output_truncated"] = True
                ev["output_missing_reason"] = "超过总证据长度上限，未纳入本轮 LLM 输入"
            else:
                ev["output"] = ev["output"][:remain]
                ev["output_used_len"] = len(ev["output"])
                ev["output_truncated"] = True
            total = MAX_TOTAL_EVIDENCE_CHARS
        else:
            total += len(ev["output"])

        evidences.append(ev)

    return {
        "items_count": len(items or []),
        "evidence_count": len(evidences),
        "evidences": evidences,
        "total_output_chars": total,
        "has_raw_output": any(bool(x.get("output")) for x in evidences),
    }


def collect_evidence_from_context(context: Dict[str, Any]) -> Dict[str, Any]:
    items = context.get("last_executions") or []
    return collect_evidence_from_items(items)


def _build_prompt(
    question: str,
    context: Optional[Dict[str, Any]],
    evidence_bundle: Dict[str, Any],
) -> List[Dict[str, str]]:
    context = context or {}
    current_device = context.get("current_device") or {}
    topic = context.get("current_intent") or context.get("current_topic") or ""

    device_text = {
        "device_name": current_device.get("device_name") or current_device.get("hostname") or current_device.get("netmiko_device_name"),
        "mgmt_ip": current_device.get("mgmt_ip"),
        "device_type": current_device.get("device_type"),
        "topic": topic,
    }

    evidence_parts = []
    for idx, ev in enumerate(evidence_bundle.get("evidences") or [], 1):
        evidence_parts.append(
            "\n".join([
                f"### 证据 {idx}",
                f"命令: {ev.get('command')}",
                f"设备: {ev.get('device_name') or device_text.get('device_name')}",
                f"管理IP: {ev.get('mgmt_ip') or device_text.get('mgmt_ip')}",
                f"执行状态: {ev.get('execution_status')}, ok={ev.get('ok')}",
                f"审计文件: {ev.get('audit_path')}",
                f"输出来源: {ev.get('output_source')}",
                f"输出原始长度: {ev.get('output_original_len')}, 本次送入长度: {ev.get('output_used_len')}, 是否截断: {ev.get('output_truncated')}",
                "原始输出如下：",
                "```",
                ev.get("output") or "[无原始输出]",
                "```",
            ])
        )

    evidence_text = "\n\n".join(evidence_parts)

    system_prompt = (
        "你是企业网络运维排障助手。"
        "你必须基于用户提供的网络设备命令原始输出进行分析。"
        "不要编造不存在的证据。"
        "不要只说“需要进一步解析”或“命令已返回输出”。"
        "如果输出中没有明显异常，要明确说明在本次日志/命令窗口内未发现明确异常，并说明依据。"
        "如果输出被截断，要说明结论边界。"
        "输出格式必须包含："
        "1）分析对象；"
        "2）已检查的命令；"
        "3）发现的异常或关键事件；"
        "4）综合判断；"
        "5）建议下一步。"
    )

    user_prompt = (
        "用户问题：\n"
        f"{question}\n\n"
        "当前设备与主题：\n"
        f"{json.dumps(device_text, ensure_ascii=False, indent=2)}\n\n"
        "MCP/Netmiko 原始输出证据：\n"
        f"{evidence_text}\n\n"
        "请直接基于以上原始输出分析。"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _call_llm(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    cfg = _load_llm_config()
    chat_url = cfg.get("chat_url")
    api_key = cfg.get("api_key")
    model = cfg.get("model")

    if not model:
        return {
            "ok": False,
            "error": "missing_model",
            "safe_config": _safe_config(cfg),
        }

    if not chat_url:
        return {
            "ok": False,
            "error": "missing_chat_url",
            "safe_config": _safe_config(cfg),
        }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "stream": False,
        "max_tokens": max(
            1200,
            int(os.getenv("NETAIOPS_V2_EVIDENCE_LLM_MAX_TOKENS", "1200") or 1200),
        ),
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    req = urllib.request.Request(
        chat_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)

        content = ""
        choices = data.get("choices") or []
        if choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message") or {}
                if isinstance(msg, dict):
                    content = msg.get("content") or ""
                if not content:
                    content = first.get("text") or ""

        if not content:
            content = data.get("content") or data.get("text") or ""

        if not str(content).strip():
            return {
                "ok": False,
                "error": "empty_llm_content",
                "raw_preview": raw[:1000],
                "safe_config": _safe_config(cfg),
            }

        return {
            "ok": True,
            "content": str(content).strip(),
            "safe_config": _safe_config(cfg),
            "model": model,
        }

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": False,
            "error": f"http_error:{exc.code}",
            "body_preview": body[:1000],
            "safe_config": _safe_config(cfg),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": repr(exc),
            "safe_config": _safe_config(cfg),
        }


def analyze_evidence_with_llm(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if items is not None:
        evidence_bundle = collect_evidence_from_items(items)
    else:
        evidence_bundle = collect_evidence_from_context(context or {})

    if not evidence_bundle.get("evidence_count"):
        return {
            "ok": False,
            "error": "no_execution_items",
            "answer": "LLM 分析失败：当前会话没有可用于分析的命令执行结果。",
            "evidence_bundle": evidence_bundle,
        }

    if not evidence_bundle.get("has_raw_output"):
        return {
            "ok": False,
            "error": "no_raw_output",
            "answer": "LLM 分析失败：已找到命令执行记录，但未能从 audit_path 或上下文字段读取到 MCP/Netmiko 原始输出。",
            "evidence_bundle": evidence_bundle,
        }

    messages = _build_prompt(question, context, evidence_bundle)
    llm_result = _call_llm(messages)

    if not llm_result.get("ok"):
        return {
            "ok": False,
            "error": llm_result.get("error"),
            "answer": (
                "LLM 分析失败：已读取 MCP/Netmiko 原始输出，但调用本地 LLM 分析失败。\n"
                "错误信息：{}\n"
                "配置摘要：{}"
            ).format(llm_result.get("error"), json.dumps(llm_result.get("safe_config"), ensure_ascii=False)),
            "evidence_bundle": evidence_bundle,
            "llm_result": llm_result,
        }

    answer = (
        "基于本地LLM对MCP/Netmiko原始命令输出的分析：\n\n"
        + llm_result.get("content", "")
    )

    return {
        "ok": True,
        "answer": answer,
        "facts": [
            "已读取当前会话命令执行结果 {} 条。".format(evidence_bundle.get("evidence_count")),
            "已从 audit_path / 上下文字段读取原始输出，总送入字符数约 {}。".format(evidence_bundle.get("total_output_chars")),
            "已调用本地 LLM 模型 {} 完成证据分析。".format(llm_result.get("model")),
        ],
        "conclusion": "已由本地 LLM 基于 MCP/Netmiko 原始输出完成分析。",
        "next_steps": [],
        "evidence_bundle": evidence_bundle,
        "llm_result": {
            "ok": True,
            "model": llm_result.get("model"),
            "safe_config": llm_result.get("safe_config"),
        },
    }


# ===== Batch61-fix LLM env bootstrap patch =====
# 目的：
# - systemd 服务进程本身能读取 EnvironmentFile；
# - 但 sudo 直跑 Python 回归脚本时不会自动继承 systemd env；
# - 因此这里在调用 _load_llm_config() 前主动读取 EnvironmentFile；
# - 只填充 os.environ 中缺失的变量，不覆盖服务进程已有环境变量；
# - 不打印、不暴露真实 API KEY。

def _batch61_strip_quotes(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _batch61_parse_env_file(path):
    result = {}
    try:
        p = Path(path)
        if not p.exists():
            return result
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = _batch61_strip_quotes(value)
            if key:
                result[key] = value
    except Exception:
        return result
    return result


def _batch61_service_env_files():
    files = [
        "/etc/netaiops-asset-agent/asset-agent.env",
        "/etc/sysconfig/netaiops-asset-agent",
        "/etc/default/netaiops-asset-agent",
    ]

    for service_file in (
        "/etc/systemd/system/netaiops-asset-agent.service",
        "/usr/lib/systemd/system/netaiops-asset-agent.service",
    ):
        try:
            p = Path(service_file)
            if not p.exists():
                continue
            for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line.startswith("EnvironmentFile="):
                    continue
                value = line.split("=", 1)[1].strip()
                # systemd 支持 EnvironmentFile=-/path，前导 - 表示文件不存在不报错
                if value.startswith("-"):
                    value = value[1:].strip()
                value = _batch61_strip_quotes(value)
                if value and value not in files:
                    files.append(value)
        except Exception:
            pass

    return files


def _batch61_bootstrap_llm_env_from_files():
    loaded_files = []
    loaded_keys = []

    for env_file in _batch61_service_env_files():
        envs = _batch61_parse_env_file(env_file)
        if not envs:
            continue
        loaded_files.append(env_file)
        for key, value in envs.items():
            if key not in os.environ and value:
                os.environ[key] = value
                if any(x in key.upper() for x in ("LLM", "OPENAI", "QWEN", "ONEAPI", "API_KEY", "TOKEN")):
                    loaded_keys.append(key)

    return {
        "loaded_files": loaded_files,
        "loaded_llm_related_keys": sorted(set(loaded_keys)),
    }


_PRE_BATCH61_FIX_CALL_LLM = _call_llm


def _call_llm(messages):
    bootstrap_info = _batch61_bootstrap_llm_env_from_files()

    cfg = _load_llm_config()
    chat_url = cfg.get("chat_url")
    api_key = cfg.get("api_key")
    model = cfg.get("model")

    if not model:
        return {
            "ok": False,
            "error": "missing_model",
            "safe_config": _safe_config(cfg),
            "bootstrap_info": bootstrap_info,
        }

    if not chat_url:
        return {
            "ok": False,
            "error": "missing_chat_url",
            "safe_config": _safe_config(cfg),
            "bootstrap_info": bootstrap_info,
        }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "stream": False,
        "max_tokens": max(
            1200,
            int(os.getenv("NETAIOPS_V2_EVIDENCE_LLM_MAX_TOKENS", "1200") or 1200),
        ),
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    req = urllib.request.Request(
        chat_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)

        content = ""
        choices = data.get("choices") or []
        if choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message") or {}
                if isinstance(msg, dict):
                    content = msg.get("content") or ""
                if not content:
                    content = first.get("text") or ""

        if not content:
            content = data.get("content") or data.get("text") or ""

        if not str(content).strip():
            return {
                "ok": False,
                "error": "empty_llm_content",
                "raw_preview": raw[:1000],
                "safe_config": _safe_config(cfg),
                "bootstrap_info": bootstrap_info,
            }

        return {
            "ok": True,
            "content": str(content).strip(),
            "safe_config": _safe_config(cfg),
            "bootstrap_info": bootstrap_info,
            "model": model,
        }

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": False,
            "error": "http_error:{}".format(exc.code),
            "body_preview": body[:1000],
            "safe_config": _safe_config(cfg),
            "bootstrap_info": bootstrap_info,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": repr(exc),
            "safe_config": _safe_config(cfg),
            "bootstrap_info": bootstrap_info,
        }
