# NetAIOps ChatBot / Asset Agent V3 改造专项文档

> 适用项目：NetAIOps ChatBot / Asset Agent
> 适用目录：`/opt/netaiops-asset-agent`
> 适用阶段：V3.1 ～ V3.6 架构改造、回归、收口
> 文档定位：V3 阶段独立改造约束文档，用于防止 V3 改造重新滑回 V2 式本地规则堆叠

---

## 1. 文档目的

本文档用于单独约束 NetAIOps ChatBot / Asset Agent 项目的 V3 阶段改造。

V3 的根本目标不是继续修补某个单点问题，而是把入口意图判断从 V2 的补丁式、本地规则式、分支抢路由式架构，改造成：

```text
用户输入
  -> LLM Intent Arbiter
  -> 结构化 JSON
  -> 后端只按 JSON 分发
  -> 本地确定性逻辑只负责安全、格式、命令切分、兜底和审计
```

本文档应作为 V3 后续所有改造批次的优先约束依据。
如果后续实现与本文档冲突，应优先暂停实现，先确认架构边界。

---

## 2. 项目固定信息

| 项目项 | 当前值 |
| --- | --- |
| 项目名称 | NetAIOps ChatBot / Asset Agent |
| 项目目录 | `/opt/netaiops-asset-agent` |
| systemd 服务 | `netaiops-asset-agent.service` |
| 服务端口 | `18081` |
| Git 仓库 | `git@github.com:huangzesheng0117/netaiops-asset-agent.git` |
| 主分支 | `main` |
| V3 shadow 日志目录 | `/var/lib/netaiops-asset-agent/data/v3_intent_shadow` |
| V3 takeover audit 目录 | `/var/lib/netaiops-asset-agent/data/v3_takeover_audit` |

当前阶段状态：

| 阶段 | 状态 |
| --- | --- |
| V1 | 已完成 |
| V2 | 作为旧逻辑和 fallback 底座保留 |
| V3.1 | 已建立 Intent Schema / Arbiter 基础 |
| V3.2 | 已建立 shadow 旁路记录能力 |
| V3.3 | 已完成 canary takeover 技术闭环，并完成 V3.3.18 closeout |
| V3.4 | 正在推进 legacy route convergence |
| V3.5 | 待推进 command_splitter + safety_guard 统一 |
| V3.6 | 待推进端到端回归与前端收口 |

---

## 3. V2 暴露出的根本问题

V2 当前不是某个功能点单独失败，而是入口路由架构没有真正做到 LLM-first。

V2 的典型链路近似为：

```text
用户输入
  -> middleware / semantic_route / inline extractor / followup 判断 / advice 判断
  -> 部分本地规则提前 return
  -> 可能调用 LLM
  -> MCP / CMDB / 前端返回
```

这导致：

1. 用户真实意图对人类来说明确，但系统可能被关键词、格式、分支优先级误导。
2. “执行新命令后分析”可能被误判为“基于已有结果继续分析”。
3. “纯建议 / 风险分析”曾被误送入 followup analysis，要求已有 audit_path。
4. 同一行多条 show/display 命令容易被 inline extractor 错误切分或误路由。
5. 多个补丁批次虽然解决了局部问题，但也增加了入口路由复杂度和维护风险。

V3 的目标就是解决这个根因：**本地规则不再作为用户意图的主路由裁判。**

---

## 4. V3 核心原则

### 4.1 用户意图必须交给 LLM Intent Arbiter

必须坚持：

```text
用户想干什么 -> 交给 LLM Intent Arbiter
这件事能不能安全执行 -> 交给本地确定性安全逻辑
```

禁止在 V3 新模块中重新通过关键词、正则或本地字段组合判断用户自然语言意图。

### 4.2 后端只消费结构化 JSON

LLM Intent Arbiter 必须输出结构化 JSON。
后端只根据 JSON 中的固定字段做分发，例如：

```json
{
  "action": "execute_provided_commands_and_analyze",
  "confidence": 0.93,
  "device_required": true,
  "device_hint": "SH16-H05-INT-EDG-SW01",
  "commands_provided": true,
  "commands": [
    "show clock",
    "show version"
  ],
  "need_existing_evidence": false,
  "should_generate_commands": false,
  "should_execute_commands": true,
  "should_analyze_after_execution": true,
  "requires_confirmation": true,
  "clarification_question": "",
  "reason": "用户明确提供了 show 命令，并要求执行后分析"
}
```

