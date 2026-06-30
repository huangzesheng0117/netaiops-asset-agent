# ChatBot V3.3-17 Audit Observability Fix

## 目标

V3.3-17 audit 修复版在保留低风险 canary 扩展的基础上，显式修复 audit 目录权限，并在 audit 写入失败时通过响应字段 v3_audit_error 暴露真实原因。

本批次仍不是全量接管，仍受 canary 边界控制。

## 边界

允许：

```text
NETAIOPS_V3_TAKEOVER_ALLOWED_USERS=v3_3_16_takeover,v3_3_17_takeover
NETAIOPS_V3_TAKEOVER_CONVERSATION_PREFIX=v3-3-16-takeover-,v3-3-17-takeover-
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

## V3.3-17 相比 V3.3-16 的增强

1. 扩大低风险 general_chat 文本解释触发范围。
2. 扩大 advice_analysis 运维建议触发范围。
3. 保持“请求上下文优先于 response conversation_id”的修复。
4. 新增 takeover audit JSONL：
   `/var/lib/netaiops-asset-agent/data/v3_takeover_audit/takeover_YYYYMMDD.jsonl`
5. audit 同时记录 taken 与 blocked reason，便于后续排查。
6. audit 写入失败不再静默吞掉，而是在响应中返回 v3_audit_error。
7. 异常时只降级返回原 V2 response，不影响普通请求。

## 成功标志

```text
app_v3_3_17_helper_unit_smoke=OK
v3_3_17_allowed_general_expanded=OK
v3_3_17_allowed_advice_expanded=OK
v3_3_17_compat_v3_3_16=OK
v3_3_17_blocked_user=OK
v3_3_17_blocked_prefix=OK
v3_3_17_blocked_cmdb_query=OK
v3_3_17_audit_observability=OK
v3_3_17_takeover_expansion_summary=OK
```

## 后续

V3.3-18 应做 V3.3 收口：全量回归、运维文档、drop-in 状态确认、Git tag。
