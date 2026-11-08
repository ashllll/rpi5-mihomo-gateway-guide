# 文档索引：这台 rp5 网关

README 回答“它在跑什么”，这里回答“为什么这样、怎么装、怎么修”。
文档随仓库版本管理，离线可读；描述对象是**本机现状**，不是通用安装手册。
返回[项目首页](../README.md)。

## 先看哪一篇

| 你想知道 | 读这篇 |
|---|---|
| 当前全设备分流、流量归属、GitHub API 配额与限制 | [当前运行策略](global-routing-and-traffic.zh-CN.md) |
| 拓扑、nft 数据面、策略路由、三层 DNS、预算准入、门禁状态机 | [架构](architecture.zh-CN.md) |
| 从裸 Debian 13 到本机状态的安装顺序与验收判据 | [部署](deployment.zh-CN.md) |
| 状态文件怎么读、timer 清单、更新与回滚、账本位置 | [运维](operations.zh-CN.md) |
| 历史上的故障、误判与调试过程（档案，不是当前配置真值） | [调试档案](deployment-debugging-summary.zh-CN.md) |
| 术语（TUN、fake-IP、auto-redirect、预算准入、门禁、包装器、休眠单元） | [术语表](../GLOSSARY.md) |
| UCG 点对点链路（`wg-ucg-pilot`）的 2026-10 试点记录（拓扑部分已被 ADR-0008 取代） | [UCG WireGuard 试点（历史）](ucg-wireguard-pilot.zh-CN.md) · [ADR-0008](adr/0008-retire-downstream-lan.md) |
| 恢复介质怎么准备、能恢复什么 | [恢复镜像边界](recovery-image.zh-CN.md) |
| 暴露面与发布红线 | [SECURITY.md](../SECURITY.md) |
| 外部项目调研（Landscape、能力报告、验收计划） | [research/](research/) |
| 分主题的常读文档（入门、架构、运维、安全、验证等） | [wiki/](wiki/) |
| 设计决策与历史取舍 | [ADR 索引](adr/README.md) |
| 拓扑、设备清单与本地 CI | [网络拓扑与设备清单](网络拓扑与设备清单.md) |
| UCG Max 调研结论与端点清单（原始抓取不随仓库发布） | [ucg-max-research/](ucg-max-research/) |

## 文档的权威范围

| 文档 | 负责 | 不负责 |
|---|---|---|
| README / 架构 / 部署 / 运维 | 这台机器的机制、判据与操作顺序 | 实时状态（看 `/run/mihomo-healthcheck/status` 与 `systemctl`） |
| [调试档案](deployment-debugging-summary.zh-CN.md) | 当时发生了什么、错在哪里 | 当前 timer、配置或安装入口 |
| [research/](research/) | 外部项目的可行性与候选路线 | 本仓库已交付的能力 |
| [ADR](adr/README.md) | 决策与替代方案 | 现场验收声明 |

首页已于 2026-11-08 对照代码和现场更新。旧拓扑图、WireGuard 试点及历史操作片段不应作为当前部署前提；当前入口与分流以[项目首页](../README.md)和[运行策略](global-routing-and-traffic.zh-CN.md)为准。

机读真值优先于文档：`/etc/mihomo/config.yaml`、`/etc/nftables.conf`、
`automation/etc/**` 与 `systemctl` 输出冲突时，以机器为准，然后回来改文档。

## 本项目不作的承诺

- 节点低延迟不等于平台可用、视频吞吐好或供应商允许下载。
- 探针 204 不等于登录后的真实业务成功。
- 检测周期不等于恢复时间；连接切换不能迁移既有 TCP / WebSocket 会话。
- 可信 LAN 防火墙不是访客隔离或公网管理方案。
- 恢复镜像与发布工件不等于本机已通过整机重刷验收。
