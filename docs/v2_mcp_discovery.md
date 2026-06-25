# NetAIOps AI资产查询平台 V2 Batch33 MCP工具发现记录

## 1. 目标

本文件记录 V2 Batch33 阶段对 Netmiko MCP 与 Prometheus MCP 的连通性、协议形态、工具清单、入参结构、基础 tools/call 返回格式和已知限制的发现结果。

Batch33 只做检查和发现，不改业务核心代码，不执行设备 CLI 命令，不重启 ChatBot 服务。

## 2. MCP 基础信息

| 项目 | 地址 |
|---|---|
| MCP 服务器 | 10.191.97.137 |
| Netmiko MCP SSE | http://10.191.97.137:10000/sse |
| Prometheus MCP SSE | http://10.191.97.137:10001/sse |
| Prometheus MCP 后端数据源 | http://10.191.198.9:8481/select/0/prometheus |
| Prometheus 直连保留地址 | http://10.191.96.43:9090 |

## 3. 协议形态

两个 MCP 均为标准 SSE + JSON-RPC 形态。

访问 /sse 后返回类似内容：

    event: endpoint
    data: /messages/?session_id=...

后续 JSON-RPC 请求通过 /messages/?session_id=... POST 发送。

初始化流程：

1. 建立 SSE 连接。
2. 等待 event: endpoint。
3. 向 endpoint POST initialize。
4. 发送 notifications/initialized。
5. 调用 tools/list 或 tools/call。

## 4. Netmiko MCP 发现结果

初始化结果：

| 项目 | 值 |
|---|---|
| serverInfo.name | netmiko server |
| serverInfo.version | 1.26.0 |
| protocolVersion | 2024-11-05 |

工具清单：

| 工具名 | 用途 | 入参 | V2处理策略 |
|---|---|---|---|
| get_network_device_list | 获取 MCP 纳管设备清单 | 无 | 允许，只读 |
| send_command_and_get_output | 向指定设备发送命令并返回输出 | name, command | 只允许通过 ChatBot 后端只读校验与人工确认后调用 |
| set_config_commands_and_commit_or_save | 发送配置命令并提交或保存 | name, commands | 禁止在 ChatBot V2 中暴露和调用 |

重要安全结论：

- V2 只应封装 get_network_device_list 和 send_command_and_get_output。
- set_config_commands_and_commit_or_save 属于配置变更工具，必须在 ChatBot 侧屏蔽。
- 即使 send_command_and_get_output 是执行单命令工具，也必须增加后端只读校验与工程师二次确认。

## 5. Prometheus MCP 发现结果

初始化结果：

| 项目 | 值 |
|---|---|
| serverInfo.name | Prometheus MCP |
| serverInfo.version | 3.2.0 |
| health_check.service | prometheus-mcp-server |
| health_check.version | 1.6.1 |
| transport | sse |
| prometheus_connectivity | healthy |

工具清单：

| 工具名 | 用途 | 入参 | V2处理策略 |
|---|---|---|---|
| health_check | 健康检查 | 无 | 允许 |
| execute_query | PromQL 即时查询 | query, time | 允许，但必须加 PromQL guard |
| execute_range_query | PromQL 区间查询 | query, start, end, step | 允许，但必须限制时间范围、step 和序列数量 |
| list_metrics | 指标枚举 | limit, offset, filter_pattern, refresh_cache | 允许，必须分页和限制 limit |
| get_metric_metadata | 指标元数据 | metric, filter_pattern, limit, offset | 允许 |
| get_targets | targets 信息 | 无 | 当前通过 VictoriaMetrics 返回 400；targets/scrape 状态应走 Prometheus 直连 |

已验证能力：

- health_check 正常。
- list_metrics 正常，当前返回 total_count=3804。
- execute_query 查询 up 正常。
- get_targets 通过 VictoriaMetrics 地址返回 400，属于预期限制，不作为 Prometheus MCP 整体失败。

## 6. 错误格式

调用不存在的工具时，MCP 返回 tools/call 级别成功，但 tool result 内部 isError=true，content 中返回错误文本，例如：

    Unknown tool: __netaiops_nonexistent_tool__

因此 V2 MCP Client 不能只判断 HTTP 202 或 JSON-RPC 是否有 result，还必须检查：

- result.content
- result.isError
- content[].text 中的错误信息

## 7. V2后续设计依据

Batch34 可以开始封装通用 MCP Client，建议实现以下能力：

- 建立 SSE session。
- 自动读取 endpoint。
- 完成 initialize / initialized。
- 执行 tools/list。
- 执行 tools/call。
- 统一返回结构：ok、is_error、tool_name、raw_result、summary、error。
- 超时控制。
- 最大输出长度控制。
- 对 Prometheus 和 Netmiko 分别封装上层 client。

## 8. 已知风险与限制

1. ChatBot 项目目录当前普通账号 baoleiji 无法直接进入，后续开发命令需要使用 sudo 或调整目录权限。
2. Prometheus MCP 当前后端主数据源为 VictoriaMetrics，不适合查询 /api/v1/targets。
3. Netmiko MCP 暴露了配置变更工具，ChatBot V2 必须主动屏蔽。
4. Prometheus 指标数量较多，V2 必须限制 list_metrics、execute_query、execute_range_query 的范围，避免高基数全量查询。
5. Netmiko 命令执行必须坚持：LLM 只生成建议，后端只读校验，工程师确认后才执行。

## 9. Batch33结论

Batch33 目标已基本满足：

- ChatBot 服务器可访问 Netmiko MCP 与 Prometheus MCP。
- 两个 MCP 均确认为 SSE + JSON-RPC 协议形态。
- 已枚举实际工具名和入参结构。
- 已验证 tools/call 调用格式。
- 已确认 Prometheus MCP 查询能力可用。
- 已确认 Netmiko MCP 设备清单能力可用。
- 已确认错误返回格式。
- 已发现并记录 get_targets 通过 VictoriaMetrics 不可用的问题。

下一步进入 Batch34：封装通用 MCP Client。
