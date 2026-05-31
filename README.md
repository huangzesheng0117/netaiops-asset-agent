# NetAIOps Asset Agent

网络信息统一查询入口，用于通过自然语言查询 CMDB 网络设备资产信息。

## 1. 项目定位

本项目 V1 版本面向网络运维场景，提供一个极简的对话式查询入口。用户可以用自然语言查询 CMDB 中的网络设备资产信息，例如管理 IP、主机名、设备序列号、EM 码、设备型号、IDC、机房、机柜、环境、用途等字段。

当前 V1 版本只做只读查询，不做任何配置修改。

## 2. V1 已实现能力

- 通过自然语言查询 CMDB 网络设备资产。
- 接入公司本地 LLM，由 LLM 解析用户查询意图。
- 后端通过白名单 Tool Plan 调用 CMDB 查询接口。
- 支持根据任意常见字段反查其他字段，例如：
  - 管理 IP 反查设备信息
  - 主机名反查管理 IP
  - 设备序列号反查主机名和管理 IP
  - EM 码反查设备信息
  - IDC / 机房 / 机柜 / 环境组合查询
- 支持查询结果表格展示。
- 支持导出 Excel。
- 支持“导出刚才结果 Excel”。
- 支持对话历史。
- 提供一键状态检查、一键验收回归、安全检查和运行备份脚本。

## 3. 访问入口

    http://<服务器IP>:18081/

## 4. 服务信息

    应用目录：/opt/netaiops-asset-agent
    配置目录：/etc/netaiops-asset-agent
    数据目录：/var/lib/netaiops-asset-agent/data
    服务名：netaiops-asset-agent.service
    监听端口：18081

## 5. 常用查询示例

    10.189.250.8 是哪台设备，主机名、序列号、型号、状态、IDC、机房、机架是什么？

    设备序列号为 FDO24130P9S 的设备，主机名和管理 IP 分别是多少？

    EM码为 EM06027 的设备主机名是什么？

    SH8 机房 G 排机柜，生产网的设备有哪些？

    WG88-SW-H19-1 这台设备的用途是什么？操作系统是什么？

## 6. 技术架构

V1 查询链路如下：

    用户自然语言
      -> 前端对话窗口
      -> 后端 Chat API
      -> 公司本地 LLM 解析 Tool Plan
      -> 后端白名单校验
      -> CMDB networkServer 只读查询
      -> 后端本地二次过滤
      -> 表格结果 / Excel 导出

LLM 只负责解析意图，不直接访问 CMDB Token，不直接拼接 SQL，不直接访问数据库。

## 7. 安全边界

- 当前版本只读查询 CMDB 网络设备信息。
- 不提供 CMDB 写入、修改、删除能力。
- 不提供网络设备配置下发能力。
- CMDB Token 和 LLM API Key 保存在 /etc/netaiops-asset-agent/asset-agent.env。
- 前端和 API 不返回真实 Token / Key。
- Tool 层只暴露只读查询类工具。

访问控制暂未在 V1 阶段启用，后续统一接入 Nginx Basic Auth 或公司统一认证入口。

## 8. 运维命令

状态检查：

    cd /opt/netaiops-asset-agent
    source venv/bin/activate
    ./tools/v1_status_check.sh

验收回归：

    cd /opt/netaiops-asset-agent
    source venv/bin/activate
    ./tools/v1_acceptance_check.sh

安全检查：

    cd /opt/netaiops-asset-agent
    source venv/bin/activate
    python tools/v1_security_check.py

运行备份：

    cd /opt/netaiops-asset-agent
    source venv/bin/activate
    ./tools/v1_backup_runtime.sh

查看服务状态：

    systemctl status netaiops-asset-agent --no-pager

查看日志：

    journalctl -u netaiops-asset-agent -n 120 --no-pager

## 9. 文档目录

V1 交付文档位于：

    docs/v1_delivery/

主要包括：

- V1_部署与运行维护.md
- V1_使用说明.md
- V1_安全说明.md
- V1_回滚说明.md
- V1_验收清单.md

## 10. V1 状态

当前 V1 已完成建设并通过回归验收。

后续如发现新的自然语言解析边界问题，可按具体案例继续小步修复。
