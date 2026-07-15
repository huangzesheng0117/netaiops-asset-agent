# V4.2-3 前置 Entry Router 接线与真实低风险切换

## 1. 批次目标

本批把 V4 Entry Router 接到 `/api/v1/chat` 的 V2/legacy 业务分支之前，并真实启用：

```text
general_chat
advice_analysis
need_clarification
```

其他 action 仍显式 fallback 到现有 V2/V3 路径。V4.2-3 不迁移 CMDB、命令生成、命令执行、pending 或 existing-evidence handler。

## 2. 入口顺序

```text
POST /api/v1/chat
-> V4 Entry Router
-> read-only canonical/legacy context snapshot
-> LLM Intent Arbiter
-> confidence/device/evidence deterministic gates
-> low-risk dispatcher
-> V4 response/audit/canonical context
-> legacy history bridge
-> frontend response
```

高置信度非低风险 action：

```text
V4 Entry Router
-> explicit stage fallback
-> reuse the same Arbiter decision as V3 shadow state
-> existing V2/V3 route
```

不会因为 fallback 再调用一次 Arbiter。

## 3. Intent 边界

Action 只能来自 `IntentDecision.action`。Entry Router 不从 question、context、snippet、raw_text、关键词或正则判断 action。

本地确定性逻辑仅处理：

- V4 stage allowed actions；
- effective confidence；
- `device_required` 但缺设备；
- `need_existing_evidence` 但无 evidence；
- canonical context 不可用；
- technical fallback；
- transport/history adapter。

低置信度、缺设备、缺 evidence、JSON/schema clarification 均转 `need_clarification`。LLM transport/client failure 才允许技术 fallback。

## 4. 生产开关

systemd drop-in：

```text
NETAIOPS_V4_ENTRY_ENABLED=1
NETAIOPS_V4_ENTRY_ALLOWED_ACTIONS=general_chat,advice_analysis,need_clarification
NETAIOPS_V4_ENTRY_LIVE_LLM=1
NETAIOPS_V4_ENTRY_MIN_CONFIDENCE=0.80
```

删除或将 `NETAIOPS_V4_ENTRY_ENABLED=0` 后重启，可关闭 V4 前置入口并恢复原 V2/V3 路径。

## 5. Context 与 Audit

低风险请求：

```text
Canonical Context read/lazy migration
-> handler
-> context turn
-> atomic V4 audit
-> audit ref
-> legacy conversation history
```

V4 context 和 audit 由服务身份 `netaiops` 写入：

```text
/var/lib/netaiops-asset-agent/data/v4_context
/var/lib/netaiops-asset-agent/data/v4_audit
```

legacy history 写入失败时不 fallback，不重复调用 LLM；响应改为显式 error，并把失败写回同一 V4 audit。

## 6. 兼容与回退

- V4 disabled：直接走原路径；
- unsupported action：stage fallback；
- Arbiter transport/client failure：technical fallback；
- invalid JSON/schema、低 confidence、缺设备/evidence：V4 clarification；
- dispatcher/context/audit/response/history 失败：V4 visible error，不静默 fallback；
- 空 `question` 仅在 Arbiter action 为 `need_clarification` 时允许进入 Dispatcher；
- 空 `question` 搭配其他 action 继续由 Dispatcher 明确拒绝；
- App Bridge import/初始化失败属于技术 fallback，路由内部异常返回显式 V4 error；
- V4 handled 后不会进入 Batch67、V2 inline/semantic/chat 或 V3 return takeover。

## 7. 本批改动

```text
M app.py
M netaiops_asset/chat_v4/__init__.py
M netaiops_asset/chat_v4/action_dispatcher.py
M netaiops_asset/chat_v4/handlers/base.py
M tests/test_v4_low_risk_dispatcher.py
M tools/check_v4_low_risk_architecture.py
A netaiops_asset/chat_v4/entry_router.py
A netaiops_asset/chat_v4/app_bridge.py
A tests/test_v4_entry_router.py
A tests/test_v4_app_bridge.py
A tests/test_v4_pre_route_integration.py
A tests/test_v4_low_risk_architecture_checker.py
A tools/check_v4_entry_router_architecture.py
A docs/v4_2_3_entry_router_live_switch.md
```

生产配置 `config.yaml` 不修改。新增 systemd drop-in并重启服务。

## 7.1 生产权限与发布身份

生产源码保持 `netaiops:netaiops` 所有权。本批由 `baoleiji` 启动脚本并完成 Git 写入；工作树文件通过 root selective sudo 精确安装：

```text
app.py = netaiops:netaiops 0640
chat_v4/__init__.py、action_dispatcher.py、handlers/base.py = netaiops:netaiops 0644
tests/test_v4_low_risk_dispatcher.py 与 tools/check_v4_low_risk_architecture.py = 保持生产现场原 UID/GID/mode
本批新增运行时源码 = netaiops:netaiops 0644
本批新增测试、工具和文档 = baoleiji:netaiops 0644
```

不执行递归 `chown/chmod`，不设置持久 ACL，不修改 global/system `safe.directory`。root 不执行 `git add/commit/push`，不创建 Git object。回退使用同样的精确 owner/group/mode 恢复。

## 7.2 架构扫描器修正

旧扫描器只要同一条件同时引用 `normalized_question` 与 `intent/action`，就误判为本地 Intent 分类。
本批改为检查 question-tainted 条件或表达式是否真正执行 action/handler/route 选择：

- 仅做空输入、长度或格式校验并 `raise`：允许；
- 根据 question 内容赋值 action、选择 handler、返回固定 handler 或映射 route：阻断。

扫描器新增独立正反例测试，避免再次用宽泛字符串或名称共现代替真实 AST 行为检查。

## 8. 验收

```text
V4.2-1 / V4.2-2 regression
entry-router unit tests
empty question + need_clarification dispatcher transaction
HandlerRequest allows empty question only for need_clarification
empty question + non-clarification rejection
real Entry Router -> Dispatcher -> Clarification Handler
real App Bridge empty-question unified response
internal exception visible V4 error, no legacy fallback
app bridge tests
AST pre-route ordering
no local action classifier
architecture checker allows validation-only empty-question guard
architecture checker rejects real question-driven action selection
service runtime import
systemd environment
health glm-5.2
general_chat live
general_chat follow-up
advice_analysis live
need_clarification live
unsupported action fallback
canonical context and audit
legacy history
no Netmiko execution
journal error scan
commit/push/remote SHA/clean
```

## 9. 后续

V4.3-1 迁移：

```text
cmdb_query
generate_commands
```

此前不会扩大 V4 side-effect action。

## Final checker and delivery convergence

The final V4.2-3 delivery validates the complete target worktree before any
production source mutation. The low-risk architecture checker distinguishes
question-shape validation and error-response construction from actual
question-driven action or handler selection. Its regression suite includes the
complete transformed dispatcher plus positive and negative AST fixtures.

## Architecture checker responsibility boundary

The V4.2-3 Entry Router checker validates the real AST call and response
contracts. It does not require an implementation detail string to be repeated
in a caller when that responsibility belongs to a delegated bridge function.

The validated internal-error chain is:

```text
app.py::_v4_try_pre_route
-> build_v4_internal_error_transport
-> visible handled V4 error
-> V4 audit attempt
-> fallback=false
-> no legacy business route replay
```

Patch marker counts are used only for idempotency. Business success is proven by
AST call ordering, bridge payload/route/audit contracts, complete-target-tree
unit tests, production live checks, context/audit checks and Git closeout.
