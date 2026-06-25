# V2 Batch34 MCP Client 封装说明

## 目标

Batch34 新增 ChatBot 后端可复用的 MCP Client 封装，为后续 Prometheus 查询、Netmiko 只读取证和排障会话闭环提供基础能力。

本批不接入 app.py，不新增对外 API，不重启线上服务。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/mcp/__init__.py | MCP 包初始化 |
| netaiops_asset/mcp/client.py | 通用 SSE + JSON-RPC MCP Client |
| netaiops_asset/mcp/netmiko_client.py | Netmiko MCP 上层封装 |
| netaiops_asset/mcp/prometheus_client.py | Prometheus MCP 上层封装 |
| tools/regress_v2_mcp_client.py | Batch34 回归脚本 |

## 安全边界

1. 通用 MCP Client 只负责协议通信，不负责判断命令是否安全。
2. Netmiko 上层封装不暴露配置变更工具。
3. Netmiko send_command_after_guard 需要 guard_status=passed 且 confirmed=True 才允许调用。
4. Batch34 回归脚本不会执行任何设备 CLI。
5. Prometheus list_metrics 默认限制 limit，避免全量高基数扫描。
6. targets/scrape 状态通过 Prometheus 直连地址查询，不依赖 VictoriaMetrics。

## 后续批次

Batch35 建议开始做设备身份解析层，把 CMDB hostname、mgmt_ip、Netmiko device name、Prometheus label 候选关系串起来。
