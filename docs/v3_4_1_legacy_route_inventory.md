# ChatBot V3.4-1 Legacy Route Inventory

## 目标

V3.4-1 只做旧路由盘点和真实入口地图，不修改线上行为，不重启服务。

## 总览

| Metric | Value |
| --- | --- |
| return_count_total | 213 |
| chat_related_return_count | 13 |
| middleware_return_count | 10 |
| chat_route_return_count | 3 |
| middleware_jsonresponse_return_count | 9 |
| middleware_jsonresponse_wrapped_count | 9 |
| chat_route_wrapped_count | 3 |
| legacy_signal_count | 1209 |

## Route Class Counts

| Route Class | Count |
| --- | --- |
| advice_analysis | 4 |
| batch_route | 16 |
| cmdb_query | 22 |
| followup | 88 |
| inline_command | 22 |
| semantic_route | 7 |
| unknown | 54 |

## Risk Level Counts

| Risk | Count |
| --- | --- |
| high | 48 |
| low | 7 |
| medium | 109 |
| unknown | 49 |

## /api/v1/chat 相关 return 地图

| Line | Function | Type | Routes | Return | JSONResponse | V3 Wrapped | Class | Risk | Snippet |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 741 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | followup | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( batch67_advice_response ), local_context=locals(), route_label="middleware_jsonresponse_line_624", ) ) |
| 763 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | inline_command | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( inline_response ), local_context=locals(), route_label="middleware_jsonresponse_line_638", ) ) |
| 775 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | inline_command | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( { "status": "error", "planner_source": "v2_inline_command_execution", "answer": "内联命令执行分支异常：{}".format(repr(_batch63_inline_exc)), "items": [], "v2": {"inline_error": repr(_batch63_inline_exc)}, } ), local_context=locals(), route_label= |
| 835 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | followup | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( confirm_response ), local_context=locals(), route_label="middleware_jsonresponse_line_694", ) ) |
| 856 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | followup | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( followup_response ), local_context=locals(), route_label="middleware_jsonresponse_line_707", ) ) |
| 900 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | followup | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( confirm_response ), local_context=locals(), route_label="middleware_jsonresponse_line_743", ) ) |
| 943 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | followup | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( followup_response ), local_context=locals(), route_label="middleware_jsonresponse_line_778", ) ) |
| 993 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | followup | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( confirm_response ), local_context=locals(), route_label="middleware_jsonresponse_line_820", ) ) |
| 1042 | v2_chat_router_middleware | chat_middleware | http | JSONResponse | True | True | followup | high | return JSONResponse( _v3_apply_chat_canary_takeover( response=( v2_response ), local_context=locals(), route_label="middleware_jsonresponse_line_861", ) ) |
| 1070 | v2_chat_router_middleware | chat_middleware | http | Await | False | False | followup | high | return await call_next(request) |
| 1989 | chat | chat_route | /api/v1/chat | _v3_apply_chat_canary_takeover | False | True | followup | medium | return _v3_apply_chat_canary_takeover( response=( response ), local_context=locals(), route_label="chat_return_line_1800", ) |
| 2070 | chat | chat_route | /api/v1/chat | _v3_apply_chat_canary_takeover | False | True | followup | medium | return _v3_apply_chat_canary_takeover( response=( response ), local_context=locals(), route_label="chat_return_line_1875", ) |
| 2126 | chat | chat_route | /api/v1/chat | _v3_apply_chat_canary_takeover | False | True | followup | medium | return _v3_apply_chat_canary_takeover( response=( response ), local_context=locals(), route_label="chat_return_line_1925", ) |

## 旧路由信号样本

