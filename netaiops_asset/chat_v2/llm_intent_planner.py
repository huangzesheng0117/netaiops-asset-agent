# -*- coding: utf-8 -*-
"""
V2 LLM-first intent planner.

Design:
- LLM understands user natural language and emits a strict JSON plan.
- Local code validates and normalizes the plan.
- Local code still owns guardrails, command templates, YES confirmation, audit and execution.

Safety:
- This module does not execute device CLI.
- This module does not call Netmiko.
- This module only creates a structured intent/action/entity plan.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


PLAN_SCHEMA_VERSION = "v2_llm_intent_plan_1"

DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,})(?![A-Za-z0-9_-])"
)

INTERFACE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:ethernet|eth|e|te|gi|ge|hu|fo)\s*\d+(?:/\d+){1,4}|(?:port-channel|po)\s*\d+)(?![A-Za-z0-9/])",
    re.I,
)


def extract_device_from_text(text: str) -> str:
    raw = str(text or "")
    m = DEVICE_TOKEN_RE.search(raw)
    if not m:
        return ""
    return m.group(1).strip()


def extract_interface_from_text(text: str) -> str:
    raw = str(text or "")
    m = INTERFACE_TOKEN_RE.search(raw)
    if not m:
        return ""
    return normalize_interface_name(m.group(1).strip())


CATEGORY_TO_V2_INTENT = {
    "cpu": "cpu_check",
    "memory": "memory_check",
    "route_table": "route_table",
    "bgp": "bgp_check",
    "bfd": "bfd_check",
    "interface_error": "interface_error_check",
    "interface_status": "interface_check",
    "interface_down": "interface_check",
    "optical_power": "optical_power_check",
    "transceiver": "optical_power_check",
    "log": "log_check",
    "device_health": "device_health_check",
    "cmdb": None,
    "unknown": None,
}

V2_ACTIONS = {
    "suggest_commands",
    "execute_pending",
    "execute_all_pending",
    "followup_analysis",
    "prometheus_query",
    "need_clarification",
}

CMDB_ACTIONS = {
    "cmdb_query",
    "asset_query",
}


def plan_v2_intent(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    q = str(question or "").strip()

    if not q:
        return _normalize_plan({
            "source": "empty",
            "action": "need_clarification",
            "category": "unknown",
            "confidence": 0.0,
            "reason": "empty_question",
        })

    llm_result = _call_llm_planner(q, context=context, user=user)
    if llm_result.get("ok"):
        return _normalize_plan(llm_result)

    # Fallback is intentionally small and generic. It is not the primary
    # understanding mechanism; it only prevents total failure when LLM endpoint
    # is temporarily unavailable.
    fallback = _fallback_plan(q, context=context)
    fallback["llm_error"] = llm_result.get("error")
    fallback["llm_status"] = llm_result.get("status")
    fallback["llm_config"] = llm_result.get("config")
    return _normalize_plan(fallback)


def v2_intent_from_plan(plan: Optional[Dict[str, Any]]) -> Optional[str]:
    if not plan:
        return None
    return plan.get("v2_intent")


def keyword_from_plan(plan: Optional[Dict[str, Any]]) -> Optional[str]:
    if not plan:
        return None
    entities = plan.get("entities") or {}
    for key in ("device_name", "hostname", "mgmt_ip", "management_ip", "device"):
        value = entities.get(key)
        if value:
            return str(value).strip()
    return None


def interface_from_plan(plan: Optional[Dict[str, Any]]) -> Optional[str]:
    if not plan:
        return None
    entities = plan.get("entities") or {}
    value = entities.get("interface") or entities.get("interface_name") or entities.get("port")
    if not value:
        return None
    return normalize_interface_name(str(value))


def is_v2_plan(plan: Optional[Dict[str, Any]]) -> bool:
    if not plan:
        return False

    action = plan.get("action")
    category = plan.get("category")
    v2_intent = plan.get("v2_intent")

    if action in V2_ACTIONS:
        return True

    if v2_intent:
        return True

    if category in CATEGORY_TO_V2_INTENT and CATEGORY_TO_V2_INTENT.get(category):
        return True

    return False


def is_cmdb_only_plan(plan: Optional[Dict[str, Any]]) -> bool:
    if not plan:
        return False
    return plan.get("action") in CMDB_ACTIONS or plan.get("category") == "cmdb"


def normalize_interface_name(name: str) -> str:
    text = str(name or "").strip()
    text = text.replace(" ", "")
    if not text:
        return text

    lower = text.lower()

    replacements = [
        ("ethernet", "Ethernet"),
        ("eth", "Ethernet"),
        ("e", "Ethernet"),
        ("ten-gigabitethernet", "TenGigabitEthernet"),
        ("tengigabitethernet", "TenGigabitEthernet"),
        ("te", "TenGigabitEthernet"),
        ("gigabitethernet", "GigabitEthernet"),
        ("gi", "GigabitEthernet"),
        ("ge", "GigabitEthernet"),
        ("hundredgige", "HundredGigE"),
        ("hundredg", "HundredGigE"),
        ("hu", "HundredGigE"),
        ("fortygige", "FortyGigE"),
        ("fo", "FortyGigE"),
        ("port-channel", "port-channel"),
        ("po", "port-channel"),
    ]

    for prefix, normalized in replacements:
        if lower.startswith(prefix):
            rest = text[len(prefix):]
            if rest.startswith(("ernet", "hernet")) and normalized == "Ethernet":
                # Avoid malformed conversion for already full Ethernet.
                continue
            return normalized + rest

    return text


def planner_debug_payload(question: str, context: Optional[Dict[str, Any]] = None, user: Optional[str] = None) -> Dict[str, Any]:
    return {
        "status": "ok",
        "planner": "v2_llm_first_intent_planner",
        "question": question,
        "plan": plan_v2_intent(question, context=context, user=user),
    }


def _normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(plan or {})

    source = result.get("source") or "unknown"
    result["source"] = source

    result["schema_version"] = result.get("schema_version") or PLAN_SCHEMA_VERSION

    action = str(result.get("action") or "").strip()
    category = str(result.get("category") or "").strip()

    if not action:
        action = "need_clarification"
    if not category:
        category = "unknown"

    action = _normalize_action(action)
    category = _normalize_category(category)

    result["action"] = action
    result["category"] = category

    entities = result.get("entities")
    if not isinstance(entities, dict):
        entities = {}

    # Common alias normalization.
    if entities.get("management_ip") and not entities.get("mgmt_ip"):
        entities["mgmt_ip"] = entities.get("management_ip")
    if entities.get("host_name") and not entities.get("device_name"):
        entities["device_name"] = entities.get("host_name")
    if entities.get("device") and not entities.get("device_name"):
        entities["device_name"] = entities.get("device")

    if entities.get("interface") or entities.get("interface_name") or entities.get("port"):
        entities["interface"] = interface_from_plan({"entities": entities})

    result["entities"] = entities

    v2_intent = result.get("v2_intent")
    if not v2_intent:
        v2_intent = CATEGORY_TO_V2_INTENT.get(category)
    result["v2_intent"] = v2_intent

    try:
        result["confidence"] = float(result.get("confidence", 0.0))
    except Exception:
        result["confidence"] = 0.0

    result["requires_v2"] = is_v2_plan(result)
    result["cmdb_only"] = is_cmdb_only_plan(result)

    return result


def _normalize_action(action: str) -> str:
    text = str(action or "").strip().lower()

    mapping = {
        "suggest": "suggest_commands",
        "suggest_command": "suggest_commands",
        "suggest_commands": "suggest_commands",
        "commands": "suggest_commands",
        "execute": "execute_pending",
        "execute_command": "execute_pending",
        "execute_pending": "execute_pending",
        "execute_all": "execute_all_pending",
        "execute_all_pending": "execute_all_pending",
        "run_pending": "execute_pending",
        "followup": "followup_analysis",
        "followup_analysis": "followup_analysis",
        "analysis": "followup_analysis",
        "prometheus": "prometheus_query",
        "prometheus_query": "prometheus_query",
        "metric_query": "prometheus_query",
        "cmdb": "cmdb_query",
        "asset": "cmdb_query",
        "asset_query": "cmdb_query",
        "cmdb_query": "cmdb_query",
        "clarify": "need_clarification",
        "need_clarification": "need_clarification",
    }

    return mapping.get(text, text or "need_clarification")


def _normalize_category(category: str) -> str:
    text = str(category or "").strip().lower()

    mapping = {
        "cpu_check": "cpu",
        "cpu_high": "cpu",
        "cpu": "cpu",
        "memory": "memory",
        "mem": "memory",
        "route": "route_table",
        "routing": "route_table",
        "route_table": "route_table",
        "bgp": "bgp",
        "bfd": "bfd",
        "interface": "interface_status",
        "interface_status": "interface_status",
        "interface_down": "interface_down",
        "port_down": "interface_down",
        "interface_error": "interface_error",
        "interface_errors": "interface_error",
        "crc": "interface_error",
        "discard": "interface_error",
        "drop": "interface_error",
        "packet_loss": "interface_error",
        "optical": "optical_power",
        "optical_power": "optical_power",
        "light_power": "optical_power",
        "transceiver": "transceiver",
        "log": "log",
        "logs": "log",
        "device_health": "device_health",
        "health": "device_health",
        "cmdb": "cmdb",
        "asset": "cmdb",
        "unknown": "unknown",
    }

    return mapping.get(text, text or "unknown")


def _call_llm_planner(question: str, context: Optional[Dict[str, Any]], user: Optional[str]) -> Dict[str, Any]:
    cfg = _load_llm_config()

    if not cfg.get("base_url") or not cfg.get("model"):
        return {
            "ok": False,
            "source": "llm_config_missing",
            "status": "config_missing",
            "error": "missing llm base_url or model",
            "config": _safe_config(cfg),
        }

    messages = _build_messages(question, context=context, user=user)

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }

    first = _post_chat_completion(cfg, payload)

    if not first.get("ok") and first.get("status_code") in (400, 404, 422):
        payload.pop("response_format", None)
        first = _post_chat_completion(cfg, payload)

    if not first.get("ok"):
        first["config"] = _safe_config(cfg)
        return first

    content = _extract_content(first.get("response"))
    data = _parse_json_from_text(content)

    if not isinstance(data, dict):
        return {
            "ok": False,
            "source": "llm_invalid_json",
            "status": "invalid_json",
            "error": "LLM did not return parseable JSON",
            "raw_content": content[:2000],
            "config": _safe_config(cfg),
        }

    data["ok"] = True
    data["source"] = "llm"
    data["raw_content"] = content[:2000]
    return data


def _build_messages(question: str, context: Optional[Dict[str, Any]], user: Optional[str]) -> list[dict[str, str]]:
    context_summary = ""
    if isinstance(context, dict):
        compact = {
            "current_device": context.get("current_device"),
            "current_topic": context.get("current_topic"),
            "current_intent": context.get("current_intent"),
            "active_focus": context.get("active_focus"),
            "last_command_suggestions_count": len(context.get("last_command_suggestions") or []),
            "last_executions_count": len(context.get("last_executions") or []),
            "rolling_summary": context.get("rolling_summary"),
        }
        context_summary = json.dumps(compact, ensure_ascii=False)[:6000]

    system = """你是 NetAIOps V2 的意图规划器。你的任务不是回答用户，而是把用户问题解析成严格 JSON。

