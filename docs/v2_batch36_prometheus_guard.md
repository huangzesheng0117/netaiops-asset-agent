# V2 Batch36 Prometheus 查询规划与 PromQL Guard

## 目标

Batch36 新增 PromQL Guard 与受控 Prometheus 查询封装，用于后续支持自然语言指标查询、历史趋势查询和排障证据查询。

本批同时修复设备身份解析中的 Prometheus label candidate 问题：

- ip label 候选只允许合法 IP。
- 主机名不再被放入 ip 候选。
- 主机名只进入 hostname、host_name、host、name、device、sysName 等名称类候选。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/observability/__init__.py | observability 包初始化 |
| netaiops_asset/observability/promql_guard.py | PromQL 安全校验 |
| netaiops_asset/observability/prometheus_query.py | 受控 Prometheus 查询封装 |
| tools/regress_v2_prometheus_guard.py | Batch36 回归脚本 |
| docs/v2_batch36_prometheus_guard.md | Batch36 说明文档 |

## Guard 策略

当前 Guard 采用保守策略：

1. 拒绝空查询。
2. 拒绝明显可疑 token。
3. 对高基数指标要求显式 label selector。
4. query_range 限制最大时间范围。
5. query_range 限制最小 step。
6. query_range 限制估算点数。
7. count(up) 等基础聚合查询允许执行。

## 高基数指标示例

包括但不限于：

- ifHCInOctets
- ifHCOutOctets
- ifInErrors
- ifOutErrors
- ifOperStatus
- ifAdminStatus
- ifDescr
- ifName
- ifAlias
- entSensorValue
- bgp4PathAttrASPathSegment

## 安全边界

Batch36 不执行任何设备 CLI 命令，只执行 Prometheus 只读查询。

## 后续批次

Batch37 建议开始建设 Netmiko CLI 只读校验层，为后续工程师确认执行只读命令做准备。
