# ChatBot V3.4-4 Follow-up Context Convergence

## 目标

V3.4-4 将 follow-up 旧分支收敛到 LLM Intent Arbiter 驱动的
`analyze_existing_evidence` action，并继续保留 V2 follow-up 逻辑作为
fallback。

本批不是全量请求切换，也不是按真实用户灰度。当前仍通过请求 payload
中的 `user` 字段和 `conversation_id` 前缀作为后端 canary trigger。

## 架构边界

主路由必须保持：

```text
当前问题 + 结构化历史上下文
        -> LLM Intent Arbiter
        -> structured action=analyze_existing_evidence
        -> context availability / confidence / safety gate
        -> V3 response generator
        -> route-return takeover
```

禁止：

```text
question -> FOLLOWUP_KEYWORDS / regex / token table -> action
question -> classify_followup(question) -> V3 route
Legacy Route Registry -> parse user natural language
```

V2 中既有 follow-up 关键词逻辑仅作为 fallback 底座保留，不参与 V3 主意图裁决。

## 上下文桥接

新增 `netaiops_asset/chat_v3/followup_context.py`：

1. 使用原始请求 `conversation_id` 读取 V2 context、legacy conversation store
   和 V3 follow-up context store。
2. 将设备、主题、最近轮次、执行证据和 rolling summary 归一化为结构化 context。
3. 将结构化 context 提供给 LLM Intent Arbiter 和 V3 response generator。
4. V3 成功返回后，以原始请求 `conversation_id` 保存精简轮次，避免 legacy
   `append_turn()` 生成新 UUID 后丢失上下文关联。
5. 模块不判断用户意图、不调用 CMDB/MCP、不执行设备命令。

V3 follow-up context 目录：

```text
/var/lib/netaiops-asset-agent/data/v3_followup_context
owner = netaiops:netaiops
mode = 750
```

## 接管条件

`analyze_existing_evidence` 仅在以下条件同时满足时接管：

- LLM Intent Arbiter 输出 `action=analyze_existing_evidence`；
- effective confidence 达到 canary 阈值；
- 原始 conversation_id 命中 canary trigger；
- 结构化 follow-up context 可用；
- response generator 真实返回 `source=llm`；
- response readiness 通过。

上下文不可用时，V3 不生成虚构回答，返回 V2 fallback，并在 audit 中记录：

```text
reason=followup_context_unavailable
```

## Audit 字段

V3.4-4 新增或明确记录：

```text
original_conversation_id
effective_conversation_id
followup_context_source
followup_context_available
followup_context_turn_count
followup_context_topic
followup_context_has_execution_evidence
followup_context_store_path
followup_context_store_turn_count
followup_context_store_error
```

## 当前 canary action

```text
general_chat
advice_analysis
analyze_existing_evidence
```

命令生成、命令执行、确认执行和配置变更仍不属于本批接管范围。

## 回退

任一环节失败时继续返回原 V2 response。`app.py`、V3 模块、systemd drop-in
和服务均应按本批备份回退；Git commit/push 只在全部 smoke 和 audit 验证通过后执行。

## V3.4-4-2-fix1：V2/non-taken return-path observation

V3.4-4-2 的首次真实 API smoke 证明，follow-up context 不能只在
V3 takeover 成功后写入。第一轮可能合法地返回 V2 fallback 或其他
non-taken response；若这些返回不保存，下一轮即使 LLM Intent Arbiter
输出 `analyze_existing_evidence`，也无法取得上一轮内容。

fix1 将上下文保存统一收口到 canary return wrapper 的 `_finish()`：

```text
eligible canary return
        -> validate response / answer / original conversation_id
        -> record return-path observation
        -> write takeover audit
        -> return response
```

保存行为不判断用户意图。主路由仍由 LLM Intent Arbiter 的结构化 action
决定。V2 follow-up 规则仍只作为 fallback 底座。

记录来源：

```text
context_record_source=v2_or_non_taken_return
context_record_source=v3_taken_return
```

错误、空回答、空问题和缺少原始 conversation_id 的返回不会写入。

`record_v3_turn()` 使用原始请求 conversation_id 作为存储键，并保存
effective conversation_id 作为审计信息。相邻的重复 return-path observation
通过稳定 fingerprint 去重，避免同一返回被多次包装时重复写入。

Audit / response 可观测字段：

```text
context_record_attempted
context_recorded
context_record_source
context_record_skip_reason
followup_context_store_path
followup_context_store_turn_count
followup_context_store_deduplicated
followup_context_turn_fingerprint
followup_context_store_error
```

版本标识：

```text
v3.4.4-2-fix1
```

## V3.4-4-2-fix2：运行时模块权限闭环

真实 systemd 请求暴露的 `PermissionError(13)` 并非数据目录不可写，
而是新增运行时模块 `followup_context.py` 对服务进程不可读。目录原子写入
探针已证明 context、audit 和 shadow 三个目录均可由服务身份写入。

本批按同目录已工作的 V3 模块归一化 owner/group/mode，并以实际服务
UID/GID 完成 direct source import、package import、context write/read/delete。

修复后模块元数据：

```text
target=netaiops:netaiops 644
peer=netaiops:netaiops 644
```

随后重新执行 fix1 的 non-taken return observation、三条 return path 回归、
真实双轮 API takeover、audit、commit 和 push 闭环。

权限修复不参与用户意图判断，不改变 LLM Intent Arbiter 主路由边界。

## V3.4.4 Closeout-1：基线审计与文档封存

执行时间：

```text
2026-07-07T16:12:44+08:00
```

本批对 V3.4.4 当前基线进行了只读运行状态、Git 远端、运行时权限、
离线回归和文档状态审计，并新增独立 closeout 文档。

确认结果：

```text
local_head=1a3745bf5adce87845ee43dad72e3fd56cbb67f7
remote_main_sha=1a3745bf5adce87845ee43dad72e3fd56cbb67f7
service_active=OK
health_http_code=200
health_reported_llm_model=qwen3-max
model_documentation_discrepancy=YES
service_identity=netaiops:netaiops
runtime_permission_probe=OK
unreadable_v3_python_files_in_production=4
regression_source_mode=git_archive_expected_HEAD
v3_offline_regression=OK
documentation_sealed=OK
```

模型状态说明：

现网 `/health` 在本批报告模型为 `qwen3-max`，与项目知识文档中的 `glm-5.2` 存在差异。`/health` 只反映进程当前报告的配置字段，不能单独证明网关真实调用模型。该差异不属于V3.4.4 Closeout 阻断项，必须在 V4.1-1 通过生产配置、`/models`、`/probe` 和真实 Chat 重新取证。文档可能需要更新。

本批没有修改 `app.py`、V2/V3 业务代码、systemd、生产配置或 takeover
开关；没有重启服务，没有执行真实设备命令，没有调用真实 Chat/LLM
业务场景，也没有 commit、push 或创建 tag。

当前状态仍是：

```text
V3.4.4 功能完成
Closeout-1 完成
Closeout-2：待执行
V4 尚未开始
```

下一步必须单独执行 V3.4.4 Closeout-2，完成 commit、push、远端 SHA
核验和 `chatbot-v3.4.4-closeout` tag 后，才能进入 V4.1。
