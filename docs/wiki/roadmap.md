# 路线图：已交付、已退役、开放项

[Wiki 首页](../README.md) · [运维](../operations.zh-CN.md) · [外部研究](../research/)

## 已交付（这台机器在跑）

| 能力 | 入口 |
|---|---|
| 部署镜像与 12 个 enabled 单元 | [`automation/install.sh`](../../automation/install.sh)、[部署](../deployment.zh-CN.md) |
| 自编译预算版核心 | [`core/`](../../core/README.md) |
| 三层 DNS 与模式机 | `mihomo-dns-mode`、[架构](../architecture.zh-CN.md) |
| 预算准入与账本 | `mihomo-measurement-budget`、[ADR-0007](../adr/0007-native-routing-budget.md) |
| 启动门禁与每分钟探针 | `mihomo-healthcheck`、[运维](../operations.zh-CN.md) |

## 已退役

- 影子评分与自动应用：2026-10-24 停止，unit 与脚本已删除。
- 旧的一键安装器、发布页与设计演示、旧现状图：与单机现状不符，已删除。
- 下载样本与 provider 动态排序：由原生 `url-test` + 预算准入取代（采样器保留但休眠）。

## 开放项

| 优先级 | 事项 | 需要先有 |
|---|---|---|
| 1 | 真实客户端验收基线（手机、视频、AI、下载、重启恢复） | 维护窗口与回滚演练 |
| 1 | 预算与告警的历史留存 | 脱敏方案与保留周期 |
| 2 | IPv6、访客隔离、容器控制面 | 单独 ADR 与隔离环境 |
| 3 | Landscape 等外部方向 | [research/](../research/) 里的门禁，先隔离验证 |

没有真实收益或无法区分原因时，先保住正常业务、记录最小脱敏证据，不再叠加调参。
不默认启用 BBR、超频或未经测试的内核参数。
