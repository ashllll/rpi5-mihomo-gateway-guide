<p align="center">RASPBERRY PI 5 · LINUX · MIHOMO TUN · LOCAL BUDGET LEDGER</p>

<h1 align="center">网关先证明它能上网，再证明它退得回去</h1>

<p align="center">
双网口透明网关：DNS 分层 + TUN 透明代理 + 按账号计量预算，<br>
启动时校验真实路由，失败就换回上一颗内核并发消息给你。
</p>

<p align="center">
<a href="#一分钟读懂">一分钟读懂</a> ·
<a href="#架构">架构</a> ·
<a href="#安全网">安全网</a> ·
<a href="#快速开始">快速开始</a> ·
<a href="#验证与边界">验证状态</a> ·
<a href="docs/README.md">RepoWiki</a>
</p>

---

## 一分钟读懂

**上游路由器 → 双网口 Linux 网关 → 交换机 / 桥接 AP → 下游设备。**
上联沿用原有联网配置，下联提供 DHCP、DNS 与透明转发；电脑、手机无需安装代理客户端。

| 你关心的事 | 网关怎么处理 |
|---|---|
| 国内站点与本地设备 | 直连规则，不经过代理 |
| 海外站点 | TUN + `auto-redirect` 把流量送进 Mihomo，按业务分组选出口 |
| DNS | dnsmasq → mosdns 分层：国内域名直连解析，其余返回 fake-IP，由代理内部再解析 |
| 额度与限流 | 自编核心把每次显式节点拨号记进本地账本，按账号预算放行 |
| 上线安全 | 启动门禁 + 浸泡 + 自动换回旧内核；异常推送到手机 |
| 出事之后 | 一条命令看门禁状态、内核槽位与最近一次部署结果 |

> **2026-10-25 的真实事故**：内核漏编 `with_gvisor` 后，`mihomo -t` 通过、进程常驻，但 TUN 设备
> 从未建立——国内直连照常工作，所有需要代理的流量静默黑洞三个小时，健康检查当时只判进程存活。
> 现在的部署路径不再相信"进程还活着"，只相信内核状态。

## 架构

**网络位置**

[![拓扑：上游路由器、双网口网关、下游交换机与 AP](assets/current-topology.svg)](assets/current-topology.svg)

生产部署里还有两条独立链路：一条专用下联给 NAS（自己的 dnsmasq 实例），一条 WireGuard 接口
供控制端点对点接入；两者都通过 `tun.include-interface` 单独纳入透明代理，不混入主 LAN。

**DNS 分层**

[![DNS：dnsmasq → mosdns → 直连解析或 Mihomo fake-IP](assets/current-dns-flow.svg)](assets/current-dns-flow.svg)

1. 客户端只认下联地址，`dnsmasq` 负责 DHCP 下发的 DNS 与少量静态主机名。
2. `mosdns` 按域名分流：国内域名走 AliDNS / doh.pub 直连解析，其余转给 Mihomo 的 DNS。
3. Mihomo 用 fake-IP `198.18.0.0/16` 返回占位地址；标为 real-IP 的域名（视频、游戏、以及
   `localdomain` 这类本地后缀）返回真实地址，避免本地服务被卷进代理。
4. 客户端拿着 fake-IP 发起连接，`auto-redirect` 把 TCP 重定向进核心，核心按规则选择出口后
   在代理内部用真实域名重新解析。

**数据面**

```text
客户端 ──► TUN 设备 Mihomo（auto-route + auto-redirect + strict-route）
              │  只接管 include-interface 列出的网段；上游网段整段排除
              ▼
        nftables：forward 白名单 + NAT + 核心自建的拦截/重定向表
        策略路由：ip rule 9000… → table 2022 → 默认路由指向 TUN
              ▼
        Mihomo 规则：直连 / 业务分组 / 显式叶子节点（拨号前先过预算）
```

网关自身的出站流量不经过 TUN（这是设计选择：更新订阅、拉规则和健康检查都能在代理坏掉时照常工作）。
也正因如此，**在网关上 `curl` 外网成功并不证明下游客户端能上网**——验证要分层做。

**组件**

| 层 | 组件 | 职责 |
|---|---|---|
| 入口 | `dnsmasq` | DHCP、下联 DNS、静态主机名；专用下联有独立实例 |
| 解析 | `mosdns` | 国内/国外域名分流，双上游 |
| 代理 | Mihomo（自编核心 + 预算补丁） | TUN 接管、规则选路、fake-IP、`mixed-port` 与控制器 |
| 预算 | `mihomo-measurement-budget` | Unix socket 账本代理：请求校验、按账号扣积分、记录超额 |
| 内核 | [`core/`](core/README.md) | 固定上游提交 + 预算补丁 + 可复现构建说明 |
| 网络 | nftables + iproute2 | 转发白名单、NAT、策略路由、核心自建的重定向表 |
| 安全网 | `mihomo-core-swap` · `deploy-v3.py` · `mihomo-healthcheck` | 双槽换核、部署门禁、只读内核探针 |
| 告警 | `mihomo-alert` · `mihomo-watch` | ServerChan / Bark 推送、五类异常边沿触发 |
| 观测 | `rp5-agent` | 汇总门禁状态、TUN 速率与账本余量，`/api/v1/status` 暴露 `gate` |

