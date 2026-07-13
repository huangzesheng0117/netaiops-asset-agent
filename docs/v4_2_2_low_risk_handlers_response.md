# V4.2-2 低风险 Handlers 与统一 Response

## 1. 批次边界

本批在 V4.2-1 contracts 和 Canonical Context Store 基础上新增：

```text
general_chat handler
advice_analysis handler
need_clarification handler
low-risk action dispatcher
unified V4 response builder
atomic V4 audit writer
context/audit transaction orchestration
```

本批明确不做：

```text
app.py 接线
V4 前置 Entry Router 生产接管
analyze_existing_evidence 迁移
CMDB/MCP/设备命令执行
生产配置修改
systemd 修改
服务重启
生产 v4_context / v4_audit 写入
```

`analyze_existing_evidence` 按当前规划留到 V4.3-3，与 execution
evidence contract 一并迁移。

## 2. Action 来源

Handler 只接受已经通过 schema 的 `IntentDecision`。Dispatcher 只读取
`decision.action`，禁止从 question、context、snippet 或 raw_text 判断 action。

V4.2-2 允许 action：

```text
general_chat
advice_analysis
need_clarification
```

其他 action 写入阶段 audit 后返回显式 fallback：

```text
action_not_enabled_in_v4_2_2
```

该 fallback 是迁移阶段配置边界，不是本地自然语言分类。

## 3. Handler 与 Response

`general_chat` 和 `advice_analysis` 通过 adapter 复用现有 V3 response
generator，继续使用统一 LLMClient、GLM 5.2 token 下限和显式 thinking
disabled。

`need_clarification` 使用 Arbiter 返回的 `clarification_question`，不调用
LLM，也不从原问题本地猜测缺失字段。

统一 Response 固定：

```text
planner_source=v4_intent_arbiter
schema_version=v4.response.v1
handler_key
confidence
side_effect_started=false
fallback_used=false
audit_id
context_recorded
```

成功 answer 必须非空。Handler action 与 IntentDecision 不一致时拒绝构造
成功响应。

## 4. Context 与 Audit

低风险成功路径：

```text
load_or_migrate canonical context
-> handler
-> append deduplicated turn
-> build/write atomic audit
-> attach audit reference
-> build V4Response
-> EntryResult
```

Context read/write、audit write 和 audit-ref write 错误均进入
`EntryStatus.error`，并通过 `EntryResult.context` 暴露；不得静默返回成功。

AuditWriter：

- 独立 audit ID 文件；
- 同目录临时文件；
- file fsync；
- `os.replace`；
- directory fsync；
- secret 递归脱敏；
- 最大尺寸限制；
- permission/write/invalid 错误可观测。

## 5. 安全边界

所有 V4.2-2 action：

```text
side_effect_started=false
CMDB=NO
MCP=NO
Netmiko=NO
Prometheus=NO
设备命令=NO
```

本批测试使用临时 context/audit 目录和 fake LLM，不访问真实 LLM 或生产
数据目录。

## 6. 测试

```text
response schema 和 action 一致性
非空 answer
general/advice handler
deterministic clarification
wrong-action rejection
empty LLM answer rejection
question 不参与 action 选择
Context 完整事务
turn 去重
audit atomic write、replace 失败保留旧文件、权限分类与脱敏
context read/write 错误可见
audit write/ref 错误可见
unsupported action 显式 fallback
AST 架构扫描
V4.2-1 全量回归
GLM 5.2 回归
V3 response generator 回归
service identity isolated smoke
```

## 7. 生产与发布

```text
app.py=NO
systemd=NO
production config=NO
service restart=NO
online behavior=NO
production context/audit write=NO
```

离线和隔离测试全部通过后，精确暂存本批新增文件，commit、push，并使用
`git ls-remote` 核对远端 SHA。普通批次不创建或移动 tag。

下一批：

```text
V4.2-3：前置 Entry Router 接线与真实低风险切换
```
