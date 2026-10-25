# 安全边界

[Wiki 首页](../README.md) · [SECURITY.md](../../SECURITY.md) · [架构](architecture.md)

权威发布红线与暴露面清单在 [SECURITY.md](../../SECURITY.md)；本页只解释为什么这样取舍。

## 信任边界

- `lan0` 入站整体放行：为了容纳 auto-redirect 的动态端口，固定端口白名单会漏掉新
  监听并切断透明代理。这是“可信内网”取舍，不是访客隔离方案。
- `wan0`/`wlan0` 只允许来自 `192.168.0.0/24` 的 SSH（tcp/22）与 rp5-agent（tcp/8799），
  以及 DHCP、ICMP 与上游 WireGuard 握手（udp/51891）。
- `proxy20` 是单台 PC 试点：只放行 DHCP、DNS 与 `ct status dnat`；转发限去
  `192.168.0.0/24`。`wg-ucg-pilot` 只允许进 Mihomo 或出 `wan0`。
- controller（`:9090`）与 mixed（`:7890`）都绑定 `192.168.1.1`，只对主 LAN；secret 不进仓库。
- 防火墙只 `destroy table` 自有表，绝不 `flush ruleset`；Mihomo 的动态表归核心。

## 不成立的推断

- “端口少 = 更安全”：动态 redirect 端口不由静态规则表达。
- “有 controller secret = 可以放进不可信 LAN”：secret 不等于隔离。
- “离线检查通过 = 目标机已收紧”：有效规则以 `nft list ruleset` 与真实监听为准。
- 仓库不安装 Landscape、容器控制面或特权容器；相关研究只存在于
  [`docs/research/`](../research/)。

凭据、订阅、节点标签、私钥与真实 MAC 的发布规则见 [SECURITY.md](../../SECURITY.md)。
