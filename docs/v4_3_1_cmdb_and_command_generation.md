# V4.3-1：CMDB 与命令生成迁移

## 1. 目标

本批把以下两个无设备执行副作用的 action 迁入 V4：

```text
cmdb_query
generate_commands
```

V4 当前直接处理集合变为：

```text
general_chat
advice_analysis
need_clarification
cmdb_query
generate_commands
```

以下仍显式 stage fallback：

```text
execute_provided_commands
execute_provided_commands_and_analyze
confirm_execute_pending
analyze_existing_evidence
```

## 2. Intent 结构化参数

`IntentDecision` 新增：

```text
CmdbQuerySpec
CommandGenerationSpec
```

CMDB handler 只读取结构化 operation、keyword、filters、fields、ips 和分页；命令 handler 只读取结构化 category、interface、max_commands 和 device_hint/context device。两者都不得从 question 文本重新判断 action、查询类型或排障类别。

## 3. CMDB 边界

```text
IntentDecision.cmdb_query
-> 字段/敏感字段/过滤器校验
-> 既有 read-only CMDB tools
-> safe items/labels
-> V4 response/audit/context/history
```

未知字段、敏感字段、未知过滤器和非法 operator 在工具调用前阻断。CMDB error 作为可见 V4 error，不静默回旧路由。

## 4. 命令生成边界

```text
IntentDecision.command_generation
-> read-only DeviceIdentityResolver(probe_prometheus=False)
-> deterministic platform command catalog
-> shared command splitter
-> V3 safety guard
-> Netmiko CLI read-only guard
-> V4 response/audit/context/history
```

输出固定标记：

```text
command_source=system_generated
requires_confirmation=true
execution_started=false
pending_created=false
side_effect_started=false
```

本批不调用 Netmiko executor，不写 pending，不执行 CLI。

## 5. 安全与回退

- 系统生成命令必须确认后才能在后续批次执行；
- blocked/review 命令不会以成功结果返回；
- 新 action 内部失败返回 visible V4 error；
- 未迁移 execute/evidence action 继续 stage fallback；
- side effect 未开始，Netmiko execution audit 和 V2 pending 必须保持不变。

## 6. 生产开关

```text
NETAIOPS_V4_ENTRY_ALLOWED_ACTIONS=general_chat,advice_analysis,need_clarification,cmdb_query,generate_commands
```

其他 V4.2-3 开关保持不变。

## 7. 验收

```text
完整 target tree 在 production mutation 前通过
真实 CMDB success/not-found/partial/error
字段和敏感字段边界
命令 splitter + 两层 safety
system_generated requires_confirmation=true
无 Netmiko execution
无 pending create/consume
canonical context/audit/legacy history
原 low-risk smoke 不回归
execute/evidence action 仍 stage fallback
service/live/Git closeout
```
