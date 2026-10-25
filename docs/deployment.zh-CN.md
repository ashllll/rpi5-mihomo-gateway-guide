# 部署：从裸 Debian 13 到本机状态

按顺序重建这台 rp5。每步都有可判定的验收点；某步失败就停在那里，不要跳步。
仓库提供运行层（脚本、单元、配置骨架），**不提供**网络事务、订阅内容、节点数据与密钥。

## 0. 前提与目标状态

| 项目 | 本机取值 |
|---|---|
| 硬件 | Raspberry Pi 5、arm64、两块有线网口（`wan0` 上联、`lan0` 下联） |
| 系统 | Raspberry Pi OS Lite / Debian 13，systemd 252+，NetworkManager |
| 附加接口 | `wan0` 上的 802.1Q VLAN 20（`proxy20`）、WireGuard `wg-ucg-pilot` |
| 网关地址 | `lan0 192.168.1.1/24`、`proxy20 192.168.20.1/24`、`wg-ucg-pilot 10.253.254.1/30` |
| 上游 | `wan0` 由上游 DHCP 分配（本机 `.117`），SSH 与 8799 只对上联私网开放 |
| 运行层 | 12 个 enabled 单元、4 个 disabled timer、6 个 `mihomo.service.d` drop-in |

## 1. 基础包与 sysctl

```sh
sudo apt update
sudo apt install -y nftables dnsmasq wireguard-tools python3 curl ca-certificates
sudo systemctl enable --now nftables
```

本机版本：dnsmasq `2.91-1+deb13u2`、nftables `1.1.3-1`、wireguard-tools `1.0.20210914-3`、
python3 `3.13.5-1`。Mihomo 与 MosDNS 不用系统包，来自 `core/` 构建与官方发布。

`/etc/sysctl.d/99-router.conf`：

```ini
net.ipv4.ip_forward=1
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
net.ipv4.conf.all.accept_redirects=0
```

各接口的 `net.ipv4.conf.*.rp_filter` 本机为 `2`（loose）。不要写成全局 `0`：
`inet rp_filter` 的 forward 白名单依赖反向路径检查拦住伪造源。

验收：`sysctl net.ipv4.ip_forward` 为 1；`ip -br addr` 里四个接口都有地址。

## 2. 网络身份（手工一次性）

NetworkManager 管三个连接，WireGuard 用 wg-quick：

```sh
# 上联：DHCP 拿地址与默认路由
nmcli con show                       # 确认 wan0 / lan0 / proxy20 三个连接
# 下联：静态地址 + 内置 DHCP/DNS 由 dnsmasq 负责
nmcli con mod lan0 ipv4.addresses 192.168.1.1/24 ipv4.method manual
# VLAN 试点：wan0 上的 802.1Q VLAN 20
nmcli con add type vlan con-name proxy20 dev wan0 id 20 \
  ipv4.method manual ipv4.addresses 192.168.20.1/24
sudo systemctl enable --now wg-quick@wg-ucg-pilot
```

`dnsmasq` 的 `interface=` 与 `listen-address` 必须和这三个连接一致；`proxy20` 的
静态租约只给一台试点 PC，租约目录在 `/run/dnsmasq-vlan-pilot/leases`。
WireGuard 只做点对点：UDP 51891 在 `wan0` 放行，对端 `10.253.254.2`。

验收：`nmcli device` 中 `wan0`、`lan0`、`proxy20` 都是 connected，`wg show` 有握手。

## 3. 数据面 nftables

`/etc/nftables.conf` 定义两个自有表并 `include /etc/nftables.d/*.nft`：

```sh
sudo nft -c -f /etc/nftables.conf    # 语法检查，不改运行状态
sudo systemctl reload nftables       # 幂等 destroy/重建自有表
sudo nft list tables                 # 应看到 inet rp_filter 与 ip rp_nat
```

