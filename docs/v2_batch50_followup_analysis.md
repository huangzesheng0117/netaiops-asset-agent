# V2 Batch50 Follow-up Analysis Router

## 目标

Batch50 让平台能够基于 V2 Conversation Context 回答后续分析追问。

用户不需要重复设备名，也不需要重复前文信息。

## 支持问题示例

    结合以上三点，给出更准确的结论，当前是否真的是CPU问题？
    如果CPU不高，下一步应该排查什么？
    还需要查Prometheus历史趋势吗？
    根据刚才结果，说明什么？
    这些结果能否说明设备本身有问题？

## 使用的上下文

- current_device
- current_topic
- last_prometheus_evidence
- last_executions
- last_analysis
- last_bulk_analysis
- rolling_summary
- recent_turns

## 安全边界

1. 本批回答追问时不执行设备 CLI。
2. 回答只读取已保存的 V2 上下文。
3. 真正执行命令仍必须通过确认执行流程。
4. 无足够上下文时返回 need_more_evidence。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/chat_v2/followup.py | V2追问分析路由 |
| tools/regress_v2_followup_analysis.py | Batch50回归脚本 |
| docs/v2_batch50_followup_analysis.md | Batch50说明文档 |

## 后续批次

Batch51：Rolling Summary 与多轮压缩增强。
