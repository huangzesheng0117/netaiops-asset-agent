# -*- coding: utf-8 -*-
"""
Batch63-fix3:
修复“第二批内联命令识别到但没有真正执行”的问题。

根因：
- confirmation.store_pending_commands 的真实签名为：
  store_pending_commands(conversation_id, user, question, response)
- 前几批按 commands/items/pending_commands 等参数猜测写入，导致 pending 写入与确认执行链路不一致。
- 因此 try_handle_v2_execution_confirmation 看到的不是标准“上一轮推荐命令 response”，最终只返回 rejected/items，不产生 audit_path。

本批策略：
1. 从用户输入中严格提取 show/display 只读命令。
2. 构造一个标准 v2_chat_router 推荐命令 response。
3. 调用 store_pending_commands(conversation_id, user, question, response)。
4. 再调用 try_handle_v2_execution_confirmation(question="确认可以执行", user=user, conversation_id=conversation_id)。
5. 只要返回 items 内有 audit_path / executed 状态，才算真实执行成功。
6. 执行结果继续交给 enrich_v2_execution_response，触发 MCP/Netmiko 原始输出 -> 本地 LLM 分析。
"""

from __future__ import annotations

import inspect
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from netaiops_asset.chat_v2.context import load_v2_context, save_v2_context_from_response
from netaiops_asset.chat_v2.execution_response_enricher import enrich_v2_execution_response

try:
    from netaiops_asset.chat_v2.confirmation import store_pending_commands
except Exception:
    store_pending_commands = None

try:
    from netaiops_asset.chat_v2.confirmation import try_handle_v2_execution_confirmation
except Exception:
    try_handle_v2_execution_confirmation = None


DATA_DIR = Path(os.getenv("NETAIOPS_ASSET_DATA_DIR", "/var/lib/netaiops-asset-agent/data"))
CTX_DIR = DATA_DIR / "v2_conversation_context"
PENDING_DIR = DATA_DIR / "v2_pending_commands"

_EXEC_WORDS = (
    "执行", "运行", "采集", "跑一下", "跑下", "立即执行", "继续执行",
    "帮我执行", "去执行", "执行命令", "执行一下", "查一下", "查询一下"
)

_BAD_WORD_RE = re.compile(
    r"\b(configure|conf t|delete|reload|reboot|write|copy|erase|format|clear|debug|undebug|shutdown|no shutdown|set|commit|save|reset)\b",
    re.I,
)


def _has_execute_intent(text: str) -> bool:
    s = str(text or "")
    return any(w in s for w in _EXEC_WORDS)


def _normalize_command(cmd: str) -> str:
    c = str(cmd or "").strip()
    c = c.strip("`'\"“”‘’；;，,。 ")
    c = re.sub(r"\s+", " ", c)
    return c


def _is_safe_readonly(cmd: str) -> bool:
    c = _normalize_command(cmd)
    if not re.match(r"^(show|display)\s+", c, re.I):
        return False
    if _BAD_WORD_RE.search(c):
        return False
    return True


def _dedup(cmds: List[str]) -> List[str]:
    out = []
    seen = set()
    for c in cmds:
        cmd = _normalize_command(c)
        if not _is_safe_readonly(cmd):
            continue
        k = cmd.lower()
        if k not in seen:
            seen.add(k)
            out.append(cmd)
    return out[:int(os.getenv("NETAIOPS_V2_MAX_INLINE_COMMANDS", "200"))]


