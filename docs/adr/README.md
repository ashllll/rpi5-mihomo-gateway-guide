# 架构决策记录（ADR）

[Wiki 首页](../README.md) · [架构](../wiki/architecture.md)

0001–0005 于 2026-10-16 根据当前源码和已有约束补录，记录日期不是原始决策发生日期；后续记录按实际讨论日期新增。
`accepted` 表示当前源码采用此取舍，不表示所有部署和真实业务已经验收；`superseded` 保留历史理由但不再是现行实现。

| ADR | 状态 | 记录的取舍 |
|---|---|---|
| [0001：机制与部署数据分离](0001-private-deployment-data.md) | accepted | 可复用源码与不可公开的运行数据分开 |
| [0002：Mihomo 作为代理与 fake-IP 真值](0002-mihomo-authority.md) | accepted | 保留成熟代理核心，外部模块不重复接管规则 |
| [0003：能力约束先于节点质量](0003-capability-policy.md) | accepted | 先限定可用业务池，再比较连接质量 |
| [0004：观测与执行分离](0004-conservative-recovery.md) | superseded by 0007 | 保留原生核心职责及旧只读评分方案的历史理由 |
| [0005：可信 LAN 与动态透明入口](0005-trusted-lan.md) | superseded by 0008 | 容纳动态 redirect，明确不提供访客隔离 |
| [0006：下载工作负载与 provider 排序](0006-download-provider-ranking.md) | superseded by 0007 | 归档有界下载采样与 provider 排序决策 |
| [0007：使用原生路由与单一检测预算](0007-native-routing-budget.md) | accepted | 原生 URLTest/fallback、授权池与单一账户预算 |
| [0008：退役下游 LAN](0008-retire-downstream-lan.md) | accepted | 单线双 VLAN；管理钉环回，不再有 lan0 网段 |

只有代价较高、有真实替代方案、未来容易被误改的取舍进入 ADR。探测 URL、端口和 timer 值属于配置参考，
不为每个常数建立 ADR。Landscape 等未上线方向进入[路线图](../wiki/roadmap.md)，不是既有决策。

新增记录使用顺序编号。改变既有决策时新增 ADR，并把旧记录标为 `superseded by ADR-NNNN`；
不要删去原来的理由。每条记录至少包含背景、结论、替代方案、后果和源码依据。
