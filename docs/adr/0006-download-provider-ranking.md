---
status: superseded
recorded: 2026-10-17
updated: 2026-10-24
---

# ADR-0006：按下载工作负载排序 provider，而非频繁重载核心

[ADR 索引](README.md) · [Wiki 首页](../README.md)

> **历史机制记录，已由 [ADR-0007](0007-native-routing-budget.md) 取代。** 2026-10-24 起不再运行下载吞吐采样、评分或动态 provider 排序。当前下载在既有授权扁平叶节点间使用原生 `url-test`，预算只由原生核心准入；当前契约见[下载分流](../wiki/download-routing.md)与[检测预算](../adaptive-selection.zh-CN.md)。下文保留此前设计和验收证据，不作为当前操作步骤。

真实下载测量表明，同一资产在不同供应商出口上的表现差异显著；轻量延迟与另一个 CDN 的探针不能代表它。
采用独立的 Release、HTTPS Git 和包下载工作负载，小流量采样后调整其候选顺序。
保留原生 fallback 与所有合格备用节点，不以轮询保证平均分配，也不声称获得单连接带宽聚合。

## 替代方案与后果

继续跨供应商轮询会把连接反复交给慢出口；每轮重写整份配置会扩大扰动范围。
因此首次接入受控加载新组，后续更新版本化 file provider 并刷新该 provider。
目标核心必须实测 payload 保底、刷新后的顺序和新请求选路；不能只依赖模拟 API。

能力约束包括中转链，普通 GLaDOS 不可作为隐藏下载出口。采样有读取、时间与并发预算；
主出口切换有收益门槛，备用排序独立维护。权限/限流响应与节点故障分开处理。
停止 timer 不恢复原规则，完整回退必须保留接入前配置和机制文件。
包下载和同域名 Git/网页不能仅靠 HTTPS 路径精确区分，规则覆盖范围须明确记录。

## 依据与验收

历史实现文件为 `automation/sbin/mihomo-download-quality` 与 `tools/check_download_quality.py`，二者于 2026-10-24 移除；[策略](../../automation/policy/capabilities.yaml)和[运行边界](../wiki/download-routing.md)仍维护当前业务规则。
验收覆盖真实核心行为、有界下载样本、实际 LAN 出口及其他业务不被改道；
小样本提速不代表长期吞吐或所有 DSH 安装操作均已验收。

## 2026-10-23 评分与预算修订

最终 overlay 默认暂停检测；质量采样另需 active_quality 和账户尝试额度，下载/X 共用账本。
排序文件变化会通过 watcher/PUT 启动整池检测，失败恢复旧文件也可能再次检测；首次改写前必须在共享账本中预留两版本全部检测开销，失败不退款。
没有足够额度则仅保留排序建议，不能先写文件再补账。当时核心原生周期与业务失败触发仍在任务账本之外；下述修订接管这些实际发起入口。
采用时间 EWMA、最少合格样本、TTL、连续失败/恢复、正常切换保持时间；限制与源站错误单独标记。
本地核心启动从交付字节率中剔除，仍不宣称测得持续吞吐。
完整约束、参考项目和未验收项见[测量与评分](../adaptive-selection.zh-CN.md)。

## 2026-10-24 原生实际发起准入

保留原生 fallback，给固定 v1.19.28 核心的 Proxy.URLTest 接入本地预算服务。
启动、周期、业务故障、刷新和回滚每次检测均先扣同一持久账本；拒绝发生在拨号和健康历史更新之前。
新 native_budget 模式取消旧的整池事务预扣，避免与核心准入双扣；额度不足时可以跳过检查，文件回滚仍执行。
质量采样含 observe/worker 均核对同核心、配置、账户策略和规范账本路径；符号链接别名保持同一把锁和文件。
旧 quality-only 预扣路径仅保留兼容，不能开启原生检测。受管启动拒绝原版或绑定错误核心。
见[核心源码与构建](../../core/README.md)及[统一预算](../adaptive-selection.zh-CN.md)。
本轮不部署、不启动预算服务、不恢复任何线上检测。

## 2026-10-24 原生选路简化（当前决定）

删除全部自研吞吐采样、评分与 provider 动态重排。Download、GitHub Download、Model Download 和 Package Download 都是扁平的原生 `url-test`，只包含既有授权叶节点，并共用一个轻量检查所有者；Streaming/Social/AI Research 使用按既有顺序的原生 fallback，TW Fixed IP 保持固定 `select`。每次原生检测通过同一本地 broker 预扣 2 次额度；沿用 `/var/lib/mihomo-measurements/quality-budget.json` 和 `quality_attempts_per_hour`，保留已有账户键与未过期历史。模板仍默认禁用检测。

当前实现不执行本 ADR 上文记录的样本下载、分数更新、provider 排序或质量专用事务。部署、平台播放、真实下载和 timer 状态均未由本轮离线源码变更证明。
