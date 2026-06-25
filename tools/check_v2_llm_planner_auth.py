#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path("/opt/netaiops-asset-agent")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from netaiops_asset.chat_v2.llm_intent_planner import plan_v2_intent

question = "设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题"
plan = plan_v2_intent(question, context=None, user="baoleiji")

safe = {
    "source": plan.get("source"),
    "action": plan.get("action"),
    "category": plan.get("category"),
    "v2_intent": plan.get("v2_intent"),
    "entities": plan.get("entities"),
    "confidence": plan.get("confidence"),
    "llm_status": plan.get("llm_status"),
    "llm_error_preview": str(plan.get("llm_error") or "")[:500],
    "llm_config": plan.get("llm_config"),
    "requires_v2": plan.get("requires_v2"),
    "cmdb_only": plan.get("cmdb_only"),
}

print(json.dumps(safe, ensure_ascii=False, indent=2))
