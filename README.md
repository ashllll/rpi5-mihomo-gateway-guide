<p align="center">RASPBERRY PI 5 · LINUX · MIHOMO</p>

<h1 align="center">让整个局域网，按业务选择出口</h1>

<p align="center">
双网口透明代理网关：设备照常联网，网关负责分流、节点探测与故障恢复。<br>
包含一键安装入口，以及独立的生产运维脚本与配置模板。
</p>

<p align="center">
<a href="#快速开始">快速开始</a> ·
<a href="#本次稳定性更新">稳定性更新</a> ·
<a href="#选择部署路径">部署路径</a> ·
<a href="#验证与边界">验证状态</a> ·
<a href="#文档导航">完整文档</a>
</p>

---

## 一台网关，接在路由器与 AP 之间

**上游路由器 → 双网口 Linux 网关 → 交换机 / 桥接 AP → 下游设备**

上联保留原有联网配置，下联提供 DHCP、DNS 与透明转发。电脑、手机和其他终端无需逐台安装代理客户端。
支持 Raspberry Pi 5，也支持符合安装要求的 Linux 主机。

[![业务选路参考：国内与游戏直连；浏览、AI、下载使用独立节点池，并遵守供应商能力限制](assets/current-routing-policy.svg)](assets/current-routing-policy.svg)

图示是生产运维方案参考，**不是一键入口的完整默认配置**。一键安装、手动生成器与生产运维层是三条独立路径。

| 使用目标 | 网关如何处理 |
|---|---|
| 国内网站与本地设备 | 使用直连规则；生产方案包含游戏直连策略 |
| X、Telegram、YouTube | 独立业务组与对应网站探测；Telegram 同时覆盖官方 IP 网段 |
| ChatGPT 等 AI 服务 | 独立 AI 池；按部署能力选择供应商，生产参考使用 GLaDOS |
| 外网文件与模型下载 | 使用明确允许下载的节点；不把普通受限节点混入下载池 |

## 快速开始

在已经联网的目标 Linux 主机上：

```sh
git clone https://github.com/ashllll/rpi5-mihomo-gateway-guide.git
cd rpi5-mihomo-gateway-guide
sudo sh setup.sh
```

安装器以不回显方式读取 HTTPS 订阅链接，检测网口、规划下游网段，生成 Mihomo、DHCP / DNS 与防火墙配置。
切换前保存恢复点，失败时回滚；重复运行复用已有订阅、网段、端口与管理密钥。

先从**上联网口 SSH 或本地控制台**操作，下联接交换机 / 桥接 AP，并关闭该链路上其他 DHCP 服务。目标机需要能访问
发行版软件源、GitHub Release 和规则源。

```sh
sudo sh setup.sh --dry-run  # 只检查环境与打印计划，不下载、不安装、不切换网络
```

预览需要现有 Python 与 iproute2；正式安装会安装缺失依赖。高级参数、无人值守输入与恢复方法见
[一键配置指南](docs/one-click-setup.zh-CN.md)。

<details>
<summary>系统要求与不自动处理的环境</summary>

| 项目 | 支持范围 |
|---|---|
| 系统 | Linux、运行中的 systemd、Python 3.9+ |
| 架构 | ARM64、ARMv7、x86-64；Mihomo 固定版本并验证 SHA256 |
| 软件包管理 | apt-get、dnf、pacman、zypper |
| 网口 | 按默认路由识别上联；唯一剩余的独立以太网口作为下联；可用已连接 Wi-Fi 上联 |
| 网络管理 | NetworkManager 或 systemd-networkd；只配置下联 |
| 订阅 | 核心可解析的 Clash/Mihomo 节点 YAML、URI 列表和 base64 URI 列表 |
| 下游 | IPv4；dnsmasq → Mihomo DNS，TUN / auto-redirect 与 nftables NAT |

单网口旁路由、OpenWrt、容器、bridge/bond/VLAN、已有 VPN/TUN 和复杂防火墙需要手动整合。
多个网口不能唯一判断时，使用 `--wan` / `--lan` 指定；检测到已有生产代理服务时需先迁移。
默认策略见 [gateway/defaults.json](gateway/defaults.json)，可用 `--defaults` 指定自己的参数文件。
自动测速不能判断平台解锁或供应商下载授权。

</details>

## 本次稳定性更新

这次更新针对生产运维层的分钟级断连与恢复扰动。**一键入口不调用这套 overlay / 评分流程。**