- `inet rp_filter`：input/forward 默认 drop，白名单见[架构文档](architecture.zh-CN.md)。
- `ip rp_nat`：`192.168.1.0/24` 出 `wan0` 做 masquerade。
- `include` 的两个文件分别是 WireGuard 试点与 VLAN 试点规则，属于部署端内容，
  **不随仓库发布**；换机器时必须按新接口重写，不能照抄地址。

验收：`nft list chain inet rp_filter input` 有 `policy drop` 与 LAN 放行；
`nft list chain ip rp_nat postrouting` 有 masquerade。

## 4. 运行层安装

`automation/install.sh` 按固定顺序做幂等安装：机制文件（`sbin`、`systemd`）覆盖写入；
配置骨架与示例只在目标不存在时写入，保留部署端已改内容。脚本只 enable 本机在用的 11 个单元，
其余装上但保持 disabled；`enable` timer 不会立刻启动它。

```sh
git clone https://github.com/ashllll/rpi5-mihomo-gateway-guide.git
cd rpi5-mihomo-gateway-guide
sudo automation/install.sh --dry-run     # 只打印计划
sudo automation/install.sh               # 真机安装
```

安装后的落点：

| 仓库 | 目标 |
|---|---|
| `automation/sbin/*` | `/usr/local/sbin/`（0755，覆盖） |
| `automation/systemd/**` | `/etc/systemd/system/`（0644，覆盖，含 `.d/` drop-in） |
| `automation/etc/dnsmasq/lan.conf` | `/etc/dnsmasq.d/lan.conf` |
| `automation/etc/dnsmasq/vlan-pilot.conf` | `/etc/dnsmasq-vlan-pilot.conf` |
| `automation/etc/dnsmasq/upstreams.conf` | `/etc/dnsmasq-upstreams.conf`（仅缺失时 seed） |
| `automation/etc/dnsmasq-upstreams.*` | `/etc/mihomo/`（模式机基线，保留已有） |
| `automation/etc/mosdns/config.yaml`、`etc/mosdns/rules/*` | `/etc/mosdns/config.yaml`、`/etc/mosdns/rules/` |
| `automation/policy/*.yaml` | `/etc/mihomo/adaptive/{capabilities,route-defaults}.yaml` |
| `automation/etc/mihomo/config.local.yaml` | `/etc/mihomo/config.local.yaml`（本地覆盖段） |
| `automation/env/*.example` | `/etc/mihomo-automation.env.example` 等（0600，需人工复制） |

安装还会以 0700 创建 `/etc/mihomo/{secrets,adaptive,providers}` 与
`/var/lib/mihomo-measurements`、`/var/lib/mihomo-extra-subscriptions`、
`/var/lib/mihomo-subscription-backups`。本机环境文件是唯一存放地址与端口的部署数据文件；
仓库里只有示例，安装器不会把示例改名为正式文件。
没有 `--enable-timers` 这一步：安装脚本按真机状态直接处理 enable/disable。

验收：`systemctl list-unit-files --state=enabled | grep -E 'mihomo|mosdns|dnsmasq'`
正好是 11 行（6 个 service + 5 个 timer）；`ls /etc/systemd/system/mihomo.service.d/` 有 6 个 `.conf`。

## 5. 核心、DNS 与配置生成

顺序不能反：Mihomo 二进制与配置先就位，mosdns 才能转发，dnsmasq 才有上游。

```sh
# 5.1 自编译核心（带预算补丁）：完整命令与 SHA 校验见 core/README.md
git clone --depth 1 --branch v1.19.28 https://github.com/MetaCubeX/mihomo.git mihomo-budget-src
git -C mihomo-budget-src apply core/mihomo-v1.19.28-budget.patch
cd mihomo-budget-src
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath \
  -tags "with_gvisor no_tailscale" \
  -ldflags "-X github.com/metacubex/mihomo/constant.Version=v1.19.28-budget-status-contract-v3" \
  -o ../mihomo ./                        # 版本串与真机一致；以 core/README.md 为准
sudo install -m0755 ../mihomo /usr/local/bin/mihomo
/usr/local/bin/mihomo -v | grep -q 'with_gvisor'

# 5.2 运行配置：订阅管线生成 proxies / proxy-groups / rules / provider，
#     本地覆盖段（tun / dns / sniffer / rule-providers / 端口）来自 automation/etc/mihomo/
sudo /usr/local/bin/mihomo -t -d /etc/mihomo

# 5.3 启动顺序由单元保证：nftables → dnsmasq（写 direct 基线）→ mihomo（写 auto）
#     → mosdns（写 mosdns 上游）；停止时反向回退
sudo systemctl start dnsmasq mihomo mosdns
```