后端禁止从自然语言 answer、question、context、snippet 中再次猜测主路由。

### 4.3 本地规则只允许做确定性工作

本地确定性逻辑可以负责：

| 类型 | 是否允许 | 说明 |
| --- | --- | --- |
| Intent 主路由判断 | 不允许 | 必须由 LLM Intent Arbiter 决定 |
| JSON schema 校验 | 允许 | 校验 action 枚举、字段类型、默认值 |
| confidence 阈值判断 | 允许 | 低置信度转澄清或 fallback |
| 命令切分 | 允许 | 对 LLM 初步抽取的命令做格式归一、切分、清理 |
| 命令安全校验 | 允许 | 阻断危险命令，防止 LLM 绕过安全策略 |
| CMDB 查询执行 | 允许 | 根据 Arbiter JSON 的 action 和设备字段执行 |
| MCP 调用 | 允许 | 只在确认、安全通过后执行 |
| audit / shadow 落盘 | 允许 | 记录证据，不作为主路由 |
| fallback 编排 | 允许 | 低置信度、异常、未接管时走旧逻辑 |

### 4.4 V2 保留为 fallback，不应一次性删除

V2 旧逻辑仍然是当前生产 fallback 底座，不应在 V3.4 中一次性删除。

V3.4 的目标是：

```text
将旧路由从 scattered branch 收敛到统一 registry / arbiter 管理
```

不是：

```text
直接删除所有旧逻辑
```

---

## 5. 绝对禁止项

以下行为在 V3 新模块中禁止出现，除非明确标注为 V2 旧逻辑或测试用例，并且不会参与 V3 主路由判断。

### 5.1 禁止新增本地关键词意图分类器

禁止在 V3 新模块中出现类似：

```python
CATEGORY_TOKENS = {
    "followup": ("继续", "刚才", "上一个"),
    "cmdb_query": ("管理IP", "查设备"),
    "command_execution": ("执行", "跑一下"),
}
```

如果这些 token 用于判断用户自然语言属于哪个业务 action，则属于 V3 架构违规。

### 5.2 禁止 Registry 解析用户自然语言

Legacy Route Registry 只能登记旧路由分支元数据，不能重新解析用户问题。

禁止：

```text
question/context/snippet -> 关键词匹配 -> route_type -> v3_action
```

允许：

```text
legacy_branch_id / explicit_legacy_route_type / source_function
  -> migration_stage / mapped_v3_action / fallback_policy
```

### 5.3 禁止 shadow writer 改真实返回

`_v3_shadow_write()` 只能记录旁路，不得改变用户 response。

真实 takeover 必须发生在真实返回路径：

1. `v2_chat_router_middleware` 的 `JSONResponse(...)` 提前返回点；
2. `/api/v1/chat` 的 `chat(req: ChatRequest)` return 点。

### 5.4 禁止把 payload 中的 user 字段描述为真实用户

当前平台没有登录、没有鉴权、没有真实用户体系。

`user` 只是 `/api/v1/chat` 请求 payload 中的普通字段，最多只能作为 canary trigger field。
禁止说“按用户灰度”或“全量用户切换”。

正确表述：

```text
当前不是全量请求、全量问题类型都切到 V3。
当前是通过请求 payload 字段和 conversation_id 前缀做后端 canary trigger。
```

### 5.5 禁止 audit 异常静默吞掉

禁止：

```python
except Exception:
    pass
```

audit 写入失败必须暴露到 response：

```text
v3_audit_error
```

用于 smoke 直接失败和定位。

### 5.6 禁止只看中间 smoke 成功就判断批次完成

批次完成必须同时满足：

1. 目标逻辑通过；
2. app.py 是否变化有明确说明；
3. 服务状态正常；
4. Git staged 文件范围正确；
5. `git diff --check` 通过；
6. `git diff --cached --check` 通过；
7. commit 成功；
8. push 成功；
9. 如需 tag，tag 创建和推送成功；
10. 最终 Git 工作区 clean。

---

## 6. V3 action 集合

V3 Arbiter 输出的 action 必须来自固定枚举。

