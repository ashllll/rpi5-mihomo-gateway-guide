<img src="assets/readme/hero.svg" width="100%" alt="深色仪表盘：左侧标题「一个会自证的网关」，右侧列出 TUN、nft、策略路由、出口四项真机状态与转发计数">

一台 Raspberry Pi 5 双/多网口透明网关的**部署镜像**：上游路由器 → rp5 → 主 LAN / VLAN20 试点 / WireGuard 对端。
下游设备不装代理客户端；国内直连，海外与指定业务流量经 TUN 与 nftables 收口，DNS 三层分流，
显式节点拨号先过本地预算准入；启动与常态门禁只认内核状态，不认进程存活。

本仓库描述**这一台机器**（Debian 13 / arm64）的现状。换机器要改网段、接口名、VLAN 与订阅数据，
它不是开箱即用的一键安装器。

<p align="center">
<a href="#30-秒看懂">30 秒看懂</a> ·
<a href="#五层栈">五层栈</a> ·
<a href="#单元表">单元表</a> ·
<a href="#安装顺序">安装</a> ·
<a href="#被删除的旧结构">旧结构</a> ·
<a href="#边界">边界</a> ·
<a href="docs/README.md">文档索引</a>
</p>

## 30 秒看懂

| 你关心的事 | 这台机器的做法 |
|---|---|
| 四个网段 | `wan0` 192.168.0.117/24（上游 DHCP）→ `lan0` 192.168.1.1/24（主 LAN）→ `proxy20` 192.168.20.1/24（`wan0` 上的 802.1Q VLAN 20，单台 PC 试点）→ `wg-ucg-pilot` 10.253.254.1/30（UCG 点对点） |
| 三层 DNS | `dnsmasq`（`lan0`/`proxy20` 各一实例，:53）→ `mosdns`（127.0.0.1:5335，国内/海外分流）→ Mihomo DNS（127.0.0.1:1053，fake-IP 198.18.0.0/16） |
| 自编译预算版核心 | `/usr/local/bin/mihomo` = `v1.19.28-budget-status-contract-v3`，`go1.27.2`，tags `no_tailscale, with_gvisor`；补丁与构建说明在 [`core/`](core/README.md) |
| 预算外壳 | `mihomo-measurement-budget.service` 常驻并建 `/run/mihomo-measurements/budget.sock`；drop-in 用 `--run-core` 校验协议与状态后拉起核心。账本 `/var/lib/mihomo-measurements/quality-budget.json` 首次预扣时创建，协议 `mihomo-budget-v1`，每次显式节点检测预扣 2 次尝试 |
| 门禁 | 启动前 `mihomo -t`；启动后 `mihomo-healthcheck --startup-check 20` 验 TUN、两张 nft 表、`redirect to` 规则、9000 规则并取一次 204；之后每分钟只读探针 + 每 5 分钟 204 出口抽样；连续 3 分钟红就自动重启核心（每小时至多一次）；状态写 `/run/mihomo-healthcheck/status`，只在迁移时告警 |

2026-10-25 真实事故：换核漏编 `with_gvisor`，`mihomo -t` 通过、进程常驻，但 TUN 从未建立，
国内直连照常、所有代理流量静默黑洞。当时的探针判定 nft 只看表是否存在，唯一的出口抽样是
`curl --socks5-hostname` 走 mixed-port（`--socks5` 绕开 TUN/redirect 链路），所以 TUN 没起来时
它照样返回 204、门禁照样绿。现在的门禁只相信内核状态。

## 五层栈

