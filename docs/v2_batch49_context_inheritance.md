# V2 Batch49 上下文读取与设备/主题继承

## 目标

Batch49 让 V2 Chat Router 在用户后续追问时读取 V2 Conversation Context，从而继承当前设备和当前排障主题。

## 支持的场景

第一轮：

    WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令

后续无需重复设备名：

    这个设备的路由表有多少条
    继续查看当前设备CPU，还需要哪些命令？
    继续给我下一批排查命令

系统会从上下文继承：

- current_device
- mgmt_ip
- device_type
- current_topic
- current_intent

## 安全边界

1. 本批不执行设备 CLI。
2. 本批只继承设备和主题，并生成建议命令。
3. 真正执行仍必须走确认执行流程。
4. 无上下文时仍会要求用户补充设备名或管理 IP。

## 新增/修改文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/chat_v2/router.py | 支持上下文继承 |
| tools/regress_v2_context_inheritance.py | Batch49 回归脚本 |
| docs/v2_batch49_context_inheritance.md | Batch49 说明文档 |

## 后续批次

Batch50：V2 Follow-up Analysis Router，基于上下文中的 last_analysis、last_executions、last_prometheus_evidence 回答分析追问。
