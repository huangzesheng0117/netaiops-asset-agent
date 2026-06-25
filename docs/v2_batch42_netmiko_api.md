# V2 Batch42 Netmiko 确认执行 API

## 目标

Batch42 将 Netmiko 确认执行能力暴露为后端 API，供后续前端确认按钮或对话式确认流程调用。

## 新增接口

| 接口 | 方法 | 作用 |
|---|---|---|
| /api/v1/netmiko/safety_policy | GET | 查看 Netmiko 执行安全策略 |
| /api/v1/netmiko/validate_commands | POST | 校验一批命令是否只读、安全、可执行 |
| /api/v1/netmiko/execute_confirmed | POST | 在 confirm_execute=YES 时执行单条已通过校验的只读命令 |

## 安全边界

1. 不暴露 Netmiko MCP 配置工具。
2. 只有 CLI Guard 返回 passed 的命令才能执行。
3. review 和 blocked 命令不会执行。
4. confirm_execute 必须严格等于 YES。
5. 每次调用都会写入审计。
6. 本接口只调用 send_command_and_get_output。

## Batch42 回归默认执行

| 项目 | 值 |
|---|---|
| 设备 | SH8-G03-DCI-BN-SW01 |
| 设备类型 | cisco_nxos |
| 命令 | show clock |

## 后续批次

Batch43 建议做前端确认按钮或对话式确认执行，使用户可以在页面上选择某条 passed 命令并确认执行。
