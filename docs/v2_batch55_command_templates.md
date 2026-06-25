# V2 Batch55 命令模板库改造

## 目标

Batch55 将 V2 的命令建议从默认兜底 `show version` 改为根据 plan.category / v2_intent / entities 选择专项只读命令模板。

## 新增能力

支持以下类型的第一批只读命令建议：

- cpu
- route_table
- interface_error
- interface_status / interface_down
- optical_power / transceiver
- bgp
- bfd
- memory
- log
- device_health

## 典型修复

用户输入：

    设备WG88-SW-H16-1的eth1/46有持续错包增长，给我命令看看是什么问题

Batch55 后应返回接口错包专项命令，例如：

    show interface Ethernet1/46
    show interface Ethernet1/46 counters errors
    show interface Ethernet1/46 counters detailed
    show interface Ethernet1/46 transceiver details
    show logging last 100

而不是只返回：

    show version

## 安全边界

1. Batch55 不执行设备 CLI。
2. 所有命令仍只作为建议命令展示。
3. 真正执行仍必须进入确认执行流程。
4. ConfirmedNetmikoExecutor 仍会再次做 CLI Guard。
5. 当前 LLM token 仍存在验证失败，plan 可能来自 fallback_minimal；该问题后续需要单独收口。
