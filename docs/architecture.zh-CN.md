# 架构：这台 rp5 在跑什么

描述对象是 2026-10-25 的 rp5（Debian 13 / arm64 / Raspberry Pi 5）：
网络身份、内核与单元状态都取自 2026-10-25 对真机 rp5 的只读取证（`ssh rp5`，86 个文件快照）。
文档不包含订阅、节点、密钥与真实 MAC；表里的地址就是本机网段，方便对照 `ip a`。

## 1. 拓扑

```text
                          UCG 交换机（上游 LAN 192.168.0.0/24）
                              │  一根网线：VLAN1 + VLAN20
                              ▼
   ┌─────────────────────────────────────────────────┐
   │ rp5 · Debian 13 · 12 个 enabled 单元            │
   │  wan0 192.168.0.117/24（VLAN1：上游 + 管理）    │
   │  proxy20 192.168.20.1/24（802.1Q VLAN 20）      │
   │  nft: inet rp_filter / ip rp_nat                │
   │  ip rule 9000–9010 → table 2022                 │
   │  ┌───────────────┐  ┌──────────────────────┐    │
   │  │ Mihomo        │  │ mosdns  :5335        │    │
   │  │ TUN 198.18.0.1│  │ dnsmasq 环回 :53     │    │
   │  └───────────────┘  └──────────────────────┘    │
   └───┬─────────────────────────┬───────────────────┘
       │ wg-ucg-pilot            │ proxy20
       │ 10.253.254.1/30         │ 192.168.20.1/24
       ▼                         ▼
   UCG 点对点（WireGuard）       RP5-PC-TEST Wi-Fi 客户端
                                     （UCG 广播，802.1Q id 20）
```

| 接口 | 地址 | 归属 | 说明 |
|---|---|---|---|
| `wan0` | `192.168.0.117/24`（DHCP，VLAN1） | NetworkManager | 上游出口；管理入口只在这里与 `wlan0` |
| `proxy20` | `192.168.20.1/24` | NetworkManager（VLAN） | `wan0` 同一根线上的 802.1Q VLAN 20，服务 UCG 广播的 `RP5-PC-TEST` Wi-Fi 客户端；DHCP/DNS 由 `dnsmasq-vlan-pilot.service` 提供，租约 12 小时，静态租约 `.10` |
| `wg-ucg-pilot` | `10.253.254.1/30` | `wg-quick@wg-ucg-pilot` | UCG 点对点；UDP 51891 在 `wan0` 上放行 |
| `Mihomo` | `198.18.0.1/30` | Mihomo TUN | 网关侧 TUN 地址；客户端看到的是 fake-IP `198.18.0.1/16` |

`wlan0` 存在但未连接；`eth0` 不可用。IPv6 转发与解析均关闭（`ipv6: false`，`ipv6` 入站 drop）。

## 2. 数据面：三张表、一条规则链

### 2.1 `inet mihomo`（Mihomo 自建，运行期动态表）

单链 `prerouting`（`hook nat prerouting priority dstnat + 1`，policy accept），规则顺序即语义：

```text
iifname "Mihomo"                                   return   # 核心自身出站不劫持
iifname != { proxy20, wg-ucg-pilot }              return   # 只服务两个下游接口
ip daddr { 192.168.0.0/16, 224.0.0.0/4 }           return   # 私网与组播直连
ip saddr @inet4_local_address_set dport 53 (tcp/udp) dnat to 198.18.0.2:53
ip daddr @inet4_local_address_set                  return
tcp option mptcp exists                            drop
ipv6                                               reject
ipv4 tcp                                           redirect to :46225
```

`inet4_local_address_set` = `{10.253.254.0/30, 127.0.0.0/8, 172.17.0.0–172.21.255.255,
192.168.0.0/24, 192.168.20.0/24, 198.18.0.0/30}`。DNS 走 `dnat` 到 TUN 内地址，
TCP 走 `redirect` 到 `:46225`（auto-redirect 的动态监听端口），UDP 交给 TUN 的 auto-route。
`tcp option mptcp exists drop` 是有意的：MPTCP 连接绕过重定向会变成半断。

### 2.2 `inet rp_filter` + `ip rp_nat`（`/etc/nftables.conf`，`nftables.service` 载入）

文件用 `destroy table` 幂等，不 `flush ruleset`，否则会清掉 Mihomo 的动态表；
末尾 `include /etc/nftables.d/ucg-pc-link.nft` 与 `vlan-pilot.nft`。仓库副本在
`automation/etc/nftables.conf` 与 `automation/etc/nftables.d/`，`install.sh` 用
`install_default` 补缺、不覆盖本地改动。

