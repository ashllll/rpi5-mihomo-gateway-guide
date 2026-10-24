# 下载分流与原生延迟选择

[Wiki 首页](../README.md) · [运维](operations.md) · [源码参考](reference.md)

本文描述生产运维层当前路由。它不代表真实订阅已配置、供应商授权已验证或设备已部署。检测默认关闭；一键安装器使用独立默认策略。

## 业务组和权限

| 工作负载 | 运行组 | 候选与规则 |
|---|---|---|
| 通用大文件下载 | Download | 仅来自 STT、ByWave 和部署清单明确授权的 GLaDOS 大流量叶节点 |
| GitHub Release | GitHub Download | 使用已授权下载叶节点；GitHub 控制域名由 GitHub Control 规则单独处理 |
| 模型文件 | Model Download | 使用现有模型下载授权节点 |
| npm / Yarn / Node 包 | Package Download | `registry.npmjs.org`、`registry.yarnpkg.com`、`unpkg.com`、`cdn.jsdelivr.net`、`nodejs.org` |

Download、GitHub Download、Model Download 和 Package Download 是扁平的原生 `url-test` 组，能够在各自已授权叶节点间自动检测延迟并切换。每个组使用相同的轻量健康 URL、期望状态和周期，并共享一个原生健康检查所有者，避免跨组重复探测。域名匹配不能识别任意 HTTPS 路径或应用进程；Git SSH、私有仓库认证、自定义 registry、Git LFS 与未列出的跳转域名需单独核验。

普通 GLaDOS AI、X 和 NF 节点不因同属一个订阅就进入下载候选。`roles.bulk_download.glados_explicit_allow` 只增加管理员明确列出的叶节点；`roles.bulk_download`、STT 隔离和现有供应商优先顺序仍由部署策略决定。规则数据不改变 AI、Streaming 或 Social 的权限。

## 检测与预算

路由与检测是两层契约：域名规则决定选哪个业务组，原生 `url-test` 决定这个组当前使用哪个获准叶节点。轻量健康 URL 只用于延迟和可达性选择，不模拟 Release 下载、Git pack、模型文件或包安装，也不测持续吞吐。它不会聚合单连接带宽；原生选择主要影响新连接。

生成模板默认 `measurement.enabled: false`，因此 URLTest 检测暂停。启用必须配置原生预算、每个候选节点唯一账户映射和受管核心预算服务；每次核心健康检测预扣 2 个账户尝试额度，失败不退还。共享账本沿用 `/var/lib/mihomo-measurements/quality-budget.json` 和历史 `quality_attempts_per_hour` 字段。预算、锁内策略复验及服务失联行为见[原生延迟选路与检测预算](../adaptive-selection.zh-CN.md)。

预算服务或策略不可用时，核心拒绝发起健康检测，不将拒绝记为节点失败；普通业务路由仍按现有配置执行。本轮没有启用预算服务、timer、线上检测或真实下载。

## 维护与验证

常规下载路由变更通过受管订阅候选生成、校验和加载流程应用。变更前应在部署端备份实际配置与 manifest，并核对候选节点标签、当前规则优先级和 provider 权限；仓库不保存这些真实数据。现有升级主机上的旧采样单元不会由新安装器自动删除，须由管理员现场确认旧作业状态后按维护流程处理。

离线回归由 `tools/check_overlay.py`、`tools/check_measurement_policy.py`、`tools/check_native_budget.py`、订阅候选/更新检查和 `tools/check_automation.py` 覆盖。它们验证合成规则、授权候选、同 URL 健康检查去重、预算预扣与失败关闭，不证明真实节点速度或目标下载成功。

现场仍需分别验证授权的 Release、Git、模型和包下载完整性、实际出口与续传表现，并确认 AI、Streaming、Social 未被改道。客户端的 registry 选择、依赖数量、并发、重试和包管理器状态也会影响安装时间。
