---
status: accepted
recorded: 2026-10-26
---

# ADR-0008：退役下游 LAN，网关只服务 UCG 单线双 VLAN

[ADR 索引](README.md) · [Wiki 首页](../README.md)

生产部署已改成「树莓派只有一根网线接 UCG 交换机」：VLAN1 承载上游与管理（`wan0`），802.1Q VLAN20 的
`proxy20` 服务 UCG 广播的 `RP5-PC-TEST` Wi-Fi 客户端（DHCP/DNS 由 `dnsmasq-vlan-pilot` 提供），
树莓派不再接独立 LAN 口，也不再自建 AP。于是 `lan0` / `192.168.1.0/24` 的 DHCP、DNS 作用域、masquerade
和 input 整口放行全部失去服务对象；它们还把核心与管理端口钉在一个会消失的地址上——`bind-address`
指向 `192.168.1.1` 时，混合端口与控制器不可达，健康门禁的出口抽样永远 `fail`，`business` 永久
`degraded`，而代理数据面其实是好的。

## 结论

- **删除 lan0 资产**：dnsmasq 的 LAN 作用域改为环回 resolver（`resolver.conf`，只监听 `127.0.0.1:53`）；
  `inet rp_filter` 的 input/forward 不再有 lan0 规则；`ip rp_nat` 不再做 LAN masquerade；
  `ip rule 9000` 的 iif 集合只剩 `{proxy20, wg-ucg-pilot}`；`config.local.yaml` 的 `include-interface`
  去掉 lan0。
- **管理面钉在环回**：核心 `bind-address: "*"`，混合端口 `127.0.0.1:7890`、控制器 `0.0.0.0:9090`
  （暴露面仍由 `inet rp_filter` input 收口），`lan-allowed-ips` 必须含 `127.0.0.1/32`；脚本删掉
  `GATEWAY_LAN_IP`，本机调用一律 `127.0.0.1`。出口抽样固定先走环回，因此 LAN 口状态不再影响门禁判定。
- **VLAN20 的管理入口**写在 include 的 `vlan-pilot.nft`（`proxy20` 上的 `tcp/22`），不靠整段放行。

## 替代方案

- **保留 lan0 作为可选下游段**：要同时维护「有 LAN」与「单线」两套拓扑、脚本里的地址歧义、以及门禁对
  链路状态的依赖，代价高于收益，否决。
- **在 UCG 上做 VLAN 终止、树莓派只做旁路**：失去单线可复现的透明代理位置，也让 DHCP/DNS 的真值散到
  两台设备，否决。

## 后果

- 不再提供树莓派自建下游网段与访客隔离；管理只能从上游 LAN 或 VLAN20 进入。
- 健康检查、告警与自愈不再因为 LAN 口掉线而误报。
- 文档与检查器以「三接口（`wan0` / `proxy20` / `wg-ucg-pilot`）」为基线；旧 LAN 相关描述只保留在
  带日期的历史记录里。

## 源码依据

`automation/etc/dnsmasq/resolver.conf`、`automation/etc/nftables.conf`、
`automation/etc/nftables.d/vlan-pilot.nft`、`automation/etc/mihomo/config.local.yaml`、
`automation/sbin/mihomo-healthcheck`、`automation/install.sh`。