| Line | Category | Risk | Matched | Context |
| --- | --- | --- | --- | --- |
| 1 | followup | medium | followup | 1: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis_forced<br>2: from netaiops_asset.chat_v2.semantic_router import build_v2_semantic_route, semantic_confirm_question_from_route<br>3: import io |
| 2 | followup | medium | followup | 1: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis_forced<br>2: from netaiops_asset.chat_v2.semantic_router import build_v2_semantic_route, semantic_confirm_question_from_route<br>3: import io<br>4: import os |
| 3 | followup | medium | followup | 1: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis_forced<br>2: from netaiops_asset.chat_v2.semantic_router import build_v2_semantic_route, semantic_confirm_question_from_route<br>3: import io<br>4: import os<br>5: import tempfile |
| 4 | semantic_route | medium | semantic,semantic_route,route,router | 2: from netaiops_asset.chat_v2.semantic_router import build_v2_semantic_route, semantic_confirm_question_from_route<br>3: import io<br>4: import os<br>5: import tempfile<br>6: import time |
| 13 | followup | medium | conversation | 11: from fastapi import Request<br>12: from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse<br>13: from pydantic import BaseModel<br>14: <br>15: from netaiops_asset.agent.conversation_actions import detect_conversation_action, handle_conversation_action |
| 14 | followup | medium | conversation | 12: from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse<br>13: from pydantic import BaseModel<br>14: <br>15: from netaiops_asset.agent.conversation_actions import detect_conversation_action, handle_conversation_action<br>16: from netaiops_asset.agent.conversation_store import ( |
| 22 | cmdb_query | medium | cmdb,CMDB | 20:     get_conversation,<br>21:     list_conversations,<br>22: )<br>23: from netaiops_asset.agent.rule_parser import parse_question<br>24: from netaiops_asset.cmdb.adapter import CMDBAdapter |
| 23 | cmdb_query | medium | cmdb,CMDB | 21:     list_conversations,<br>22: )<br>23: from netaiops_asset.agent.rule_parser import parse_question<br>24: from netaiops_asset.cmdb.adapter import CMDBAdapter<br>25: from netaiops_asset.cmdb.field_map import CMDB_FIELDS, field_labels, normalize_fields |
| 24 | cmdb_query | medium | cmdb,CMDB | 22: )<br>23: from netaiops_asset.agent.rule_parser import parse_question<br>24: from netaiops_asset.cmdb.adapter import CMDBAdapter<br>25: from netaiops_asset.cmdb.field_map import CMDB_FIELDS, field_labels, normalize_fields<br>26: from netaiops_asset.config_loader import CONFIG_PATH, get_config |
| 25 | cmdb_query | medium | cmdb,CMDB | 23: from netaiops_asset.agent.rule_parser import parse_question<br>24: from netaiops_asset.cmdb.adapter import CMDBAdapter<br>25: from netaiops_asset.cmdb.field_map import CMDB_FIELDS, field_labels, normalize_fields<br>26: from netaiops_asset.config_loader import CONFIG_PATH, get_config<br>27: from netaiops_asset.llm.client import LLMClient |
| 26 | cmdb_query | medium | cmdb,CMDB | 24: from netaiops_asset.cmdb.adapter import CMDBAdapter<br>25: from netaiops_asset.cmdb.field_map import CMDB_FIELDS, field_labels, normalize_fields<br>26: from netaiops_asset.config_loader import CONFIG_PATH, get_config<br>27: from netaiops_asset.llm.client import LLMClient<br>28: from netaiops_asset.llm.tool_planner import apply_llm_plan, plan_with_llm |
| 27 | cmdb_query | medium | cmdb,CMDB | 25: from netaiops_asset.cmdb.field_map import CMDB_FIELDS, field_labels, normalize_fields<br>26: from netaiops_asset.config_loader import CONFIG_PATH, get_config<br>27: from netaiops_asset.llm.client import LLMClient<br>28: from netaiops_asset.llm.tool_planner import apply_llm_plan, plan_with_llm<br>29: from netaiops_asset.llm.planner_policy import accept_llm_parse, build_planner_diagnostics, should_try_llm |
| 30 | cmdb_query | high | cmdb,CMDB,query_cmdb,device | 28: from netaiops_asset.llm.tool_planner import apply_llm_plan, plan_with_llm<br>29: from netaiops_asset.llm.planner_policy import accept_llm_parse, build_planner_diagnostics, should_try_llm<br>30: from netaiops_asset.security.audit import write_audit<br>31: from netaiops_asset.security.request_context import reset_request_context, set_request_context<br>32: from netaiops_asset.tools.cmdb_tools import CMDB_TOOL_CATALOG, tool_query_cmdb_device_detail, tool_query_cmdb_devices, tool_query_cmdb_devices_by_ips |
| 31 | cmdb_query | high | cmdb,CMDB,query_cmdb,device | 29: from netaiops_asset.llm.planner_policy import accept_llm_parse, build_planner_diagnostics, should_try_llm<br>30: from netaiops_asset.security.audit import write_audit<br>31: from netaiops_asset.security.request_context import reset_request_context, set_request_context<br>32: from netaiops_asset.tools.cmdb_tools import CMDB_TOOL_CATALOG, tool_query_cmdb_device_detail, tool_query_cmdb_devices, tool_query_cmdb_devices_by_ips<br>33: from netaiops_asset.web.ui import render_index_html |
| 32 | cmdb_query | high | cmdb,CMDB,query_cmdb,device | 30: from netaiops_asset.security.audit import write_audit<br>31: from netaiops_asset.security.request_context import reset_request_context, set_request_context<br>32: from netaiops_asset.tools.cmdb_tools import CMDB_TOOL_CATALOG, tool_query_cmdb_device_detail, tool_query_cmdb_devices, tool_query_cmdb_devices_by_ips<br>33: from netaiops_asset.web.ui import render_index_html<br>34: from netaiops_asset.chat_v2.router import try_handle_v2_chat |
| 33 | inline_command | medium | command | 31: from netaiops_asset.security.request_context import reset_request_context, set_request_context<br>32: from netaiops_asset.tools.cmdb_tools import CMDB_TOOL_CATALOG, tool_query_cmdb_device_detail, tool_query_cmdb_devices, tool_query_cmdb_devices_by_ips<br>33: from netaiops_asset.web.ui import render_index_html<br>34: from netaiops_asset.chat_v2.router import try_handle_v2_chat<br>35: from netaiops_asset.chat_v2.confirmation import store_pending_commands, try_handle_v2_execution_confirmation |
| 34 | inline_command | medium | command | 32: from netaiops_asset.tools.cmdb_tools import CMDB_TOOL_CATALOG, tool_query_cmdb_device_detail, tool_query_cmdb_devices, tool_query_cmdb_devices_by_ips<br>33: from netaiops_asset.web.ui import render_index_html<br>34: from netaiops_asset.chat_v2.router import try_handle_v2_chat<br>35: from netaiops_asset.chat_v2.confirmation import store_pending_commands, try_handle_v2_execution_confirmation<br>36: from netaiops_asset.chat_v2.context import save_v2_context_from_response, get_context_debug |
| 35 | inline_command | medium | command | 33: from netaiops_asset.web.ui import render_index_html<br>34: from netaiops_asset.chat_v2.router import try_handle_v2_chat<br>35: from netaiops_asset.chat_v2.confirmation import store_pending_commands, try_handle_v2_execution_confirmation<br>36: from netaiops_asset.chat_v2.context import save_v2_context_from_response, get_context_debug<br>37: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis |
| 36 | inline_command | medium | command | 34: from netaiops_asset.chat_v2.router import try_handle_v2_chat<br>35: from netaiops_asset.chat_v2.confirmation import store_pending_commands, try_handle_v2_execution_confirmation<br>36: from netaiops_asset.chat_v2.context import save_v2_context_from_response, get_context_debug<br>37: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis<br>38: from netaiops_asset.chat_v2.llm_intent_planner import planner_debug_payload |
| 37 | inline_command | medium | command | 35: from netaiops_asset.chat_v2.confirmation import store_pending_commands, try_handle_v2_execution_confirmation<br>36: from netaiops_asset.chat_v2.context import save_v2_context_from_response, get_context_debug<br>37: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis<br>38: from netaiops_asset.chat_v2.llm_intent_planner import planner_debug_payload<br>39: from netaiops_asset.chat_v2.plan_dispatcher import build_dispatch_debug_payload |
| 38 | followup | medium | followup | 36: from netaiops_asset.chat_v2.context import save_v2_context_from_response, get_context_debug<br>37: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis<br>38: from netaiops_asset.chat_v2.llm_intent_planner import planner_debug_payload<br>39: from netaiops_asset.chat_v2.plan_dispatcher import build_dispatch_debug_payload<br>40: from netaiops_asset.chat_v2.execution_response_enricher import normalize_execution_confirmation_question, enrich_v2_execution_response |
| 39 | followup | medium | followup | 37: from netaiops_asset.chat_v2.followup import try_handle_v2_followup_analysis<br>38: from netaiops_asset.chat_v2.llm_intent_planner import planner_debug_payload<br>39: from netaiops_asset.chat_v2.plan_dispatcher import build_dispatch_debug_payload<br>40: from netaiops_asset.chat_v2.execution_response_enricher import normalize_execution_confirmation_question, enrich_v2_execution_response<br>41:  |
| 43 | followup | medium | history,conversation | 41: <br>42: <br>43: CONFIG = get_config()<br>44: APP_NAME = CONFIG.get("app", {}).get("name", "netaiops-asset-agent")<br>45: APP_VERSION = CONFIG.get("app", {}).get("version", "0.1.0-v1-batch17-conversation-history") |
| 44 | followup | medium | history,conversation | 42: <br>43: CONFIG = get_config()<br>44: APP_NAME = CONFIG.get("app", {}).get("name", "netaiops-asset-agent")<br>45: APP_VERSION = CONFIG.get("app", {}).get("version", "0.1.0-v1-batch17-conversation-history")<br>46: START_TIME = time.time() |
| 45 | followup | medium | history,conversation | 43: CONFIG = get_config()<br>44: APP_NAME = CONFIG.get("app", {}).get("name", "netaiops-asset-agent")<br>45: APP_VERSION = CONFIG.get("app", {}).get("version", "0.1.0-v1-batch17-conversation-history")<br>46: START_TIME = time.time()<br>47:  |
| 46 | followup | medium | history,conversation | 44: APP_NAME = CONFIG.get("app", {}).get("name", "netaiops-asset-agent")<br>45: APP_VERSION = CONFIG.get("app", {}).get("version", "0.1.0-v1-batch17-conversation-history")<br>46: START_TIME = time.time()<br>47: <br>48: app = FastAPI(title=APP_NAME, version=APP_VERSION) |
| 47 | followup | medium | history,conversation | 45: APP_VERSION = CONFIG.get("app", {}).get("version", "0.1.0-v1-batch17-conversation-history")<br>46: START_TIME = time.time()<br>47: <br>48: app = FastAPI(title=APP_NAME, version=APP_VERSION)<br>49:  |
| 114 | semantic_route | medium | planner_source | 112:     return {<br>113:         "response_type": "dict",<br>114:         "keys": sorted([str(key) for key in response.keys()])[:80],<br>115:         "status": response.get("status"),<br>116:         "planner_source": response.get("planner_source"), |
| 115 | semantic_route | medium | planner_source | 113:         "response_type": "dict",<br>114:         "keys": sorted([str(key) for key in response.keys()])[:80],<br>115:         "status": response.get("status"),<br>116:         "planner_source": response.get("planner_source"),<br>117:         "request_id": response.get("request_id"), |
| 116 | followup | medium | conversation | 114:         "keys": sorted([str(key) for key in response.keys()])[:80],<br>115:         "status": response.get("status"),<br>116:         "planner_source": response.get("planner_source"),<br>117:         "request_id": response.get("request_id"),<br>118:         "conversation_id": response.get("conversation_id"), |
| 117 | followup | medium | conversation | 115:         "status": response.get("status"),<br>116:         "planner_source": response.get("planner_source"),<br>117:         "request_id": response.get("request_id"),<br>118:         "conversation_id": response.get("conversation_id"),<br>119:         "count": response.get("count"), |
| 118 | followup | medium | conversation | 116:         "planner_source": response.get("planner_source"),<br>117:         "request_id": response.get("request_id"),<br>118:         "conversation_id": response.get("conversation_id"),<br>119:         "count": response.get("count"),<br>120:         "returned": response.get("returned"), |
| 125 | followup | high | conversation | 123:         "v2_execution_policy": v2_payload.get("execution_policy"),<br>124:     }<br>125: <br>126: <br>127: def _v3_shadow_write(shadow_state, question, user, conversation_id, v2_route, v2_response=None, extra=None): |
| 126 | followup | high | conversation | 124:     }<br>125: <br>126: <br>127: def _v3_shadow_write(shadow_state, question, user, conversation_id, v2_route, v2_response=None, extra=None):<br>128:     try: |
| 127 | followup | high | conversation | 125: <br>126: <br>127: def _v3_shadow_write(shadow_state, question, user, conversation_id, v2_route, v2_response=None, extra=None):<br>128:     try:<br>129:         if not isinstance(shadow_state, dict): |
| 128 | followup | high | conversation | 126: <br>127: def _v3_shadow_write(shadow_state, question, user, conversation_id, v2_route, v2_response=None, extra=None):<br>128:     try:<br>129:         if not isinstance(shadow_state, dict):<br>130:             return |
| 129 | followup | high | conversation | 127: def _v3_shadow_write(shadow_state, question, user, conversation_id, v2_route, v2_response=None, extra=None):<br>128:     try:<br>129:         if not isinstance(shadow_state, dict):<br>130:             return<br>131:         if not shadow_state.get("enabled"): |
| 268 | semantic_route | medium | route | 266:             ).strip().lower() in {"1", "true", "yes", "on", "enabled"}<br>267:             _v3_gate_for_generator = merged_extra.get("takeover_gate_if_enabled") or merged_extra.get("takeover_gate_runtime") or {}<br>268:             _v3_generator_context = {<br>269:                 "question": str(question or ""),<br>270:                 "v2_route": v2_route, |
| 269 | semantic_route | medium | route | 267:             _v3_gate_for_generator = merged_extra.get("takeover_gate_if_enabled") or merged_extra.get("takeover_gate_runtime") or {}<br>268:             _v3_generator_context = {<br>269:                 "question": str(question or ""),<br>270:                 "v2_route": v2_route,<br>271:                 "v2_response": v2_response, |
| 270 | semantic_route | medium | route | 268:             _v3_generator_context = {<br>269:                 "question": str(question or ""),<br>270:                 "v2_route": v2_route,<br>271:                 "v2_response": v2_response,<br>272:                 "extra": merged_extra, |
| 271 | semantic_route | medium | route | 269:                 "question": str(question or ""),<br>270:                 "v2_route": v2_route,<br>271:                 "v2_response": v2_response,<br>272:                 "extra": merged_extra,<br>273:             } |
| 272 | semantic_route | medium | route | 270:                 "v2_route": v2_route,<br>271:                 "v2_response": v2_response,<br>272:                 "extra": merged_extra,<br>273:             }<br>274:             _v3_generated_response = generate_v3_response( |
| 305 | followup | high | conversation | 303:         write_shadow_record(<br>304:             question=str(question or ""),<br>305:             conversation_id=conversation_id,<br>306:             user=user,<br>307:             v2_route=str(v2_route or ""), |
| 306 | followup | medium | conversation | 304:             question=str(question or ""),<br>305:             conversation_id=conversation_id,<br>306:             user=user,<br>307:             v2_route=str(v2_route or ""),<br>308:             v2_summary=_v3_shadow_response_summary(v2_response), |
| 307 | followup | medium | conversation | 305:             conversation_id=conversation_id,<br>306:             user=user,<br>307:             v2_route=str(v2_route or ""),<br>308:             v2_summary=_v3_shadow_response_summary(v2_response),<br>309:             v3_decision=_v3_normalized_decision, |
| 308 | semantic_route | medium | route | 306:             user=user,<br>307:             v2_route=str(v2_route or ""),<br>308:             v2_summary=_v3_shadow_response_summary(v2_response),<br>309:             v3_decision=_v3_normalized_decision,<br>310:             v3_plan=_v3_normalized_plan, |
| 309 | semantic_route | medium | route | 307:             v2_route=str(v2_route or ""),<br>308:             v2_summary=_v3_shadow_response_summary(v2_response),<br>309:             v3_decision=_v3_normalized_decision,<br>310:             v3_plan=_v3_normalized_plan,<br>311:             shadow_dir=_v3_shadow_os.environ.get( |
| 443 | cmdb_query | medium | cmdb,CMDB,管理IP,管理 IP,设备类型 | 441:     )<br>442:     if _v3_canary_contains_any(text, positive_danger_tokens):<br>443:         return "", "question_not_low_risk_canary"<br>444: <br>445:     query_tokens = ("查一下", "查询设备", "管理IP", "管理 IP", "设备类型", "CMDB", "cmdb") |
| 444 | cmdb_query | medium | cmdb,CMDB,管理IP,管理 IP,设备类型 | 442:     if _v3_canary_contains_any(text, positive_danger_tokens):<br>443:         return "", "question_not_low_risk_canary"<br>444: <br>445:     query_tokens = ("查一下", "查询设备", "管理IP", "管理 IP", "设备类型", "CMDB", "cmdb")<br>446:     negative_query_phrases = ( |
| 445 | cmdb_query | medium | cmdb,CMDB,管理IP,管理 IP,设备类型 | 443:         return "", "question_not_low_risk_canary"<br>444: <br>445:     query_tokens = ("查一下", "查询设备", "管理IP", "管理 IP", "设备类型", "CMDB", "cmdb")<br>446:     negative_query_phrases = (<br>447:         "不要查询设备", |
| 446 | cmdb_query | medium | cmdb,CMDB,管理IP,管理 IP,设备类型 | 444: <br>445:     query_tokens = ("查一下", "查询设备", "管理IP", "管理 IP", "设备类型", "CMDB", "cmdb")<br>446:     negative_query_phrases = (<br>447:         "不要查询设备",<br>448:         "不查询设备", |
| 447 | cmdb_query | medium | cmdb,CMDB,管理IP,管理 IP,设备类型 | 445:     query_tokens = ("查一下", "查询设备", "管理IP", "管理 IP", "设备类型", "CMDB", "cmdb")<br>446:     negative_query_phrases = (<br>447:         "不要查询设备",<br>448:         "不查询设备",<br>449:         "无需查询设备", |
| 455 | advice_analysis | low | advice | 453:     )<br>454:     if _v3_canary_contains_any(text, query_tokens) and not _v3_canary_contains_any(text, negative_query_phrases):<br>455:         return "", "question_not_low_risk_canary"<br>456: <br>457:     advice_tokens = ( |
| 456 | advice_analysis | low | advice,建议,是否建议 | 454:     if _v3_canary_contains_any(text, query_tokens) and not _v3_canary_contains_any(text, negative_query_phrases):<br>455:         return "", "question_not_low_risk_canary"<br>456: <br>457:     advice_tokens = (<br>458:         "是否建议", |
| 457 | advice_analysis | low | advice,建议,是否建议 | 455:         return "", "question_not_low_risk_canary"<br>456: <br>457:     advice_tokens = (<br>458:         "是否建议",<br>459:         "建议", |
| 458 | advice_analysis | low | advice,建议,风险,是否建议 | 456: <br>457:     advice_tokens = (<br>458:         "是否建议",<br>459:         "建议",<br>460:         "风险", |
| 459 | advice_analysis | low | advice,建议,风险,是否建议 | 457:     advice_tokens = (<br>458:         "是否建议",<br>459:         "建议",<br>460:         "风险",<br>461:         "是否需要", |
| 467 | advice_analysis | low | advice,建议,怎么处理,排查思路 | 465:         "怎么处理",<br>466:         "排查思路",<br>467:         "运维建议",<br>468:     )<br>469:     explicit_advice_constraints = ( |
| 468 | advice_analysis | low | advice,建议,排查思路 | 466:         "排查思路",<br>467:         "运维建议",<br>468:     )<br>469:     explicit_advice_constraints = (<br>470:         "只给运维建议", |
| 469 | inline_command | low | 命令 | 467:         "运维建议",<br>468:     )<br>469:     explicit_advice_constraints = (<br>470:         "只给运维建议",<br>471:         "不要生成命令", |
| 470 | inline_command | low | 命令 | 468:     )<br>469:     explicit_advice_constraints = (<br>470:         "只给运维建议",<br>471:         "不要生成命令",<br>472:         "不生成命令", |
| 471 | inline_command | low | 命令 | 469:     explicit_advice_constraints = (<br>470:         "只给运维建议",<br>471:         "不要生成命令",<br>472:         "不生成命令",<br>473:         "无需生成命令", |
| 473 | inline_command | low | 命令 | 471:         "不要生成命令",<br>472:         "不生成命令",<br>473:         "无需生成命令",<br>474:     )<br>475:     if _v3_canary_contains_any(text, advice_tokens) and _v3_canary_contains_any(text, explicit_advice_constraints): |
| 474 | inline_command | low | 命令 | 472:         "不生成命令",<br>473:         "无需生成命令",<br>474:     )<br>475:     if _v3_canary_contains_any(text, advice_tokens) and _v3_canary_contains_any(text, explicit_advice_constraints):<br>476:         return "advice_analysis", "ok" |
| 475 | inline_command | low | 命令 | 473:         "无需生成命令",<br>474:     )<br>475:     if _v3_canary_contains_any(text, advice_tokens) and _v3_canary_contains_any(text, explicit_advice_constraints):<br>476:         return "advice_analysis", "ok"<br>477:  |
| 476 | advice_analysis | low | advice,advice_analysis | 474:     )<br>475:     if _v3_canary_contains_any(text, advice_tokens) and _v3_canary_contains_any(text, explicit_advice_constraints):<br>476:         return "advice_analysis", "ok"<br>477: <br>478:     general_tokens = ( |
| 477 | advice_analysis | low | advice,advice_analysis | 475:     if _v3_canary_contains_any(text, advice_tokens) and _v3_canary_contains_any(text, explicit_advice_constraints):<br>476:         return "advice_analysis", "ok"<br>477: <br>478:     general_tokens = (<br>479:         "只做文本解释", |
| 478 | advice_analysis | low | advice,advice_analysis | 476:         return "advice_analysis", "ok"<br>477: <br>478:     general_tokens = (<br>479:         "只做文本解释",<br>480:         "解释一下", |
| 540 | followup | medium | conversation | 538:         or bool(allowed_prefixes and any(conversation_id.startswith(prefix) for prefix in allowed_prefixes))<br>539:     )<br>540: <br>541: <br>542: def _v3_apply_chat_canary_takeover(response, local_context=None, route_label=""): |
| 541 | semantic_route | medium | route | 539:     )<br>540: <br>541: <br>542: def _v3_apply_chat_canary_takeover(response, local_context=None, route_label=""):<br>543:     audit_event = { |
| 542 | semantic_route | medium | route | 540: <br>541: <br>542: def _v3_apply_chat_canary_takeover(response, local_context=None, route_label=""):<br>543:     audit_event = {<br>544:         "version": "v3.3.17", |
| 543 | semantic_route | medium | route | 541: <br>542: def _v3_apply_chat_canary_takeover(response, local_context=None, route_label=""):<br>543:     audit_event = {<br>544:         "version": "v3.3.17",<br>545:         "mode": "canary", |
| 544 | semantic_route | medium | route | 542: def _v3_apply_chat_canary_takeover(response, local_context=None, route_label=""):<br>543:     audit_event = {<br>544:         "version": "v3.3.17",<br>545:         "mode": "canary",<br>546:         "route_label": route_label, |
| 545 | semantic_route | medium | route | 543:     audit_event = {<br>544:         "version": "v3.3.17",<br>545:         "mode": "canary",<br>546:         "route_label": route_label,<br>547:         "taken": False, |
| 546 | semantic_route | medium | route | 544:         "version": "v3.3.17",<br>545:         "mode": "canary",<br>546:         "route_label": route_label,<br>547:         "taken": False,<br>548:         "reason": "not_evaluated", |
| 547 | semantic_route | medium | route | 545:         "mode": "canary",<br>546:         "route_label": route_label,<br>547:         "taken": False,<br>548:         "reason": "not_evaluated",<br>549:     } |
| 548 | semantic_route | medium | route | 546:         "route_label": route_label,<br>547:         "taken": False,<br>548:         "reason": "not_evaluated",<br>549:     }<br>550:     try: |
| 582 | followup | low | conversation | 580: <br>581:         should_audit = _v3_canary_should_audit(user, conversation_id)<br>582:         allowed_users = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_USERS", "")<br>583:         allowed_prefixes = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX", "")<br>584:         allowed_actions = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS", "general_chat,advice_analysis") |
| 583 | followup | low | conversation | 581:         should_audit = _v3_canary_should_audit(user, conversation_id)<br>582:         allowed_users = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_USERS", "")<br>583:         allowed_prefixes = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX", "")<br>584:         allowed_actions = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS", "general_chat,advice_analysis")<br>585:         allowed_sources = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_SOURCES", "llm") |
| 584 | followup | low | conversation | 582:         allowed_users = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_USERS", "")<br>583:         allowed_prefixes = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX", "")<br>584:         allowed_actions = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS", "general_chat,advice_analysis")<br>585:         allowed_sources = _v3_canary_env_csv("NETAIOPS_V3_TAKEOVER_ALLOWED_SOURCES", "llm")<br>586:  |

## V3.4 后续建议

1. V3.4-2 建立 Legacy Route Registry，先登记旧路由类型，不改行为。
2. V3.4-3 优先收敛 general_chat / advice_analysis。
3. V3.4-4 再处理 follow-up / 多轮上下文。
4. V3.4-5 单独处理 inline 抢路由，但不进入 V3.5 command splitter。
5. V3.4-6 再删除或禁用重复旧分支。

## 边界

- 本批不修改 app.py。
- 本批不重启服务。
- 本批不扩大 V3 takeover 范围。
- 本批只新增 inventory 工具和文档。