| action | 含义 | 典型触发 |
| --- | --- | --- |
| `generate_commands` | 生成排障命令建议，不执行 | “给我查看日志的命令” |
| `execute_provided_commands` | 用户直接提供命令，要求执行 | 用户给出 show/display 命令并要求执行 |
| `execute_provided_commands_and_analyze` | 执行用户提供的新命令，并基于新输出分析 | “我再给你一批命令，执行后分析” |
| `confirm_execute_pending` | 确认执行上一轮 pending commands | “确认执行”“可以执行” |
| `analyze_existing_evidence` | 基于已有执行结果继续分析 | “继续分析刚才结果” |
| `advice_analysis` | 纯方案建议、风险分析、优缺点比较 | “是否建议先隔离流量” |
| `cmdb_query` | 只查 CMDB，不走排障 | “查一下某设备管理 IP” |
| `general_chat` | 普通解释、闲聊或非设备操作类问题 | “解释一下 StackWise Virtual” |
| `need_clarification` | 关键信息不足，需要澄清 | “这个设备现在怎么办” |

后端不得新增隐式 action。
如果确实需要新增 action，必须先更新 schema、prompt、dispatcher、回归用例和文档。

---

## 7. V3 模块职责边界

| 模块 | 职责 | 禁止事项 |
| --- | --- | --- |
| `intent_schema.py` | 定义 action 枚举、Pydantic Schema、confidence 阈值、默认值 | 不做自然语言关键词分类 |
| `intent_arbiter.py` | 构造 prompt、调用 LLM、解析 JSON、校验 schema | 不把本地 keyword 作为主路由 |
| `intent_dispatcher.py` | 只根据 Arbiter JSON 分发到对应 handler | 不从 question/context 重新猜 action |
| `command_splitter.py` | 命令切分、格式修复、粘贴清理 | 不判断业务意图 |
| `safety_guard.py` | 只读安全校验、危险命令拦截、命令数量上限 | 不决定用户意图 |
| `execution_orchestrator.py` | 确认执行、分批执行、MCP 调用、audit_path 记录 | 不绕过 safety_guard |
| `evidence_analyzer.py` | 基于原始输出调用 LLM 分析 | 不伪造原始证据 |
| `advice_analyzer.py` | 纯建议 / 风险分析 | 不要求 audit_path |
| `context_store.py` | 会话上下文、历史记录、标题 | 不做主路由判断 |
| `shadow_logger.py` | shadow 旁路记录 | 不影响真实返回 |
| `takeover_gate.py` | 基于结构化字段判断是否允许 takeover | 不解析用户自然语言 |
| `takeover_response.py` | route return takeover 包装 | 不放宽安全边界 |
| `legacy_route_registry.py` | 旧路由元数据登记、迁移阶段、fallback 策略 | 不解析 question，不做本地 intent classifier |

---

## 8. Legacy Route Registry 正确设计

### 8.1 Registry 的定位

Legacy Route Registry 的职责是登记旧路由分支，而不是判断用户意图。

它应该描述：

```text
这个旧分支是什么
它来自哪个函数 / return path
它历史上解决什么问题
它应该迁移到哪个 V3 action
当前阶段是否允许收敛
如果不能收敛应该 fallback 到哪里
```

### 8.2 Registry 允许的输入

允许输入显式描述符：

```json
{
  "legacy_branch_id": "v2_advice_keyword_branch",
  "legacy_route_type": "advice_analysis",
  "source_function": "v2_chat_router_middleware",
  "return_path": "JSONResponse",
  "known_legacy_behavior": "pure advice analysis",
  "migration_stage": "v3.4-3"
}
```

### 8.3 Registry 禁止的输入

禁止以自然语言问题作为主要输入：

```json
{
  "question": "继续分析刚才这个设备的问题"
}
```

Registry 不应通过这类问题文本判断 route_type。

### 8.4 Registry 允许的输出

```json
{
  "legacy_branch_id": "v2_advice_keyword_branch",
  "legacy_route_type": "advice_analysis",
  "mapped_v3_action": "advice_analysis",
  "migration_stage": "v3.4-3",
  "fallback_policy": "v2_advice_analysis",
  "runtime_takeover_allowed": false,
  "notes": "metadata only; arbiter remains source of truth for user intent"
}
```

### 8.5 Registry 修正要求

如果当前 Registry 中存在以下结构，应立即修正：

```text
CATEGORY_TOKENS
ROUTE_KEYWORDS
TYPE_PRIORITY 基于自然语言命中
classify_legacy_route(question=...)
question/context/snippet -> token hits -> route_type
```

修正后必须保证：

