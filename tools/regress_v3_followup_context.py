# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.followup_context import (
    build_followup_context,
    delete_followup_context,
    record_v3_turn,
)
from netaiops_asset.chat_v3.intent_arbiter import build_context_summary


def assert_case(name, condition, payload=None):
    if not condition:
        raise AssertionError(f"{name} failed: {payload}")
    print(f"{name}=OK")


def main() -> int:
    temp_dir = Path(tempfile.mkdtemp(prefix="v3442_followup_context_"))
    os.environ["NETAIOPS_V3_FOLLOWUP_CONTEXT_DIR"] = str(temp_dir)

    conversation_id = "v3-3-16-takeover-regress-v3442-context"
    delete_followup_context(conversation_id)

    empty = build_followup_context(
        conversation_id=conversation_id,
        user="v3_3_16_takeover",
    )
    assert_case(
        "followup_context_empty_before_record",
        empty.get("available") is False
        and empty.get("turn_count") == 0,
        empty,
    )

    first = record_v3_turn(
        conversation_id=conversation_id,
        user="v3_3_16_takeover",
        question="维护前为什么要确认主备状态？",
        response={
            "status": "ok",
            "answer": "因为错误判断主备状态可能导致流量中断。",
            "planner_source": "v3_response_generator",
        },
        action="advice_analysis",
        route_label="chat_return_line_1875",
    )
    assert_case(
        "followup_context_record_first_turn",
        first.get("turn_count") == 1,
        first,
    )

    bridge = build_followup_context(
        conversation_id=conversation_id,
        user="v3_3_16_takeover",
    )
    assert_case(
        "followup_context_available_after_record",
        bridge.get("available") is True
        and bridge.get("turn_count") == 1
        and bridge.get("source") == "v3_followup_context_store",
        bridge,
    )

    summary = build_context_summary(bridge.get("arbiter_context"))
    assert_case(
        "followup_context_visible_to_arbiter",
        "维护前为什么要确认主备状态" in summary
        and "流量中断" in summary
        and "followup_context_available" in summary,
        summary,
    )

    record_v3_turn(
        conversation_id=conversation_id,
        user="v3_3_16_takeover",
        question="继续分析最主要的风险。",
        response={
            "status": "ok",
            "answer": "最主要的风险是维护对象判断错误。",
            "planner_source": "v3_response_generator",
        },
        action="analyze_existing_evidence",
        route_label="middleware_jsonresponse_line_707",
    )
    bridge = build_followup_context(
        conversation_id=conversation_id,
        user="v3_3_16_takeover",
    )
    assert_case(
        "followup_context_preserves_original_conversation_id",
        bridge.get("original_conversation_id") == conversation_id
        and bridge.get("turn_count") == 2,
        bridge,
    )

    assert_case(
        "followup_context_delete",
        delete_followup_context(conversation_id) is True,
    )
    assert_case(
        "followup_context_deleted",
        build_followup_context(
            conversation_id=conversation_id,
            user="v3_3_16_takeover",
        ).get("available")
        is False,
    )

    print("regress_v3_followup_context=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
