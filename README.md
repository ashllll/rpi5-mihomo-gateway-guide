# Mihomo 透明代理网关

**双网口 Linux 网关，让下游设备通过 Mihomo 自动分流。**

支持 Raspberry Pi 5 与符合要求的 Linux 主机。从一个 HTTPS 节点订阅开始，自动检测网口、规划网段，配置 DHCP / DNS、分流策略与 systemd 服务。

[快速开始](#快速开始只输入订阅链接) · [适用范围](#自动配置与适用范围) · [检查与恢复](#一键安装的检查与恢复) · [故障排查](#故障排查) · [完整文档](#文档导航)

> **验证状态** · 一键安装已通过模拟、真实核心解析和 Linux 命名空间 CI；真实网关与下游客户端仍待验收。历史验证记录见下文，不代表当前实机状态。

## 网关放在哪里

**上游路由器 → 双网口 Mihomo 网关 → 交换机 / 桥接 AP → 下游设备**

上联保留原有联网配置，下联提供 DHCP / DNS 与透明转发。管理终端从上游内网访问网关。

<details>
<summary>查看完整生产拓扑（虚构示例地址）</summary>

[![当前双网口透明网关架构：上游路由器、管理终端、树莓派与下游 AP](assets/current-topology.svg)](assets/current-topology.svg)

图示为已有生产部署参考，示例地址均为虚构值。实线表示物理连接，虚线表示 SSH 管理路径。管理终端接上游 Wi-Fi，不要求树莓派启用 wlan0。

`10.66.0.0/24` 与 `10.77.0.0/24` 只是示例，一键入口自动选择实际网段，手动生成器由部署者提供参数；生成器要求两侧都是 RFC1918 私有网段、
互不重叠，并要求下游有可用主机地址。

</details>

## 快速开始：只输入订阅链接

在已联网的目标 Linux 主机上下载并解压本项目，或用已有的 Git 获取：

```sh
git clone https://github.com/ashllll/rpi5-mihomo-gateway-guide.git
cd rpi5-mihomo-gateway-guide
sudo sh setup.sh
```

首次运行通过不回显的输入框读取 HTTPS 订阅链接，无需填写节点名称、策略池或网口配置。
通过上联网口连接 SSH，或使用本地控制台；下联接交换机 / 桥接 AP，并关闭该链路上其他设备的 DHCP 服务。
目标机需能访问发行版软件源、GitHub Release 和规则源，安装器会自动安装缺失依赖。

```sh
sudo sh setup.sh --dry-run  # 检测环境并打印计划，不安装、不下载、不切换网络
# 预览需要现有 Python 和 iproute2；缺少时会提示，正式安装会安装依赖。
```

安装前校验下载摘要、订阅节点和生成配置，切换前备份并设置恢复计时器，异常或健康检查失败时回滚。
重复运行复用已有订阅、网段、端口和管理密钥；无需每次重新填写。完整参数、无人值守输入和恢复命令见
[一键配置与模拟验证](docs/one-click-setup.zh-CN.md)。

### 自动配置与适用范围

| 项目 | 自动行为 / 要求 |
|---|---|
| 系统 | Linux + 运行中的 systemd；Python 3.9+，缺失时由入口安装 |
| 架构与依赖 | ARM64、ARMv7、x86-64；支持 apt-get、dnf、pacman、zypper；固定 Mihomo 版本并校验 SHA256 |
| 网口 | 根据 IPv4 默认路由选择物理上联，可用已连接的 Wi-Fi；选择唯一剩余的独立以太网口作为下联 |
| 网段与端口 | 避开现有地址、路由网段和已占用端口；生成稳定的私有 /24 网段、网关与 DHCP 地址池 |
| 网络管理 | NetworkManager 或 systemd-networkd；仅配置下联，保留上联配置 |
| DNS 与转发 | dnsmasq → Mihomo DNS，TUN / auto-redirect + nftables NAT；下游 IPv4 only |
| 分流 | 私网与国内直连，其余默认代理；AI、流媒体、Telegram、下载分类组自动引用 provider |
| 节点更新 | 原生 provider 定期更新；支持核心可解析的 Clash/Mihomo 节点 YAML、URI 列表和 base64 URI 列表 |
| 服务管理 | 独立的 `mihomo-gateway*` 服务、配置目录与 nftables 表；提供备份、重复安装和回滚 |

“通用”限定在以上可识别环境。单网口旁路由、OpenWrt、容器、bridge/bond/VLAN、现有 VPN/TUN 或复杂防火墙需要手动整合。
多个网口无法唯一判断时，脚本停止并提示，可用 `--wan` / `--lan` 明确指定；已有生产代理服务也需要先迁移。
策略默认值集中在 [`gateway/defaults.json`](gateway/defaults.json)，可以用 `--defaults` 指定自定义文件。
自动延迟选路无法仅凭订阅判断平台解锁或供应商的下载授权，相关限制仍应遵守供应商说明。

## 选择你的部署方式

| 需求 | 入口 | 配置与边界 |
|---|---|---|
| 新主机一键配置 | [`setup.sh`](setup.sh) | `/etc/mihomo-gateway/`；自动网口、策略、依赖与恢复，不需要站点 JSON |
| 手动规划与分阶段安装 | [`tools/render.py`](tools/render.py) | `site.local.json` → 9 个私有文件；只生成文件，随后手动部署到 `/etc/mihomo` 等目录 |
| 维护已有生产方案 | [`automation/`](automation/README.zh-CN.md) | 16 个运维脚本、24 个 systemd 单元和 MosDNS 骨架；依赖已有站点配置与节点能力数据 |
| 从空白介质恢复系统 | [无节点恢复镜像](docs/recovery-image.zh-CN.md) | 独立的预发布镜像；写盘会清空目标盘，恢复后配置账号、网络与订阅 |

一键入口不调用生产运维层的 overlay、评分或隔离流程。本页的生产拓扑和运维机制用于理解已有部署，
不会全部自动安装到新网关；[当前生产架构](docs/current-architecture.zh-CN.md)与[历史档案](docs/deployment-debugging-summary.zh-CN.md)分开维护。

## 一键安装的检查与恢复

```sh
sudo systemctl status mihomo-gateway mihomo-gateway-dhcp mihomo-gateway-firewall
sudo journalctl -u mihomo-gateway -n 80 --no-pager
sudo python3 /opt/mihomo-gateway/setup.py --rollback latest
```

日志与备份可能含节点信息，应留在本机。配置在 `/etc/mihomo-gateway/`，程序在 `/opt/mihomo-gateway/`，
私有备份在 `/var/lib/mihomo-gateway/backups/`。回滚恢复网络配置与原服务状态，已安装的依赖包保留。
恢复程序若遇到错误，会保留恢复标记并输出独立恢复命令；完整机制见[一键配置文档](docs/one-click-setup.zh-CN.md)。

## 三层验收

1. 本机：服务 active、语法通过、路由正确，只证明基本进程与上游状态。
2. 显式代理：`curl --proxy` 走混合端口，证明端口与节点可用（地址端口以部署参数为准）。
3. 真实 LAN 客户端：关闭客户端本机 VPN / TUN / 系统代理，验证 DHCP、DNS 和透明 TCP / UDP。

```sh
dig @10.77.0.1 www.baidu.com
dig @10.77.0.1 github.com
curl --noproxy '*' --max-time 20 -I https://www.baidu.com
curl --noproxy '*' --max-time 20 -I https://github.com
curl --noproxy '*' --max-time 20 -I https://www.youtube.com
```

`--noproxy '*'` 只是让 curl 忽略代理环境变量，**不会关闭系统 VPN / TUN**，必须人工确认客户端流量真的经过 LAN。
Mac 同时连 Wi-Fi、有线和本地 TUN 时默认路由可能绕过 Pi，要先核对接口和路由，不能凭一次 curl 成功宣布网关转发
成功。之后还要实际播放视频、测试 AI 流式会话；有回退通道时再验证 Mihomo 重启后的动态端口、基础 NAT、DNS 回退
和真实客户端访问，不要在唯一管理连接上直接做停服务或重启设备测试。

## 故障排查

| 现象 | 先检查 |
|---|---|
| 一键入口提示网口有歧义 | 确认实际上联与独立下联，用 `--wan` / `--lan` 指定；接口名不必叫 wan0 / lan0 |
| SSH 经过将要重配的 LAN | 从上联重新连接，或使用本地控制台，再运行安装 |
| 下载失败或 SHA256 不符 | 检查软件源、GitHub 与规则源连通性；不要跳过摘要校验 |
| 订阅拒绝或没有节点 | 确认是有效 HTTPS 节点订阅，非登录页、空订阅或只有远程 provider 引用的配置 |
| 检测到旧代理、DNS/DHCP、VPN 或防火墙冲突 | 按现有方案先做迁移；安装器不会覆盖未知部署 |
| PC 无法上网，但网关本机 curl / 显式代理正常 | LAN INPUT 是否漏掉 Mihomo auto-redirect 的动态端口；不要写死事件端口 |
| 一次 curl 成功却仍上不了网 | 客户端是否还有本地 VPN / TUN / 系统代理；路由是否绕过 Pi |
| 规则更新失败 | GitHub 403 / 配额；保留旧规则，配额恢复后重试 |
| dnsmasq 起不来 | 已有 include / server / listen 选项冲突 |
| 视频走了 AI 池 | 规则顺序是否让 `DOMAIN-KEYWORD,google` 抢先覆盖了 googlevideo |
| 大文件下载没有变快 | 节点是否真的允许大流量；客户端是否支持分块；单流不会被聚合 |
| ping 通但 SSH 超时 / 恢复后仍异常 | INPUT 规则、来源网段与 sshd；`nft -c -f` 只查语法，`nft -f` 才应用；不要删除 Mihomo 的表 |

## 验证记录与持续检查

2026-10-12 的本地与 CI 验证结果如下，全部使用脱敏合成数据；[完整说明与复现命令](docs/one-click-setup.zh-CN.md)。

| 验证层 | 结果与边界 |
|---|---|
| 安装逻辑与故障注入 | 33 项通过，含 9 个激活阶段失败、NetworkManager / networkd、重复安装与回滚 |
| 真实 Mihomo v1.19.32 | Darwin ARM64 核心摘要已核对；4 种有效订阅通过、4 种无效订阅拒绝，生成配置通过核心校验 |
| 原有项目回归 | 生成器、运维层、策略、订阅候选与更新、规则更新、恢复镜像检查通过 |
| 发布检查 | ShellCheck、Python 编译、文档链接、隐私扫描与 Gitleaks 通过 |
| Linux 命名空间 | [GitHub Actions 已通过](https://github.com/ashllll/rpi5-mihomo-gateway-guide/actions/runs/38201843668)：真实 nftables/dnsmasq 解析、防火墙重复加载、删除与外部表保留 |
| 实机与下游 | 真实网卡、网络管理器切换、DHCP、透明 TCP/UDP 和重启恢复尚待目标机验收 |

```sh
python3 tools/check_setup.py
python3 tools/validate_public.py
python3 tools/check_publication_privacy.py
# 仅限 Linux 测试机；脚本会创建独立网络命名空间：
sudo python3 tools/check_setup_linux.py
```

[GitHub Actions](https://github.com/ashllll/rpi5-mihomo-gateway-guide/actions/workflows/validate.yml) 在 push / PR 时运行完整回归。
模拟测试、解析检查和本机显式代理成功，各自只证明对应层次；上线前仍按上面的三层验收确认真实下游路径。

## 文档导航

| 你要完成的事 | 文档 |
|---|---|
| 安装、指定网口、配置订阅与回滚 | [一键配置指南](docs/one-click-setup.zh-CN.md) |
| 理解网络、DNS 与组件职责 | [当前架构](docs/current-architecture.zh-CN.md) |
| 手动生成配置并分阶段上线 | [部署指南](docs/deployment-current.zh-CN.md) |
| 排查转发、动态端口与客户端路径 | [运维指南](docs/operations-current.zh-CN.md) |
| 配置定时任务与生产运维层 | [自动化层](automation/README.zh-CN.md) · [智能选节点设计](docs/adaptive-selection.zh-CN.md) |
| 校验镜像、写盘与恢复 | [无节点恢复镜像](docs/recovery-image.zh-CN.md) |
| 评估 Landscape 融合 | [可行性](docs/landscape-feasibility.zh-CN.md) · [能力报告](docs/landscape-capabilities.zh-CN.md) · [验收计划](docs/landscape-acceptance.zh-CN.md) |

## 进阶参考

新安装优先使用 `setup.sh`。以下保留手动生成器、已有生产运维层与恢复镜像的独立流程。

<details>
<summary>手动配置生成器：参数、生成文件与安装映射</summary>

## 进阶：手动配置生成器

生成器只需要 Python 3 标准库；生成阶段不需要 root，也不会接触正在运行的网关。实际部署还需目标系统上的
nftables、dnsmasq、Mihomo 等组件，版本与准备事项见 [分阶段部署](docs/deployment-current.zh-CN.md)。

```sh
git clone https://github.com/ashllll/rpi5-mihomo-gateway-guide.git
cd rpi5-mihomo-gateway-guide
cp examples/site.example.json site.local.json
# 编辑 site.local.json：接口名、网段、LAN 地址、DHCP 范围和端口要与本机实际环境一致。
python3 tools/render.py --site site.local.json --output rendered

# 仓库自带检查（在检出目录运行）：
python3 tools/validate_public.py
python3 tools/check_render.py
python3 tools/check_overlay.py
python3 tools/check_automation.py

# 在目标系统上检查候选配置（只检查，不安装、不重启服务）：
sudo nft -c -f rendered/nftables.conf
sudo dnsmasq --test --conf-file=rendered/dnsmasq-lan.conf
mihomo -t -d rendered/mihomo
```

网关上的自动化层（systemd 单元与运维脚本）用安装器部署，先演练再落地；参数、定时任务与安全边界见
[自动化层说明](automation/README.zh-CN.md)：

```sh
sudo automation/install.sh --dry-run        # 只打印计划
sudo automation/install.sh                  # 安装脚本与单元，保留已存在的部署数据
sudo automation/install.sh --enable-timers  # 按需启用八个 timer
```

`tools/render.py` 只有 `--site` 和 `--output` 两个参数；输出目录必须尚不存在，脚本拒绝覆盖已有目录。生成目录权限
`0700`、9 个文件权限 `0600`，controller secret 每次随机生成。配套 env 的 LAN 地址、mixed/controller 端口与 WAN 接口
直接来自同一份站点参数；无线管理接口等独有字段仍需审阅。已有 env 应合并共享字段并保留原有独有设置。

### 站点参数

| 字段 | 示例值 | 说明 |
|---|---|---|
| `wan_interface` / `lan_interface` | `wan0` / `lan0` | 上游 / 下游接口名，部署端按永久 MAC 固定 |
| `management_cidr` | `10.66.0.0/24` | 允许 SSH 管理 Pi 的上游内网网段，同时作为直连规则 |
| `lan_cidr` | `10.77.0.0/24` | 下游网段，必须与上游不重叠 |
| `lan_address` | `10.77.0.1` | 网关地址，同时是 DHCP 网关和 DNS |
| `dhcp_start` / `dhcp_end` | `10.77.0.100` / `10.77.0.199` | DHCP 地址池，必须排除网关地址 |
| `dhcp_lease` | `12h` | DHCP 租约时长 |
| `mixed_port` / `controller_port` | `7890` / `9090` | 混合端口与 Mihomo 控制端口（secret 每次随机） |
| `mihomo_dns_port` / `mosdns_port` | `1053` / `5335` | Mihomo DNS 端口；MosDNS 监听端口（生成器不配置 MosDNS，骨架见 `automation/etc/mosdns/`） |
| `dns_upstreams` | `223.5.5.5`、`1.12.12.12` | Mihomo DNS 的引导 / 默认上游 |

生成器会校验接口名合法且 WAN ≠ LAN、两侧都是 RFC1918 且不重叠、LAN 前缀不超过 `/30`、网关与 DHCP 端点可用、
DHCP 范围有序且不含网关、4 个端口是 `1024-65535` 内互不相同的整数、租约格式合法、至少一个 DNS 上游。它只写文件：
不联网、不安装、不重启服务，也不修改 sshd、NetworkManager、路由或 IPv6 配置。

### 生成的 9 个文件

| 文件 | 内容 |
|---|---|
| `nftables.conf` | 输入 / 转发 / NAT 规则与地址伪装 |
| `dnsmasq-lan.conf` | 下游 DHCP 与 DNS 转发 |
| `gateway-sysctl.conf` | IPv4 转发与 rp_filter / redirect 相关开关 |
| `mihomo-automation.env` | 与网络配置一致的共享参数，以及待审阅的自动化独有参数默认值 |
| `mihomo/config.yaml` | Mihomo 主配置：TUN、DNS、mixed-port、控制端口、provider 与示例规则 |
| `mihomo/providers/general-a.yaml`、`general-b.yaml` | 通用池，**只允许放允许承载大流量下载的节点** |
| `mihomo/providers/ai-only.yaml`、`download-only.yaml` | AI 专用池；明确授权的下载节点池 |

四个 provider 文件里是故意不可用的占位节点（`REPLACE-*`，server 为 `*.example.invalid`），替换成自己的文件型
provider（顶层 `proxies` 列表）之前**这些配置没有真实出口**；供应商原始订阅要放在私有位置另行转换，不要把含
`rules` / `proxy-groups` 的整份订阅直接当 provider 文件使用。生成的规则覆盖有限：只有少量 AI、模型下载与 GitHub
下载域名，加上 LAN 与管理网段的直连规则，其余未知目的地由 `MATCH,Browsing` 兜底，正式使用前按自己的业务补齐；
也不要把 `DOMAIN-KEYWORD,google` 放在 googlevideo 之前，否则视频可能被误分到 AI 池。

### 手动安装生成文件

| 生成文件 | 目标位置 |
|---|---|
| `rendered/nftables.conf` | `/etc/nftables.conf` |
| `rendered/dnsmasq-lan.conf` | `/etc/dnsmasq.d/lan.conf` |
| `rendered/gateway-sysctl.conf` | `/etc/sysctl.d/90-gateway.conf` |
| `rendered/mihomo-automation.env` | `/etc/mihomo-automation.env`（0600；已有文件先合并共享字段） |
| `rendered/mihomo/` | `/etc/mihomo` |

保留上游 Wi-Fi 管理终端或本地控制台并备份目标文件，先检查已有 dnsmasq 的 `include` / `server` / `listen` 冲突；
Mihomo 的 systemd 单元需要自己准备（`ExecStart` 用实际二进制和 `-d /etc/mihomo`），再分阶段应用 sysctl、
DHCP / DNS、NAT 和代理。nftables 需要支持 `destroy table`；不要为了绕过报错使用 `flush ruleset`，语法检查必须在
目标系统上进行。

</details>

<details>
<summary>已有生产部署：环境与 DNS 数据流</summary>

## 已有生产部署参考

| 项目 | 说明 |
|---|---|
| 目标环境 | 单点、可信的本地内网（家庭 / 小工作室）；不适用于公网远程管理、多租户隔离 |
| 网关位置 | 上游路由器（DHCP / NAT）与下游交换机 / 桥接 AP 之间，下游为 IPv4 only |
| 分流方式 | Mihomo rule 模式 + TUN / auto-redirect 透明转发 |
| 管理方式 | 管理终端连接上游 Wi-Fi，经上游 LAN 访问 Pi 的 wan0 地址（SSH） |
| iKuai | 无依赖：旧方案里的 iKuai 设备已经移除，上游路由器与下游 AP 都不需要运行代理 |

Pi 不需要连接管理 Wi-Fi，也不需要 wlan0：只要管理终端能经上游内网访问 wan0 的地址即可。WAN / LAN 接口在部署端
按永久 MAC 固定命名，仓库不发布真实 MAC、SSID 或 NetworkManager 连接文件。

### DNS 数据流：生产分层与生成器基线

[![DNS 数据流：生产通过 MosDNS 分层，生成器直接转到 Mihomo DNS](assets/current-dns-flow.svg)](assets/current-dns-flow.svg)

实线为已有生产 DNS 路径；虚线是手动生成器和一键安装使用的 dnsmasq → Mihomo 简化路径。MosDNS 的分流配置与自定义域名规则已随 `automation/` 发布，
规则数据（国内 / 代理域名集合与 CN IP 集合）仍属部署端。MosDNS 不是替代 DHCP 的服务，dnsmasq 始终承担下游 DHCP。

</details>

<details>
<summary>生产运维层：业务选路、自适应机制与网络经验</summary>

## 生产运维层：多供应商与选路策略

[![业务选路：国内与游戏直连，浏览、AI、下载分池并遵守供应商能力](assets/current-routing-policy.svg)](assets/current-routing-policy.svg)

图示为已有生产方案的业务职责；手动生成器模板只提供三个代理组和少量示例规则，国内 / 游戏直连规则需自行补齐。
`automation/sbin/mihomo-policy-overlay` 与 `mihomo-adaptive-*` 是已发布的可执行实现：机制在仓库里，
节点能力、节点标签与阈值属于部署数据，公开的能力清单只保留服务定义。

- 三个业务池独立：`Browsing`（url-test）、`AI`（fallback）、`Download`（load-balance）。
- AI 池使用专用供应商（本部署为 GLaDOS），优先稳定地区和出口。
- 下载池由 STT、ByWave 和明确授权下载的 GLaDOS 节点组成；普通 GLaDOS 节点不会默认加入下载池，专用下载节点
  也不能只靠名称正则判断。
- 进阶设计用 `allowed_services`、`download_allowed`、`region`、`metered`、`enabled` 描述能力，先做能力过滤
  再评分。这些是选路元数据，不是公开生成器支持的站点字段，不能直接加入 `site.local.json`。
- AI 长连接与会话稳定性优先：切换不能迁移已有 TCP / WebSocket 会话，因此需要切换收益门槛、保持时间和故障
  冷却，避免为几毫秒延迟频繁断开连接。
- 下载吞吐是另一回事：`load-balance` 只在连接之间分配，不会把单个 `git clone` 或单文件流聚合成多供应商
  带宽；多连接下载需要支持分块的客户端，并遵守供应商限制。

## 生产运维层：自适应机制

[![自动化与自愈：定时任务、受门禁的评分与应用流水线](assets/current-automation.svg)](assets/current-automation.svg)

机制说明见[智能选节点设计](docs/adaptive-selection.zh-CN.md)，可执行实现在
[`automation/`](automation/README.zh-CN.md)：`mihomo-adaptive-score` 只做影子评分采集，
`mihomo-adaptive-apply` 只把已进入冷却的持续失败节点移出候选池，两次改动之间先语法验证再有限应用。

```text
采集 → 更新业务指标 → 能力过滤 → 连续失败门槛
  → 影子决策 → 保底候选池检查 → 备份候选配置
  → 语法验证 → 有限应用 → 分类业务验证 → 保留或回滚
```

一次失败只记录；连续失败才隔离，且必须为每个业务池保留最少候选数；上游本身故障时暂停节点惩罚；恢复要走冷却
与连续成功观察。`generate_204` 只能证明轻量连通性，不能证明 AI 会话、YouTube 播放或模型下载速度；HTTP 401
只能证明请求到达服务，不能证明账号已登录。

## 生产运维经验与网络验收注意事项

- **动态重定向端口**：2026-10-11 曾把 LAN INPUT 改成固定业务端口白名单，漏掉 Mihomo auto-redirect 的动态 TCP
  监听端口，PC 因此无法上网，而网关本机 curl 和显式代理仍正常。本方案以可信内网为边界，LAN INPUT 整体放行，
  不把事件端口写死；这不是适用于不可信访客网络的通用安全策略。
- **SSH**：生成器不修改 sshd，发行版现有认证策略不变；需要密码登录时用 `sshd -T` 确认，改完先 `sshd -t` 再
  reload，并从第二条连接验证。上游侧 SSH 只放行 `management_cidr` 整个网段，不按单台管理终端 IP 限制，也
  不需要公网映射。
- **更新节奏**：订阅更新间隔至少 24 小时，低峰执行并加随机延迟；`Persistent=true` 的 timer 可能在首次启动或
  补执行时立即触发一次，不能只看 `OnCalendar`。更新需要互斥、候选验证、回滚和业务检查；规则更新遇到
  GitHub 403 / 配额耗尽时保留旧规则，配额恢复后重试。
- **自动化层**：`automation/install.sh` 只覆盖脚本与单元，已存在的部署数据文件一律保留；八个 timer 中评分采集与
  执行是分开的，基础联网未稳定前不要启用 `mihomo-adaptive-apply.timer`；高频探测有流量成本，不能无预算测速。
- **下游 IPv4 only**：Mihomo 生成配置设 `ipv6: false`；已发布的 [MosDNS 骨架](automation/etc/mosdns/config.yaml)
  在 `main` 序列里对 qtype 28 / 64 / 65 返回 NOERROR 空答案（这是过渡取舍，不是通用推荐）。
  LAN 侧仍应关闭 IPv6 RA / DHCPv6，并确认上游 IPv6 不会被误分发到下游。
- **实测上下文（不是吞吐保证）**：低负载采样时两口协商 2.5Gbps 全双工、4 核 Cortex-A76、约 8GiB 内存、NVMe，
  约 99% CPU idle、约 34°C、可用内存约 6.7GiB，只说明当时有余量，不构成加密转发吞吐承诺；单队列网卡共享
  中断且 RPS 关闭，未实施 BBR、超频、performance governor 或未经验证的 backlog 调参。

</details>

<details>
<summary>无节点恢复镜像：校验、写盘与首次启动</summary>

## 无节点恢复镜像

从 [GitHub Release](https://github.com/ashllll/rpi5-mihomo-gateway-guide/releases/tag/recovery-2026.10.12)
下载 `.img.xz`、`image-manifest.json`、`SHA256SUMS` 和 `gateway_image.py`。镜像可用 Raspberry Pi Imager 写入，
也可在 macOS / Linux 上执行一条命令完成校验、写盘、回读和首次启动配置：

```sh
# 先查看磁盘列表，替换为你要清空的备用 SD 卡 / USB NVMe；不能选择正在运行的系统盘。
sudo python3 gateway_image.py restore \
  --image rpi5-gateway-node-free.img.xz --manifest image-manifest.json \
  --device /dev/diskN --user myadmin --ssh-key "$HOME/.ssh/id_ed25519.pub"
```

工具会要求输入完整的目标盘确认语句，**目标盘原有数据会全部清空**。macOS 使用 `/dev/diskN`，Linux 使用实际整盘路径；
加 `--dry-run` 可只校验。镜像默认通过有线上游 DHCP 联网，代理、DHCP 服务、NAT 与自动化定时任务暂不启用，
避免恢复后沿用旧网段或抢发 DHCP。个人站点参数与订阅随后按[恢复说明](docs/recovery-image.zh-CN.md)配置。
已发布镜像包含的是其构建时的源码快照，不包含此次新增的一键入口；使用新入口前需获取最新仓库。
当前镜像标为预发布：已提供模拟与文件系统检查，未在承载现网的树莓派上重刷启动验收。

</details>

## Landscape 融合验收计划

[逐步验收与回滚清单](docs/landscape-acceptance.zh-CN.md)覆盖平台预检、隔离启动、DNS/DHCP、透明 TCP/UDP、
业务策略、故障注入、重启、升级降级与完整回退，并给出只读预检命令、逐项证据字段和恢复流程。当前仅完成
验收设计：尚未实现 Landscape 部署、恢复工具或执行实网验收，清单中的用例全部保持待测。

## 隐私与安全边界

- 仓库有意排除运营凭据：不含订阅 URL / 令牌、节点凭据、Wi-Fi 或面板密码、私钥与证书、完整 `/etc/mihomo`
  归档、未脱敏的路由器备份；文档里的私有 IPv4 示例描述网络结构，不是凭据。
- 已发布的自动化源码遵守同一边界：订阅链接、节点标签、面板 token 与真实地址分别来自
  `/etc/mihomo-automation.env`、`/etc/mihomo/secrets` 与 `capabilities.yaml` 的 `overlay` 段，仓库里只有示例值与空清单。
- 一键入口的 `/etc/mihomo-gateway/` 与 `/var/lib/mihomo-gateway/` 包含订阅、节点缓存、管理密钥或备份，均应保持 root 私有，不得上传。
- 生成的候选配置包含随机 controller secret，必须保持私有；不要提交 `rendered/` 或 `site.local.json`。
- [工具](tools/validate_public.py) 与 CI 是附加防线，不是“任意归档都无密钥”的证明；发布前还应按
  [SECURITY.md](SECURITY.md) 用 Gitleaks 扫描工作区和 Git 历史（输出需脱敏）。发现误发布的凭据时不要开包含该
  值的公开 issue：先吊销，再通过私有渠道联系仓库所有者。

<details>
<summary>仓库结构与检查工具</summary>

## 仓库结构

| 路径 | 内容 |
|---|---|
| [`setup.sh`](setup.sh) / [`gateway/setup.py`](gateway/setup.py) | 一键入口、环境探测、下载验证、配置生成、安装事务与恢复 |
| [`gateway/defaults.json`](gateway/defaults.json) | 固定核心版本及摘要、默认端口、DNS、策略分类与更新周期 |
| [`docs/one-click-setup.zh-CN.md`](docs/one-click-setup.zh-CN.md) | 支持范围、订阅格式、高级参数、回滚与验证记录 |
| [`tools/check_setup.py`](tools/check_setup.py) | 有状态模拟、故障注入与可选真实核心解析验证 |
| [`tools/check_setup_linux.py`](tools/check_setup_linux.py) | 隔离 Linux 网络命名空间中的 nftables / dnsmasq 验证 |
| [`automation/`](automation/README.zh-CN.md) | 网关自动化脚本与 systemd 单元、三份 dnsmasq 上游基线、MosDNS 骨架、能力清单模板与安装器 |
| [`docs/current-architecture.zh-CN.md`](docs/current-architecture.zh-CN.md) | 当前架构、DNS 数据流、代理职责、自动化层边界与实测上下文 |
| [`docs/deployment-current.zh-CN.md`](docs/deployment-current.zh-CN.md) | 准备、生成、候选校验、手动安装映射、MosDNS 第二阶段与上线门禁 |
| [`docs/adaptive-selection.zh-CN.md`](docs/adaptive-selection.zh-CN.md) | 能力优先、指标与评分、隔离与恢复、会话与下载的设计说明 |
| [`docs/operations-current.zh-CN.md`](docs/operations-current.zh-CN.md) | 动态端口事件、端口表、三层验收与恢复判断 |
| [`examples/site.example.json`](examples/site.example.json) | 站点参数模板（虚构 10.66 / 10.77 网段） |
| [`tools/render.py`](tools/render.py) | 生成 9 个私有配置文件，含配套 env；输出目录必须不存在，权限 0700 / 0600 |
| [`tools/validate_public.py`](tools/validate_public.py) | 发布结构与隐私模式检查（全仓库禁值扫描，含订阅节点标签分隔符） |
| [`tools/check_render.py`](tools/check_render.py) | 生成器隔离、权限与参数传播自检 |
| [`tools/check_overlay.py`](tools/check_overlay.py) | 用合成配置验证 policy-overlay 的池选择、幂等性与空清单时 fail-closed |
| [`tools/check_automation.py`](tools/check_automation.py) | 自动化层语法、禁值、env 一致性检查与沙箱安装验证 |
| [`tools/check_subscription_candidate.py`](tools/check_subscription_candidate.py) | 通过真实候选入口检查合并、引用重命名、缓存与未变化判定 |
| [`tools/check_subscription_update.py`](tools/check_subscription_update.py) | 离线执行完整订阅更新流程，验证写入、中断、重载与健康检查失败后的回滚 |
| [`assets/`](assets/current-topology.mmd) | 四张当前架构图的 Mermaid 源码与导出 SVG，以及历史拓扑图 |
| [`.github/workflows/validate.yml`](.github/workflows/validate.yml) | CI：发布隐私、原有回归、一键安装模拟与隔离 Linux 命名空间检查 |
| [`SECURITY.md`](SECURITY.md) | 隐私与安全边界、禁止提交清单、凭据泄露处理 |

</details>

<details>
<summary>历史档案（2026-07）</summary>

## 历史档案

以下内容记录 2026-07 的部署，其中的拓扑、网段、供应商和选路均已变化，只作为历史记录保留，不代表当前配置，也不应直接复制：
[部署、调试与优化长文](docs/deployment-debugging-summary.zh-CN.md)（文首已标注历史档案，性能数据是当时设备的测试记录）、
[拓扑浏览页面](web/index.html)、[`assets/network-topology.svg`](assets/network-topology.svg) 和 [`assets/network-topology.png`](assets/network-topology.png)（页面顶部已注明历史档案）。

</details>

<details>
<summary>架构图维护：可编辑源文件与导出命令</summary>

## 架构图维护

当前图使用 Mermaid 绘制并导出为 SVG，README 无需依赖 Mermaid 客户端即可显示。点击图片可查看原图。
可编辑源文件：[物理拓扑](assets/current-topology.mmd)、[DNS 数据流](assets/current-dns-flow.mmd)、
[业务选路](assets/current-routing-policy.mmd)、[自动化与自愈](assets/current-automation.mmd)；
共享样式见 [diagram-theme.json](assets/diagram-theme.json)。

修改源文件后，使用 Node.js 和 Mermaid CLI 重新导出（首次运行需要下载工具）：

```sh
npx --yes --package @mermaid-js/mermaid-cli@12.0.0 mmdc -i assets/current-topology.mmd -o assets/current-topology.svg -c assets/diagram-theme.json -b white
npx --yes --package @mermaid-js/mermaid-cli@12.0.0 mmdc -i assets/current-dns-flow.mmd -o assets/current-dns-flow.svg -c assets/diagram-theme.json -b white
npx --yes --package @mermaid-js/mermaid-cli@12.0.0 mmdc -i assets/current-routing-policy.mmd -o assets/current-routing-policy.svg -c assets/diagram-theme.json -b white
npx --yes --package @mermaid-js/mermaid-cli@12.0.0 mmdc -i assets/current-automation.mmd -o assets/current-automation.svg -c assets/diagram-theme.json -b white
```

若提示缺少浏览器，先运行 `npx --yes puppeteer browsers install chrome-headless-shell`，或通过
`PUPPETEER_EXECUTABLE_PATH` 指定本机已安装的兼容 Chromium / Chrome；该路径不应写入公开配置。

发布前检查文字、箭头与实际配置是否一致，并运行隐私检查；不要把真实网段、MAC 或订阅填入图中。

</details>

## License

本仓库使用 [CC0 1.0 Universal](LICENSE)，在法律允许的最大范围内可以复制、修改、分发和用于商业用途；第三方产品名与商标归各自权利人所有。
