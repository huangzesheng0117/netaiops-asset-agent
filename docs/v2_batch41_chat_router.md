# V2 Batch41 Chat API 意图分流与 Netmiko 命令建议

## 目标

Batch41 将 V2 能力接入 `/api/v1/chat` 主对话入口。

本批解决的问题：

- 用户在前端提问排障/取证类问题时，不再只返回 CMDB 资产表。
- 系统会识别路由表、CPU、接口、BGP、BFD 等取证类意图。
- 系统会先解析设备身份，再生成 Netmiko 只读命令建议。
- 每条建议命令都经过 CLI Guard 校验。
- 本批不会自动执行设备 CLI。

## 支持的首批 V2 意图

| 意图 | 示例 |
|---|---|
| route_table | 某设备路由表有多少条 |
| cpu_check | 某设备当前CPU利用率怎么排查 |
| interface_check | 某设备接口状态怎么查 |
| bgp_check | 某设备BGP邻居状态怎么查 |
| bfd_check | 某设备BFD状态怎么查 |

## 安全边界

Batch41 只生成命令建议，不执行命令。

真正执行 Netmiko 命令仍需后续确认执行流程：

1. CLI Guard 返回 passed。
2. 工程师明确确认。
3. 后端调用 ConfirmedNetmikoExecutor。
4. 保存审计。

## 后续批次

Batch42 建议接入确认执行接口和前端确认交互，使用户可以在页面上确认执行 passed 命令。