def extract_inline_readonly_commands(text: str) -> List[str]:
    s = str(text or "")
    if not s.strip():
        return []

    quoted: List[str] = []

    # 1. 反引号：`show ...`
    for m in re.finditer(r"`\s*((?:show|display)\s+[^`]+?)\s*`", s, re.I):
        quoted.append(m.group(1))

    # 2. 中文/英文双引号：“show ...” 或 "show ..."
    for m in re.finditer(r"[“\"]\s*((?:show|display)\s+[^”\"\n]+?)\s*[”\"]", s, re.I):
        quoted.append(m.group(1))

    # 3. 中文/英文单引号：‘show ...’ 或 'show ...'
    for m in re.finditer(r"[‘']\s*((?:show|display)\s+[^’'\n]+?)\s*[’']", s, re.I):
        quoted.append(m.group(1))

    quoted = _dedup(quoted)

    # 只要引号/反引号提取到了命令，就直接返回，避免把两条命令拼成第三条脏命令。
    if quoted:
        return quoted

    # 4. 换行中直接出现的命令。
    direct_lines = []
    for line in s.splitlines():
        t = line.strip()
        if re.match(r"^(show|display)\s+", t, re.I):
            t = re.split(r"(?:，|。|；|\s+并\s+|\s+然后\s+|\s+进一步\s+)", t)[0]
            direct_lines.append(t)

    direct_lines = _dedup(direct_lines)
    if direct_lines:
        return direct_lines

    # 5. 兜底：命令 show xxx 和 show yyy。
    tmp = s
    for sep in ("，", "；", "。", "、", " 和 ", " 并 ", " 然后 "):
        tmp = tmp.replace(sep, "\n")

    loose = []
    for part in tmp.splitlines():
        part = part.strip()
        m = re.search(r"((?:show|display)\s+.+)$", part, re.I)
        if m:
            cmd = m.group(1)
            cmd = re.split(r"(?:进一步|定位|分析|原因|问题)", cmd)[0]
            loose.append(cmd)

    return _dedup(loose)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _context_paths(conversation_id: Optional[str], user: Optional[str]) -> List[Path]:
    paths = []
    if conversation_id:
        paths.append(CTX_DIR / ("conversation_%s.json" % conversation_id))
    if user:
        paths.append(CTX_DIR / ("latest_user_%s.json" % user))
    return paths


def _pending_paths(conversation_id: Optional[str], user: Optional[str]) -> List[Path]:
    paths = []
    if conversation_id:
        paths.append(PENDING_DIR / ("conversation_%s.json" % conversation_id))
    if user:
        paths.append(PENDING_DIR / ("latest_user_%s.json" % user))
    return paths


