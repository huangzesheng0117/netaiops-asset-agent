# -*- coding: utf-8 -*-
"""V4.2-2 general_chat handler."""

from __future__ import annotations

from netaiops_asset.chat_v3.intent_schema import IntentAction
from netaiops_asset.chat_v4.handlers.base import (
    HandlerOutcome,
    HandlerRequest,
    generate_with_v3_adapter,
)


class GeneralChatHandler:
    action = IntentAction.general_chat
    handler_key = IntentAction.general_chat.value

    def handle(self, request: HandlerRequest) -> HandlerOutcome:
        return generate_with_v3_adapter(
            request,
            expected_action=self.action,
            response_mode="chat",
        )
