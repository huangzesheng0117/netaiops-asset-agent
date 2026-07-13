# V4.2-1 核心骨架与 Canonical Context Store

## 1. 批次边界

本批创建 `netaiops_asset.chat_v4` 基础包、版本化 contracts、Canonical Context Store、legacy 只读迁移适配和 audit adapter。

本批明确不做：

```text
app.py 接线
V4 Entry Router 生产接管
V2/V3 路由改变
CMDB/MCP/设备命令执行
生产配置修改
systemd 修改
服务重启
```

因此本批完成的是“可导入、可测试、可持久化的 V4 基础设施”，不是 V4 线上路由切换。

## 2. Contracts

固定版本：

```text
v4.entry.v1
v4.response.v1
v4.audit.v1
v4.context.v1
```

复用 V3 `IntentAction`，不建立第二套 action 枚举。`EntryResult` 明确：

- handled / clarification / fallback / error；
- side effect 开始后 fallback 必须关闭；
- clarification 必须对应 `need_clarification`；
- fallback 必须有明确技术原因。

## 3. Canonical Context

核心字段：

```text
conversation_id
request_user_field
title
created_at / updated_at / revision
device_context
topic
rolling_summary
recent_turns
last_intent
pending
execution_evidence
analysis_history
audit_refs
migration
metadata
```

Context 只保存结构化事实，不判断用户 Intent。

## 4. 存储保证

`ContextStore` 提供：

- conversation ID SHA256 文件映射；
- 同目录临时文件、file fsync、`os.replace`、directory fsync；
- 每会话 `fcntl.flock` 独占锁；
- revision 冲突检测；
- turn fingerprint 去重；
- recent turns/evidence/analysis/audit refs 上限；
- secret 递归脱敏；
- raw output 截断；
- 最大 context 字节限制；
- corrupt/schema 文件 quarantine；
- permission/write/schema/migration/conflict/invalid 可观测错误。

Corrupt 文件不会被当成空 context 静默覆盖。

默认目录：

```text
/var/lib/netaiops-asset-agent/data/v4_context
```

也可通过 `NETAIOPS_V4_CONTEXT_DIR` 指定。V4.2-1 测试使用临时目录，不写生产 context 数据。

## 5. Legacy 迁移

`context_migration.py` 只读：

- V3 `build_followup_context()`；
- legacy `get_conversation()`。

它将现有结构归一化为 `v4.context.v1`，记录 original/effective conversation ID 和 source metadata。旧 store 不删除、不改写。

Lazy migration 规则：

1. 先读取 V4 context；
2. V4 存在则直接返回；
3. V4 not_found 才读取 legacy；
4. corrupt/schema/permission 错误阻止 migration；
5. 并发 migration 通过 revision conflict 重新读取已有 V4 context。

## 6. 测试范围

```text
contracts 与 schema
fallback/side-effect 边界
atomic write 与失败保留原文件
多进程并发 append
revision conflict
turn/audit 去重
secret 脱敏与 raw output 上限
context 最大尺寸
corrupt/schema quarantine
permission 错误
legacy build/lazy migration
old source 不变
corrupt V4 阻止 migration
AST 架构扫描
runtime import
```

## 7. 后续

V4.2-2 在本基础上实现低风险 handlers 和 response builder。V4.2-3 才最小修改 `app.py`，将允许的低风险 action 放到 V2 前置 Entry Router。
