# V2 Batch37 Netmiko CLI 只读安全校验层

## 目标

Batch37 新增 Netmiko CLI 只读安全校验层，为后续工程师确认执行只读命令做准备。

本批不执行任何设备 CLI，不接入 app.py，不重启服务。

## 新增文件

| 文件 | 作用 |
|---|---|
| netaiops_asset/netmiko/__init__.py | Netmiko 安全辅助包初始化 |
| netaiops_asset/netmiko/cli_guard.py | CLI 只读安全校验核心逻辑 |
| tools/regress_v2_cli_guard.py | Batch37 回归脚本 |
| docs/v2_batch37_cli_guard.md | Batch37 说明文档 |

## 校验状态

| 状态 | 含义 |
|---|---|
| passed | 明显只读命令，可进入工程师确认环节 |
| review | 可能只读但敏感、高输出或高风险，默认不直接执行 |
| blocked | 配置、变更、重启、保存、删除、危险 debug 等命令，直接拒绝 |

## 当前规则原则

1. show/display/get/diagnose/tmsh show/tmsh list/list 等只读命令可按平台进入 passed。
2. configure/system-view/set/unset/delete/save/reload/reboot/shutdown 等直接 blocked。
3. show running-config/display current-configuration/show tech-support 等敏感只读命令进入 review。
4. ping/traceroute 等主动探测命令进入 review。
5. diagnose debug/debug/monitor capture 等高风险 debug 或抓包命令进入 review。
6. 命令中出现 ;、&&、||、重定向等可疑 token 直接 blocked。
7. 管道只允许 include/exclude/begin/section/count/grep/match/find/display/no-more/json 等安全过滤。

## 安全边界

Batch37 只是校验命令文本，不执行命令。

后续真正执行时，仍必须满足：

1. CLI Guard 返回 passed。
2. 工程师二次确认 confirmed=True。
3. 后端审计记录命令、设备、用户、时间、结果。