必须只输出 JSON，不要输出 Markdown，不要解释。

JSON schema:
{
  "action": "suggest_commands | execute_pending | execute_all_pending | followup_analysis | prometheus_query | cmdb_query | need_clarification",
  "category": "cpu | memory | route_table | bgp | bfd | interface_error | interface_status | interface_down | optical_power | transceiver | log | device_health | cmdb | unknown",
  "entities": {
    "device_name": "",
    "mgmt_ip": "",
    "interface": "",
    "peer": "",
    "time_range": "",
    "metric": "",
    "symptom": ""
  },
  "confidence": 0.0,
  "reason": ""
}

判断原则：
1. 用户问设备故障、排障、命令、告警、接口、BGP、CPU、内存、光功率、错包、CRC、drop、discard、路由表时，优先 action=suggest_commands 或 followup_analysis，不要简单判为 cmdb_query。
2. 用户只是问资产信息、型号、序列号、机房、管理IP、所属环境时，才 action=cmdb_query，category=cmdb。
3. 用户说“执行上述命令/确认执行/YES”时，action=execute_pending 或 execute_all_pending。
4. 用户说“结合以上/刚才/这些结果/总结/结论/下一步”时，action=followup_analysis。
5. “错包、错误包、error、CRC、discard、drop、丢包、包增长、接口错误增长”属于 category=interface_error。
6. “接口down、端口down、link down、物理口异常”属于 interface_down 或 interface_status。
7. 提取设备名、管理IP、接口名等实体。接口 eth1/46、Eth1/46、Ethernet1/46 都放到 entities.interface。
8. 不能编造设备名；没有就留空，可以结合上下文继承。"""

    examples = """示例：