## 安全网

一次部署现在必须同时过四道关，任何一道失败都会带着证据退回去：

| 关卡 | 检查什么 | 失败之后 |
|---|---|---|
| 单元门禁 | `ExecStartPost` 运行 `mihomo-healthcheck --startup-check`，确认 TUN 设备、两张 nft 表与策略路由都存在 | 这次启动直接判失败，不再被 systemd 的 `-` 前缀吞掉 |
| 部署门禁 | 候选内核先浸泡 45 秒，再跑同一检查并读控制器连接表 | 恢复配置与单元，`mihomo-core-swap rollback` 换回旧内核，然后**再验一次** |
| 常态观测 | 每分钟只读内核状态：设备、两张表、9000 规则、计数器；每 5 分钟一次经代理端口的 204 出口抽样 | 写 `/run/mihomo-healthcheck/status`，只在状态迁移时发一条告警 |
| 进程/主机 | 单元失败、温度/降频、磁盘、内存、上联异常 | `OnFailure` 与 `mihomo-watch` 边沿触发推送，同一条消息有冷却去重 |

```sh
sudo /usr/local/sbin/mihomo-healthcheck            # 现在的门禁结论（exit 1 = 代理链路不可用）
sudo /usr/local/sbin/mihomo-healthcheck --egress   # 立即做一次出口抽样，不花预算积分
sudo /usr/local/sbin/mihomo-core-swap status       # 内核槽位、运行中组件与校验和
sudo cat /var/backups/mihomo-rollout-*/deploy-result.json   # 最近部署：post_start / post_soak / rollback_exercised
sudo /usr/local/sbin/mihomo-alert --test           # 验证手机通道
```

内核换槽用 `mihomo-core-swap install <binary> <sha256>` 预置、`activate` 单次 `renameat2` 换入、
`rollback` 换回；运行中的进程保持旧 inode，重启才真正切换，所以任何一步失败都不会留下半个内核。
内核必须带 `with_gvisor`：安装器会拒绝 `-v` 不含该驱动的二进制，CI 也会在固定提交上实跑一次构建。

## 快速开始

**三条部署路径，按起点选一条：**

| 你的起点 | 入口 | 私有配置位置 |
|---|---|---|
| 一台新 Linux 主机 | [`setup.sh`](setup.sh) | `/etc/mihomo-gateway/` |
| 自己规划网络、分阶段上线 | [`tools/render.py`](tools/render.py) | `site.local.json` → 9 个私有文件 |
| 维护已有的 Mihomo / MosDNS 网关 | [`automation/`](automation/README.zh-CN.md) | `/etc/mihomo`、`/etc/mosdns` 与部署 manifest |

```sh
# 一键入口：检测网口、规划网段、生成配置，切换前保存恢复点
git clone https://github.com/ashllll/rpi5-mihomo-gateway-guide.git
cd rpi5-mihomo-gateway-guide
sudo sh setup.sh --dry-run    # 只打印计划，不下载、不安装、不切网络
sudo sh setup.sh

# 已有网关：只装运维层，再按需打开维护 timer
sudo automation/install.sh --dry-run
sudo automation/install.sh
sudo automation/install.sh --enable-timers

# 自编内核（带预算补丁）走同一套能力门禁
sudo python3 gateway/setup.py --core /path/to/mihomo --core-sha256 <sha256>
```

`gateway/setup.py` 会按架构校验发布资产的 SHA256，并拒绝 `-v` 不含 `with_gvisor` 的内核；
本机若存在预算 drop-in，还会要求内核声明 `mihomo-budget-v1`，避免"装了预算、内核却不说协议"。

<details>
<summary>系统要求与不自动处理的环境</summary>

| 项目 | 支持范围 |
|---|---|
| 系统 | Linux、运行中的 systemd、Python 3.9+ |
| 架构 | ARM64、ARMv7、x86-64；Mihomo 固定提交并校验 SHA256 |
| 软件包管理 | apt-get、dnf、pacman、zypper |
| 网口 | 按默认路由识别上联；唯一剩余独立以太网口作为下联；可用已连接 Wi-Fi 上联 |
| 网络管理 | NetworkManager 或 systemd-networkd；只配置下联 |
| 订阅 | Clash / Mihomo 节点 YAML、URI 列表、base64 URI 列表 |
| 下游 | IPv4；dnsmasq → Mihomo DNS，TUN / auto-redirect 与 nftables NAT |

单网口旁路由、OpenWrt、容器、bridge/bond/VLAN、已有 VPN/TUN 与复杂防火墙需要手动整合。
多默认路由时用 `--wan` / `--lan` 指定；检测到已有生产代理服务时先迁移。
默认参数见 [`gateway/defaults.json`](gateway/defaults.json)，可用 `--defaults` 指定自己的文件。
</details>