| 层 | 组件 / 单元 | 端口与接口 | 真机证据 | 仓库文件 → 安装位置 |
|---|---|---|---|---|
| 入口 | `dnsmasq.service`、`dnsmasq-vlan-pilot.service` | `192.168.1.1:53`、`192.168.20.1:53`；DHCP 池 `.100–.199` | `ss -lntup` 两个 dnsmasq；`dnsmasq --test` 通过 | [`automation/etc/dnsmasq/`](automation/etc/dnsmasq/) → `/etc/dnsmasq.d/lan.conf`、`/etc/dnsmasq-vlan-pilot.conf`（`upstreams.conf` 仅在缺失时 seed） |
| 分流解析 | `mosdns.service` | `127.0.0.1:5335`（API `127.0.0.1:9091`） | `mosdns` v5.3.4；规则集 `/etc/mosdns/rules/current/{proxy-list,direct-list,apple-cn,google-cn,geoip-cn}.txt` | [`automation/etc/mosdns/config.yaml`](automation/etc/mosdns/config.yaml) → `/etc/mosdns/config.yaml` |
| 代理核心 | `mihomo.service`（`--run-core` 校验后拉起） | `192.168.1.1:7890` mixed、`192.168.1.1:9090` controller、`127.0.0.1:1053` DNS、`0.0.0.0:46225` redirect、TUN `198.18.0.1/30` | `mihomo -v` 版本串；`ip link` 有 `Mihomo`；`ip rule` 有 9000 | [`automation/systemd/mihomo.service`](automation/systemd/mihomo.service) + 6 个 drop-in；二进制来自 [`core/`](core/README.md) |
| 数据面 | `nftables.service` 载入 `/etc/nftables.conf`；Mihomo 自建 `inet mihomo` | `ip rule` 9000–9010 → table 2022；`inet mihomo` prerouting；`inet rp_filter` + `ip rp_nat` | `nft list tables`、`ip rule show`；状态文件 `tun/redirect/filter/route` 四项 | 部署端文件 `/etc/nftables.conf`、`/etc/nftables.d/*.nft`（**不随仓库发布**）；结构见[架构文档](docs/architecture.zh-CN.md) |
| 门禁告警 | `mihomo-healthcheck.timer`、`mihomo-watch.timer`、`mihomo-alert@.service`、`mihomo-measurement-budget.service` | — | `/run/mihomo-healthcheck/status`；`journalctl -u mihomo-healthcheck` | [`automation/sbin/`](automation/sbin/) → `/usr/local/sbin/`；[`automation/systemd/`](automation/systemd/) → `/etc/systemd/system/` |

数据面一句话：`ip rule 9000` 把 `lan0`/`proxy20`/`wg-ucg-pilot` 的流量送进表 `2022`，
`inet mihomo` 在 prerouting 先做白名单（核心自身、非内网、私网段直接 return），
再把 DNS 查询 `dnat` 到 `198.18.0.2:53`、TCP 重定向到 `:46225`、UDP 交给 TUN。
过滤由 `inet rp_filter`（input/forward 默认 drop）与 `ip rp_nat`（`192.168.1.0/24` 出 `wan0` 做 masquerade）完成。

## 单元表

**12 个 enabled（发布层）**

| 单元 | 作用 | 周期 |
|---|---|---|
| `mihomo.service` | 代理核心，`ExecStart` 被 drop-in 覆盖为预算 broker 的 `--run-core` | 常驻 |
| `mihomo-measurement-budget.service` | 预算准入 broker：建 socket；账本在首次预扣时创建 | 常驻 |
| `mihomo-healthcheck.timer` | 四要素探针 + 计数器，失败时按锁恢复进程 | 每 1 分钟（抖动 3s） |
| `mihomo-dnsmasq-guard.timer` | 分层解析探针：LAN 侧 DNS 迟滞/无答案时升级到 `:5335` 与公共 DoH 判定；只有 dnsmasq 静默才重启，重启后有冷却与 15 分钟预算，单次事故只推一条 | 每 30 秒 |
| `mihomo-watch.timer` | 单元/温度/磁盘/内存/WAN 巡检 | 开机 90s 后每 2 分钟 |
| `mosdns.service` | 分流解析；起停时切换 dnsmasq 上游模式 | 常驻 |
| `mosdns-healthcheck.timer` | 校验 5335 分流与 metrics | 开机 30s 后每 1 分钟 |
| `mosdns-rules-update.timer` | 原子更新 MosDNS 规则数据 | 每天 04:20（抖动 30 分钟） |
| `mosdns-binary-update.timer` | 官方稳定版 MosDNS 换核 | 每周日 05:10（抖动 1 小时） |
| `dnsmasq.service` | `lan0` DHCP + DNS；启动前写入直连上游基线 | 常驻 |
| `dnsmasq-vlan-pilot.service` | `proxy20` 试点 DHCP + DNS（租约 10 分钟） | 常驻 |
| `homeassistant-access-guard.service` | 从 `wan0`/`wlan0` REJECT `tcp/8123`，Home Assistant UI 只对下游 LAN 开放 | 开机一次（oneshot） |

