# V2 Batch39 Trouble Session 与 Evidence Builder

## 目标

Batch39 新增排障会话与证据汇总底座，用于把 CMDB、Prometheus、Netmiko 三类证据串成一个结构化排障会话。

本批不接入 app.py，不重启服务，不执行新的设备 CLI 命令。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/troubleshoot/__init__.py | 排障模块包初始化 |
| netaiops_asset/troubleshoot/session.py | 排障会话 JSON 存储 |
| netaiops_asset/troubleshoot/evidence_builder.py | 证据汇总构建器 |
| tools/regress_v2_troubleshoot_evidence.py | Batch39 回归脚本 |
| docs/v2_batch39_troubleshoot_evidence.md | Batch39 说明文档 |

## 证据来源

当前 Batch39 汇总三类证据：

1. 设备身份解析证据：CMDB + Netmiko inventory + Prometheus label candidate。
2. Prometheus up 状态证据：通过 PromQL Guard 后执行只读 up{ip="..."} 查询。
3. Netmiko 只读取证审计：读取已有 Batch38 审计文件，不执行新的 CLI。

## 会话文件位置

默认保存到：

/var/lib/netaiops-asset-agent/data/v2_troubleshoot_sessions

## 后续批次

Batch40 建议完成 V2 阶段性收口：

1. 整体回归脚本。
2. README 和文档补充。
3. Git diff 检查。
4. Git 提交与推送。
