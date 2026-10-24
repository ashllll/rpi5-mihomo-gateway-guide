# RepoWiki：透明代理网关知识库

从“为什么这样设计”到“怎样部署、验证和恢复”的统一入口。文档随源码版本管理，离线可读，
无需额外 Wiki 服务或 GitHub Wiki 仓库。返回[项目首页](../README.md)。

## 阅读前先确认

本次策略更新日期：2026-10-24。工作区基于 `55cdd097a4ea4c8c4957361a4bdf6f3835694de1`，包含尚未提交的原生选路简化；
这不是 GitHub 发布收据，也不是实时设备状态页。部署端配置、供应商授权和实际节点可用性必须现场核对。

| 标记 | 含义 |
|---|---|
| 已实现 | 当前源码存在对应行为，不代表已在你的机器上线 |
| 模板 / 参考 | 需要部署参数、节点能力、规则和现场整合 |
| 待验收 | 需要真实硬件或用户端证据，脚本通过不能代替 |
| 提议 | 还未实现，不可作为现有功能使用 |
| 历史 | 某时刻的记录，不是当前配置的权威来源 |

源码与随版本维护的文档共同说明机制；设备运行配置说明当前部署；历史日志只说明当时发生过什么。
遇到冲突，先确认版本与部署路径，不直接按旧文档覆盖生产配置。

## 按目标进入

| 目标 | 起点 | 下一步 |
|---|---|---|
| 第一次了解或接手 | [入门与部署选择](wiki/getting-started.md) | [架构与数据流](wiki/architecture.md) |
| 阅读代码与配置 | [源码及配置参考](wiki/reference.md) | [术语表](../GLOSSARY.md) |
| 理解设计取舍 | [ADR 索引](adr/README.md) | 对应源码与验收条件 |
| 新机器安装 | [一键配置指南](one-click-setup.zh-CN.md) | [测试与验收](wiki/validation.md) |
| 手动上线已有方案 | [分阶段部署](deployment-current.zh-CN.md) | [自动化层](../automation/README.zh-CN.md) |
| 网络故障与恢复 | [运维手册](wiki/operations.md) | [真实 LAN 验收](operations-current.zh-CN.md) |
| 安全及公开发布 | [安全边界](wiki/security.md) | [安全政策](../SECURITY.md) |
| 了解原生检测与预算 | [能力现状与路线图](wiki/roadmap.md) | [原生选路与预算](adaptive-selection.zh-CN.md) |
| GitHub / 模型 / 包下载 | [下载分流](wiki/download-routing.md) | [运维手册](wiki/operations.md) |
| YouTube / Instagram / X 路由 | [媒体业务池与验证边界](wiki/x-video-routing.md) | [真实应用验收](wiki/validation.md) |
| 修改或维护这份 Wiki | [文档维护约定](wiki/maintenance.md) | [测试矩阵](wiki/validation.md) |

## 先识别自己的部署路径

| 路径 | 入口 | 不应混用的边界 |
|---|---|---|
| A：新主机一键安装 | [setup.sh](../setup.sh) → [gateway/setup.py](../gateway/setup.py) | 独立 `mihomo-gateway*` 单元和目录；不调用生产 overlay |
| B：手动候选生成 | [tools/render.py](../tools/render.py) | 只生成私有文件，不安装、不切换网络 |
| C：已有网关自动化 | [automation/install.sh](../automation/install.sh) | 安装脚本/单元，保留既有部署数据；不是完整网络安装器 |

恢复镜像是另一条恢复路径，不是上述入口的自动升级包。详见[恢复镜像](recovery-image.zh-CN.md)。

## 文档地图与权威范围

| 文档集合 | 负责回答 | 不负责回答 |
|---|---|---|
| 本 RepoWiki | 当前机制、入口、取舍、操作导航与证据要求 | 个人订阅、实时节点排名、账号可用性 |
| [自动化层说明](../automation/README.zh-CN.md) | 脚本、timer、安装语义与部署参数 | 一键安装的默认行为 |
| [当前架构参考](current-architecture.zh-CN.md) | 双网口拓扑、DNS 分层及生产方案约束 | 最新机器性能或实时服务状态 |
| [原生选路与预算](adaptive-selection.zh-CN.md) | 原生检查所有权、账户账本和业务池策略 | 平台解锁、真实播放或下载性能保证 |
| [早期调试档案](deployment-debugging-summary.zh-CN.md) | 历史演进与故障背景 | 当前 timer、安装入口和配置真值 |
| Landscape 研究 | [可行性](landscape-feasibility.zh-CN.md)、[能力报告](landscape-capabilities.zh-CN.md)、[验收计划](landscape-acceptance.zh-CN.md) | 已上线集成；目前仍是候选路线 |

已有[拓扑图](../assets/current-topology.svg)、[DNS 图](../assets/current-dns-flow.svg)和
[业务策略图](../assets/current-routing-policy.svg)描述生产参考方案，不是三条部署入口共用的完整默认配置。
旧自动化评分与应用流程图已归档为[历史图](../assets/historical-automation-2026-10-11.svg)，
仅供对照；当前行为以原生选路与预算文档为准。

## 本项目不作的承诺

- 节点低延迟不等于平台可用、视频吞吐好或供应商允许下载。
- X API 可达、ChatGPT 命令行响应、YouTube 204 都不能证明登录后的真实业务成功。
- 检测周期不等于恢复时间；连接切换不能迁移既有 TCP / WebSocket 会话。
- 可信 LAN 防火墙不是访客隔离或公网管理方案。
- 文档完整不等于所有平台、硬件、停电恢复和长期稳定性已经验收。

需要新结论时，请按[验证分层](wiki/validation.md)补证据，而不是修改措辞来掩盖未知状态。
