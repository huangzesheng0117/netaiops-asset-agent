# V2 Batch46 Web 前端测试准备

## 目标

Batch46 用于正式前端测试前的准备和冒烟验证。

本批不修改核心业务逻辑，不执行设备 CLI，不重启服务。

## 新增文件

| 文件 | 作用 |
|---|---|
| docs/v2_frontend_test_cases.md | Web 前端人工测试用例 |
| docs/v2_batch46_frontend_test.md | Batch46 说明文档 |
| tools/regress_v2_web_smoke.py | Web/API 冒烟回归脚本 |
| tools/v2_frontend_test_summary.sh | 前端测试前状态摘要脚本 |

## 冒烟覆盖范围

1. 服务健康检查。
2. V1 CMDB 查询。
3. V2 路由表问题进入 v2_chat_router。
4. V2 CPU 问题返回 Prometheus 当前 CPU 证据。
5. 对话式确认不带 YES 时不执行。
6. Netmiko safety_policy 正常。
7. Netmiko validate_commands 能区分 passed/review/blocked。

## 安全边界

Batch46 不执行设备 CLI。

其中“确认执行第1条命令”只测试不带 YES 的场景，预期状态为 pending_confirmation，不会触发 Netmiko MCP 执行。
