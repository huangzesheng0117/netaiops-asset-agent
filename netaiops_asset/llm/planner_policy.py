from __future__ import annotations

from typing import Any


def _intent(parsed: dict[str, Any] | None) -> str:
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("intent") or "")


def _reason(parsed: dict[str, Any] | None) -> str:
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("reason") or "")


def _filters(parsed: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    value = parsed.get("filters")
    return value if isinstance(value, dict) else {}


def _fields(parsed: dict[str, Any] | None) -> list[str]:
    if not isinstance(parsed, dict):
        return []
    value = parsed.get("fields")
    return value if isinstance(value, list) else []


def _confidence(plan_result: dict[str, Any] | None) -> float:
    try:
        return float((plan_result or {}).get("plan", {}).get("confidence") or 0)
    except Exception:
        return 0.0


def _has_assist_keyword(question: str, keywords: list[str]) -> bool:
    q = str(question or "")
    q_lower = q.lower()
    for item in keywords:
        k = str(item or "").strip()
        if not k:
            continue
        if k.lower() in q_lower:
            return True
    return False


def should_try_llm(
    question: str,
    rule_parsed: dict[str, Any],
    requested_mode: str | None,
    llm_cfg: dict[str, Any],
) -> tuple[bool, str]:
    mode = str(requested_mode or "auto").strip().lower()
    if mode not in {"auto", "rule", "llm"}:
        mode = "auto"

    if not bool(llm_cfg.get("enabled", False)):
        return False, "llm_disabled"

    if mode == "rule":
        return False, "requested_rule_mode"

    if mode == "llm":
        return True, "requested_llm_mode"

    if bool(llm_cfg.get("always_try_for_parse", False)):
        return True, "always_try_for_parse"

    if _intent(rule_parsed) == "clarify":
        return True, "rule_needs_clarification"

    if _reason(rule_parsed) == "fallback_search":
        return True, "rule_fallback_search"

    if bool(llm_cfg.get("planner_keep_rule_for_exact_ip", True)):
        if _intent(rule_parsed) == "query_device_detail" and _reason(rule_parsed) == "detected_ip":
            return False, "keep_rule_for_exact_ip"

    policy = str(llm_cfg.get("planner_policy", "assistive")).strip().lower()

    if policy == "fallback_only":
        return False, "policy_fallback_only_rule_succeeded"

    if policy == "assistive":
        keywords = llm_cfg.get("planner_assist_keywords", [])
        if isinstance(keywords, list) and _has_assist_keyword(question, keywords):
            return True, "assistive_keyword_matched"

    return False, "rule_parser_sufficient"


def accept_llm_parse(
    rule_parsed: dict[str, Any],
    llm_parsed: dict[str, Any] | None,
    plan_result: dict[str, Any] | None,
    requested_mode: str | None,
    llm_cfg: dict[str, Any],
) -> tuple[bool, str]:
    if not isinstance(llm_parsed, dict):
        return False, "llm_parsed_empty"

    if (plan_result or {}).get("status") != "ok":
        return False, "llm_plan_not_ok"

    mode = str(requested_mode or "auto").strip().lower()
    if mode == "llm":
        return True, "requested_llm_mode_accept"

    conf = _confidence(plan_result)
    min_conf = float(llm_cfg.get("planner_min_confidence", 0.72) or 0.72)
    if conf < min_conf:
        return False, f"confidence_too_low:{conf:.2f}"

    rule_intent = _intent(rule_parsed)
    llm_intent = _intent(llm_parsed)

    if llm_intent == "clarify" and rule_intent != "clarify":
        return False, "do_not_downgrade_successful_rule_to_clarify"

    if rule_intent == "clarify" or _reason(rule_parsed) == "fallback_search":
        return True, "rule_weak_accept_llm"

    if rule_intent == "query_device_detail" and _reason(rule_parsed) == "detected_ip":
        return False, "do_not_override_exact_ip_rule"

    rule_filter_count = len(_filters(rule_parsed))
    llm_filter_count = len(_filters(llm_parsed))
    rule_field_count = len(_fields(rule_parsed))
    llm_field_count = len(_fields(llm_parsed))

    accept_equal = bool(llm_cfg.get("planner_accept_equal_filters", True))
    if accept_equal:
        if llm_filter_count >= rule_filter_count and llm_field_count >= rule_field_count:
            return True, "llm_not_weaker_than_rule"
    else:
        if llm_filter_count > rule_filter_count:
            return True, "llm_has_more_filters"

    return False, "rule_parser_result_kept"


def build_planner_diagnostics(
    requested_mode: str | None,
    should_try: bool,
    should_try_reason: str,
    accepted: bool,
    accept_reason: str,
    rule_parsed: dict[str, Any],
    llm_plan_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "requested_mode": requested_mode or "auto",
        "should_try_llm": should_try,
        "should_try_reason": should_try_reason,
        "accepted_llm": accepted,
        "accept_reason": accept_reason,
        "rule_intent": _intent(rule_parsed),
        "rule_reason": _reason(rule_parsed),
        "rule_filter_count": len(_filters(rule_parsed)),
        "llm_plan_status": (llm_plan_result or {}).get("status"),
        "llm_confidence": _confidence(llm_plan_result),
        "llm_latency_ms": ((llm_plan_result or {}).get("llm") or {}).get("latency_ms"),
        "llm_headers": ((llm_plan_result or {}).get("llm") or {}).get("headers"),
    }
