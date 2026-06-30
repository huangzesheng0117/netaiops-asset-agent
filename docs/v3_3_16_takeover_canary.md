# ChatBot V3.3-16 Request-Context Canary Takeover

## 原始代码复核结论

已基于现网打包文件复核：

- `app.py` 中 `/api/v1/chat` 的主路由函数为 `chat(req: ChatRequest)`
- `chat()` 中存在 3 个 return
- `v2_chat_router_middleware` 中存在 9 个 `JSONResponse(...)` 提前返回
- `_v3_shadow_write()` 只适合作为 shadow 观测点，不适合作为真实返回改写点
- `conversation_id` 在 `append_turn()` 后可能变为新 UUID，不能用最终 response 里的 `conversation_id` 做 canary 前缀判断
- V3 response generator 当前安全 action 只支持 `general_chat`、`advice_analysis`、`need_clarification`、`cmdb_query`

## 本批修正

本批次修正为：

- canary 判断优先从原始请求上下文 `payload` / `req` / `locals()` 取 `user`、`conversation_id`、`question`
- `response["conversation_id"]` 仅作为兜底
- 同时包装 `v2_chat_router_middleware` 的 `JSONResponse(...)`
- 同时包装 `/api/v1/chat` 的 `chat()` route return
- 不再把真实接管逻辑放到 `_v3_shadow_write()`
- canary 低风险判断改为白名单优先，避免误杀“不要查询设备”“不要生成命令”等否定约束
- 命令解释类暂不作为独立 `command_safety` action，而是映射到 `general_chat` 或后续 V3.3-17 再独立设计

## canary 边界

只允许：

```text
NETAIOPS_V3_TAKEOVER_ENABLED=1
NETAIOPS_V3_TAKEOVER_ALLOWED_USERS=v3_3_16_takeover
NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX=v3-3-16-takeover-
NETAIOPS_V3_TAKEOVER_ALLOWED_ACTIONS=general_chat,advice_analysis
NETAIOPS_V3_TAKEOVER_ALLOWED_SOURCES=llm
```

明确不接管：

- CMDB 查询
- 设备查询
- 命令执行
- 配置变更
- 非白名单用户
- 非白名单 conversation_id 前缀

## 成功标志

```text
app_v3_3_16_helper_unit_smoke=OK
v3_3_16_request_context_allowed_general_chat=OK
v3_3_16_request_context_allowed_advice_analysis=OK
v3_3_16_request_context_blocked_user=OK
v3_3_16_request_context_blocked_prefix=OK
v3_3_16_request_context_blocked_missing_cmdb=OK
v3_3_16_request_context_takeover_summary=OK
```
