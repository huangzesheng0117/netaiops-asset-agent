# -*- coding: utf-8 -*-
"""V4.2-2 deterministic need_clarification handler."""

from __future__ import annotations

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.handlers.base import (
    HandlerOutcome,
    HandlerRequest,
    ensure_expected_action,
)


DEFAULT_CLARIFICATION = (
    "请补充更明确的设备、对象、时间范围、已有证据或你希望我完成的具体操作。"
)


class ClarificationHandler:
    action = IntentAction.need_clarification
    handler_key = IntentAction.need_clarification.value

    def handle(self, request: HandlerRequest) -> HandlerOutcome:
        mismatch = ensure_expected_action(request, self.action)
        if mismatch is not None:
            return mismatch

        answer = str(
            request.decision.clarification_question
            or DEFAULT_CLARIFICATION
        ).strip()
        return HandlerOutcome.success(
            action=self.action,
            handler_key=self.handler_key,
            answer=answer,
            status="need_clarification",
            source="deterministic_clarification",
            metadata={"llm_called": False},
        )
