# V2 Batch54 Plan Validator + Action Dispatcher

## 目标

Batch54 在 Batch53 LLM-first Intent Planner 之后，新增本地 Plan Validator + Action Dispatcher。

LLM 或 fallback 只负责生成结构化 plan；本地 dispatcher 决定安全动作路线。

## Dispatch Route

| route | 含义 |
|---|---|
| v2_chat_router | 生成 V2 排障/取证建议命令 |
| v2_execution_confirmation | 进入确认执行流程 |
| v2_followup_analysis | 基于上下文追问分析 |
| v1_cmdb | 回退 V1 CMDB 查询 |
| need_clarification | 要求补充设备、接口、现象等关键信息 |

## 本批新增能力

1. 校验 action/category/entities。
2. 标准化 device_name、mgmt_ip、interface。
3. 支持从上下文继承设备。
4. 对 fallback_minimal 标记 degraded=true。
5. 将 interface_error、cpu、route_table、followup、execute、cmdb 分发到不同 route。
6. 新增调试接口：

    POST /api/v1/v2/dispatch_plan

## 安全边界

Batch54 不执行设备 CLI。

执行命令仍必须走 v2_execution_confirmation，并由确认流程做 YES 和只读校验。

## 仍需收口问题

当前 LLM 调用仍存在 token 验证失败，plan 可能来自 fallback_minimal。该问题不阻塞 Batch54 的本地校验与分发建设，但不能带到最终验收。
