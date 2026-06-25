# NetAIOps Asset Agent V2 Web 前端测试用例

## 1. 测试前确认

测试地址：

    http://10.191.97.151:18081/

测试目标：

1. 确认 V1 CMDB 查询仍然正常。
2. 确认 V2 路由表类问题进入 v2_chat_router。
3. 确认 V2 CPU 类问题返回 Prometheus 当前 CPU 证据。
4. 确认 V2 只生成 Netmiko 建议命令，不自动执行。
5. 确认不带 YES 的执行确认不会触发命令执行。
6. 确认带 YES 后才执行上一轮第 N 条 passed 命令。
7. 确认执行结果包含初步分析、关键证据、判断、建议下一步。
8. 确认危险命令不会被执行。

## 2. V1 基础 CMDB 查询

### 用例 1：按设备名查资产

输入：

    SH8-G03-DCI-BN-SW01

预期：

1. 返回 CMDB 资产信息。
2. 设备名应包含 SH8-G03-DCI-BN-SW01。
3. 管理 IP 应包含 10.192.251.101。
4. 不应出现 V2 命令建议。

### 用例 2：按管理 IP 查资产

输入：

    10.192.251.101

预期：

1. 返回 CMDB 资产信息。
2. 能看到设备名 SH8-G03-DCI-BN-SW01。
3. 不应触发 Netmiko 执行。

## 3. V2 路由表命令建议

输入：

    SH8-G03-DCI-BN-SW01的路由表有多少条

预期：

1. 回答中出现“已进入 V2 排障取证流程”。
2. 设备解析结果应为：
   - CMDB 主机名：SH8-G03-DCI-BN-SW01
   - 管理 IP：10.192.251.101
   - Netmiko 设备名：SH8-G03-DCI-BN-SW01
   - 设备类型：cisco_nxos
3. 返回路由表相关建议命令，例如：
   - show ip route summary
   - show ipv6 route summary
   - show ip route | count
4. 命令安全校验状态应为 passed。
5. 不应自动执行命令。

## 4. V2 CPU + Prometheus 当前证据

输入：

    SH8-G03-DCI-BN-SW01当前CPU利用率是多少？我该通过哪些命令排查？

预期：

1. 回答中出现“已进入 V2 排障取证流程：CPU 利用率排查”。
2. 回答中出现“Prometheus 当前 CPU 证据”。
3. 当前已验证可命中的 PromQL 类似：

    avg(cpmCPUTotal1minRev{ip="10.192.251.101"})

4. 回答中应展示当前 CPU 值。
5. 返回 Netmiko 建议命令，例如：
   - show system resources
   - show processes cpu
   - show processes cpu sort
   - show logging last 100
6. 不应自动执行设备命令。

## 5. 对话式确认：不带 YES 不执行

前提：先执行第 4 节 CPU 问题，让系统返回建议命令。

输入：

    确认执行第1条命令

预期：

1. 系统提示需要显式确认。
2. 状态应为 pending_confirmation。
3. 不应返回真实设备命令输出。
4. 不应执行设备命令。

## 6. 对话式确认：带 YES 执行

前提：先执行第 4 节 CPU 问题，让系统返回建议命令。

输入：

    确认执行第1条命令 YES

预期：

1. 系统执行上一轮第 1 条 passed 命令。
2. 默认第 1 条应为：

    show system resources

3. 回答中出现：
   - 已确认执行第 1 条只读命令
   - 初步分析
   - 关键证据
   - 判断
   - 建议下一步
   - 原始输出预览
4. 输出中应能看到 NX-OS system resources 相关内容，例如 Load average、CPU states、Memory usage。
5. 后端应生成 Netmiko 审计文件。

## 7. 错误序号测试

前提：先执行第 4 节 CPU 问题，让系统返回建议命令。

输入：

    确认执行第99条命令 YES

预期：

1. 系统提示没有第 99 条命令。
2. 不执行任何设备命令。

## 8. 无上下文确认测试

打开新会话后直接输入：

    确认执行第1条命令 YES

预期：

1. 系统提示没有找到上一轮待确认命令。
2. 不执行任何设备命令。

## 9. 危险命令安全策略

当前前端不会直接让用户输入任意命令执行。后端接口层已具备如下策略：

1. configure terminal 为 blocked。
2. show running-config 为 review。
3. 只有 passed 且 confirm_execute=YES 才能执行。
4. Netmiko MCP 的配置工具 set_config_commands_and_commit_or_save 不暴露给前端。

## 10. 测试结论记录模板

测试人员：

测试时间：

浏览器：

测试结果：

| 用例 | 是否通过 | 备注 |
|---|---|---|
| V1 按设备名查资产 |  |  |
| V1 按 IP 查资产 |  |  |
| V2 路由表建议命令 |  |  |
| V2 CPU + Prometheus 证据 |  |  |
| 不带 YES 不执行 |  |  |
| 带 YES 执行并分析 |  |  |
| 错误序号不执行 |  |  |
| 无上下文确认不执行 |  |  |


## 11. V2 多轮上下文冒烟测试

以下测试用于验证 V2 是否能支撑连续多轮对话。

### 第 1 轮

输入：

    WG88-SW-H15-1当前CPU利用率异常，给我第一批排查命令

预期：

- 进入 v2_chat_router。
- 识别设备 WG88-SW-H15-1。
- 返回 Prometheus 当前 CPU 证据。
- 返回 4 条 CPU 排查建议命令。

### 第 2 轮

输入：

    将你上述给出的命令在设备上执行，然后根据命令的结果给出分析

预期：

- 进入 v2_execution_confirmation。
- 不执行命令。
- 提示需要“确认执行全部命令 YES”。

### 第 3 轮

输入：

    确认执行全部命令 YES

预期：

- 批量执行上一轮 passed 只读命令。
- 返回命令执行结果摘要。
- 返回综合分析。
- 返回建议下一步。

### 第 4 轮

输入：

    结合以上三点，给出更准确的结论，当前是否真的是CPU问题？

预期：

- 进入 v2_followup_analysis。
- 不要求重新输入设备名。
- 沿用上一轮 V2 会话上下文。
- 给出综合结论。

### 第 5 轮

输入：

    如果CPU不高，下一步应该排查什么？还需要查Prometheus历史趋势吗？

预期：

- 进入 v2_followup_analysis。
- 回答中提到 Prometheus 历史趋势。
- 不回退 V1。

### 第 6 轮

输入：

    这个设备的路由表有多少条？

预期：

- 进入 v2_chat_router。
- 继承当前设备 WG88-SW-H15-1。
- 返回路由表相关建议命令。

### 第 7 轮

输入：

    继续查看当前设备CPU，还需要哪些命令？

预期：

- 进入 v2_chat_router。
- 继承当前设备。
- 识别为 CPU 排查问题。

### 第 8～12 轮

可继续输入：

    根据刚才结果，还需要继续看CPU历史趋势吗？
    总结一下目前这个设备CPU排查到的结论
    根据刚才结果，下一步要重点看哪些日志？
    继续给我下一批排查命令
    这些结果说明设备本身有问题吗？

预期：

- 不回退 V1。
- 不要求重新输入设备名。
- 能基于上下文继续回答。
- recent_turns、rolling_summary 持续更新。