本机另有 2 个非发布层单元处于 enabled：`ikuai-wan-watch.service`（只读记录上游 WAN 抖动）、
`rp5-agent.service`（`0.0.0.0:8799` 只读状态，供 T-Display-S3 监视器）。它们属于这台机器的
本地附加物，不在上面的安装脚本里。

其余 worker（`mihomo-healthcheck.service`、`mihomo-watch.service`、`mihomo-alert@.service`、
`mihomo-download-quality.service` 等）是 `static`：没有 `[Install]`，由 timer 或 `OnFailure` 拉起，
既不算 enabled 也不应 disable。

**4 个休眠 disabled**（随仓库安装、保持关闭；2026-10-25 已清掉本机 2 个退役评分单元，真机与仓库一致）

| 单元 | 原因 |
|---|---|
| `mihomo-download-quality.timer` | 质量采样脚本会写 `./providers/quality-*.yaml`，但配置没声明对应的 file provider，产物无人读取；开 timer 只烧采样带宽、不改选路 |
| `mihomo-social-quality.timer` | 同上，仅 X 视频场景，`*:0/2` 每 2 分钟一轮 |
| `mihomo-rules-update.timer` | 规则更新改由核心 `rule-providers`（file 型）与 MosDNS 更新承担 |
| `mihomo-subscription-update.timer` | 订阅更新暂走手动/离线管线，避免无人值守换配置 |

休眠不等于卸载：文件仍在 `/etc/systemd/system/`，`systemctl start` 可手动跑一次。

## 仓库与真机的一致范围

2026-10-25 对真机做了一次逐文件 `sha256sum` 对照，结论写在这里，免得以后靠猜：

| 范围 | 文件 | 状态 |
| --- | --- | --- |
| 机制实现 | `automation/sbin/*`（22 个脚本） | 与 `/usr/local/sbin/` **逐字节一致** |
| 单元与 drop-in | `automation/systemd/**`（32 个文件） | 与 `/etc/systemd/system/` **逐字节一致** |
| 数据与解析 | `automation/etc/mosdns/{config.yaml,rules/*}`、`automation/etc/dnsmasq-upstreams.{direct,mihomo,mosdns}`、`automation/policy/route-defaults.yaml`、`automation/etc/NetworkManager/dispatcher.d/90-wifi-management-routing` | **逐字节一致** |
| 脱敏模板 | `automation/etc/dnsmasq/{lan.conf,vlan-pilot.conf}` | 仓库用 RFC 5737 / RFC 7042 占位，真机是真实 `192.168.x` 地址、MAC 与主机名，**按设计不同** |
| 站点态 | `automation/policy/capabilities.yaml` | 账号、`enabled` 开关与私有覆写只存在于真机 `/etc/mihomo/adaptive/`，仓库是脱敏模板 |
| 站点态 | `automation/etc/mihomo/config.local.yaml` | 只是片段；真机 `/etc/mihomo/config.yaml` 是合并后的完整配置 |
| 凭据 | `secrets/*`、`/etc/mihomo-alert.env`、`/etc/mihomo/reconcile-targets.conf` | 不发布、不入库 |

`/etc/dnsmasq.d/README` 是 Debian 自带的说明文件，不属于本仓库。

