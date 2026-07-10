# V4.1 GLM 5.2 兼容性说明

## 1. 批次范围

V4.1-2 只修改 ChatBot 侧模型兼容代码并执行离线测试，不重启
`netaiops-asset-agent.service`，不改变当前服务进程的线上行为。

本批不修改生产 `/etc/netaiops-asset-agent/config.yaml`。生产模型切换、
服务重启、真实 `/models`、`/probe`、Chat 和回归统一放到 V4.1-3。

## 2. 兼容修改

- `LLMClient.probe()` 使用 `max(1200, configured max_tokens)`，并显式发送 `thinking={"type":"disabled"}`；
- `LLMClient.chat()` 在成功前校验 choices、message 和非空 content；
- 空 content 返回 `LLM_EMPTY_CONTENT`；
- 增加 requested/reported model、finish reason、max tokens 和 content length；
- 不把 `reasoning_content` 当作最终答案；
- V3 response generator 不再固定 900，并显式发送 `thinking={"type":"disabled"}`；
- V2 planner/evidence analyzer 移除 `qwen3-max` 静默默认值；
- V2 直接 LLM 调用使用至少 1200 的输出预算；
- Batch67 legacy advice 分支收敛到统一 `LLMClient.chat()`；
- legacy advice 请求显式发送 `thinking={"type":"disabled"}`，并使用 `response_format=False`；
- legacy advice 返回 requested/reported model、finish reason、token budget 和 content length 观测；
- “不要生成命令”等否定约束不再单独把普通解释问题误判为 advice。

## 3. 离线测试

`tests/test_llm_glm52_compat.py` 使用 mock HTTP，不访问真实 LLM 网络，覆盖：

1. 配置 32/900 时 probe 使用 1200；
2. 配置 1800 时保留 1800；
3. stop + 非空 content；
4. length + 空 content；
5. reasoning-only；
6. choices/message 缺失；
7. model 缺失；
8. requested/reported model 和 finish reason；
9. V3 generator token floor；
10. V2 不再猜测 `qwen3-max`；
11. legacy advice 使用统一 `LLMClient.chat()`；
12. legacy advice 显式关闭 thinking/JSON mode；
13. probe 最终 HTTP payload 显式关闭 thinking；
14. V3 response generator 调用显式关闭 thinking。
13. 普通解释加“不要生成命令”不会被 advice 路由抢占；
14. advice 空正文错误保留 finish reason 和模型观测。

统一检查入口：

```bash
/opt/netaiops-asset-agent/venv/bin/python -B \
  /opt/netaiops-asset-agent/tools/check_llm_glm52_compat.py
```

## 4. 发布边界

V4.1-2 成功后允许工作区保留预期未提交变更。V4.1-3 才执行：

- 生产配置切换到 `glm-5.2`；
- 服务重启与 ready 轮询；
- `/api/v1/llm/config`、`/models`、`/probe` 和真实 Chat；
- V1/V2/V3 回归；
- 精确暂存、commit、push 和远端 SHA 核验。
