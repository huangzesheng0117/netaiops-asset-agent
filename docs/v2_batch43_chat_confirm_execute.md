# V2 Batch43 对话式确认执行

## 目标

Batch43 让用户可以在同一个 ChatBot 对话框中完成 V2 命令建议与确认执行。

## 使用方式

第一轮提问示例：

    SH8-G03-DCI-BN-SW01目前CPU利用率，我该通过哪些命令去排查？

系统返回 V2 建议命令列表，例如：

    第1条：show system resources
    第2条：show processes cpu
    第3条：show processes cpu sort
    第4条：show logging last 100

第二轮确认执行示例：

    确认执行第1条命令 YES

## 安全边界

1. 用户必须先生成 V2 建议命令。
2. 后端保存上一轮待确认命令。
3. 只有 guard_status=passed 的命令可执行。
4. 确认语句必须包含 YES。
5. 执行仍然走 ConfirmedNetmikoExecutor。
6. 每次执行都会生成审计文件。
7. review/blocked 命令不会执行。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/chat_v2/confirmation.py | 对话式确认执行逻辑 |
| tools/regress_v2_chat_confirm_execute.py | Batch43回归脚本 |
| docs/v2_batch43_chat_confirm_execute.md | Batch43说明文档 |

## 本批真实执行验证

Batch43 回归脚本会先通过 /api/v1/chat 生成 CPU 排查建议命令，然后分别验证：

1. 只输入“确认执行第1条命令”时，不执行。
2. 输入“确认执行第1条命令 YES”时，执行第 1 条通过校验的只读命令。
3. 默认真实执行命令为：

    show system resources

## 后续批次

Batch44 建议增强 V2 综合回答能力：执行完成后，将命令输出交给 LLM 做简短分析，而不是只返回原始输出预览。
