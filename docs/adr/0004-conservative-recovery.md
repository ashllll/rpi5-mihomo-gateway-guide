---
status: superseded
recorded: 2026-10-16
updated: 2026-10-24
---

# ADR-0004：只读观测，移除重复选路控制者

[ADR 索引](README.md) · [测量与评分机制](../adaptive-selection.zh-CN.md)

> **历史决策记录，已由 [ADR-0007](0007-native-routing-budget.md) 取代。** 本 ADR 记录 2026-10-16 的只读观测方案。当前约定见[原生延迟选路与检测预算](../adaptive-selection.zh-CN.md)。

原方案允许业务复测与可选隔离。连接开销审核后，改为核心原生选路、被动影子评分，
健康检查只看进程。旧 adaptive-apply 不再写配置、恢复 base-pools 或发请求。
默认停止测量不表示进程或业务故障；业务必须由真实客户端独立验收。

替代方案是继续维护两套故障切换和配置写入者，会增加重复测量、历史资格恢复及配置重载风险。
保留原生选路意味着源码的影子健康状态不会直接隔离节点，质量排序只作用于显式启用的专用 provider。

[进程检查](../../automation/sbin/mihomo-healthcheck)、历史只读采集程序 `automation/sbin/mihomo-adaptive-score`（2026-10-24 已移除）、
[回归](../../tools/check_stability.py)分别验证当时职责，离线通过不能替代线上恢复验收。
