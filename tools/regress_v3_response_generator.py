# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from netaiops_asset.chat_v3.response_generator import (
    build_frontend_response,
    extract_frontend_answer,
    generate_v3_response,
)


class FakeLLM:
    def __init__(self, status="ok", content=None):
        self.status = status
        self.content = content or "这是由测试 LLM 生成的正式前端回答，用于验证 V3 响应生成器能够补齐答案内容。"
        self.called = 0
        self.messages = None

    def chat(self, messages, **kwargs):
        self.called += 1
        self.messages = messages
        if self.status != "ok":
            return {"status": "error", "error_code": "FAKE_LLM_ERROR", "message": "fake failure"}
        return {"status": "ok", "content": self.content, "usage": {"total_tokens": 10}}


def assert_case(name, condition, payload=None):
    if not condition:
        raise AssertionError(f"{name} failed: {payload}")
    print(f"{name}=OK")


def gate(**overrides):
    data = {
        "enabled": True,
        "eligible": True,
        "takeover": True,
        "reason": "eligible",
    }
    data.update(overrides)
    return data


def main() -> int:
    answer = extract_frontend_answer(plan={"reason": "内部意图判断理由"})
    assert_case("plan_reason_not_frontend_answer", answer == "", answer)

    generated = generate_v3_response(
        question="这个设备怎么办？",
        conversation_id="conv-clarify",
        plan={"action": "need_clarification", "handler_key": "need_clarification", "response_mode": "clarification"},
        gate=gate(),
    )
    assert_case("need_clarification_generated", generated.ready is True and generated.status == "need_clarification", generated.as_dict())

    response = build_frontend_response(
        question="这个设备怎么办？",
        conversation_id="conv-clarify",
        generated=generated,
    )
    assert_case("need_clarification_frontend_contract", response["status"] == "need_clarification" and response["answer"], response)

    generated = generate_v3_response(
        question="解释一下 StackWise Virtual",
        conversation_id="conv-existing",
        plan={
            "action": "general_chat",
            "handler_key": "general_chat",
            "response_mode": "chat",
            "answer": "StackWise Virtual 是 Cisco 的虚拟化技术，可以把两台交换机逻辑上组成一台设备。",
        },
        gate=gate(),
    )
    assert_case("general_chat_existing_answer_ready", generated.ready is True and generated.source == "existing_frontend_answer", generated.as_dict())

    generated = generate_v3_response(
        question="解释一下 StackWise Virtual",
        conversation_id="conv-no-live",
        plan={"action": "general_chat", "handler_key": "general_chat", "response_mode": "chat", "reason": "internal only"},
        gate=gate(),
        allow_live_llm=False,
    )
    assert_case(
        "general_chat_no_answer_no_live_blocked",
        generated.ready is False and generated.reason == "live_llm_disabled_and_no_existing_answer",
        generated.as_dict(),
    )

    fake = FakeLLM()
    generated = generate_v3_response(
        question="解释一下 StackWise Virtual",
        conversation_id="conv-fake-llm",
        plan={"action": "general_chat", "handler_key": "general_chat", "response_mode": "chat", "reason": "internal only"},
        gate=gate(),
        allow_live_llm=True,
        llm_client=fake,
    )
    assert_case("general_chat_fake_llm_ready", generated.ready is True and generated.source == "llm" and fake.called == 1, generated.as_dict())

    response = build_frontend_response(
        question="解释一下 StackWise Virtual",
        conversation_id="conv-fake-llm",
        generated=generated,
    )
    assert_case("general_chat_fake_llm_frontend_contract", response["status"] == "ok" and response["planner_source"] == "v3_response_generator", response)

    fake = FakeLLM(content="建议先确认业务低峰窗口、当前主备状态和上联流量，再决定是否隔离流量后执行维护。")
    generated = generate_v3_response(
        question="是否建议重启 standby 前先隔离流量？",
        conversation_id="conv-advice",
        plan={"action": "advice_analysis", "handler_key": "advice_analysis", "response_mode": "advice", "reason": "internal only"},
        gate=gate(),
        allow_live_llm=True,
        llm_client=fake,
    )
    assert_case("advice_fake_llm_ready", generated.ready is True and generated.action == "advice_analysis", generated.as_dict())

    fake = FakeLLM(status="error")
    generated = generate_v3_response(
        question="解释一下 StackWise Virtual",
        conversation_id="conv-llm-fail",
        plan={"action": "general_chat", "handler_key": "general_chat", "response_mode": "chat"},
        gate=gate(),
        allow_live_llm=True,
        llm_client=fake,
    )
    assert_case("fake_llm_failure_blocked", generated.ready is False and generated.reason == "llm_generation_failed", generated.as_dict())

    generated = generate_v3_response(
        question="查 device01",
        conversation_id="conv-cmdb",
        plan={
            "action": "cmdb_query",
            "handler_key": "cmdb_query",
            "response_mode": "cmdb",
            "items": [{"hostname": "device01", "management_ip": "192.0.2.1"}],
        },
        gate=gate(),
    )
    assert_case("cmdb_items_ready", generated.ready is True and generated.count == 1, generated.as_dict())

    generated = generate_v3_response(
        question="查 missing",
        conversation_id="conv-cmdb-missing",
        plan={"action": "cmdb_query", "handler_key": "cmdb_query", "response_mode": "cmdb"},
        gate=gate(),
    )
    assert_case("cmdb_missing_items_blocked", generated.ready is False and generated.reason == "missing_cmdb_items", generated.as_dict())

    generated = generate_v3_response(
        question="执行 show version",
        conversation_id="conv-exec",
        plan={"action": "execute_provided_commands", "handler_key": "execute_provided_commands", "response_mode": "execute"},
        gate=gate(),
    )
    assert_case("execute_action_blocked", generated.ready is False and generated.reason == "blocked_action", generated.as_dict())

    generated = generate_v3_response(
        question="解释一下 StackWise Virtual",
        conversation_id="conv-gate-block",
        plan={
            "action": "general_chat",
            "handler_key": "general_chat",
            "response_mode": "chat",
            "answer": "即使有答案，gate 不 eligible 时也不应该接管。",
        },
        gate=gate(eligible=False, takeover=False, reason="low_effective_confidence"),
    )
    assert_case("gate_not_eligible_blocks_generation", generated.ready is False and generated.reason.startswith("gate_not_eligible"), generated.as_dict())

    print("regress_v3_response_generator=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
