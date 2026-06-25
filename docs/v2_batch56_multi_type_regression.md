# V2 Batch56 连续多类型问题回归

## 目标

Batch56 验证 Batch53～Batch55 的新架构在多种问题类型下都能稳定工作。

覆盖类型：

- CPU 利用率异常
- 接口错包 / 错误包增长
- BGP 邻居异常
- 路由表查询
- 光功率异常
- 端口 down
- CMDB 资产类问题边界

## 验证点

1. 排障类问题进入 v2_chat_router。
2. 资产类问题继续进入 V1 CMDB，不误入 V2。
3. Plan Dispatcher route 正确。
4. v2_intent 正确。
5. 设备名、管理 IP、接口名解析正确。
6. 命令模板正确，不再只返回 show version。
7. 所有建议命令均为只读 passed。
8. 所有建议命令仍要求确认。
9. 未带 YES 的“执行上述命令”不会执行设备 CLI，只返回 pending_confirmation。
10. 本批不执行任何设备 CLI。

## 当前已知边界

LLM Token 仍存在“令牌验证失败”，所以当前 plan_source 可能仍为 fallback_minimal。
该问题不影响本批本地分发与模板回归，但不能带入最终验收。