| 之前的问题 | 当前策略 |
|---|---|
| 核心业务探测间隔为 2–5 分钟 | AI、X、Telegram、YouTube 组采用可配置的 45 秒主动探测 |
| 某个请求卡住，后续业务检查跟着等待 | 每分钟并行检查各业务路径 |
| 下载池隔离频繁热加载整个配置 | 默认只启用影子评分；隔离执行 timer 改为显式启用 |
| 单业务异常触发全局 reload / restart | 在对应业务组内复测；仅进程未运行时请求重启 |
| 有测速数字就认为节点健康 | 同时核对期望状态码对应的 per-URL 健康状态 |
| API 正常，ChatGPT 网页 403 被混为同一种状态 | 分别记录 API 连通性、网页状态与网站拒绝访问 |
| Telegram 只有域名分流 | 补充 [Telegram 官方网段](https://core.telegram.org/resources/cidr.txt) |

45 秒是检测周期，**不是恢复时间承诺**。已有 TCP / WebSocket 连接不能无缝迁移到另一节点；
网站验证、账号状态、上游线路和供应商故障仍需分别判断。

配置在 [capabilities.yaml](automation/policy/capabilities.yaml) 的 `stability` 与 `services` 段；
节点标签清单仍留在部署端。已存在的 manifest 不会被安装器覆盖，需要手动合并新的设置。
仅安装脚本或编辑 manifest 不会更新正在运行的策略；下一次满足更新间隔、校验与健康门禁的成功订阅更新，
才会重新应用 overlay 并加载配置。上线后核对运行配置的组检测周期与 Telegram 网段，再做分层业务验收。
完整机制与边界见 [智能选节点设计](docs/adaptive-selection.zh-CN.md)。

## 选择部署路径

| 你的起点 | 使用入口 | 私有配置位置 |
|---|---|---|
| 一台新 Linux 主机 | [setup.sh](setup.sh) | `/etc/mihomo-gateway/` |
| 自己规划网络、分阶段上线 | [tools/render.py](tools/render.py) | `site.local.json` → 9 个私有文件，随后手动部署 |
| 维护已有 Mihomo / MosDNS 网关 | [automation/](automation/README.zh-CN.md) | `/etc/mihomo`、`/etc/mosdns` 与部署 manifest |
| 保持 UCG 为客户端网关，按设备接入 Mihomo | [UCG / WireGuard 单设备试点](docs/ucg-wireguard-pilot.zh-CN.md) | UCG 原生策略与 Pi 私有 WireGuard 配置；手动整合 |
| 从备用介质恢复系统 | [无节点恢复镜像](docs/recovery-image.zh-CN.md) | 独立预发布镜像；写盘会清空目标盘 |

生产运维层的安装方式：

```sh
sudo automation/install.sh --dry-run
sudo automation/install.sh
sudo automation/install.sh --enable-timers
```

默认启用七个 timer，**不启用** `mihomo-adaptive-apply.timer`。升级已有部署时，若它已启用，安装器不会替你停用：

```sh
sudo systemctl disable --now mihomo-adaptive-apply.timer
```

运维人员编辑过的能力清单、DNS 配置与参数文件保留。安装后的订阅、controller token、节点清单与网络参数仍需自行配置。
详细步骤见 [部署指南](docs/deployment-current.zh-CN.md) 与 [自动化层说明](automation/README.zh-CN.md)。

## 验证与边界

| 验证层 | 已有证据 | 不能由此推断 |
|---|---|---|
| 一键安装逻辑 | 33 项模拟、故障注入与重复安装 / 回滚检查 | 所有真实网卡与网络管理器组合都可用 |
| 核心解析 | 已核对的真实 Mihomo 核心与有效 / 无效订阅检查 | 平台解锁、账号可用或下载吞吐 |
| Linux 网络 | [隔离命名空间 CI](https://github.com/ashllll/rpi5-mihomo-gateway-guide/actions/runs/38201843668) 验证 nftables / dnsmasq | 目标机的真实 LAN、无线环境与重启恢复 |
| 本次生产策略 | 2026-10-16 四轮共 16 次显式代理检查通过，使用者确认浏览器 / App 恢复正常 | 真实 LAN 全路径验收、长期零断连或固定恢复时间 |
| 故障切换机制 | 隔离核心在 3 秒测试周期下约 2.93 秒切到备用出口 | 生产 45 秒周期具有相同切换时间 |

一键安装的真实目标机与下游验收仍需完成；生产策略的验证不能替代它。
模拟、核心解析、本机请求、显式代理与真实 LAN 客户端是不同层次的证据。

**上线前按三层验收：**本机服务与路由 → 显式代理 → 关闭客户端本地 VPN/TUN 后的真实 LAN。
最后实际播放视频、测试 AI 流式会话，并在有备用管理通道时验证重启恢复。
`curl --noproxy '*'` 只忽略代理环境变量，不会关闭系统 VPN/TUN。

```sh
python3 tools/validate_public.py
python3 tools/check_overlay.py
python3 tools/check_automation.py
python3 tools/check_stability.py
```

[GitHub Actions](https://github.com/ashllll/rpi5-mihomo-gateway-guide/actions/workflows/validate.yml) 在 push / PR 时运行完整回归。
历史安装验证命令见 [一键配置指南](docs/one-click-setup.zh-CN.md)。

## 检查与恢复

<details>
<summary>一键安装：服务、日志与回滚命令</summary>

```sh
sudo systemctl status mihomo-gateway mihomo-gateway-dhcp mihomo-gateway-firewall
sudo journalctl -u mihomo-gateway -n 80 --no-pager
sudo python3 /opt/mihomo-gateway/setup.py --rollback latest
```

程序在 `/opt/mihomo-gateway/`，配置在 `/etc/mihomo-gateway/`，备份在 `/var/lib/mihomo-gateway/backups/`。
日志与备份可能含节点信息，应留在本机；回滚恢复网络与原服务状态，已安装依赖包保留。

</details>

<details>
<summary>生产运维：先区分是哪一层故障</summary>

| 现象 | 下一步 |
|---|---|
| 本机能上网，LAN 客户端不能 | 核对真实客户端路由与 auto-redirect 的动态端口；不要只测显式代理 |
| ChatGPT 命令行 403，但浏览器正常 | 查看 `chatgpt_state`；网站拒绝该请求不等于节点断网 |
| 订阅或规则更新失败 | 查看有效期、GitHub 配额与下载校验；失败时保留旧配置 |
| 视频进入 AI 池 | 检查规则顺序，避免宽泛 Google 规则抢先匹配 googlevideo |
| 大文件仍然慢 | 核对节点下载许可、线路吞吐与客户端分块；load-balance 不会聚合单条连接 |
| ping 可达、SSH 超时 | 检查 INPUT、管理来源网段与 sshd；不要直接认定主机离线 |

本方案以可信 LAN 为前提，LAN INPUT 整体放行以容纳动态透明代理端口；不是访客网络隔离方案。
生成器不改 sshd，密码登录可按部署需要保留。
网络、DNS 与恢复检查见 [运维指南](docs/operations-current.zh-CN.md)。

</details>

## 文档导航

| 想做的事 | 继续阅读 |
|---|---|
| 安装、订阅格式、指定网口与回滚 | [一键配置指南](docs/one-click-setup.zh-CN.md) |
| 理解拓扑与 DNS 分层 | [当前架构](docs/current-architecture.zh-CN.md) · [拓扑图](assets/current-topology.svg) · [DNS 图](assets/current-dns-flow.svg) |
| 手动配置与分阶段上线 | [部署指南](docs/deployment-current.zh-CN.md) · [站点参数模板](examples/site.example.json) |
| 业务策略、定时任务与评分 | [自动化层](automation/README.zh-CN.md) · [智能选节点](docs/adaptive-selection.zh-CN.md) |
| 故障定位与真实 LAN 验收 | [运维指南](docs/operations-current.zh-CN.md) |
| 查看 UCG 设备分流的配置前后、验证与回退边界 | [2026-10-20 链路迭代记录](docs/ucg-wireguard-pilot.zh-CN.md) · [参数清单](examples/ucg-pilot.example.json) |
| 校验、写盘与恢复系统 | [无节点恢复镜像](docs/recovery-image.zh-CN.md) |
| 评估 Landscape 融合 | [可行性](docs/landscape-feasibility.zh-CN.md) · [能力报告](docs/landscape-capabilities.zh-CN.md) · [验收计划](docs/landscape-acceptance.zh-CN.md) |
| 查看早期方案 | [2026-07 历史档案](docs/deployment-debugging-summary.zh-CN.md) · [历史拓扑](web/index.html) |

Landscape 当前只有验收设计，尚未实现融合部署，也未执行对应实网验收。

## 隐私与安全

仓库发布机制代码与示例参数，**不发布订阅 URL、节点标签与凭据、controller token、Wi-Fi 密码或真实网络配置**。
`site.local.json`、`rendered/`、运行配置、日志和备份必须留在部署端。
公开 manifest 的 `overlay` 清单保持为空；不要把个人订阅粘贴到 issue 或 PR。

提交前使用发布检查与 Gitleaks 扫描工作区和 Git 历史。发现误发布凭据时先吊销，通过私有渠道处理，参见
[SECURITY.md](SECURITY.md)。

[CC0 1.0 Universal](LICENSE) · 第三方产品名与商标归各自权利人所有。
