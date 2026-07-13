# -*- coding: utf-8 -*-
"""V4.2-2 advice_analysis handler."""

from __future__ import annotations

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.handlers.base import (
    HandlerOutcome,
    HandlerRequest,
    generate_with_v3_adapter,
)


class AdviceAnalysisHandler:
    action = IntentAction.advice_analysis
    handler_key = IntentAction.advice_analysis.value

    def handle(self, request: HandlerRequest) -> HandlerOutcome:
        return generate_with_v3_adapter(
            request,
            expected_action=self.action,
            response_mode="advice",
        )
