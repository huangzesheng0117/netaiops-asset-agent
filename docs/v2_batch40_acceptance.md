# V2 Batch40 阶段性总体验收说明

## 目标

Batch40 对 V2 Batch33-Batch39 的底层能力进行阶段性总体验收。

本批不接入 app.py，不重启服务，不提交 Git，不执行新的设备 CLI 命令。

## 已覆盖能力

1. MCP SSE + JSON-RPC 连通性。
2. Netmiko MCP 工具发现与设备清单读取。
3. Prometheus MCP 健康检查、即时查询和 Prometheus targets 直连查询。
4. 设备身份解析：CMDB、Netmiko inventory、Prometheus label candidate。
5. PromQL Guard：拦截裸高基数指标，允许受控查询。
6. CLI Guard：区分 passed、review、blocked。
7. Netmiko 确认执行安全流程：未确认时阻断，危险命令阻断。
8. Trouble Session 与 Evidence Builder：汇总身份、Prometheus、Netmiko审计证据。

## 安全说明

Batch40 不执行新的设备 CLI。

Netmiko 相关验收只做：

- 读取 MCP inventory。
- 校验命令文本。
- 构造执行计划。
- 验证未确认时阻断。
- 读取已有 Batch38 审计文件。

## 新增文件

| 文件 | 作用 |
|---|---|
| tools/regress_v2_acceptance.py | V2阶段性总体验收脚本 |
| tools/v2_status_check.sh | V2状态检查脚本 |
| docs/v2_batch40_acceptance.md | Batch40说明文档 |

## 后续建议

Batch40 通过后，建议下一步进行：

1. 汇总 Git diff。
2. 更新 README 的 V2 阶段能力说明。
3. 执行最终安全检查。
4. 由你确认后再进行 Git commit / push。
