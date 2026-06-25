# V2 Batch38 Netmiko 确认执行流程

## 目标

Batch38 新增 Netmiko 只读命令确认执行流程。

该流程用于确保 AI 或后端生成的 CLI 命令不能直接执行，必须经过：

1. CLI Guard 只读安全校验。
2. 明确 human confirmation。
3. 只调用 Netmiko MCP 的 send_command_and_get_output。
4. 保存结构化审计记录。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/netmiko/executor.py | Netmiko 确认执行服务 |
| tools/regress_v2_netmiko_confirmed_execute.py | Batch38 回归脚本 |
| docs/v2_batch38_netmiko_confirmed_execute.md | Batch38 说明文档 |

## 安全流程

执行只读命令必须满足：

- command 通过 CLI Guard，status=passed。
- confirm_execute 必须严格等于 YES。
- confirmed_by 有明确操作者。
- 仅调用 send_command_and_get_output。
- 不调用 set_config_commands_and_commit_or_save。

## 默认首次验证命令

| 项目 | 值 |
|---|---|
| 设备 | SH16-A04-ACI-2001 |
| 设备类型 | cisco_nxos |
| 命令 | show clock |

## 审计记录

审计记录默认保存到：

/var/lib/netaiops-asset-agent/data/v2_netmiko_exec_audit

记录内容包括：

- execution_id
- plan
- guard结果
- confirmed状态
- confirmed_by
- executed_at
- output_preview
- error
- audit_path

## 后续批次

Batch39 建议开始建设 Trouble Session 与 Evidence Builder，把 CMDB、Prometheus、Netmiko 执行结果串成排障闭环。