## 安装顺序

仓库只发布“装成这台机器”的运行层，不含网络事务、订阅内容与凭据。从裸 Debian 13 起：

```sh
# 1. 网络身份（手工一次性）：wan0 DHCP、lan0 静态 /24、proxy20 = wan0 上的 VLAN 20、
#    wg-quick@wg-ucg-pilot，及 /etc/sysctl.d/99-router.conf
sudo ip -br addr                      # 四个接口都要有地址
# 2. 数据面：/etc/nftables.conf → inet rp_filter + ip rp_nat（结构见架构文档）
sudo nft -c -f /etc/nftables.conf
# 3. 运行层：脚本、单元、env 示例、dnsmasq/mosdns/mihomo 配置骨架
sudo automation/install.sh --dry-run
sudo automation/install.sh            # 机制文件覆盖、配置保已有；只 enable 真机那 12 个单元
# 4. 核心与配置：/usr/local/bin/mihomo 用 core/ 构建并校验版本串
sudo /usr/local/sbin/mihomo-healthcheck --startup-check 30
sudo cat /run/mihomo-healthcheck/status
```

细节与判定顺序见[部署文档](docs/deployment.zh-CN.md)；日常操作见[运维文档](docs/operations.zh-CN.md)。

## 被删除的旧结构

这个仓库曾经同时维护“一键安装器 + 手动渲染器 + 运维层”三条产品线。它们与这台机器的现状不符，已删除：

- `setup.sh`、`gateway/`：旧的一键安装器与默认值；网络事务由部署端手工完成，不再随仓库发布。
- `web/`、`design-demos/`、根目录 `dir-*-hero.svg`：旧发布页与设计演示。
- 旧的影子评分与自动应用脚本，以及 4 个 unit（2 个 service、2 个 timer）：已被原生 URL 检测 + 预算准入取代。
- `assets/current-*.{mmd,svg}`：旧网段与旧 DNS 流程的 8 个过期生成物。

保留 `assets/historical-automation-2026-10-11.*` 作为历史快照；现状以
[`assets/network-topology.svg`](assets/network-topology.svg) 为准。旧调试档案保留在
[docs/deployment-debugging-summary.zh-CN.md](docs/deployment-debugging-summary.zh-CN.md)，
外部项目研究移到 [docs/research/](docs/research/)。

## 边界

| 有证据 | 不能由此推断 |
|---|---|
| 2026-10-25 对真机 rp5 的只读取证（`ssh rp5`，86 个文件快照）、服务状态、端口、单元表 | 所有硬件组合、断电恢复与长期稳定性都已验收 |
| 启动门禁 + 30 秒解析自愈 + 每分钟四要素探针 + 5 分钟出口抽样 + 连红 3 分钟自愈重启 | 检测周期不等于瞬时可用；重启只治黑障，救不了节点或上游故障；连接切换会迁移已有 TCP/WebSocket 会话 |
| 预算服务校验协议、账本与预扣 | 平台解锁、账号可用、节点吞吐或供应商允许下载 |
| `nft -c -f` 通过 | 目标机之外的环境可用；本机 nftables 表是部署端文件，不是通用模板 |

可信 LAN 模型：`lan0` 入站整体放行是为了容纳动态透明代理端口，不做访客隔离。
公开暴露面只有 `192.168.0.0/24` 可达的 SSH 与 `8799`，详见 [SECURITY.md](SECURITY.md)。
本仓库不发布订阅地址、节点标签、controller secret、WireGuard 私钥与真实 MAC。

发布前自查：

```sh
python3 tools/validate_public.py
python3 tools/check_automation.py        # 脚本/单元/env 示例与安装器的一致性
python3 tools/check_dnsmasq_guard.py     # 解析自愈的分层判定、预算与告警去重
python3 tools/check_publication_privacy.py
```

[CC0 1.0 Universal](LICENSE) · 第三方产品名与商标归各自权利人所有。