用户：设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题
输出：{"action":"suggest_commands","category":"interface_error","entities":{"device_name":"WG88-SW-H16-1","mgmt_ip":"","interface":"eth1/46","peer":"","time_range":"","metric":"","symptom":"持续错包增长"},"confidence":0.95,"reason":"接口错包增长排障，需要建议只读命令"}

用户：WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令
输出：{"action":"suggest_commands","category":"cpu","entities":{"device_name":"WG88-SW-H15-1","mgmt_ip":"","interface":"","peer":"","time_range":"","metric":"cpu","symptom":"CPU利用率异常"},"confidence":0.95,"reason":"CPU排障命令建议"}

用户：总结一下目前这个设备CPU排查到的结论
输出：{"action":"followup_analysis","category":"cpu","entities":{"device_name":"","mgmt_ip":"","interface":"","peer":"","time_range":"","metric":"cpu","symptom":"总结当前排查结论"},"confidence":0.9,"reason":"基于上下文做追问分析"}

用户：SH8-G03-DCI-BN-SW01的路由表有多少条
输出：{"action":"suggest_commands","category":"route_table","entities":{"device_name":"SH8-G03-DCI-BN-SW01","mgmt_ip":"","interface":"","peer":"","time_range":"","metric":"route_table","symptom":"查看路由表条目数量"},"confidence":0.9,"reason":"路由表取证命令建议"}"""

    user_msg = "用户={}\n上下文摘要={}\n当前问题={}".format(
        user or "",
        context_summary or "无",
        question,
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": examples},
        {"role": "user", "content": user_msg},
    ]


def _load_llm_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}

    for key in ("NETAIOPS_LLM_PLANNER_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL", "LOCAL_LLM_BASE_URL", "CHATBOT_LLM_BASE_URL", "QWEN_BASE_URL", "ONEAPI_BASE_URL"):
        if os.getenv(key):
            cfg["base_url"] = os.getenv(key)
            break

    for key in ("NETAIOPS_LLM_PLANNER_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "LOCAL_LLM_API_KEY", "CHATBOT_LLM_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY", "ONEAPI_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "LLM_TOKEN"):
        if os.getenv(key):
            cfg["api_key"] = os.getenv(key)
            break

    for key in ("NETAIOPS_LLM_PLANNER_MODEL", "OPENAI_MODEL", "LLM_MODEL", "LOCAL_LLM_MODEL", "CHATBOT_LLM_MODEL", "QWEN_MODEL"):
        if os.getenv(key):
            cfg["model"] = os.getenv(key)
            break

    config_path = os.getenv("NETAIOPS_CONFIG", "/etc/netaiops-asset-agent/config.yaml")
    path = Path(config_path)

    if path.exists():
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            flat = _flatten(data)
            if not cfg.get("base_url"):
                cfg["base_url"] = _pick_exact_key(flat, (
                    "llm.base_url", "llm.api_base", "llm.endpoint", "llm.url",
                    "openai.base_url", "openai.api_base", "local_llm.base_url",
                    "chatbot.llm.base_url", "chatbot_llm.base_url",
                )) or _pick_llm_url(flat)
            if not cfg.get("api_key"):
                cfg["api_key"] = _pick_exact_key(flat, (
                    "llm.api_key", "llm.apikey", "llm.token",
                    "openai.api_key", "openai.token",
                    "local_llm.api_key", "local_llm.token",
                    "chatbot.llm.api_key", "chatbot_llm.api_key",
                    "qwen.api_key", "qwen.token",
                    "oneapi.api_key", "oneapi.token",
                )) or _pick_key(flat, ("api_key", "apikey", "token", "secret"))
            if not cfg.get("model"):
                cfg["model"] = _pick_exact_key(flat, (
                    "llm.model", "llm.model_name", "openai.model",
                    "local_llm.model", "chatbot.llm.model",
                    "chatbot_llm.model", "qwen.model",
                )) or _pick_key(flat, ("model", "model_name", "llm_model"))
        except Exception as exc:
            cfg["config_error"] = repr(exc)

    if cfg.get("base_url"):
        cfg["chat_url"] = _to_chat_completions_url(str(cfg["base_url"]).strip())

    if not cfg.get("model"):
        cfg["model"] = "qwen3-max"

    cfg["timeout"] = int(os.getenv("NETAIOPS_LLM_PLANNER_TIMEOUT", "45"))
    return cfg


def _flatten(data: Any, prefix: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = "{}.{}".format(prefix, k) if prefix else str(k)
            result.update(_flatten(v, key))
    else:
        result[prefix] = data
    return result



def _pick_exact_key(flat: Dict[str, Any], names: Tuple[str, ...]) -> Optional[str]:
    lowered = {str(k).lower(): v for k, v in flat.items()}
    for name in names:
        value = lowered.get(str(name).lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _pick_llm_url(flat: Dict[str, Any]) -> Optional[str]:
    candidates = []
    for key, value in flat.items():
        if not isinstance(value, str):
            continue
        if not value.startswith(("http://", "https://")):
            continue
        k = key.lower()
        if any(x in k for x in ("llm", "openai", "qwen", "chat")) and any(x in k for x in ("url", "base", "endpoint", "api")):
            candidates.append(value)

    if candidates:
        return candidates[0]

    return None


def _pick_key(flat: Dict[str, Any], names: Tuple[str, ...]) -> Optional[str]:
    for key, value in flat.items():
        if value is None:
            continue
        k = key.lower()
        if not any(x in k for x in ("llm", "openai", "qwen", "chat")):
            continue
        if any(k.endswith(name) or ("." + name) in k for name in names):
            return str(value)
    return None


def _to_chat_completions_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    if url.endswith("/v1/"):
        return url.rstrip("/") + "/chat/completions"
    return url + "/v1/chat/completions"


def _post_chat_completion(cfg: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    chat_url = cfg.get("chat_url") or _to_chat_completions_url(str(cfg.get("base_url") or ""))
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    api_key = cfg.get("api_key")
    if api_key:
        headers["Authorization"] = "Bearer " + str(api_key)

    req = urllib.request.Request(chat_url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=int(cfg.get("timeout") or 45)) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status_code": resp.status,
                "response": json.loads(body),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": False,
            "source": "llm_http_error",
            "status": "http_error",
            "status_code": exc.code,
            "error": body[:2000],
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": "llm_exception",
            "status": "exception",
            "error": repr(exc),
        }


def _extract_content(response: Any) -> str:
    if not isinstance(response, dict):
        return str(response or "")

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(msg, dict):
            return str(msg.get("content") or "")
        if choices[0].get("text"):
            return str(choices[0].get("text"))

    return json.dumps(response, ensure_ascii=False)


def _parse_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            return data if isinstance(data, dict) else None
        except Exception:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    return None


def _fallback_plan(question: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text = str(question or "")
    entities: Dict[str, Any] = {
        "device_name": "",
        "mgmt_ip": "",
        "interface": "",
        "peer": "",
        "time_range": "",
        "metric": "",
        "symptom": "",
    }

    ip = re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    if ip:
        entities["mgmt_ip"] = ip.group(0)

    dev = extract_device_from_text(text)
    if dev:
        entities["device_name"] = dev

    intf = extract_interface_from_text(text)
    if intf:
        entities["interface"] = intf

    lower = text.lower()

    if any(x in text for x in ("确认执行全部", "执行全部")) and "YES" in text:
        action = "execute_all_pending"
        category = "unknown"
    elif any(x in text for x in ("确认执行", "执行第")) and "YES" in text:
        action = "execute_pending"
        category = "unknown"
    elif any(x in text for x in ("总结", "结论", "以上", "刚才", "这些结果", "下一步")):
        action = "followup_analysis"
        category = "cpu" if "cpu" in lower or "CPU" in text else "unknown"
    elif any(x in text for x in ("端口down", "接口down", "端口 Down", "接口 Down", "物理口down", "物理口 Down")) or "link down" in lower or "interface down" in lower or "port down" in lower:
        action = "suggest_commands"
        category = "interface_down"
        entities["symptom"] = "接口/端口 down"
    elif any(x in text for x in ("错包", "错误包", "丢包", "包增长", "CRC", "crc", "discard", "drop", "error")):
        action = "suggest_commands"
        category = "interface_error"
        entities["symptom"] = "接口错误/错包"
    elif "cpu" in lower or "CPU" in text:
        action = "suggest_commands"
        category = "cpu"
    elif "路由" in text or "route" in lower:
        action = "suggest_commands"
        category = "route_table"
    elif "bgp" in lower or "邻居" in text:
        action = "suggest_commands"
        category = "bgp"
    elif "光" in text or "transceiver" in lower:
        action = "suggest_commands"
        category = "optical_power"
    else:
        action = "cmdb_query"
        category = "cmdb"

    return {
        "ok": True,
        "source": "fallback_minimal",
        "action": action,
        "category": category,
        "entities": entities,
        "confidence": 0.3,
        "reason": "LLM planner unavailable; minimal fallback used",
    }


def _safe_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "has_base_url": bool(cfg.get("base_url")),
        "has_chat_url": bool(cfg.get("chat_url")),
        "has_api_key": bool(cfg.get("api_key")),
        "api_key_preview": (str(cfg.get("api_key"))[:4] + "***" + str(cfg.get("api_key"))[-4:]) if cfg.get("api_key") else "",
        "model": cfg.get("model"),
        "config_error": cfg.get("config_error"),
    }

# ===== LLM CONFIG ENV EXPAND FIX BEGIN =====

# ===== LLM CONFIG ENV EXPAND FIX BEGIN =====
# This block intentionally overrides _load_llm_config and _safe_config.
# Purpose:
# 1. Prefer NETAIOPS_LLM_API_KEY from systemd env.
# 2. Expand config values like api_key: NETAIOPS_LLM_API_KEY or ${NETAIOPS_LLM_API_KEY}.
# 3. Keep diagnostics masked.

def _llm_fix_strip_quotes(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def _llm_fix_env_value(keys):
    for key in keys:
        value = os.environ.get(key)
        if value is not None and str(value).strip():
            return _llm_fix_strip_quotes(value), "env:" + key
    return None, None


def _llm_fix_expand_env_reference(value):
    raw = _llm_fix_strip_quotes(value)
    if not raw:
        return raw, "empty"

    name = None

    if raw.startswith("${") and raw.endswith("}") and len(raw) > 3:
        name = raw[2:-1].strip()
    elif raw.startswith("$") and len(raw) > 1:
        name = raw[1:].strip()
    elif re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", raw or ""):
        name = raw

    if name and os.environ.get(name) is not None:
        return _llm_fix_strip_quotes(os.environ.get(name)), "envref:" + name

    return raw, "literal"


def _llm_fix_flatten(data, prefix=""):
    result = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = "{}.{}".format(prefix, k) if prefix else str(k)
            result.update(_llm_fix_flatten(v, key))
    else:
        result[prefix] = data
    return result


def _llm_fix_pick_exact(flat, names):
    lowered = {str(k).lower(): v for k, v in flat.items()}
    for name in names:
        value = lowered.get(str(name).lower())
        if value is not None and str(value).strip():
            return value
    return None


def _llm_fix_pick_fuzzy_url(flat):
    for key, value in flat.items():
        if not isinstance(value, str):
            continue
        text = _llm_fix_strip_quotes(value)
        if not text.startswith(("http://", "https://")):
            continue
        k = str(key).lower()
        if any(x in k for x in ("llm", "openai", "qwen", "chat")) and any(x in k for x in ("url", "base", "endpoint", "api")):
            return text
    return None


def _llm_fix_pick_fuzzy_key(flat):
    for key, value in flat.items():
        if value is None:
            continue
        k = str(key).lower()
        if not any(x in k for x in ("llm", "openai", "qwen", "chat")):
            continue
        if any(x in k for x in ("api_key", "apikey", "token", "secret")):
            return value
    return None


def _load_llm_config():
    cfg = {}
    sources = {}

    base_url, src = _llm_fix_env_value((
        "NETAIOPS_LLM_BASE_URL",
        "NETAIOPS_LLM_API_BASE",
        "NETAIOPS_LLM_PLANNER_BASE_URL",
        "OPENAI_BASE_URL",
        "LLM_BASE_URL",
        "LOCAL_LLM_BASE_URL",
        "CHATBOT_LLM_BASE_URL",
        "QWEN_BASE_URL",
        "ONEAPI_BASE_URL",
    ))
    if base_url:
        cfg["base_url"] = base_url
        sources["base_url"] = src

    api_key, src = _llm_fix_env_value((
        "NETAIOPS_LLM_API_KEY",
        "NETAIOPS_LLM_TOKEN",
        "NETAIOPS_LLM_PLANNER_API_KEY",
        "OPENAI_API_KEY",
        "LLM_API_KEY",
        "LOCAL_LLM_API_KEY",
        "CHATBOT_LLM_API_KEY",
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "ONEAPI_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "LLM_TOKEN",
    ))
    if api_key:
        cfg["api_key"] = api_key
        sources["api_key"] = src

    model, src = _llm_fix_env_value((
        "NETAIOPS_LLM_MODEL",
        "NETAIOPS_LLM_PLANNER_MODEL",
        "OPENAI_MODEL",
        "LLM_MODEL",
        "LOCAL_LLM_MODEL",
        "CHATBOT_LLM_MODEL",
        "QWEN_MODEL",
    ))
    if model:
        cfg["model"] = model
        sources["model"] = src

    config_path = os.getenv("NETAIOPS_CONFIG", "/etc/netaiops-asset-agent/config.yaml")
    path = Path(config_path)

    if path.exists():
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            flat = _llm_fix_flatten(data)

            if not cfg.get("base_url"):
                value = _llm_fix_pick_exact(flat, (
                    "llm.base_url", "llm.api_base", "llm.endpoint", "llm.url",
                    "openai.base_url", "openai.api_base",
                    "local_llm.base_url",
                    "chatbot.llm.base_url", "chatbot_llm.base_url",
                )) or _llm_fix_pick_fuzzy_url(flat)
                if value:
                    expanded, src2 = _llm_fix_expand_env_reference(value)
                    cfg["base_url"] = expanded
                    sources["base_url"] = "config:" + src2

            if not cfg.get("api_key"):
                value = _llm_fix_pick_exact(flat, (
                    "llm.api_key", "llm.apikey", "llm.token",
                    "openai.api_key", "openai.token",
                    "local_llm.api_key", "local_llm.token",
                    "chatbot.llm.api_key", "chatbot_llm.api_key",
                    "qwen.api_key", "qwen.token",
                    "oneapi.api_key", "oneapi.token",
                )) or _llm_fix_pick_fuzzy_key(flat)
                if value:
                    expanded, src2 = _llm_fix_expand_env_reference(value)
                    cfg["api_key"] = expanded
                    sources["api_key"] = "config:" + src2

            if not cfg.get("model"):
                value = _llm_fix_pick_exact(flat, (
                    "llm.model", "llm.model_name",
                    "openai.model",
                    "local_llm.model",
                    "chatbot.llm.model", "chatbot_llm.model",
                    "qwen.model",
                ))
                if value:
                    expanded, src2 = _llm_fix_expand_env_reference(value)
                    cfg["model"] = expanded
                    sources["model"] = "config:" + src2

        except Exception as exc:
            cfg["config_error"] = repr(exc)

    if cfg.get("base_url"):
        cfg["chat_url"] = _to_chat_completions_url(str(cfg["base_url"]).strip())

    if not cfg.get("model"):
        cfg["model"] = "qwen3-max"
        sources["model"] = "default:qwen3-max"

    cfg["timeout"] = int(os.getenv("NETAIOPS_LLM_PLANNER_TIMEOUT", "45"))
    cfg["_debug_sources"] = sources
    return cfg


def _safe_config(cfg):
    key = str(cfg.get("api_key") or "")
    return {
        "has_base_url": bool(cfg.get("base_url")),
        "has_chat_url": bool(cfg.get("chat_url")),
        "has_api_key": bool(key),
        "api_key_preview": (key[:4] + "***" + key[-4:]) if key else "",
        "api_key_len": len(key),
        "debug_sources": cfg.get("_debug_sources"),
        "model": cfg.get("model"),
        "config_error": cfg.get("config_error"),
    }
# ===== LLM CONFIG ENV EXPAND FIX END =====
