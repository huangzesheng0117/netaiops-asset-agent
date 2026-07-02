#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

START = "# V3_ROUTE_RETURN_CANARY_MARKER_BEGIN"
END = "# V3_ROUTE_RETURN_CANARY_MARKER_END"

NEW_BLOCK = r"""# V3_ROUTE_RETURN_CANARY_MARKER_BEGIN
# V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_R6_MARKER_BEGIN
def _v3_canary_env_bool(name, default="0"):
    try:
        import os
        return str(os.getenv(name, default)).strip().lower() in {
            "1", "true", "yes", "on", "enabled"
        }
    except Exception:
        return False


def _v3_canary_env_float(name, default="0.70"):
    try:
        import os
        return float(str(os.getenv(name, default) or default).strip())
    except Exception:
        return float(default)


def _v3_canary_env_csv(name, default=""):
    try:
        import os
        raw = str(os.getenv(name, default) or "")
        return {item.strip() for item in raw.split(",") if item.strip()}
    except Exception:
        return set()


def _v3_canary_norm_action(value):
    if value in (None, ""):
        return ""
    try:
        enum_value = getattr(value, "value", None)
        if enum_value not in (None, ""):
            return str(enum_value)
    except Exception:
        pass
    text = str(value).strip()
    if text.startswith("IntentAction."):
        return text.split(".", 1)[1]
    return text


def _v3_canary_as_dict(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(value):
            data = asdict(value)
            if isinstance(data, dict):
                return dict(data)
    except Exception:
        pass
    for method_name in ("model_dump", "dict", "as_dict"):
        try:
            method = getattr(value, method_name, None)
            if callable(method):
                data = method()
                if isinstance(data, dict):
                    return dict(data)
        except Exception:
            pass
    try:
        data = vars(value)
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    return {}


def _v3_canary_get_key(obj, key):
    if obj is None:
        return None
    if isinstance(obj, dict):
        value = obj.get(key)
        if value not in (None, ""):
            return value
    data = _v3_canary_as_dict(obj)
    if isinstance(data, dict):
        value = data.get(key)
        if value not in (None, ""):
            return value
    try:
        value = getattr(obj, key)
        if value not in (None, ""):
            return value
    except Exception:
        pass
    return None


def _v3_canary_get_nested(data, keys):
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = _v3_canary_get_key(current, key)
        if current in (None, ""):
            return None
    return current


def _v3_canary_extract_value(local_context, response, candidate_keys):
    local_context = local_context if isinstance(local_context, dict) else {}
    candidate_keys = tuple(candidate_keys)

    for key in candidate_keys:
        value = local_context.get(key)
        if value not in (None, ""):
            return str(value)

    for preferred_name in ("payload", "req", "request_payload", "chat_request"):
        preferred = local_context.get(preferred_name)
        for key in candidate_keys:
            value = _v3_canary_get_key(preferred, key)
            if value not in (None, ""):
                return str(value)

    for obj_name, obj in local_context.items():
        if obj_name in {"response", "_v3_response", "v2_response"}:
            continue
        for key in candidate_keys:
            value = _v3_canary_get_key(obj, key)
            if value not in (None, ""):
                return str(value)

    for key in candidate_keys:
        value = _v3_canary_get_key(response, key)
        if value not in (None, ""):
            return str(value)

    return ""


def _v3_canary_audit_path():
    try:
        import datetime as _dt
        import os
        from pathlib import Path
        audit_dir = Path(os.getenv(
            "NETAIOPS_V3_TAKEOVER_AUDIT_DIR",
            "/var/lib/netaiops-asset-agent/data/v3_takeover_audit",
        ))
        audit_dir.mkdir(parents=True, exist_ok=True)
        return audit_dir / f"takeover_{_dt.datetime.now().strftime('%Y%m%d')}.jsonl", ""
    except Exception as exc:
        return None, repr(exc)


def _v3_canary_write_audit(event):
    try:
        import datetime as _dt
        import json
        path, path_error = _v3_canary_audit_path()
        if path is None:
            return f"audit_path_error:{path_error}"
        record = dict(event or {})
        record.setdefault("timestamp", _dt.datetime.now().isoformat(timespec="seconds"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return ""
    except Exception as exc:
        return repr(exc)


def _v3_canary_should_audit(user, conversation_id):
    allowed_users = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_USERS", "")
    allowed_prefixes = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX", "")
    user = str(user or "")
    conversation_id = str(conversation_id or "")
    return bool(allowed_users and user in allowed_users) or bool(
        allowed_prefixes and any(conversation_id.startswith(prefix) for prefix in allowed_prefixes)
    )


def _v3_canary_inspect_shadow_state(value):
    data = value if isinstance(value, dict) else _v3_canary_as_dict(value)
    if not isinstance(data, dict) or not data:
        return False, {}, "", "", "empty_or_unserializable"

    state_keys = {"v3_plan", "v3_decision", "plan", "decision"}
    if not any(key in data for key in state_keys):
        return False, data, "", "", "no_plan_decision_fields"

    plan, decision = _v3_canary_extract_plan_decision(data)
    action, action_source = _v3_canary_arbiter_action(plan, decision)
    if not action:
        return False, data, "", action_source, "arbiter_action_missing"

    return True, data, action, action_source, ""


def _v3_canary_rejected_candidate_summary(source, data, reason):
    data = data if isinstance(data, dict) else {}
    plan, decision = _v3_canary_extract_plan_decision(data)
    action, action_source = _v3_canary_arbiter_action(plan, decision)
    return {
        "source": str(source or ""),
        "reason": str(reason or "invalid_shadow_state"),
        "action": action,
        "action_source": action_source,
        "state_keys": sorted(str(key) for key in data.keys())[:40],
        "plan_keys": sorted(str(key) for key in plan.keys())[:40],
        "decision_keys": sorted(str(key) for key in decision.keys())[:40],
    }


def _v3_canary_extract_existing_shadow_state(local_context):
    local_context = local_context if isinstance(local_context, dict) else {}
    rejected = []
    explicit_keys = ("v3_shadow_state", "_v3_shadow_state", "shadow_state")

    for key in explicit_keys:
        if key not in local_context or local_context.get(key) is None:
            continue
        valid, data, _action, _action_source, reason = _v3_canary_inspect_shadow_state(
            local_context.get(key)
        )
        if valid:
            return data, key, rejected
        rejected.append(_v3_canary_rejected_candidate_summary(key, data, reason))

    for local_name, value in local_context.items():
        if local_name in explicit_keys:
            continue
        data = value if isinstance(value, dict) else _v3_canary_as_dict(value)
        if not isinstance(data, dict) or not data:
            continue
        if not any(
            key in data for key in ("v3_plan", "v3_decision", "plan", "decision")
        ):
            continue

        source = "local_context_nested:{}".format(local_name)
        valid, data, _action, _action_source, reason = _v3_canary_inspect_shadow_state(data)
        if valid:
            return data, source, rejected
        rejected.append(_v3_canary_rejected_candidate_summary(source, data, reason))

    return {}, "", rejected


def _v3_canary_extract_payload(local_context):
    local_context = local_context if isinstance(local_context, dict) else {}
    for key in ("payload", "req", "request_payload", "chat_request"):
        value = local_context.get(key)
        if value is not None:
            return value
    return None


def _v3_canary_build_shadow_state(question, user, conversation_id, local_context):
    try:
        builder = globals().get("_v3_shadow_build")
        if not callable(builder):
            return {}, "shadow_builder_missing", ""
        payload = _v3_canary_extract_payload(local_context)
        state = builder(question, user=user, conversation_id=conversation_id, payload=payload)
        if not isinstance(state, dict):
            state = _v3_canary_as_dict(state)
        if not isinstance(state, dict):
            return {}, "shadow_builder_return_not_dict", ""
        error = str(state.get("error") or "")
        if error:
            return state, "shadow_builder_error", error
        return state, "rebuilt_in_takeover_wrapper", ""
    except Exception as exc:
        return {}, "shadow_builder_exception", repr(exc)


def _v3_canary_extract_plan_decision(shadow_state):
    state = shadow_state if isinstance(shadow_state, dict) else {}
    plan = state.get("v3_plan")
    if plan in (None, ""):
        plan = state.get("plan")
    decision = state.get("v3_decision")
    if decision in (None, ""):
        decision = state.get("decision")
    plan = _v3_canary_as_dict(plan)
    decision = _v3_canary_as_dict(decision)
    if not isinstance(plan, dict):
        plan = {}
    if not isinstance(decision, dict):
        decision = {}
    return plan, decision


def _v3_canary_arbiter_action(plan, decision):
    decision = decision if isinstance(decision, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    for source_name, source in (("decision", decision), ("plan", plan)):
        for key in ("action", "handler_key"):
            action = _v3_canary_norm_action(source.get(key))
            if action:
                return action, source_name
    return "", ""


def _v3_canary_effective_confidence(plan, decision):
    for source in (decision if isinstance(decision, dict) else {}, plan if isinstance(plan, dict) else {}):
        for key in ("effective_confidence", "confidence"):
            try:
                value = source.get(key)
                if value not in (None, ""):
                    return float(value)
            except Exception:
                pass
        try:
            value = _v3_canary_get_nested(source, ("metadata", "effective_confidence"))
            if value not in (None, ""):
                return float(value)
        except Exception:
            pass
    return 0.0


def _v3_canary_response_mode(action):
    return "advice" if action == "advice_analysis" else "chat"


def _v3_canary_normalize_plan_decision(plan, decision, action):
    plan = dict(plan or {})
    decision = dict(decision or {})
    action = _v3_canary_norm_action(action) or _v3_canary_norm_action(decision.get("action")) or _v3_canary_norm_action(plan.get("action"))
    if action:
        plan["action"] = action
        plan["handler_key"] = action
        decision["action"] = action
    plan.setdefault("response_mode", _v3_canary_response_mode(action))
    plan.setdefault("accepted", True)
    plan.setdefault("requires_confirmation", False)
    plan.setdefault("safety_allowed", True)
    decision.setdefault("accepted", True)
    decision.setdefault("requires_confirmation", False)
    decision.setdefault("safety_allowed", True)
    confidence = _v3_canary_effective_confidence(plan, decision)
    if confidence:
        plan.setdefault("confidence", confidence)
        plan.setdefault("effective_confidence", confidence)
        decision.setdefault("confidence", confidence)
        decision.setdefault("effective_confidence", confidence)
    return plan, decision, action


def _v3_canary_optional_shadow_write(shadow_state, question, user, conversation_id, route_label, response):
    try:
        writer = globals().get("_v3_shadow_write")
        if callable(writer) and isinstance(shadow_state, dict):
            writer(shadow_state, question, user, conversation_id, route_label, response)
            return ""
    except Exception as exc:
        return repr(exc)
    return ""


def _v3_apply_chat_canary_takeover(response, local_context=None, route_label=""):
    audit_event = {
        "version": "v3.4.3-r6",
        "mode": "canary",
        "route_label": route_label,
        "taken": False,
        "reason": "not_evaluated",
        "intent_source": "llm_intent_arbiter",
    }
    try:
        if not _v3_canary_env_bool("NETAIOPS_V3_TAKEOVER_ENABLED", "0"):
            audit_event["reason"] = "disabled"
            return response
        if not isinstance(response, dict):
            audit_event["reason"] = "response_not_dict"
            return response

        local_context = local_context if isinstance(local_context, dict) else {}
        user = _v3_canary_extract_value(local_context, response, ("user", "username", "operator"))
        conversation_id = _v3_canary_extract_value(
            local_context,
            response,
            ("conversation_id", "conversationId", "session_id", "sessionId"),
        )
        question = _v3_canary_extract_value(
            local_context,
            response,
            ("question", "message", "prompt", "query", "content", "text"),
        )
        audit_event.update({
            "user": user,
            "conversation_id": conversation_id,
            "question_len": len(str(question or "")),
            "question_prefix": str(question or "")[:120],
        })

        should_audit = _v3_canary_should_audit(user, conversation_id)
        allowed_users = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_USERS", "")
        allowed_prefixes = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX", "")
        allowed_actions = _v3_canary_env_csv(
            "NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS",
            "general_chat,advice_analysis",
        )
        allowed_sources = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_SOURCES", "llm")
        min_confidence = _v3_canary_env_float("NETAIOPS_V3_TAKEOVER_MIN_CONFIDENCE", "0.70")

        def _finish(reason, output_response, **extra):
            audit_event.update(extra)
            audit_event["reason"] = reason
            audit_error = ""
            if should_audit:
                audit_error = _v3_canary_write_audit(audit_event)
            if audit_error and isinstance(output_response, dict):
                safe_output = dict(output_response)
                safe_output["v3_audit_error"] = audit_error
                return safe_output
            return output_response

        if not allowed_users or user not in allowed_users:
            return _finish("user_not_allowed", response)
        if not allowed_prefixes or not any(conversation_id.startswith(prefix) for prefix in allowed_prefixes):
            return _finish("conversation_prefix_not_allowed", response)

        shadow_state, shadow_source, rejected_candidates = _v3_canary_extract_existing_shadow_state(
            local_context
        )
        audit_event["arbiter_state_rejected_candidates"] = rejected_candidates
        audit_event["arbiter_state_rejected_count"] = len(rejected_candidates)

        shadow_error = ""
        if not shadow_state:
            shadow_state, shadow_source, shadow_error = _v3_canary_build_shadow_state(
                question, user, conversation_id, local_context
            )
        audit_event["arbiter_state_source"] = shadow_source or "missing"
        if shadow_error:
            audit_event["arbiter_state_error"] = shadow_error
        if not shadow_state:
            return _finish("arbiter_state_missing", response)

        plan, decision = _v3_canary_extract_plan_decision(shadow_state)
        if not plan and not decision:
            return _finish("arbiter_plan_decision_missing", response)

        raw_action, action_source = _v3_canary_arbiter_action(plan, decision)
        plan, decision, action = _v3_canary_normalize_plan_decision(plan, decision, raw_action)
        audit_event["action"] = action
        audit_event["action_source"] = action_source
        if not action:
            return _finish("arbiter_action_missing", response)

        stage_allowed_actions = {"general_chat", "advice_analysis"}
        if action not in stage_allowed_actions:
            return _finish("arbiter_action_not_v3_4_3_text_advice", response)

        if allowed_actions and "*" not in allowed_actions and action not in allowed_actions:
            return _finish("action_not_allowed", response, allowed_actions=sorted(allowed_actions))

        confidence = _v3_canary_effective_confidence(plan, decision)
        audit_event["confidence"] = confidence
        audit_event["min_confidence"] = min_confidence
        if confidence < min_confidence:
            return _finish("arbiter_confidence_below_threshold", response)

        from netaiops_asset.chat_v3.response_generator import generate_v3_response
        from netaiops_asset.chat_v3.takeover_gate import evaluate_takeover
        from netaiops_asset.chat_v3.takeover_response import evaluate_response_readiness

        gate = evaluate_takeover(
            plan=plan,
            decision=decision,
            enabled=True,
            min_confidence=min_confidence,
            allowed=stage_allowed_actions,
        ).as_dict()
        audit_event["gate"] = gate
        if gate.get("takeover") is not True and gate.get("eligible") is not True:
            return _finish("takeover_gate_not_eligible", response)

        context = {
            "question": question,
            "route_label": route_label,
            "v2_response": response,
            "v3_4_3": {
                "intent_source": "llm_intent_arbiter",
                "arbiter_state_source": audit_event["arbiter_state_source"],
                "convergence_scope": "general_chat/advice_analysis",
            },
        }

        generated = generate_v3_response(
            question=question,
            conversation_id=conversation_id,
            plan=plan,
            decision=decision,
            context=context,
            gate=gate,
            allow_live_llm=_v3_canary_env_bool("NETAIOPS_V3_RESPONSE_GENERATOR_LIVE_LLM", "0"),
        ).as_dict()
        audit_event["generator_ready"] = generated.get("ready")
        audit_event["generator_source"] = generated.get("source")
        audit_event["generator_reason"] = generated.get("reason")

        if generated.get("ready") is not True:
            return _finish("generator_not_ready", response)

        source = str(generated.get("source") or "")
        if allowed_sources and source not in allowed_sources:
            return _finish("source_not_allowed", response, source=source, allowed_sources=sorted(allowed_sources))

        answer = str(generated.get("answer") or "").strip()
        if not answer:
            return _finish("empty_answer", response)

        plan_after = dict(plan or {})
        plan_after["answer"] = answer
        readiness = evaluate_response_readiness(plan=plan_after, decision=decision, gate=gate).as_dict()
        audit_event["readiness_ready"] = readiness.get("ready")
        audit_event["readiness_reason"] = readiness.get("reason")
        if readiness.get("ready") is not True:
            return _finish("readiness_not_ready", response)

        shadow_write_error = ""
        if audit_event["arbiter_state_source"] == "rebuilt_in_takeover_wrapper":
            shadow_write_error = _v3_canary_optional_shadow_write(
                shadow_state, question, user, conversation_id,
                f"v3_4_3_r6_rebuilt_{route_label}", response
            )
        if shadow_write_error:
            audit_event["shadow_write_error"] = shadow_write_error

        mutated = dict(response)
        mutated["answer"] = answer
        mutated["message"] = answer
        mutated["status"] = "ok"
        mutated["planner_source"] = "v3_response_generator"
        mutated["v3_takeover"] = True
        mutated["v3_takeover_mode"] = "canary"
        mutated["v3_takeover_action"] = action
        mutated["v3_takeover_source"] = source
        mutated["v3_takeover_route_label"] = route_label
        mutated["v3_takeover_reason"] = "v3_4_3_preflight_arbiter_rebuild_text_advice_convergence"
        mutated["v3_arbiter_state_source"] = audit_event["arbiter_state_source"]
        audit_event["taken"] = True
        audit_event["answer_len"] = len(answer)
        return _finish("taken", mutated, source=source)
    except Exception as exc:
        audit_event["reason"] = "exception"
        audit_event["error"] = repr(exc)
        audit_error = _v3_canary_write_audit(audit_event)
        if isinstance(response, dict):
            safe_response = dict(response)
            safe_response["v3_takeover_error"] = repr(exc)
            if audit_error:
                safe_response["v3_audit_error"] = audit_error
            return safe_response
        return response


# V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_R6_MARKER_END
# V3_ROUTE_RETURN_CANARY_MARKER_END
"""


