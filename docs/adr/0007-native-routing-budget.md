---
status: accepted
recorded: 2026-10-24
---

# ADR-0007：使用原生路由与单一检测预算

[ADR 索引](README.md) · [Wiki 首页](../README.md)

## 背景

业务测速、质量评分、provider 动态排序和 Mihomo 原生 URLTest 会重复检查相同节点，增加维护入口、重复网络开销和预算边界。下载还需要跨全部已授权候选自动切换；AI、固定地区与媒体流量则必须保留既有池权限及偏好顺序。

## 决策

媒体补充决策（2026-10-31）：Social / Streaming 经用户授权启用可选的逐节点媒体速度准入，
合格成员仍由原生 fallback 选择，沿用共享计量账本；不涉及下载或 AI 池。
本 ADR 中媒体组仅按原始顺序、无吞吐样本的约束在此范围内由[当前媒体策略](../wiki/x-video-routing.md)替代。

- 使用 Mihomo 原生 `url-test` 管理 Auto、Developer 和扁平的授权 Download、GitHub Download、Model Download、Package Download 叶节点池；不添加吞吐样本、评分状态或动态 provider 排序。
- 使用原生 `fallback` 保留 AI Research、Streaming、Social 的既有节点顺序；TW Fixed IP 使用固定 `select`。YouTube 与 Instagram 复用 Streaming，X 复用 Social；域名表不能扩大节点权限或跨业务池路由。
- 生成配置设置 `unified-delay: false`。它不替代原生核心的截止时间修复。
- 原生健康检测默认关闭，由部署者显式开启；开启后每次检查由固定核心版本在拨号前向本地 broker 申请预算。broker 仅接受明确的叶节点身份，锁内重新读取配置与策略并以文件方式校验。
- 每个原生检测固定预扣 2 次尝试，沿用 `/var/lib/mihomo-measurements/quality-budget.json` 与 `quality_attempts_per_hour`；保留已有账户键和未到期历史。失败不退款；失联拒绝检测，不伪造节点失败，也不影响业务路由。
- 同 URL、期望状态和周期共享单一健康检查所有者；锁内不调用 HTTP RPC。

## 后果与验证

普通浏览及获准下载可以由核心自动根据延迟或可达性选择节点，Streaming/Social/AI fallback 保持管理员排序。原生健康探针只用于选路，不证明平台解锁、登录、真实播放、下载完整性或持续吞吐；这些仍需独立客户端验收。超额预算可能跳过健康检测，但不会停止业务连接或重置账本。

当前源码以 `check_overlay.py`、`check_measurement_policy.py`、`check_native_budget.py`、订阅回归和安装器回归验证合成配置及账本边界。本次只完成离线源码变更，没有部署服务、启用 timer、连接真实节点或进行公网测速；真实播放与下载验收仍未完成。前序评分与下载样本方案保留在[历史 ADR](0004-conservative-recovery.md)与[历史下载 ADR](0006-download-provider-ranking.md)中。
2026-10-25 现状：预算服务与原生检测已在这台 rpi5 上运行（25 处 `measurement-` 检测经 broker 预扣）；
上述“未部署”结论属于当时的离线阶段，操作口径以[运维文档](../operations.zh-CN.md)为准。
