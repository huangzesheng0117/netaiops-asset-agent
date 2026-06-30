# ChatBot V3.4-2 Legacy Route Registry

## 目标

V3.4-2 建立 Legacy Route Registry，用结构化方式描述 V2 旧路由类型、风险等级、可映射的 V3 action 以及当前阶段是否允许作为 takeover candidate。

本批不修改 app.py，不重启服务，不改变线上行为。

## 背景

V3.4-1 已完成旧路由盘点与地图，确认 app.py 中仍存在大量旧路由信号，包括 advice_analysis、batch_route、cmdb_query、followup、inline_command、semantic_route 等。

V3.4-2 不直接收敛这些旧分支，而是先建立统一 registry，后续 V3.4-3 到 V3.4-6 再逐批把旧分支接入 registry 或替换重复判断。

## 新增文件

| 文件 | 作用 |
| --- | --- |
| netaiops_asset/chat_v3/legacy_route_registry.py | Legacy Route Registry 主模块 |
| tools/v3_4_2_legacy_route_registry_check.py | Registry 单元与 inventory 分类校验工具 |
| docs/v3_4_2_legacy_route_registry.md | 本批说明文档 |

## route_type

V3.4-2 定义以下 route_type：

| route_type | 含义 | 当前处理策略 |
| --- | --- | --- |
| general_chat | 普通文本解释类 | 可映射 general_chat |
| advice_analysis | 建议、排查思路、风险分析类 | 可映射 advice_analysis |
| followup | 多轮上下文追问类 | 暂不在 V3.4-2 接管，留到 V3.4-4 |
| cmdb_query | CMDB 或设备资产查询类 | 暂不接管，继续 fallback |
| command_explanation | 命令解释类 | 识别但暂不接管，留到 V3.4-5 |
| command_execution | 真实执行命令类 | 不接管 |
| config_change | 配置变更类 | 不接管 |
| inline_command | inline 命令类泛型 | 不接管 |
| semantic_route | 语义路由旧分支 | 只登记，不直接接管 |
| batch_route | batchXX 历史分支 | 只登记，不直接接管 |
| unknown | 未识别类型 | 不接管 |

## V3.4-2 takeover candidate 边界

V3.4-2 只把以下类型标记为 takeover candidate：

```text
general_chat
advice_analysis
```

并且必须满足：

```text
risk_level = low
v3_action in general_chat,advice_analysis
canary_triggered = true
```

注意：当前平台没有真实用户体系。这里的 canary_triggered 仍然只能理解为请求 payload 字段和 conversation_id 前缀组成的技术触发条件，不是用户灰度。

## 非目标

本批不做以下事情：

- 不修改 app.py
- 不修改 v2_chat_router_middleware
- 不修改 /api/v1/chat return path
- 不扩大 V3 takeover 环境变量
- 不新增 command splitter
- 不修改命令执行安全逻辑
- 不删除旧路由分支
- 不重启 netaiops-asset-agent.service

## 校验方式

本批校验包括：

1. py_compile registry 模块
2. py_compile registry check 工具
3. 重跑 V3.4-1 inventory 到临时 report 目录
4. Registry 单元用例覆盖：
   - general_chat
   - advice_analysis
   - followup
   - cmdb_query
   - command_explanation
   - command_execution
   - config_change
5. Registry 对 V3.4-1 inventory 结果做分类
6. 校验 app.py SHA256 前后一致
7. 校验服务 active
8. 校验 git diff --check 和 git diff --cached --check
9. 只提交本批预期三个文件

## 后续批次

V3.4-3 可以基于本 registry 收敛 general_chat / advice_analysis 类旧分支。

V3.4-4 再收敛 follow-up / 多轮上下文类旧分支。

V3.4-5 再处理 inline 抢路由，但不进入 V3.5 的 command splitter。

V3.4-6 再删除或禁用重复旧分支。