## 验证与边界

| 验证层 | 已有证据 | 不能由此推断 |
|---|---|---|
| 一键安装逻辑 | 34 项模拟、故障注入、重复安装与回滚检查 | 所有真实网卡与网络管理器组合都可用 |
| 内核构建 | CI 在固定提交 + 补丁上实跑 `go build -tags "with_gvisor no_tailscale"`，并断言 `-v` 与预算协议 | 平台解锁、账号可用或节点吞吐 |
| Linux 网络 | 隔离命名空间 e2e 验证 nftables / dnsmasq | 目标机真实 LAN、无线环境与重启恢复 |
| 部署安全网 | 2026-10-25 负向演练：故意写坏 TUN 设备名 → `ROLLED_BACK`、`rollback_verified=true`、退出码 1 | 所有硬件故障都能被软件回滚覆盖 |
| 真实客户端 | 需要关闭客户端本地 VPN/TUN 后按三层验收（本机 → 显式代理 → 真实 LAN） | 一次通过不等于长期零断连 |

```sh
python3 tools/validate_public.py
python3 tools/check_automation.py      # 需要 PyYAML
python3 tools/check_stability.py
python3 tools/check_setup.py
```

[GitHub Actions](https://github.com/ashllll/rpi5-mihomo-gateway-guide/actions/workflows/validate.yml)
在 push / PR 时运行完整回归，其中 `core-build` 任务负责证明"带 gVisor 的内核可以自动构建"。

## 目录结构

```text
gateway/        一键安装器（检测、生成、快照、回滚、主机端看门狗）
automation/     运维层：sbin 脚本、systemd 单元、env 模板、可选 drop-in、安装器
core/           自编内核：固定提交、预算补丁、可复现构建说明
tools/          离线校验器与渲染器（隐私、安装、稳定性、订阅、预算策略）
docs/           RepoWiki：架构、部署、运维、ADR、验收与恢复
assets/         拓扑、DNS、自动化与历史图
examples/       站点参数与清理后的示例配置
```

## 检查与恢复

<details>
<summary>先区分是哪一层故障</summary>

| 现象 | 下一步 |
|---|---|
| 本机能上网，LAN 客户端不能 | 跑 `mihomo-healthcheck`；再看真实客户端路由与 `auto-redirect` 动态端口，别只测显式代理 |
| `mihomo-healthcheck` 退出 1 | 读 `/run/mihomo-healthcheck/status`：`tun` / `redirect` / `filter` / `route` 哪个为 0 |
| 进程活着但全断 | 查 `journalctl -u mihomo` 是否有 `gVisor is not included` 或 `Start TUN listening error` |
| 订阅或规则更新失败 | 查有效期、GitHub 配额与下载校验；失败时应保留旧配置 |
| 大文件仍然慢 | 核对节点下载许可与客户端分块；健康选择不会聚合单条连接的带宽 |
| 手机没收到告警 | `mihomo-alert --test`；密钥只从 `/etc/mihomo-alert.env` 读取 |

本方案以可信 LAN 为前提，LAN INPUT 整体放行以容纳动态透明代理端口；不是访客网络隔离方案。
</details>

## 文档导航

**[进入 RepoWiki](docs/README.md)**：按入门、架构、源码配置、ADR、运维、安全与验收阅读。

| 想做的事 | 继续阅读 |
|---|---|
| 完整知识库与设计决策 | [RepoWiki 首页](docs/README.md) · [ADR 索引](docs/adr/README.md) |
| 安装、订阅格式、指定网口与回滚 | [一键配置指南](docs/one-click-setup.zh-CN.md) |
| 拓扑、DNS 分层与当前状态 | [当前架构](docs/current-architecture.zh-CN.md) · [拓扑图](assets/current-topology.svg) · [DNS 图](assets/current-dns-flow.svg) |
| 启动门禁、换核、告警与出口抽样 | [运维指南](docs/operations-current.zh-CN.md) |
| 业务路由、预算与定时任务 | [自动化层](automation/README.zh-CN.md) · [原生选路与预算](docs/adaptive-selection.zh-CN.md) |
| 手动配置与分阶段上线 | [部署指南](docs/deployment-current.zh-CN.md) · [站点参数模板](examples/site.example.json) |
| 故障定位与真实 LAN 验收 | [运维指南](docs/operations-current.zh-CN.md) |
| 内核补丁与构建 | [core/README](core/README.md) |
| 校验、写盘与恢复系统 | [无节点恢复镜像](docs/recovery-image.zh-CN.md) |

## 隐私与安全

仓库只发布机制代码与示例参数，**不发布订阅地址、节点标签与凭据、controller token、
Wi-Fi 密码或真实网络配置**。`site.local.json`、`rendered/`、运行配置、日志与备份必须留在部署端。
提交前跑发布检查与 Gitleaks；误发布凭据先吊销，再走私密修复流程，见 [SECURITY.md](SECURITY.md)。

[CC0 1.0 Universal](LICENSE) · 第三方产品名与商标归各自权利人所有。
