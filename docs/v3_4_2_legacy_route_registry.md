# ChatBot V3.4-2 Fix: Metadata-only Legacy Route Registry

## 目标

V3.4-2 fix 用于修正上一版 Legacy Route Registry 的架构偏离。

上一版 `legacy_route_registry.py` 把 Registry 实现成了本地关键词分类器，这是错误方向。本批将其修正为仅登记旧路由分支元数据的 Registry。

本批不修改 `app.py`，不重启服务，不改变线上行为，不扩大 V3 takeover 范围。

## 最高优先级边界

V3 改造不能使用本地规则、关键词、正则、字段组合去匹配用户原始文本并决定主路由。

用户想干什么，必须交给 LLM Intent Arbiter 判断。后端只消费 Arbiter 输出的结构化 JSON。

## 本批修正内容

| 文件 | 修正 |
| --- | --- |
| `netaiops_asset/chat_v3/legacy_route_registry.py` | 移除本地关键词分类器，改为 metadata-only Registry |
| `tools/v3_4_2_legacy_route_registry_check.py` | 改为检查 Registry 是否只接受显式 descriptor |
| `docs/v3_4_2_legacy_route_registry.md` | 更新文档，明确 Registry 不解析用户自然语言 |

## Registry 允许做什么

Legacy Route Registry 只允许登记和解析显式旧路由描述符，例如：

```json
{
  "legacy_branch_id": "v2_advice_analysis_return",
  "legacy_route_type": "advice_analysis",
  "source_function": "v2_chat_router_middleware",
  "return_path": "JSONResponse",
  "known_legacy_behavior": "existing pure advice analysis path",
  "migration_stage": "v3.4-3"
}
```

Registry 可以输出：

```json
{
  "legacy_route_type": "advice_analysis",
  "mapped_v3_action": "advice_analysis",
  "migration_stage": "v3.4-3",
  "fallback_policy": "v2_advice_analysis",
  "runtime_takeover_allowed": false
}
```

## Registry 禁止做什么

Registry 禁止读取或解析用户原始文本。

禁止重新出现：

```text
CATEGORY_TOKENS
ROUTE_KEYWORDS
TYPE_PRIORITY 基于自然语言命中
classify_legacy_route(question=...)
question/context/snippet -> token hits -> route_type
```

Registry 禁止接受以下字段作为主输入：

```text
question
context
snippet
prompt
message
raw_text
text
user_input
user_message
```

如果输入中包含这些字段，应直接拒绝，而不是解析。

## 当前阶段行为

V3.4-2 fix 完成后：

1. Registry 仍然没有接入 `app.py`；
2. Registry 不影响真实 `/api/v1/chat` 返回；
3. Registry 不扩大 V3 takeover 范围；
4. Registry 只为 V3.4 后续收敛批次提供元数据；
5. 用户意图仍然必须由 LLM Intent Arbiter 决定；
6. V2 fallback 仍然保留。

## 校验方式

本批校验包括：

1. `py_compile` Registry 模块和检查工具；
2. 静态检查 Registry 源码中不存在 `CATEGORY_TOKENS`、`ROUTE_KEYWORDS`、`classify_legacy_route` 等结构；
3. 检查 Registry dataclass 不包含 `question/context/snippet` 字段；
4. 检查 `descriptor_from_dict()` 拒绝自然语言字段；
5. 重跑 V3.4-1 inventory，但只做 read-only summary，不用它推导用户意图；
6. 检查 `app.py` SHA256 前后一致；
7. 检查服务 active；
8. `git diff --check` 和 `git diff --cached --check`；
9. staged 文件范围只包含本批预期文件；
10. commit + push。

## 后续要求

完成 V3.4-2 fix 后，才能进入 V3.4-3。

V3.4-3 收敛 general_chat / advice_analysis 旧分支时，必须遵守：

```text
用户意图 -> LLM Intent Arbiter -> 结构化 JSON -> dispatcher
旧路由分支元数据 -> Registry -> migration/fallback information
```

禁止：

```text
用户原始文本 -> Registry 关键词判断 -> route_type/action
```

## 结论

V3.4-2 fix 的完成标准不是“Registry 能根据文字判断类型”，而是：

```text
Registry 不再判断用户意图
Registry 只登记旧路由元数据
Arbiter 仍然是用户意图唯一裁判
```