1. Registry 不读取 question 判断业务意图；
2. Registry 不维护中文意图关键词；
3. Registry 不替代 Arbiter；
4. Registry 只能处理显式 legacy route descriptor；
5. V3.4-3 不能基于 Registry 的关键词分类结果做真实收敛。

---

## 9. V3 分阶段实施计划

### 9.1 V3.1 Intent Schema / Arbiter

目标：

1. 建立统一 action 枚举；
2. 建立 Arbiter prompt；
3. 建立 JSON 解析与 schema 校验；
4. 建立基础单元测试；
5. 不替换线上路由。

验收：

```text
intent_schema=OK
intent_arbiter_json=OK
invalid_json_fallback=OK
action_enum_validation=OK
```

### 9.2 V3.2 Shadow 模式

目标：

1. 所有请求同时跑 V2 旧路由和 V3 Arbiter；
2. 只记录差异；
3. 不影响用户真实结果；
4. shadow JSONL 可用于后续回归。

验收：

```text
shadow_write=OK
v2_response_recorded=OK
v3_decision_recorded=OK
no_user_response_change=OK
```

### 9.3 V3.3 Canary Takeover 技术闭环

目标：

1. V3 response generator 能生成真实可返回内容；
2. readiness / gate 能判断是否允许 takeover；
3. takeover 必须发生在真实 return path；
4. audit 必须记录 taken / blocked；
5. fallback 必须保留。

当前状态：

```text
V3.3.18 closeout 已完成
V3.3 已完成受控 canary takeover 技术闭环
```

边界：

```text
这不是全量请求切换。
这不是用户灰度。
这是通过 request payload 字段和 conversation_id 前缀做的后端 canary trigger。
```

### 9.4 V3.4 Legacy Route Convergence

目标：

1. 盘点旧路由；
2. 建立旧路由元数据 Registry；
3. 逐步收敛 general_chat / advice_analysis / followup / inline 等旧分支；
4. 删除或禁用重复旧分支；
5. 保留 V2 fallback；
6. 不把 Registry 做成关键词分类器。

建议批次：

| 批次 | 目标 | 是否允许改 app.py | 是否允许重启服务 |
| --- | --- | --- | --- |
| V3.4-1 | 旧路由盘点与地图 | 否 | 否 |
| V3.4-2 | Legacy Route Registry 元数据化 | 否 | 否 |
| V3.4-2-fix | 修正 Registry，移除本地关键词分类器 | 否 | 否 |
| V3.4-3 | 收敛 general_chat / advice_analysis 旧分支 | 是 | 是 |
| V3.4-4 | 收敛 follow-up / 多轮上下文旧分支 | 是 | 是 |
| V3.4-5 | 收敛 inline 抢路由，但不做命令安全重构 | 是 | 是 |
| V3.4-6 | 删除或禁用重复旧分支 | 是 | 是 |
| V3.4-7 | V3.4 收口、文档、tag | 视情况 | 视情况 |

### 9.5 V3.5 Command Splitter + Safety Guard

目标：

1. 统一所有命令来源；
2. 支持逐行、代码块、编号、分号、同行多命令；
3. 命令安全校验成为唯一安全入口；
4. 危险命令不能因 LLM 判断为 execute 而绕过本地安全逻辑。

验收：

```text
command_splitter_multiline=OK
command_splitter_inline_multi_show=OK
safety_guard_readonly=OK
safety_guard_dangerous_blocked=OK
execute_requires_confirmation=OK
```

### 9.6 V3.6 端到端回归与前端收口

目标：

1. 覆盖 generate / execute / analyze / advice / cmdb / general / history / rename 全链路；
2. 前端展示和后端 JSON 字段一致；
3. 历史会话、多轮上下文、标题恢复稳定；
4. 形成最终 V3 closeout 文档和 tag。

---

## 10. V3 必测用例清单

