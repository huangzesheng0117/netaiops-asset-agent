# ChatBot V3.4.4 Closeout

## 1. 当前状态

```text
V3.4.4 功能实现：完成
V3.4.4 Closeout-1：完成
V3.4.4 Closeout-2：待执行
V4：尚未开始
```

本文档在 Closeout-1 中创建。它记录基线审计结果，但不代表 commit、
push 和 tag 已完成。

## 2. 基线证据

```text
audit_time=2026-07-07T16:12:44+08:00
branch=main
local_head=1a3745bf5adce87845ee43dad72e3fd56cbb67f7
local_subject=Fix V3 follow-up runtime permissions and persistence
remote_main_sha=1a3745bf5adce87845ee43dad72e3fd56cbb67f7
local_origin_main=0cf012d000000000000000000000000000000000
remote_tracking_note=local origin/main 与真实远端不一致；本地 remote-tracking ref 可能陈旧，真实远端以 git ls-remote 为准
closeout_tag=absent_and_pending
```

Closeout-1 要求真实 GitHub `main` SHA 与本地 HEAD 一致；本地
`origin/main` 只作为参考，不能代替 `git ls-remote`。

## 3. 服务与运行时

```text
service=netaiops-asset-agent.service
service_active=OK
service_pid=12345
service_identity=netaiops:netaiops
port_18081=LISTEN
health_http_code=200
health_json=VALID
health_reported_llm_model=qwen3-max
model_documentation_discrepancy=YES
service_restart=NO
```

模型状态说明：

现网 `/health` 在本批报告模型为 `qwen3-max`，与项目知识文档中的 `glm-5.2` 存在差异。`/health` 只反映进程当前报告的配置字段，不能单独证明网关真实调用模型。该差异不属于V3.4.4 Closeout 阻断项，必须在 V4.1-1 通过生产配置、`/models`、`/probe` 和真实 Chat 重新取证。文档可能需要更新。

运行时权限探针以实际 systemd 服务身份完成：

- V3 关键模块 source/package import；
- `v3_followup_context` 写入、读取、rename 和删除；
- `v3_takeover_audit` 写入、读取、rename 和删除；
- `v3_intent_shadow` 写入、读取、rename 和删除；
- 已存在 JSON/JSONL 数据抽样解析。

日志中关键错误模式匹配数：

```text
journal_critical_matches=0
```

该计数只作为人工复核信息，不能单独代替当前服务、health、import、
权限探针和回归结果。

## 4. 离线回归

生产工作树中当前执行用户不可读的相关 V3 Python 文件数量：

```text
unreadable_v3_python_files=4
```

为避免修改生产文件 owner、mode 或 ACL，本批从预期 Git HEAD 生成独立
Git archive checkout，并由 `baoleiji` 在该 checkout 中运行离线回归：

```text
regression_source_mode=git_archive_expected_HEAD
```

以下检查已通过。历史 V3.4.2 Registry 检查要求所有 `runtime_takeover_allowed=false`，与已经完成的 V3.4.4 follow-up takeover 状态冲突，因此不再作为当前阻断条件；本批改用 V3.4.4 当前状态检查：

```text
regress_v3_all.py
regress_v3_followup_context.py
v3_4_1_legacy_route_inventory.py
embedded_v3_4_4_registry_current_state_check.py
v3_4_4_2_followup_static_check.py
v3_4_4_2_fix1_static_check.py
v3_4_4_2_followup_direct_test.py
v3_4_4_2_fix1_return_observation_test.py
```

两个 direct regression 的脚本路径、`app.py` 路径、报告路径和契约检查器路径
均使用单行 Bash 参数展开，并在执行前完成运行级路径展开断言：

```text
direct_test_path_expansion_runtime_dry_run=OK
direct_test_contract_checker_path_expansion=OK
```

随后两个 direct regression 均先通过位置参数契约检查，再显式传入：

```text
argument_1=<Git archive checkout>/app.py
argument_2=<WORK_DIR>/<test-report>.json
```

两个报告文件均已确认存在、非空、可解析为 JSON object，并包含预期
顶层字段。

测试日志保存在：

```text
/tmp/netaiops_v344_closeout1_20260707_161216
```

Registry 当前状态检查确认：

```text
Registry 不包含本地自然语言 intent classifier
Registry 拒绝 question/context/snippet/raw_text 等字段
所有 resolution 继续要求 LLM Intent Arbiter
仅 v2_followup_return 的 runtime_takeover_allowed=True
v2_followup_return -> analyze_existing_evidence
migration_stage=v3.4-4
其他尚未迁移分支 runtime_takeover_allowed=False
historical_v3_4_2_registry_check=SKIPPED_BY_DESIGN
```

Closeout-1 不运行真实设备命令，不扩大 takeover，也不进行 GLM 5.2
真实 Chat 验证；模型兼容和真实验证属于 V4.1。

## 5. V3.4.4 完成范围

已完成：

- `general_chat`、`advice_analysis` 和
  `analyze_existing_evidence` 的受控 route-return takeover；
- V2、legacy 和 V3 follow-up context 兼容读取；
- original/effective conversation ID 区分；
- non-taken return observation；
- turn fingerprint 和相邻去重；
- context/audit/shadow 可观测；
- metadata-only Legacy Route Registry；
- 运行时模块和数据目录权限闭环；
- V2 fallback 保留。

尚未完成并已转入 V4：

- 前置 V4 Entry Router；
- 全 action V4 handler；
- canonical context source of truth；
- CMDB、命令生成、直接执行、确认执行和执行后分析迁移；
- V2 正常主路由退出；
- GLM 5.2 ChatBot 侧兼容修复和验证；
- 最终后端、前端和发布收口。

## 6. 本批变更边界

Closeout-1 只允许：

```text
M docs/netaiops_asset_agent_v3_refactor_guide.md
M docs/v3_4_4_followup_convergence.md
?? docs/v3_4_4_closeout.md
```

本批明确没有：

```text
app.py change
runtime Python change
systemd change
production config change
service restart
online behavior change
commit
push
tag
```

## 7. 下一步

下一步只能执行独立的 V3.4.4 Closeout-2：

1. 复核 Closeout-1 输出和文档 diff；
2. 精确暂存上述三份 Markdown；
3. staged scope 和敏感信息检查；
4. commit；
5. push `origin main`；
6. `git ls-remote` 核验真实远端 SHA；
7. 创建并推送 `chatbot-v3.4.4-closeout`；
8. 核验远端 tag；
9. 确认工作区 clean。

完成 Closeout-2 后，才允许进入 V4.1-1。