- `input` policy **drop**：基础表放行 `lo`、`established/related` 与 ICMP；`wan0`/`wlan0`
  只放行来自 `192.168.0.0/24` 的 `tcp/22`、`tcp/8799`、DHCP（68/546）与上游
  WireGuard 握手 `udp/51891`。`proxy20` 与 `wg-ucg-pilot` 的服务规则全在 include 里：
  VLAN20 的 DHCP、DNS、`ct status dnat` 与 `tcp/22` 由 `vlan-pilot.nft` 放行
  （笔记本漫游到 `RP5-PC-TEST` SSID 时仍能 SSH 回来），WireGuard 的 dnat 与
  masquerade 由 `ucg-pc-link.nft` 放行。
- `forward` policy **drop**：基础表只放行 established/related；VLAN20 的
  `proxy20↔Mihomo`、`proxy20→wan0`（限去 `192.168.0.0/24`）与
  `wg-ucg-pilot→Mihomo/wan0` 由两个 include 放行。
- `ip rp_nat` 的 `postrouting` 自身不放 masquerade：`proxy20` 与 WireGuard 的 NAT
  在各自 include 文件里。

> 注意命名冲突：nft 表 `rp_filter` 与内核 sysctl `net.ipv4.conf.*.rp_filter` 是两件事。
> 本机 sysctl 取 `2`（loose，见 `/etc/sysctl.d/99-router.conf` 与各接口值）；
> 表名是历史遗留，不代表 sysctl 语义。

### 2.3 策略路由

```text
9000  from all iif {proxy20, wg-ucg-pilot}       goto 9002
9001  from all                                    goto 9010
9002  nop
9003  to 198.18.0.0/30                            lookup 2022
9004  not from all dport 53                       lookup main suppress_prefixlength 0
9004  from all iif Mihomo                         goto 9010
9005  not from all iif lo                         lookup 2022
9005  from 0.0.0.0 iif lo                         lookup 2022
9005  from 198.18.0.0/30 iif lo                   lookup 2022
9010  nop
```

表 `2022` 是 Mihomo 为 `198.18.0.2` 安装的拆分路由（`0.0.0.0/1`、`128.0.0.0/1` 等）。
`suppress_prefixlength 0` 让 DNS 查询在 `main` 表里只认更具体的路由，
配合 `dnat 198.18.0.2:53` 把查询强制送进 TUN，而不会被默认路由抢走。
9000 是门禁判定的那个规则号：没有它，`redirect/filter/route` 三项都不成立。

## 3. DNS 三层

```text
客户端（VLAN20）──53──► dnsmasq-vlan-pilot (proxy20 192.168.20.1)
                              │  server=127.0.0.1#1053，直投不看国内解析
                              ▼
                       Mihomo DNS 127.0.0.1:1053
                         └─ fake-IP 198.18.0.1/16；
                            fake-ip-filter 内的域名保留真实地址

网关本机 ──53──► dnsmasq 环回 (127.0.0.1)
                     │  上游读 /etc/dnsmasq-upstreams.conf（mosdns / mihomo / direct）
                     ▼
              mosdns 127.0.0.1:5335
              ├─ 国内域名/国内 IP ─► AliDNS / DNSPod（DoH，直连，concurrent 2）
              └─ 代理域名/其它     ─► Mihomo DNS 127.0.0.1:1053
```

1. **dnsmasq**：两个作用域。`resolver.conf` 只服务环回（`interface=lo`、
   `listen-address=127.0.0.1`），供网关自身工具与健康检查使用，`no-resolv` +
   `servers-file` 把上游选择外置，`cache-size=1000`；`vlan-pilot.conf` 由独立进程
   服务 `proxy20`，`server=127.0.0.1#1053` 直投 Mihomo DNS，`cache-size=0`
   （避免与 fake-IP 混存）。`host-record` 只登记网关自身名字。
2. **mosdns**：规则集来自 `/etc/mosdns/rules/current/*.txt`（`proxy-list`、`direct-list`、
   `apple-cn`、`google-cn`、`geoip-cn`）。`cache_cn` 只缓存国内真实答案，
   fake-IP 不进 MosDNS 长期缓存；`no_ecs` 关闭 ECS；`forward_cn` 并发请求
   `dns.alidns.com`（dial 223.5.5.5）与 `doh.pub`（dial 1.12.12.12）。
   服务以 `DynamicUser` 运行，`ProtectSystem=strict`。
3. **Mihomo DNS**：`enhanced-mode: fake-ip`，`fake-ip-range 198.18.0.1/16`，
   `fake-ip-filter` 内的本地/视频/游戏域名返回真实地址。客户端拿 fake-IP 建连，
   核心按规则选出口后，在代理内部用真实域名重新解析。

**上游模式机**：`mihomo-dns-mode` 原子改写 `/etc/dnsmasq-upstreams.conf`，
`mosdns` 启动后写 `mosdns`（127.0.0.1#5335），`mihomo` 启动后写 `auto`，
停止后写 `direct`（223.5.5.5 等）。所以“DNS 走谁”取决于当前哪个服务在跑，
不是配置文件里写死的。当前实测：`server=127.0.0.1#5335@lo`。

