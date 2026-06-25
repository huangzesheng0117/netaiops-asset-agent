# V2 Batch48 Conversation Context Store

## 目标

Batch48 新增 V2 会话上下文存储能力，为后续多轮追问理解打基础。

本批只解决“把上下文存起来”，不做复杂追问推理。

## 新增能力

每轮 V2 响应后，写入结构化上下文：

- current_device
- current_topic
- current_intent
- last_prometheus_evidence
- last_command_suggestions
- last_executions
- last_analysis
- last_bulk_analysis
- rolling_summary
- recent_turns

## 数据目录

默认目录：

    /var/lib/netaiops-asset-agent/data/v2_conversation_context

## 新增接口

调试接口：

    GET /api/v1/v2/context?conversation_id=xxx
    GET /api/v1/v2/context?user=baoleiji

该接口只读上下文 JSON，不执行任何设备命令。

## 安全边界

1. Batch48 不执行设备 CLI。
2. 只写入本地 JSON 上下文。
3. 后续 Batch49 才会基于上下文做设备和主题继承。
4. 后续 Batch50 才会基于上下文做 follow-up analysis。

## 后续批次

Batch49：上下文读取与设备/主题继承。
