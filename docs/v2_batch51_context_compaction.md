# V2 Batch51 Rolling Summary 与多轮压缩增强

## 目标

Batch51 用于增强 V2 Conversation Context 的长期多轮对话能力，使平台不依赖完整原文上下文，也能支撑二三十轮连续排障对话。

## 新增能力

1. active_focus

记录当前排障焦点：

    device_name
    mgmt_ip
    device_type
    topic
    intent
    last_action_intent

2. resolved_findings

保存已经确认的结构化发现，例如：

    Prometheus 当前 CPU 指标已命中
    已执行 4 条只读命令
    当前证据不支持设备整体 CPU 高负载

3. open_questions

保存后续待确认问题，例如：

    是否需要查询 Prometheus 历史趋势
    是否需要继续分析日志
    是否需要排查接口错误或协议震荡

4. context_stats

记录上下文压缩状态，例如：

    recent_turns_count
    last_command_suggestions_count
    last_executions_count
    open_questions_count
    resolved_findings_count
    rolling_summary_chars

5. Rolling Summary 增强

rolling_summary 现在包含：

    当前设备
    当前焦点
    当前排障主题
    最近 Prometheus 证据
    最近建议命令
    最近执行命令
    最近批量分析
    最近追问分析
    已确认发现
    待继续确认
    最近对话轮次

## 压缩策略

默认保留：

    recent_turns: 最近 30 轮
    last_command_suggestions: 最近 30 条
    last_executions: 最近 50 条
    open_questions: 最近 30 条
    resolved_findings: 最近 30 条
    rolling_summary: 最多 12000 字符

## 安全边界

Batch51 不执行设备 CLI。

回归脚本使用合成 V2 响应来验证上下文压缩逻辑，不调用 Netmiko MCP。

## 后续批次

Batch52：连续 10～20 轮端到端回归与前端冒烟测试用例增强。