## 4. 预算准入

```text
mihomo.service.d/measurement-budget.conf
  ExecStart = /usr/local/sbin/mihomo-measurement-budget --run-core /usr/local/bin/mihomo
                  │
mihomo-measurement-budget.service（无参数）＝常驻 broker
                  │ 建 /run/mihomo-measurements (0700) 与 budget.sock (0600)
                  │ 持有 owner 锁；无请求时不动账本
                  ▼
        --run-core：核验核心 -measurement-budget-protocol == mihomo-budget-v1，
        读 config 与 capabilities，向 broker 取 status 并比对；全部一致才
                  ▼
        execve /usr/local/bin/mihomo，注入 MIHOMO_MEASUREMENT_SOCKET
        核心在显式节点拨号前发请求
                  request{protocol:"mihomo-budget-v1", name, addr, dialer, deadline_ms}
                  ├─ 拒绝：dialer-proxy、分组、非显式叶节点、超时窗口 >31s
                  └─ 通过：预扣 2 次尝试（reserve_native），失败不退还
```

预算外壳只做准入，不改路由、不代理流量；核心被 `--run-core` 拉起，所以
`systemctl restart mihomo` 会同时重置核心与 `--run-core` 进程；broker 服务独立常驻。
账本 `/var/lib/mihomo-measurements/quality-budget.json` 在**首次预扣**时才原子创建，
按账户记录尝试次数与上次预扣时间。当前账本有 4 个账户（账户名属于部署数据，不在仓库与文档中展开）。
配置侧由同一策略生成 7 个 `measurement-<16hex>` inline provider（18 个 proxy-group 引用）。
`mihomo-measurement-policy` 是同一份策略的库：订阅管线与预算服务共用它来生成/校验
原生 URLTest 的 health 字段与账户配额。未启用的质量采样（download/social）也只通过
`reserve` 支取同一份预算。

## 5. 门禁状态机

```text
启动 ─► ExecStartPre: mihomo -t -d /etc/mihomo        配置语法错误 → 不启动
     ─► ExecStart:   包装器拉起核心
     ─► ExecStartPost: healthcheck --startup-check 20
          TUN 设备 + inet mihomo + inet rp_filter + ip rule 9000
          失败 → 单元失败 → OnFailure mihomo-alert@%N
     ─► 常态: mihomo-healthcheck.timer 每分钟
          四要素 + 转发计数器；进程停了才尝试 systemctl restart（先抢维护锁）
          业务判定 business=verified|idle|failed|degraded
     ─► 每 5 分钟 --egress：经 socks5 127.0.0.1:7890 取 gstatic 204（不花预算）
     ─► 状态迁移才告警；mihomo-watch.timer 另查单元/温度/磁盘/内存/WAN
```

`/run/mihomo-healthcheck/status` 是唯一机读结论，格式为单行 `key=value`：

```text
2026-10-25T16:15:00+08:00 mode=process-running action=none business=verified \
  tun=1 redirect=1 filter=1 route=1 egress=ok packets=197703 fatal=none
```

- `mode`：探针运行模式；`action`：本次是否触发了恢复动作。
- `business`：`verified`（计数器在增长）、`idle`（链路在、无流量）、
  `failed`（四要素缺失）、`degraded`（egress 失败）。
- `tun/redirect/filter/route`：四个判据，任一为 0 即失败。
- `egress`：最近一次出口抽样；`packets`：转发计数快照；`fatal`：致命原因。
- 只在状态**迁移**时调用 `mihomo-alert --health`，避免每分钟刷屏。

`mihomo-healthcheck` 带参行为：无参 = timer 模式（抢 `/run/lock/mihomo-maintenance.lock`，
拿不到锁直接退出 0，互斥于订阅/规则/质量任务）；`--observe-only` 只读不重启；
`--startup-check [秒]` 供启动门禁；`--egress` 立即抽样。退出码 1 表示代理链路不可用。

## 6. 版本与包

| 组件 | 版本/来源 | 路径 |
|---|---|---|
| Mihomo | `v1.19.28-budget-status-contract-v3`，`go1.27.2`，`no_tailscale, with_gvisor` | `/usr/local/bin/mihomo`，构建见 [`core/`](../core/README.md) |
| MosDNS | `v5.3.4-0-gb732318` | `/usr/local/bin/mosdns` |
| dnsmasq | `2.91-1+deb13u2` | 系统包 |
| nftables | `1.1.3-1` | 系统包 |
| wireguard-tools | `1.0.20210914-3` | 系统包 |
| Python | `3.13.5` | 系统包（sbin 下 Python 脚本的解释器） |

版本串是门禁的一部分：换核后 `mihomo -v` 必须仍包含 `with_gvisor` 与
`budget-status-contract`，否则启动前的 `-t` 与运行期预算协议都可能失真。
