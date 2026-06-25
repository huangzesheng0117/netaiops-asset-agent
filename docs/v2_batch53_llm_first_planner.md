# V2 Batch53 LLM-first Intent Planner

## 目标

Batch53 将 V2 自然语言理解入口从“本地规则优先”改为“LLM-first Planner 优先”。

LLM 负责：

- 识别用户动作 action
- 识别问题类型 category
- 提取设备、接口、peer、时间窗口、指标、症状等实体
- 判断是否应该进入 V2

本地代码负责：

- 校验和归一化 LLM JSON Plan
- 设备解析
- 接口名规范化
- 后续动作分发
- 安全校验
- YES 确认
- 审计
- 调用 Prometheus / Netmiko

## 本批解决的问题

示例：

    设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题

原先由于本地规则未覆盖“错包增长”，会回退到 V1 CMDB 查询。

Batch53 后，LLM-first Planner 应识别为：

    action=suggest_commands
    category=interface_error
    v2_intent=interface_error_check
    device_name=WG88-SW-H16-1
    interface=Ethernet1/46

## 注意

Batch53 只完成 LLM-first Planner 和 V2 接管。

后续：

- Batch54：Plan Validator + Action Dispatcher
- Batch55：命令模板库改造，由 plan.category 选择模板
- Batch56：连续多类型问题回归

因此 Batch53 后，接口错包类问题应不再回退 V1，但专项命令模板会在 Batch55 系统化增强。

## 安全边界

Batch53 不执行设备 CLI。

LLM 只输出结构化计划，不直接决定执行命令。