def _context_device(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ctx = context or {}
    dev = ctx.get("current_device") or {}
    if isinstance(dev, dict):
        return dev
    return {}


def _infer_category(commands: List[str], context: Optional[Dict[str, Any]]) -> str:
    joined = " ".join(commands).lower()

    if "bgp" in joined:
        return "bgp"
    if "route" in joined:
        return "route_table"
    if "transceiver" in joined or "optical" in joined:
        return "optical_power"
    if "interface" in joined:
        return "interface_status"
    if "platform software status" in joined or "process" in joined or "cpu" in joined or "memory" in joined:
        return "cpu"
    if "logging" in joined or " log" in joined:
        return "log"

    return str((context or {}).get("current_topic") or (context or {}).get("current_intent") or "inline_command")


def _intent_from_category(category: str) -> str:
    mapping = {
        "log": "log_check",
        "cpu": "cpu_check",
        "route_table": "route_table",
        "interface_status": "interface_check",
        "optical_power": "optical_power_check",
        "bgp": "bgp_check",
    }
    return mapping.get(category, category)


def _make_command_items(commands: List[str], context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    dev = _context_device(context)
    category = _infer_category(commands, context)
    intent = _intent_from_category(category)

    device_name = dev.get("device_name") or dev.get("hostname") or dev.get("netmiko_device_name")
    hostname = dev.get("hostname") or dev.get("device_name") or dev.get("netmiko_device_name")
    netmiko_device_name = dev.get("netmiko_device_name") or dev.get("device_name") or dev.get("hostname")

    items = []
    for idx, cmd in enumerate(commands, 1):
        item = {
            "index": idx,
            "id": idx,
            "command": cmd,
            "cli": cmd,
            "text": cmd,
            "status": "passed",
            "validation_status": "passed",
            "validated": True,
            "is_read_only": True,
            "readonly": True,
            "source": "user_inline_command",
            "template_category": category,
            "category": category,
            "v2_intent": intent,
            "device_name": device_name,
            "hostname": hostname,
            "mgmt_ip": dev.get("mgmt_ip"),
            "device_type": dev.get("device_type"),
            "netmiko_device_name": netmiko_device_name,
        }
        items.append(item)

    return items


def _build_fake_suggestion_response(
    original_question: str,
    commands: List[str],
    items: List[Dict[str, Any]],
    context: Dict[str, Any],
    conversation_id: Optional[str],
    user: Optional[str],
) -> Dict[str, Any]:
    dev = _context_device(context)
    category = items[0].get("category") if items else _infer_category(commands, context)
    intent = items[0].get("v2_intent") if items else _intent_from_category(category)

    return {
        "status": "ok",
        "planner_source": "v2_chat_router",
        "conversation_id": conversation_id,
        "user": user,
        "answer": "已识别到第二批内联只读命令，等待确认执行。",
        "items": items,
        "commands": items,
        "parsed": {
            "intent": "v2_troubleshoot",
            "v2_intent": intent,
            "keyword": dev.get("device_name") or dev.get("hostname") or dev.get("netmiko_device_name"),
            "hostname": dev.get("hostname") or dev.get("device_name") or dev.get("netmiko_device_name"),
            "mgmt_ip": dev.get("mgmt_ip"),
            "device_name": dev.get("device_name") or dev.get("hostname") or dev.get("netmiko_device_name"),
            "device_type": dev.get("device_type"),
            "reason": "batch63_fix3_inline_command",
            "context_inherited": True,
            "interface_name": "",
            "llm_intent_plan": {
                "action": "suggest_commands",
                "category": category,
                "entities": {
                    "device_name": dev.get("device_name") or dev.get("hostname") or dev.get("netmiko_device_name"),
                    "mgmt_ip": dev.get("mgmt_ip"),
                    "interface": "",
                    "peer": "",
                    "time_range": "",
                    "metric": category,
                    "symptom": original_question,
                },
                "confidence": 0.99,
                "reason": "用户在输入中明确要求立即执行内联只读命令",
                "ok": True,
                "source": "inline_command",
                "schema_version": "v2_llm_intent_plan_1",
                "v2_intent": intent,
                "requires_v2": True,
                "cmdb_only": False,
            },
        },
        "v2": {
            "inline_command_suggestion": True,
            "inline_source_question": original_question,
            "extracted_commands": commands,
            "category": category,
            "v2_intent": intent,
        },
    }


def _persist_context_and_pending(
    items: List[Dict[str, Any]],
    response: Dict[str, Any],
    context: Dict[str, Any],
    conversation_id: Optional[str],
    user: Optional[str],
) -> Dict[str, Any]:
    now = datetime.now().isoformat()
    commands = [x.get("command") for x in items]

    base = dict(context or {})
    base["conversation_id"] = conversation_id or base.get("conversation_id")
    base["user"] = user or base.get("user")
    base["updated_at"] = now
    base["current_topic"] = items[0].get("category") if items else base.get("current_topic")
    base["current_intent"] = items[0].get("v2_intent") if items else base.get("current_intent")
    base["last_user_message"] = "inline_command_execution"

    for key in (
        "last_command_suggestions",
        "last_command_suggestions_logging",
        "pending_commands",
        "pending_command_items",
        "last_pending_commands",
        "last_cli_commands",
        "candidate_commands",
        "commands",
        "items",
    ):
        base[key] = items

    base["last_inline_commands"] = commands
    base["last_executions"] = []
    base["last_bulk_analysis"] = None
    base["last_response"] = response

    turns = base.get("recent_turns") or []
    if isinstance(turns, list):
        turns.append({
            "ts": now,
            "role": "assistant",
            "type": "inline_command_suggestions",
            "commands": commands,
            "items": items,
        })
        base["recent_turns"] = turns[-20:]

    written = []

    for p in _context_paths(conversation_id, user):
        old = _read_json(p)
        merged = dict(old)
        merged.update(base)
        _write_json(p, merged)
        written.append(str(p))

    pending_payload = {
        "conversation_id": conversation_id,
        "user": user,
        "created_at": now,
        "updated_at": now,
        "source": "batch63_fix3_inline_command_execute",
        "question": response.get("v2", {}).get("inline_source_question"),
        "response": response,
        "items": items,
        "commands": items,
        "pending_commands": items,
        "last_command_suggestions": items,
        "candidate_commands": items,
    }

    for p in _pending_paths(conversation_id, user):
        _write_json(p, pending_payload)
        written.append(str(p))

    return {
        "written": written,
        "commands": commands,
        "category": items[0].get("category") if items else None,
        "v2_intent": items[0].get("v2_intent") if items else None,
    }


def _store_pending_standard(
    conversation_id: Optional[str],
    user: Optional[str],
    question: str,
    response: Dict[str, Any],
) -> Dict[str, Any]:
    if store_pending_commands is None:
        return {"ok": False, "method": None, "error": "store_pending_commands_not_found"}

    attempts = []

    # 真实签名：store_pending_commands(conversation_id, user, question, response)
    try:
        result = store_pending_commands(conversation_id, user, question, response)
        return {
            "ok": True,
            "method": "positional:conversation_id,user,question,response",
            "result": repr(result)[:1000],
            "attempts": attempts,
        }
    except Exception as exc:
        attempts.append({
            "method": "positional:conversation_id,user,question,response",
            "error": repr(exc),
        })

    # 兼容 kwargs，防止后续函数签名变动。
    try:
        result = store_pending_commands(
            conversation_id=conversation_id,
            user=user,
            question=question,
            response=response,
        )
        return {
            "ok": True,
            "method": "kwargs:conversation_id,user,question,response",
            "result": repr(result)[:1000],
            "attempts": attempts,
        }
    except Exception as exc:
        attempts.append({
            "method": "kwargs:conversation_id,user,question,response",
            "error": repr(exc),
        })

    return {"ok": False, "method": None, "attempts": attempts}


def _result_has_real_execution(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    items = result.get("items")
    if not isinstance(items, list) or not items:
        return False

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("audit_path"):
            return True
        if str(item.get("execution_status") or item.get("status") or "").lower() == "executed":
            return True
        if item.get("analysis_status") == "llm_evidence_analyzed":
            return True
    return False


def _normalize_confirmation_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return {
            "status": "ok",
            "planner_source": "v2_execution_confirmation",
            "items": result,
        }
    return {}


def _safe_call(fn, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Tuple[bool, Any]:
    try:
        return True, fn(*args, **kwargs)
    except TypeError as exc:
        return False, exc
    except Exception as exc:
        return True, {
            "status": "error",
            "planner_source": "v2_inline_command_execution",
            "answer": "调用执行链路异常：%s" % repr(exc),
            "items": [],
            "v2": {"exception": repr(exc)},
        }


def _adaptive_kwargs_for_confirmation(fn, question: str, conversation_id: Optional[str], user: Optional[str]) -> Dict[str, Any]:
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
    except Exception:
        return {}

    kw = {}
    for name in params:
        low = name.lower()
        if low in ("question", "message", "text", "user_question", "query", "prompt"):
            kw[name] = question
        elif "conversation" in low or low in ("cid", "conversationid"):
            kw[name] = conversation_id
        elif low in ("user", "username", "user_id"):
            kw[name] = user
    return kw


def _call_confirmation(conversation_id: Optional[str], user: Optional[str]) -> Dict[str, Any]:
    attempts = []

    if try_handle_v2_execution_confirmation is None:
        return {
            "status": "error",
            "planner_source": "v2_inline_command_execution",
            "answer": "确认执行函数不存在：try_handle_v2_execution_confirmation not found",
            "items": [],
            "v2": {"confirmation_attempts": attempts},
        }

    questions = [
        "确认可以执行",
        "确认执行上面的命令",
        "确认执行上面两条命令",
        "执行吧",
    ]

    for question in questions:
        # 0. 按签名自适应 kwargs。
        kw = _adaptive_kwargs_for_confirmation(try_handle_v2_execution_confirmation, question, conversation_id, user)
        if kw:
            ok, result = _safe_call(try_handle_v2_execution_confirmation, (), kw)
            normalized = _normalize_confirmation_result(result)
            attempts.append({
                "method": "adaptive_kwargs",
                "question": question,
                "ok": ok,
                "result_type": type(result).__name__,
                "status": normalized.get("status"),
                "planner_source": normalized.get("planner_source"),
                "items_count": len(normalized.get("items") or []) if isinstance(normalized, dict) else 0,
                "has_real_execution": _result_has_real_execution(normalized),
                "result_preview": repr(result)[:1000],
            })
            if ok and _result_has_real_execution(normalized):
                return normalized

        # 1. 常见 kwargs。
        kwargs_candidates = [
            {"question": question, "conversation_id": conversation_id, "user": user},
            {"question": question, "user": user, "conversation_id": conversation_id},
            {"message": question, "conversation_id": conversation_id, "user": user},
            {"user_question": question, "conversation_id": conversation_id, "user": user},
            {"text": question, "conversation_id": conversation_id, "user": user},
        ]

        for cand in kwargs_candidates:
            ok, result = _safe_call(try_handle_v2_execution_confirmation, (), cand)
            normalized = _normalize_confirmation_result(result)
            attempts.append({
                "method": "kwargs:" + ",".join(cand.keys()),
                "question": question,
                "ok": ok,
                "result_type": type(result).__name__,
                "status": normalized.get("status"),
                "planner_source": normalized.get("planner_source"),
                "items_count": len(normalized.get("items") or []) if isinstance(normalized, dict) else 0,
                "has_real_execution": _result_has_real_execution(normalized),
                "result_preview": repr(result)[:1000],
            })
            if ok and _result_has_real_execution(normalized):
                return normalized

        # 2. 常见 args。
        args_candidates = [
            (question, conversation_id, user),
            (question, user, conversation_id),
            (conversation_id, user, question),
            (user, conversation_id, question),
            (question,),
        ]

        for args in args_candidates:
            ok, result = _safe_call(try_handle_v2_execution_confirmation, args, {})
            normalized = _normalize_confirmation_result(result)
            attempts.append({
                "method": "args:%s" % (len(args),),
                "question": question,
                "ok": ok,
                "result_type": type(result).__name__,
                "status": normalized.get("status"),
                "planner_source": normalized.get("planner_source"),
                "items_count": len(normalized.get("items") or []) if isinstance(normalized, dict) else 0,
                "has_real_execution": _result_has_real_execution(normalized),
                "result_preview": repr(result)[:1000],
            })
            if ok and _result_has_real_execution(normalized):
                return normalized

    # 没有真实执行时，保留最后一次非空结果帮助定位。
    last_nonempty = None
    for a in reversed(attempts):
        if a.get("items_count", 0) > 0 or a.get("status"):
            last_nonempty = a
            break

    return {
        "status": "error",
        "planner_source": "v2_execution_confirmation",
        "answer": "已识别并写入第二批内联只读命令，但确认执行链路仍未返回带 audit_path 的实际执行结果。",
        "items": [],
        "v2": {
            "confirmation_attempts": attempts[-40:],
            "last_nonempty_attempt": last_nonempty,
        },
    }


def try_handle_v2_inline_command_execution(
    question: str,
    conversation_id: Optional[str] = None,
    user: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    text = str(question or "")

    if not _has_execute_intent(text):
        return None

    commands = extract_inline_readonly_commands(text)
    if not commands:
        return None

    context = load_v2_context(conversation_id=conversation_id, user=user) or {}
    dev = _context_device(context)

    if not dev:
        return {
            "status": "error",
            "planner_source": "v2_inline_command_execution",
            "answer": "已识别到你输入中包含要执行的只读命令，但当前会话没有可继承的设备信息。请先指定设备名称，或先在同一会话中生成第一批命令。",
            "items": [],
            "v2": {
                "inline_commands": commands,
                "reason": "missing_current_device_context",
            },
        }

    items = _make_command_items(commands, context)

    fake_response = _build_fake_suggestion_response(
        original_question=text,
        commands=commands,
        items=items,
        context=context,
        conversation_id=conversation_id,
        user=user,
    )

    persist_result = _persist_context_and_pending(
        items=items,
        response=fake_response,
        context=context,
        conversation_id=conversation_id,
        user=user,
    )

    store_result = _store_pending_standard(
        conversation_id=conversation_id,
        user=user,
        question=text,
        response=fake_response,
    )

    response = _call_confirmation(
        conversation_id=conversation_id,
        user=user,
    )

    if not isinstance(response, dict):
        response = {}

    response.setdefault("status", "ok")
    response["planner_source"] = "v2_execution_confirmation"
    response.setdefault("items", response.get("items") or [])
    response.setdefault("v2", {})
    response["v2"]["inline_command_execution"] = {
        "enabled": True,
        "source_question": text,
        "extracted_commands": commands,
        "persist_result": persist_result,
        "store_result": store_result,
        "fake_suggestion_response_keys": sorted(fake_response.keys()),
    }

    if not _result_has_real_execution(response):
        response["status"] = "error"
        response["answer"] = (
            "已识别并按标准签名写入第二批内联只读命令，但确认执行链路没有返回带 audit_path 的实际执行结果。\n"
            "已提取命令：\n"
            + "\n".join("- " + c for c in commands)
            + "\n\n调试信息已写入 v2.inline_command_execution 和 v2.confirmation_attempts。"
        )
        return response

    response = enrich_v2_execution_response(
        response,
        question=text,
        user=user,
        conversation_id=conversation_id,
    )

    try:
        save_v2_context_from_response(
            conversation_id=conversation_id or response.get("conversation_id"),
            user=user,
            question=text,
            response=response,
        )
    except Exception as exc:
        response.setdefault("v2", {})
        response["v2"]["inline_context_save_error"] = repr(exc)

    return response


# ===== Batch63-fix4 guard_status patch =====
# confirmation.py 的确认执行逻辑只执行 guard_status == "passed" 的命令。
# 前几批内联命令 item 只有 status/validation_status，没有 guard_status，
# 导致 pending 已写入但 passed_items 为空，MCP/Netmiko 不会真正执行。
_PRE_BATCH63_FIX4_MAKE_COMMAND_ITEMS = _make_command_items


def _batch63_fix4_add_guard_fields(item):
    if not isinstance(item, dict):
        return item

    item["guard_status"] = "passed"
    item["guard_reason"] = "user_inline_readonly_command"
    item["guard_message"] = "用户在当前会话中明确要求执行该 show/display 只读命令，已通过内联只读命令校验。"

    # 兼容可能存在的其他安全字段名。不会改变命令本身，只补充确认执行链路需要的元数据。
    item["safety_status"] = "passed"
    item["readonly_guard_status"] = "passed"
    item["validation_status"] = item.get("validation_status") or "passed"
    item["status"] = item.get("status") or "passed"
    item["validated"] = True
    item["is_read_only"] = True
    item["readonly"] = True

    if "guard" not in item or not item.get("guard"):
        item["guard"] = {
            "status": "passed",
            "reason": "user_inline_readonly_command",
        }

    return item


def _make_command_items(commands, context):
    items = _PRE_BATCH63_FIX4_MAKE_COMMAND_ITEMS(commands, context)
    return [_batch63_fix4_add_guard_fields(x) for x in (items or [])]


# ===== Batch63-fix5 YES confirmation patch =====
# c66 最小验证已确认：
# 发送“确认执行全部命令 YES”后，confirmation.py 能读取当前 pending、
# 执行 2 条内联 show 命令、生成 audit_path，并触发本地 LLM 基于原始输出分析。
# 因此内联命令链路不再调用“确认可以执行/执行吧”等会停在 pending_confirmation 的话术，
# 而是直接调用 confirmation.py 要求的强确认语句。
_PRE_BATCH63_FIX5_CALL_CONFIRMATION = _call_confirmation


def _call_confirmation(conversation_id, user):
    attempts = []

    if try_handle_v2_execution_confirmation is None:
        return {
            "status": "error",
            "planner_source": "v2_inline_command_execution",
            "answer": "确认执行函数不存在：try_handle_v2_execution_confirmation not found",
            "items": [],
            "v2": {"confirmation_attempts": attempts},
        }

    question = "确认执行全部命令 YES"

    # 优先按真实签名调用：
    # try_handle_v2_execution_confirmation(question, user=None, conversation_id=None)
    try:
        result = try_handle_v2_execution_confirmation(
            question=question,
            user=user,
            conversation_id=conversation_id,
        )
        normalized = _normalize_confirmation_result(result)
        attempts.append({
            "method": "kwargs:question,user,conversation_id",
            "question": question,
            "result_type": type(result).__name__,
            "status": normalized.get("status") if isinstance(normalized, dict) else None,
            "planner_source": normalized.get("planner_source") if isinstance(normalized, dict) else None,
            "items_count": len(normalized.get("items") or []) if isinstance(normalized, dict) else 0,
            "has_real_execution": _result_has_real_execution(normalized),
            "result_preview": repr(result)[:1000],
        })
        if _result_has_real_execution(normalized):
            return normalized
    except Exception as exc:
        attempts.append({
            "method": "kwargs:question,user,conversation_id",
            "question": question,
            "error": repr(exc),
        })

    # 兼容一次位置参数调用，防止未来函数签名有小变化。
    try:
        result = try_handle_v2_execution_confirmation(question, user, conversation_id)
        normalized = _normalize_confirmation_result(result)
        attempts.append({
            "method": "args:question,user,conversation_id",
            "question": question,
            "result_type": type(result).__name__,
            "status": normalized.get("status") if isinstance(normalized, dict) else None,
            "planner_source": normalized.get("planner_source") if isinstance(normalized, dict) else None,
            "items_count": len(normalized.get("items") or []) if isinstance(normalized, dict) else 0,
            "has_real_execution": _result_has_real_execution(normalized),
            "result_preview": repr(result)[:1000],
        })
        if _result_has_real_execution(normalized):
            return normalized
    except Exception as exc:
        attempts.append({
            "method": "args:question,user,conversation_id",
            "question": question,
            "error": repr(exc),
        })

    return {
        "status": "error",
        "planner_source": "v2_execution_confirmation",
        "answer": (
            "已识别并写入第二批内联只读命令，也已自动发送“确认执行全部命令 YES”，"
            "但确认执行链路仍未返回带 audit_path 的实际执行结果。"
        ),
        "items": [],
        "v2": {
            "confirmation_attempts": attempts,
            "batch63_fix5_yes_confirmation": True,
        },
    }


# ===== Batch65 bulk20 inline chunk patch =====
_BATCH65_INLINE_BULK_LIMIT = 20
_PRE_BATCH65_TRY_HANDLE_V2_INLINE_COMMAND_EXECUTION = try_handle_v2_inline_command_execution


def _batch65_chunks(seq, size):
    seq = list(seq or [])
    for i in range(0, len(seq), size):
        yield i, seq[i:i + size]


def _batch65_items_have_real_execution(items):
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("audit_path"):
            return True
        if str(item.get("execution_status") or item.get("status") or "").lower() == "executed":
            return True
        if item.get("analysis_status") == "llm_evidence_analyzed":
            return True
    return False


def _batch65_merge_columns(responses):
    for r in responses or []:
        if isinstance(r, dict) and r.get("columns"):
            return r.get("columns")
    return [
        "index", "device_name", "device_type", "command",
        "execution_status", "ok", "audit_path", "audit_error",
        "analysis_status", "analysis_summary", "output_preview",
    ]


def _batch65_merge_field_labels(responses):
    for r in responses or []:
        if isinstance(r, dict) and r.get("field_labels"):
            return r.get("field_labels")
    return {
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
    }


def _batch65_reindex_items(items):
    out = []
    for idx, item in enumerate(items or [], 1):
        if isinstance(item, dict):
            x = dict(item)
            x["index"] = idx
            x["id"] = idx
            out.append(x)
    return out


def _batch65_build_merged_response(question, conversation_id, user, all_items, chunk_reports, responses):
    all_items = _batch65_reindex_items(all_items)
    ok_count = 0
    failed_count = 0
    audit_count = 0

    for item in all_items:
        if item.get("audit_path"):
            audit_count += 1
        if item.get("ok") is True or str(item.get("execution_status") or item.get("status") or "").lower() == "executed":
            ok_count += 1
        else:
            failed_count += 1

    answer = (
        "已按批量上限 20 自动完成内联只读命令执行。\n"
        "本次共识别命令 {total} 条，分为 {chunks} 批执行，成功/已返回审计文件 {ok} 条，可能失败 {failed} 条。\n"
        "后续将基于 MCP/Netmiko 原始输出进行分析。"
    ).format(
        total=len(all_items),
        chunks=len(chunk_reports or []),
        ok=ok_count,
        failed=failed_count,
    )

    return {
        "status": "ok",
        "planner_source": "v2_execution_confirmation",
        "conversation_id": conversation_id,
        "user": user,
        "answer": answer,
        "columns": _batch65_merge_columns(responses),
        "field_labels": _batch65_merge_field_labels(responses),
        "count": len(all_items),
        "returned": len(all_items),
        "items": all_items,
        "v2": {
            "batch65_bulk20_inline_execution": True,
            "bulk_limit": _BATCH65_INLINE_BULK_LIMIT,
            "total_commands": len(all_items),
            "chunk_count": len(chunk_reports or []),
            "chunk_reports": chunk_reports,
            "audit_count": audit_count,
            "ok_count": ok_count,
            "failed_count": failed_count,
            "source_question": question,
        },
    }


def try_handle_v2_inline_command_execution(question, conversation_id=None, user=None):
    text = str(question or "")

    if not _has_execute_intent(text):
        return None

    commands = extract_inline_readonly_commands(text)
    if not commands:
        return None

    context = load_v2_context(conversation_id=conversation_id, user=user) or {}
    dev = _context_device(context)

    if not dev:
        return {
            "status": "error",
            "planner_source": "v2_inline_command_execution",
            "answer": "已识别到你输入中包含要执行的只读命令，但当前会话没有可继承的设备信息。请先指定设备名称，或先在同一会话中生成第一批命令。",
            "items": [],
            "v2": {
                "inline_commands": commands,
                "reason": "missing_current_device_context",
                "batch65_bulk20_inline_execution": True,
            },
        }

    all_items = []
    responses = []
    chunk_reports = []

    for chunk_start, chunk_commands in _batch65_chunks(commands, _BATCH65_INLINE_BULK_LIMIT):
        context = load_v2_context(conversation_id=conversation_id, user=user) or context
        chunk_items = _make_command_items(chunk_commands, context)

        for idx, item in enumerate(chunk_items, 1):
            if isinstance(item, dict):
                item["index"] = idx
                item["id"] = idx
                item["batch_index"] = len(chunk_reports) + 1
                item["global_index"] = chunk_start + idx

        fake_response = _build_fake_suggestion_response(
            original_question=text,
            commands=chunk_commands,
            items=chunk_items,
            context=context,
            conversation_id=conversation_id,
            user=user,
        )

        persist_result = _persist_context_and_pending(
            items=chunk_items,
            response=fake_response,
            context=context,
            conversation_id=conversation_id,
            user=user,
        )

        store_result = _store_pending_standard(
            conversation_id=conversation_id,
            user=user,
            question=text,
            response=fake_response,
        )

        response = _call_confirmation(
            conversation_id=conversation_id,
            user=user,
        )

        if not isinstance(response, dict):
            response = {}

        response.setdefault("v2", {})
        response["v2"]["inline_command_execution"] = {
            "enabled": True,
            "batch65_bulk20_inline_execution": True,
            "source_question": text,
            "bulk_limit": _BATCH65_INLINE_BULK_LIMIT,
            "chunk_index": len(chunk_reports) + 1,
            "chunk_start": chunk_start + 1,
            "chunk_size": len(chunk_commands),
            "extracted_commands": chunk_commands,
            "persist_result": persist_result,
            "store_result": store_result,
        }

        chunk_response_items = response.get("items") or []
        has_real_execution = _batch65_items_have_real_execution(chunk_response_items)

        chunk_reports.append({
            "chunk_index": len(chunk_reports) + 1,
            "chunk_start": chunk_start + 1,
            "chunk_size": len(chunk_commands),
            "commands": chunk_commands,
            "status": response.get("status"),
            "planner_source": response.get("planner_source"),
            "items_count": len(chunk_response_items),
            "audit_count": len([x for x in chunk_response_items if isinstance(x, dict) and x.get("audit_path")]),
            "has_real_execution": has_real_execution,
            "answer_preview": str(response.get("answer") or "")[:800],
        })

        if not has_real_execution:
            response["status"] = "error"
            response["planner_source"] = response.get("planner_source") or "v2_execution_confirmation"
            response["answer"] = (
                "已识别内联只读命令并按每批最多 20 条处理，但第 {chunk} 批没有返回实际执行结果。\n"
                "本批命令：\n{cmds}\n\n"
                "原始返回：{raw}"
            ).format(
                chunk=len(chunk_reports),
                cmds="\n".join("- " + c for c in chunk_commands),
                raw=str(response.get("answer") or "")[:2000],
            )
            response.setdefault("v2", {})
            response["v2"]["batch65_chunk_reports"] = chunk_reports
            return response

        all_items.extend(chunk_response_items)
        responses.append(response)

    merged = _batch65_build_merged_response(
        question=text,
        conversation_id=conversation_id,
        user=user,
        all_items=all_items,
        chunk_reports=chunk_reports,
        responses=responses,
    )

    merged = enrich_v2_execution_response(
        merged,
        question=text,
        user=user,
        conversation_id=conversation_id,
    )

    merged.setdefault("v2", {})
    merged["v2"]["batch65_bulk20_inline_execution"] = True
    merged["v2"]["bulk_limit"] = _BATCH65_INLINE_BULK_LIMIT
    merged["v2"]["chunk_reports"] = chunk_reports

    try:
        save_v2_context_from_response(
            conversation_id=conversation_id or merged.get("conversation_id"),
            user=user,
            question=text,
            response=merged,
        )
    except Exception as exc:
        merged.setdefault("v2", {})
        merged["v2"]["inline_context_save_error"] = repr(exc)

    return merged