预算 broker 不是可选项：`mihomo.service.d/measurement-budget.conf` 把 `ExecStart` 覆盖成
`/usr/local/sbin/mihomo-measurement-budget --run-core /usr/local/bin/mihomo`。
`mihomo-measurement-budget.service`（无参数）先常驻并建 `/run/mihomo-measurements`（0700）与
`budget.sock`（0600）；`--run-core` 核验核心协议、配置与 broker 状态一致后才 `execve` 核心，
并注入 `MIHOMO_MEASUREMENT_SOCKET`。账本在首次预扣时才原子创建。
协议或状态不一致时启动会失败，这是有意的——装了预算却绕开它比不装更危险。

验收：`systemctl status mihomo mosdns dnsmasq` 全绿；
`ss -lntup | grep -E '(:53|:5335|:7890|:9090|:46225|:8799)'` 与[架构文档](architecture.zh-CN.md)一致。

## 6. 门禁与定时任务

```sh
sudo /usr/local/sbin/mihomo-healthcheck --startup-check 30   # 手工跑一次启动门禁
sudo systemctl enable --now mihomo-healthcheck.timer mihomo-watch.timer \
  mosdns-healthcheck.timer mosdns-rules-update.timer mosdns-binary-update.timer
sudo cat /run/mihomo-healthcheck/status
```

`mihomo.service` 的 `ExecStartPost` 已经包含 `--startup-check 20`，
上面的手工命令用于排障或换核后复验。`/run/mihomo-healthcheck/status` 的四要素
（`tun/redirect/filter/route`）全为 1 才算启动成功；只看 `systemctl is-active` 不够，
2026-10-25 的 gVisor 事故就是“进程活、TUN 死”。

## 7. 换核与回滚

| 对象 | 工具 | 说明 |
|---|---|---|
| Mihomo 二进制 | `mihomo-core-swap install/activate/rollback/status` | `renameat2` 双槽原子换；运行中进程保持旧 inode，重启才真正切换 |
| MosDNS 二进制 | `mosdns-update-binary`、`mosdns-rollout-rollback` | 只接受 `/var/backups/mosdns-rollout-*.tar.gz` 回滚点 |
| DNS 上游 | `mihomo-dns-mode auto|mosdns|mihomo|direct` | 子状态机；mosdns/mihomo 停止时自动回落 |
| 配置 | 订阅管线自带备份 | 失败保留旧配置与旧单元 |

换核后必须复验：`mihomo -v` 版本串、`mihomo-healthcheck --startup-check 30`、
`/run/mihomo-healthcheck/status` 四要素；任一步失败就 `mihomo-core-swap rollback` 再验一次。

## 8. 本节不覆盖的内容

- 订阅地址、节点标签、controller secret、WireGuard 私钥：只存在部署端
  `/etc/mihomo/secrets/`、`/etc/mihomo-automation.env`、`/etc/mihomo-alert.env`。
- 节点能力清单（哪些节点允许下载、哪些进 AI 池）：`/etc/mihomo/adaptive/capabilities.yaml`，
  仓库只发布空 `overlay` 模板。
- 上游路由器、UCG、交换机与 AP 的配置：属于现场整合，不在本仓库事务内。
- 恢复镜像与整机重刷：见[恢复镜像](recovery-image.zh-CN.md)（发布工件，不是本机状态）。
