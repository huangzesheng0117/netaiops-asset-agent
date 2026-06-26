# ChatBot V3.3-15 Takeover Preflight Guardrails

## 目标

V3.3-15 的目标不是立即开启真实接管，而是在 V3.3-14 已完成 live LLM shadow dry-run 的基础上，建设真实接管前的预检工具与稳定边界。

本批次坚持：

- `NETAIOPS_V3_TAKEOVER_ENABLED=0`
- 不修改 `app.py`
- 不重启服务
- 不让 `/api/v1/chat` 的真实响应来源变成 `v3_takeover` 或 `v3_response_generator`
- 只通过 shadow 日志判断哪些场景已经具备候选接管条件

## 验证内容

工具 `tools/v3_3_15_takeover_preflight.py` 会执行以下安全验证：

1. 发起多类真实 `/api/v1/chat` 请求。
2. 确认 HTTP 200。
3. 确认真实 API 响应仍然不是 V3 接管。
4. 确认 shadow 日志增长。
5. 确认 `v3_plan` / `v3_decision` 均已归一化为 dict。
6. 确认 runtime gate 仍保持 disabled。
7. 确认 live LLM response generator 在适合场景中可生成 shadow 候选答案。
8. 输出候选接管统计，但不改变线上行为。

## 成功标志

```text
v3_3_15_preflight_api=OK
v3_3_15_no_real_takeover=OK
v3_3_15_preflight_summary=OK
```

## 后续节奏

- V3.3-15：接管前预检与候选统计。
- V3.3-16：极窄范围真实接管开关设计与最小闭环。
- V3.3-17：接管范围扩大到低风险文本/分析类场景。
- V3.3-18：V3.3 收口、文档、回归、Git tag。
