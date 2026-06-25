# V2 Batch35 设备身份解析层说明

## 目标

Batch35 新增设备身份解析层，用于把用户输入的主机名、管理IP、序列号或其他关键词，统一解析成后续排障所需的设备身份信息。

核心目标：

1. 从 CMDB 查询设备详情。
2. 从 Netmiko MCP 设备清单中匹配设备名和登录地址。
3. 构造 Prometheus label 候选。
4. 可选执行只读 Prometheus up{ip="..."} 查询，发现真实指标标签。
5. 为后续 Prometheus 查询、Netmiko 只读取证和排障会话提供统一设备身份输入。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/device_identity/__init__.py | 设备身份解析包初始化 |
| netaiops_asset/device_identity/resolver.py | 设备身份解析核心逻辑 |
| tools/regress_v2_device_identity.py | Batch35 回归脚本 |
| docs/v2_batch35_device_identity.md | Batch35 说明文档 |

## 安全边界

Batch35 不执行任何设备 CLI 命令。

允许的只读动作：

- 查询 CMDB 设备详情。
- 查询 Netmiko MCP 设备清单。
- 执行 Prometheus 即时查询 up{ip="..."}。
- 构造 Prometheus label 候选。

## 输出结构

resolver.resolve(keyword) 返回结构化结果，核心字段包括：

- status
- keyword
- keyword_type
- cmdb_count
- cmdb_items
- selected_cmdb
- hostname
- mgmt_ip
- netmiko_match
- netmiko_match_reason
- prometheus_label_candidates
- prometheus_up_probe
- warnings

## 后续批次

Batch36 建议开始建设 Prometheus 查询规划与 PromQL guard，把自然语言指标查询转成受控 PromQL 查询。