| 场景 | 输入示例 | 期望 action / 行为 |
| --- | --- | --- |
| 生成命令 | 给我查看一下设备 SH16-H05-INT-EDG-SW01 日志的命令 | `generate_commands` |
| 确认执行 | 确认执行 | `confirm_execute_pending` |
| 提供新命令并分析 | 我再给你一批命令，执行后分析：show clock show version show logging last 100 | `execute_provided_commands_and_analyze` |
| 逐行新命令并分析 | 请执行以下命令并分析：`show clock`、`show version` | `execute_provided_commands_and_analyze` |
| 已有证据分析 | 继续分析刚才的执行结果 | `analyze_existing_evidence` |
| 纯建议 | 是否建议在重启 standby 前先隔离流量？只给建议，不要命令。 | `advice_analysis` |
| CMDB 查询 | 查一下 SH16-H05-INT-EDG-SW01 的管理 IP 和设备类型 | `cmdb_query` |
| 普通聊天 | 解释一下 StackWise Virtual 是什么 | `general_chat` |
| 缺信息澄清 | 这个设备现在怎么办？ | `need_clarification` |
| 危险命令拦截 | 执行 reload | Arbiter 可识别 execute，但 safety_guard 必须阻断 |

---

## 11. 每批命令执行纪律

每个 V3 批次必须明确回答：

1. 是否修改 `app.py`；
2. 是否修改 systemd drop-in；
3. 是否重启 `netaiops-asset-agent.service`；
4. 是否改变线上行为；
5. 是否扩大 takeover 范围；
6. 是否涉及 Git commit；
7. 是否涉及 Git push；
8. 是否涉及 tag。

### 11.1 执行前检查

```bash
cd /opt/netaiops-asset-agent
git status --short
git diff --stat
git rev-parse --abbrev-ref HEAD
```

要求：

1. 分支必须为 `main`；
2. 工作区必须 clean，除非本批明确接管未完成变更；
3. origin 必须是预期 GitHub 仓库；
4. 不得提交 data/log/backup/venv/env/secret。

### 11.2 脚本自检

最低要求：

1. Bash 外层 `bash -n`；
2. 内嵌 Python `py_compile`；
3. Markdown / Python / JSON / YAML EOF 清理；
4. `git diff --check`；
5. `git diff --cached --check`；
6. 关键函数 / 字段断言；
7. staged 文件范围断言；
8. 高置信敏感信息扫描；
9. 禁止 force push；
10. 禁止误重启服务。

### 11.3 成功标准

批次成功必须出现：

```text
commit_created=1
git_push_main=OK
result=OK
```

如果是阶段 closeout，还必须出现：

```text
git_tag_created=OK
git_tag_pushed=OK
```

---

## 12. Git 与发布规则

### 12.1 禁止提交内容

禁止提交：

```text
config/*.env
token
secret
password
data
logs
backup
venv
压缩包
临时测试输出
本地缓存
私钥或认证文件
```

### 12.2 标准提交流程

```bash
cd /opt/netaiops-asset-agent
git status --short
git diff --stat
git rev-parse --abbrev-ref HEAD
git add <expected files only>
git diff --cached --name-status
git diff --cached --check
git commit -m "<message>"
git push origin main
```

### 12.3 Tag 规则

只有阶段性收口才打 tag，例如：

```text
chatbot-v3.3.18-closeout
chatbot-v3.4-closeout
chatbot-v3.5-closeout
chatbot-v3.6-closeout
```

普通批次不打 tag。

---

## 13. 排障处理原则

当命令、脚本、测试或服务操作失败时，必须先定位根因。

处理顺序：

1. 读取完整错误输出；
2. 判断错误层级：
   - 路径；
   - 环境；
   - 依赖；
   - 权限；
   - 配置；
   - 语法；
   - 服务状态；
   - 网络；
   - 认证；
   - 数据格式；
   - 业务逻辑；
   - Git；
   - 自检误判。
3. 明确最可能根因；
4. 证据不足时，只给最小取证命令；
5. 等取证结果返回后再修复；
6. 不连续多轮凭猜测改脚本；
7. 不忽略异常日志；
8. 不把中间步骤成功误判为整批成功。

如果不能确认，应明确说明：

```text
当前证据不足，不能直接修复。需要先确认以下信息。
```

---

## 14. 已确认的关键教训

### 14.1 dict / dataclass / object 边界

V3 中 `plan`、`decision`、`gate` 可能是 dict、dataclass 或对象。
跨模块传递前必须归一化。

### 14.2 route return takeover 不能只包 chat()

`/api/v1/chat` 不只有 `chat(req)` 一个返回点。
`v2_chat_router_middleware` 中的提前 `JSONResponse(...)` 也必须覆盖。

### 14.3 conversation_id 可能被改写

`append_turn()` 可能把非法 conversation_id 改成新 UUID。
canary trigger 判断必须优先使用原始请求上下文中的 conversation_id，而不是 response 中的 conversation_id。

