# 架构与数据流（速览）

[Wiki 首页](../README.md) · [架构全文](../architecture.zh-CN.md) · [ADR](../adr/README.md)

本页只是入口。拓扑、nftables 规则顺序、`ip rule` 与门禁状态机的权威描述在
[架构文档](../architecture.zh-CN.md)；本页保留一张平面划分表，避免两处各写一份。

## 两个平面

| 平面 | 组件 | 负责 | 不负责 |
|---|---|---|---|
| 数据面 | `dnsmasq`、`mosdns`、Mihomo（TUN/redirect）、`inet rp_filter`/`ip rp_nat` | DNS 入口与三层分流、透明转发、选定出口 | 供应商授权、账号状态、吞吐保证 |
| 维护面 | 订阅/覆盖生成、预算 broker、健康探针、定时更新 | 生成候选、准入检测、互斥、恢复与告警 | 替客户端迁走既有连接 |

`nftables.service` 只载入自有表（`destroy table` 后重建），Mihomo 的动态表与
redirect 端口由核心自己管理；这是“不 `flush ruleset`”的原因。

## 证据链

客户端拿到 fake-IP 只证明解析成功。此后还要经过默认路由、`lan0` 转发、
`inet mihomo` 的 `dnat/redirect`、规则命中与节点出口；显式代理与网关本机
`curl` 都绕过了其中一段，不能替代真实 LAN 验收。判定“已就绪”只认
`/run/mihomo-healthcheck/status` 的 `tun/redirect/filter/route` 四要素。

当前基线是 IPv4 下游、无访客隔离、无双栈；Landscape 等外部方向只存在于
[research/](../research/)，不在数据面里。
