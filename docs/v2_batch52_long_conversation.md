# V2 Batch52 连续多轮端到端回归与前端冒烟测试增强

## 目标

Batch52 用于验证 V2 在真实 HTTP Chat API 下的连续多轮对话能力。

重点验证：

1. 第一轮明确设备和问题。
2. 第二轮使用“上述/刚才/这些命令”等上下文表达。
3. 未带 YES 时不执行命令。
4. 带 YES 后批量执行上一轮 passed 只读命令。
5. 后续多轮追问不回退 V1。
6. 后续问题可继承当前设备和当前主题。
7. Follow-up Analysis 能基于 last_executions、last_bulk_analysis、last_prometheus_evidence 和 rolling_summary 回答。
8. 上下文 recent_turns、active_focus、context_stats、rolling_summary 正常更新。

## 本批真实执行

回归脚本会执行一次确认后的批量只读命令：

    show system resources
    show processes cpu
    show processes cpu sort
    show logging last 100

这些命令必须由 V2 建议生成，并经过 CLI Guard 和 YES 确认流程。

## 回归覆盖的 12 轮对话

1. WG88-SW-H15-1 当前 CPU 异常，生成第一批排查命令。
2. 将上述命令执行并分析，不带 YES，预期 pending_confirmation。
3. 确认执行全部命令 YES。
4. 结合以上三点，判断是否真是 CPU 问题。
5. 如果 CPU 不高，下一步查什么，是否要查历史趋势。
6. 这个设备的路由表有多少条。
7. 继续查看当前设备 CPU，还需要哪些命令。
8. 根据刚才结果，还需要继续看 CPU 历史趋势吗。
9. 总结目前这个设备 CPU 排查结论。
10. 根据刚才结果，下一步重点看哪些日志。
11. 继续给我下一批排查命令。
12. 这些结果说明设备本身有问题吗。

## 前端冒烟测试建议

Batch52 通过后，Web 前端可以重点按同样 12 轮进行人工冒烟测试。

测试重点不是每个回答字句完全一致，而是：

1. 不要突然要求重新输入设备名。
2. 不要在第 4 轮以后回退到 V1 CMDB 查询。
3. 不要把“上述/刚才/这些结果”理解成新问题。
4. 不带 YES 不执行命令。
5. 带 YES 后只执行 passed 只读命令。
6. 批量执行后能继续基于上下文追问。
7. 页面上能看到综合结论、建议下一步和上下文延续提示。

## 安全边界

1. 本批不新增任何配置修改能力。
2. 命令执行仍由确认执行流程控制。
3. review 和 blocked 命令不会批量执行。
4. 仍不暴露 Netmiko MCP 配置工具。
