# V2 Batch45 Prometheus 当前 CPU 证据接入 Chat Router

## 目标

Batch45 将 Prometheus 当前 CPU 证据接入 V2 Chat Router。

用户询问 CPU 类问题时，系统不再只给 Netmiko 建议命令，还会尝试通过 Prometheus 只读查询获取当前 CPU 证据。

## 查询策略

当前按设备 mgmt_ip 依次尝试常见 CPU 指标：

- cpmCPUTotal5minRev
- cpmCPUTotal5min
- cpmCPUTotal1minRev
- cpmCPUTotal1min
- hrProcessorLoad
- cpu_usage
- cpu_utilization
- device_cpu_usage
- system_cpu_usage
- sysCpuUsage

如果未命中，会通过 list_metrics 搜索疑似 CPU 指标候选，并在回答中提示需要补充现网指标映射。

## 安全边界

1. 本批不执行设备 CLI。
2. 只执行 Prometheus 只读查询。
3. PromQL 仍通过 GuardedPrometheusQueryService 执行。
4. Netmiko 命令仍只作为建议，必须后续确认才会执行。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/observability/device_metrics.py | 设备级 Prometheus 指标探测 |
| tools/regress_v2_prometheus_cpu_chat.py | Batch45 回归脚本 |
| docs/v2_batch45_prometheus_cpu_chat.md | Batch45 说明文档 |

## 后续建议

如果本批发现现网 CPU 指标名不在内置候选中，应根据 metric_hints 和实际 Prometheus 指标建立设备厂商/型号到 CPU PromQL 的映射表。