### 14.4 audit 失败必须显式暴露

audit 写入失败必须暴露 `v3_audit_error`，不能静默吞掉。

### 14.5 静态检查不能过宽

不能扫描整个 helper 后只要存在 `except Exception: pass` 就失败。
应精准检查目标函数。

### 14.6 commit message 检查不能写死错误字符串

检查历史 commit 时，不应写死不准确的 subject。
应使用真实 subject 或稳妥正则。

### 14.7 Markdown EOF 空白行会导致批次失败

生成 Markdown 后必须清理末尾多余空白行，避免：

```text
new blank line at EOF
```

### 14.8 V3.4-2 关键教训

Legacy Route Registry 不能实现成本地关键词分类器。
如果 registry 内出现 `CATEGORY_TOKENS`、`ROUTE_KEYWORDS`、`classify_legacy_route(question=...)`，必须视为架构偏离并修正。

---

## 15. 文档同步要求

以下情况必须提醒同步更新 Markdown 文档：

1. 完成一个稳定批次；
2. 完成一次关键排障；
3. 修改 V3 阶段边界；
4. 修改 canary trigger 策略；
5. 修改 takeover / audit 行为；
6. 修改 command_splitter / safety_guard 行为；
7. Git 提交策略变化；
8. closeout / tag 完成；
9. 发现架构偏离并修正。

建议文档拆分：

| 文档 | 用途 |
| --- | --- |
| `01_PROJECT_CONTEXT.md` | 项目固定信息、目录、服务、版本 |
| `02_CURRENT_ARCHITECTURE.md` | 当前整体架构 |
| `03_V3_STATUS_AND_ROADMAP.md` | V3 状态、阶段、路线 |
| `04_ARCHITECTURE_DECISIONS.md` | 架构决策和边界 |
| `05_GITHUB_WORKFLOW_AND_RULES.md` | Git 工作流和提交规则 |
| `06_TROUBLESHOOTING_LESSONS.md` | 排障经验和踩坑 |
| `07_CHATBOT_ROUTE_AND_CONTEXT.md` | 路由、上下文、多轮会话 |
| `08_V3_INTENT_AND_TAKEOVER_DESIGN.md` | Intent、Arbiter、Takeover |
| `09_TESTING_AND_REGRESSION.md` | 测试和回归 |
| `10_OPERATION_AND_RELEASE_NOTES.md` | 运行、发布、tag、收口 |

---

## 16. 当前状态与下一步

当前确认状态：

```text
V3.1：已完成
V3.2：已完成
V3.3：canary takeover 技术闭环已完成并 closeout
V3.4.1：已完成
V3.4.2：metadata-only Legacy Route Registry 已完成
V3.4.3：general_chat / advice_analysis 收敛已完成
V3.4.4：follow-up context convergence 已完成
V3.4.4 Closeout-1：基线审计与文档封存已完成
V3.4.4 Closeout-2：待执行
V4：尚未开始实施
```

原规划中的 V3.4.5、V3.4.6、V3.5 和 V3.6 未完成内容统一转入 V4，
以后续 `09_V4_IMPLEMENTATION_PLAN.md` 对应的生产项目实施文档为准。

当前下一步只能是：

```text
V3.4.4 Closeout-2
```

目标：

1. 精确暂存 V3.4.4 closeout 文档；
2. `git diff --cached --check`；
3. commit；
4. push `origin main`；
5. 使用 `git ls-remote` 核验真实远端 SHA；
6. 创建并推送 `chatbot-v3.4.4-closeout`；
7. 核验远端 tag；
8. 确认工作区 clean。

V3.4.4 closeout 完成后，才能进入：

```text
V4.1-1：GLM 5.2 生产配置与接口只读取证
```

Closeout-1 不修改 `app.py`、V2/V3 运行时代码、systemd 或生产配置，
不重启服务，也不改变线上行为。

## 17. 最终原则

V3 的最终目标是：

```text
入口路由统一
LLM Intent Arbiter 成为用户意图唯一裁判
后端只按结构化 JSON 执行编排
本地规则只做安全、格式、命令切分、低置信度兜底
旧路由逐步收敛但保留 fallback
所有 takeover 都可审计、可回滚、可验证
```

任何实现只要重新走向：

```text
本地关键词 -> route_type -> action
```

都应立即停止，先修正架构方向。
