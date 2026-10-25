---
status: accepted
recorded: 2026-10-16
---

# ADR-0002：Mihomo 保持代理选路与 fake-IP 真值

[ADR 索引](README.md) · [Wiki 首页](../README.md)

多供应商代理需要成熟的协议、规则和组健康检查。继续使用 Mihomo 负责代理出站、TUN/auto-redirect
以及 fake-IP；dnsmasq 提供客户端入口，生产参考可加入 MosDNS 分类层，但不建立第二套 fake-IP 映射。

## 替代方案

让多个代理/DNS/路由控制器各自接管流量会产生重复劫持、环路和难以归因的选择。
直接用新路由平台替换核心也会带来规则、协议和会话迁移成本。Landscape 当前只做评估，不默认为已接管。

## 后果

- 核心管理的动态表和监听端口必须保留，外部防火墙只管理自身基础表。
- 手动生成基线是 dnsmasq → Mihomo；MosDNS 需要单独准备数据和候选验收。
- fake-IP DNS 成功不能证明透明转发成功；必须从真实 LAN 验证。
- IPv4 下游是当前基线，双栈和 eBPF 融合需要独立设计、回退与验收。

## 依据与验收

[MosDNS 配置](../../automation/etc/mosdns/config.yaml)、[架构](../architecture.zh-CN.md)、
[Landscape 验收设计（外部研究）](../research/landscape-acceptance.zh-CN.md)、
[真机部署记录](../deployment.zh-CN.md)。
验收覆盖 DNS 映射、规则命中、动态入口及真实 TCP/UDP，而不是只检查进程 active。
2026-10-25 复核：旧一键生成器与其默认值已删除，本 ADR 的“手动生成基线”改为
本机实际状态机（`mihomo-dns-mode` 在 mosdns/mihomo/direct 间切换）。
