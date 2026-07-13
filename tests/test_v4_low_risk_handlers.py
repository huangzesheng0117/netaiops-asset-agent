#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from dataclasses import replace

from netaiops_asset.chat_v3.intent_schema import IntentAction, IntentDecision
from netaiops_asset.chat_v4.context_store import ContextStore
from netaiops_asset.chat_v4.handlers import (
    AdviceAnalysisHandler,
    ClarificationHandler,
    GeneralChatHandler,
    HandlerRequest,
)


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.max_tokens = 1200
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return {
            "status": "ok",
            "content": self.content,
            "requested_model": "glm-5.2",
            "reported_model": "glm-5.2",
            "finish_reason": "stop",
            "max_tokens_used": kwargs.get("max_tokens"),
            "content_length": len(self.content),
        }


class RaisingLLM:
    def chat(self, *args, **kwargs):
        raise AssertionError("clarification handler must not call LLM")


class V4LowRiskHandlerTests(unittest.TestCase):
    def _request(self, action, question, llm_client=None):
        store = ContextStore(root="/tmp/v4-handler-test-unused")
        context = store.new_context(
            "conv-handler",
            request_user_field="handler_test",
        )
        return HandlerRequest(
            question=question,
            conversation_id="conv-handler",
            request_id="req-handler",
            request_user_field="handler_test",
            decision=IntentDecision(
                action=action,
                confidence=0.95,
                reason="arbiter selected action",
                clarification_question=(
                    "请补充设备名称。"
                    if action == IntentAction.need_clarification
                    else ""
                ),
            ),
            canonical_context=context,
            allow_live_llm=True,
            llm_client=llm_client,
        )

    def test_general_chat_handler_uses_existing_generator_adapter(self):
        fake = FakeLLM(
            "StackWise Virtual 能将两台交换机组合为一个逻辑系统，并提供控制平面冗余。"
        )
        outcome = GeneralChatHandler().handle(
            self._request(
                IntentAction.general_chat,
                "解释一下 StackWise Virtual",
                fake,
            )
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.action, IntentAction.general_chat)
        self.assertTrue(outcome.answer)
        self.assertEqual(outcome.metadata["llm_status"], "ok")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(
            fake.calls[0]["kwargs"]["thinking"],
            {"type": "disabled"},
        )

    def test_advice_handler_uses_exact_decision_action(self):
        fake = FakeLLM(
            "建议先确认主备状态和流量路径，再设置回退条件，最后评估是否需要隔离流量。"
        )
        outcome = AdviceAnalysisHandler().handle(
            self._request(
                IntentAction.advice_analysis,
                "是否建议在重启 standby 前先隔离流量？",
                fake,
            )
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.action, IntentAction.advice_analysis)
        self.assertEqual(len(fake.calls), 1)

    def test_clarification_handler_is_deterministic_and_no_llm(self):
        outcome = ClarificationHandler().handle(
            self._request(
                IntentAction.need_clarification,
                "这个设备怎么办？",
                RaisingLLM(),
            )
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "need_clarification")
        self.assertEqual(outcome.answer, "请补充设备名称。")
        self.assertFalse(outcome.metadata["llm_called"])

    def test_handler_rejects_action_mismatch(self):
        fake = FakeLLM("这是一个足够长的通用解释回答，用于动作不匹配测试。")
        outcome = GeneralChatHandler().handle(
            self._request(
                IntentAction.advice_analysis,
                "是否建议切换？",
                fake,
            )
        )
        self.assertFalse(outcome.ok)
        self.assertIn("handler action mismatch", outcome.detail)
        self.assertEqual(len(fake.calls), 0)

    def test_empty_llm_answer_is_not_success(self):
        fake = FakeLLM("")
        outcome = GeneralChatHandler().handle(
            self._request(
                IntentAction.general_chat,
                "解释 BGP",
                fake,
            )
        )
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.answer)

    def test_live_llm_disabled_without_answer_is_failure(self):
        fake = FakeLLM(
            "这段内容不应被调用，因为 live LLM 已被关闭。"
        )
        request = self._request(
            IntentAction.general_chat,
            "解释一下 BGP",
            fake,
        )
        request = replace(request, allow_live_llm=False)
        outcome = GeneralChatHandler().handle(request)
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.detail,
            "live_llm_disabled_and_no_existing_answer",
        )
        self.assertEqual(len(fake.calls), 0)

    def test_question_content_does_not_change_selected_handler(self):
        fake = FakeLLM(
            "这是对概念的解释，不执行任何设备操作，也不会生成或下发命令。"
        )
        request = self._request(
            IntentAction.general_chat,
            "reload 这个词是什么意思？这里只做概念解释。",
            fake,
        )
        outcome = GeneralChatHandler().handle(request)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.action, IntentAction.general_chat)
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
