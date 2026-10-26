# 安全边界

[Wiki 首页](../README.md) · [SECURITY.md](../../SECURITY.md) · [架构](architecture.md)

权威发布红线与暴露面清单在 [SECURITY.md](../../SECURITY.md)；本页只解释为什么这样取舍。

## 信任边界

- 入站不再整口放行：`lo` 与 established/related 之外，`wan0`/`wlan0` 只允许来自
  `192.168.0.0/24` 的 SSH（tcp/22）与 rp5-agent（tcp/8799），以及 DHCP、ICMP 与
  上游 WireGuard 握手（udp/51891）。透明代理靠 `ip rule 9000` 与 `redirect`，
  不靠“整口可信”。
- `proxy20` 是单台 PC 试点：放行 DHCP、DNS、`ct status dnat` 与 `tcp/22`（管理口）；
  转发限去 `192.168.0.0/24`。`wg-ucg-pilot` 只允许进 Mihomo 或出 `wan0`。
- 本机管理调用固定走环回：mixed `127.0.0.1:7890`、controller `0.0.0.0:9090`
  （核心 `bind-address: "*"`）、dnsmasq `127.0.0.1:53`；secret 不进仓库。
- 防火墙只 `destroy table` 自有表，绝不 `flush ruleset`；Mihomo 的动态表归核心。

## 不成立的推断

- “端口少 = 更安全”：动态 redirect 端口不由静态规则表达。
- “有 controller secret = 可以放进不可信 LAN”：secret 不等于隔离。
- “离线检查通过 = 目标机已收紧”：有效规则以 `nft list ruleset` 与真实监听为准。
- 仓库不安装 Landscape、容器控制面或特权容器；相关研究只存在于
  [`docs/research/`](../research/)。

凭据、订阅、节点标签、私钥与真实 MAC 的发布规则见 [SECURITY.md](../../SECURITY.md)。