def replace_block(app_path: Path) -> dict[str, object]:
    text = app_path.read_text(encoding="utf-8")
    start_count = text.count(START)
    end_count = text.count(END)
    if start_count != 1 or end_count != 1:
        raise SystemExit(f"ERROR: expected exactly one V3 route-return canary block, got start={start_count}, end={end_count}")

    start = text.index(START)
    end = text.index(END, start) + len(END)
    old_block = text[start:end]

    accepted_old_markers = (
        "_v3_canary_low_risk_action" in old_block
        or "V3_4_3_ARBITER_TEXT_ADVICE_CONVERGENCE_MARKER_BEGIN" in old_block
        or "V3_4_3_ARBITER_REBUILD_TEXT_ADVICE_CONVERGENCE_MARKER_BEGIN" in old_block
        or "V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_MARKER_BEGIN" in old_block
        or "V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_R3_MARKER_BEGIN" in old_block
        or "V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_R6_MARKER_BEGIN" in old_block
    )
    if not accepted_old_markers:
        raise SystemExit("ERROR: old canary block does not look like expected V3 takeover canary block")

    new_text = text[:start] + NEW_BLOCK + text[end:]
    changed = new_text != text
    if changed:
        app_path.write_text(new_text, encoding="utf-8")

    return {
        "app_path": str(app_path),
        "changed": changed,
        "old_block_len": len(old_block),
        "new_block_len": len(NEW_BLOCK),
        "old_had_low_risk_action": "_v3_canary_low_risk_action" in old_block,
        "new_has_preflight_rebuild_r6_marker": "V3_4_3_PREFLIGHT_ARB_REBUILD_TEXT_ADVICE_R6_MARKER_BEGIN" in NEW_BLOCK,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    args = parser.parse_args()
    result = replace_block(Path(args.app))
    for key, value in result.items():
        print(f"{key}={value}")
    print("v3_4_3_patch_app=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
