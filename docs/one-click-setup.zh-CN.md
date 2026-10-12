# 一键配置与模拟验证

项目根目录的 `setup.sh` 是完整网关安装入口。把项目下载、解压到目标 Linux 主机后运行：

```sh
sudo sh setup.sh
```

首次运行只询问机场的 **HTTPS 订阅链接**，输入不回显。脚本检测上联、下联、网络管理器、已有路由与网段、
端口占用和 CPU 架构，安装依赖，下载并核对 Mihomo 的 SHA256，生成网络、DHCP/DNS、透明代理和分流配置。
依赖由发行版包管理器安装；不要求手工配置 Python 虚拟环境、pip、节点列表或策略组。
目标机需能访问发行版软件源、GitHub Release 和规则源；订阅链接本身不能替代首次下载核心所需的联网条件。

## 自动识别的范围

| 项目 | 行为 |
|---|---|
| 系统 | Linux + systemd，Python 3.9+；入口可自动安装 Python |
| 包管理器 | apt-get、dnf、pacman、zypper；Debian/Ubuntu/Raspberry Pi OS 使用 dnsmasq-base，避免启动发行版的全接口 DHCP 服务 |
| CPU | ARM64、ARMv7、x86-64；核心版本和各架构校验值集中在 `gateway/defaults.json` |
| 网络管理器 | NetworkManager 或 systemd-networkd；存在竞争管理器时停止并说明原因 |
| 上联 | 有 IPv4 默认路由的物理接口，优先最低 metric；允许有线或已连接的 Wi-Fi 上联 |
| 下联 | 唯一剩余的、未加入 bridge/bond 的物理以太网接口；不依赖 eth0、wan0、lan0 等固定名称 |
| 网段 | 从 RFC1918 范围选择与已知地址及所有路由表不重叠的 /24；按主机标识稳定选择，重复安装复用站点参数 |
| 地址与 DHCP | 自动分配网关、地址池和 DNS；下联 IPv4 only，上联配置保持原样 |
| 策略 | 国内与私网直连，其余默认代理；AI、流媒体、Telegram、下载组自动引用订阅节点，默认由 Auto 选择节点 |
| 订阅刷新 | Mihomo 原生 provider 定时刷新；新增、删除节点自动反映到策略组 |
| 服务端口 | 初次安装自动避开已占用的 TCP/UDP 端口，重复安装复用已有端口 |

“通用”指根据支持环境检测并生成配置。单网口旁路由、OpenWrt、容器、bridge/bond/VLAN 拓扑、现有 VPN/TUN、
多个出口、已部署的其他代理和复杂防火墙需要先明确网络归属，脚本不会猜测并覆盖这些环境。
多个网口或双上联不能唯一判断时，可以明确指定，例如：

```sh
sudo sh setup.sh --wan enp1s0 --lan enp2s0
sudo sh setup.sh --lan-cidr 10.77.0.0/24
```

接口和网段只是示例。指定网段也要通过冲突检查。SSH 会话若经过将要重配的 LAN，安装会停止，需从上联重新连接或使用本地控制台。
下游接交换机或桥接 AP；该链路上已有的路由器 DHCP 应先关闭。脚本无法从本机路由表证明远端设备上不存在另一个 DHCP 服务。

## 策略与订阅

订阅交给 Mihomo 原生 provider 解析，不发送到第三方订阅转换网站。支持带 `proxies` 节点的 Clash/Mihomo YAML、
核心支持的 URI 列表和 base64 URI 列表；只有远程 provider 引用、没有节点的配置不在此入口的支持范围。支持范围以固定版本核心的解析结果为准，
初次安装会启动一个关闭 TUN/DNS、只监听回环控制端口的临时核心，读取其 provider API，确认至少有一个可用节点。
只含公告、DIRECT、空列表或登录 HTML 的返回不会进入网络切换。

机场的完整配置中只导入节点，不采用机场提供的 DNS、监听地址、规则或管理端口。策略配置来自项目的
[`gateway/defaults.json`](../gateway/defaults.json)：国内域名/IP 集合取自 MetaCubeX 的规则仓库，服务域名分类为可编辑默认值。
自动延迟选择不能判断节点是否解锁某个平台、是否允许大流量下载，也不能替代吞吐测试；这些供应商限制不可能仅靠订阅链接可靠推断。

不依赖现有 `automation/` 中需要人工维护节点能力标签的 overlay。完整安装使用独立的 `mihomo-gateway*` 服务与目录，
已有生产 `mihomo`/MosDNS 自动化部署需要先迁移，不会被本入口静默接管。

高级参数可复制默认策略文件后调整，用 `--defaults /path/to/defaults.json` 指定。更换核心版本时必须同步更新官方压缩包 SHA256。
核心不自动升级；规则和订阅按配置中的周期刷新。

## 预览、无人值守输入与重复安装

```sh
sudo sh setup.sh --dry-run
# 只检测并输出计划，不安装依赖、不下载、不询问链接、不写入文件。
```

预览需要已安装 Python 和 iproute2 才能读取网络状态；缺失时会说明，不会为了预览安装它们。
正式安装另外检查 TUN、现有防火墙和 DNS/DHCP 监听冲突，再下载和验证候选。

无人值守安装从权限为 `0600` 的文件读取链接，避免链接进入 shell 历史和进程参数：

```sh
sudo sh setup.sh --subscription-file /root/airport.url
```

重复运行复用原订阅、LAN 网段、端口和 controller secret；提供新的 `--subscription-file` 可更换订阅。
安装后也可以执行 `sudo python3 /opt/mihomo-gateway/setup.py`。变更 LAN 接口归属前，应回滚原安装。

## 备份、回滚与验收

下载、核心解析、`mihomo -t`、`dnsmasq --test`、`nft -c` 都在改变 LAN 前完成。网络切换前备份文件、服务状态、
原 LAN 连接及内核参数，注册 systemd 恢复计时器，并限制激活阶段的执行时间。普通异常、信号中断、健康门禁失败会执行回滚；
进程被杀或切换途中重启，恢复计时器从备份运行独立的恢复程序。

```sh
sudo python3 /opt/mihomo-gateway/setup.py --rollback latest
```

恢复过程若失败，会保留恢复标记和备份，并打印可直接运行的恢复命令。依赖包安装不属于网络配置事务，回滚会保留已安装的软件包。
备份保存在 `/var/lib/mihomo-gateway/backups/`，包含私密配置，保持 root 专用权限，不能发布。

安装器会检查服务、LAN 地址、上联默认路由、provider、DNS 和本机显式代理 HTTPS。成功消息只是**本机安装门禁通过**。
最后仍应让真实下游设备重新获取地址，检查 DHCP、DNS、国内直连、海外 TCP/UDP、管理连接及重启后的行为。
本机 HTTP 成功不能证明透明转发或真实设备验收通过。

运行文件：

- `/etc/mihomo-gateway/`：私有订阅、配置、规则缓存和 controller secret。
- `/opt/mihomo-gateway/`：固定版本核心、安装/恢复程序及策略默认值。
- `mihomo-gateway.service`、`mihomo-gateway-dhcp.service`、`mihomo-gateway-firewall.service`：独立的系统服务。
- nftables 仅管理自己的 `inet mihomo_gateway` 表；不执行 `flush ruleset`，不覆盖 SSH 配置。

## 模拟测试

```sh
python3 tools/check_setup.py
```

标准库测试在临时目录中写入真实生成文件，用有状态的 Linux 命令模拟器检查服务、地址、连接与内核参数的变化。
覆盖自动/歧义网口、Wi-Fi 上联、虚拟接口排除、VPN 路由网段冲突、端口占用、只读预览、下载失败与摘要不符、
两种网络管理器、重复安装、9 个激活阶段故障、部分写入、进程中断、健康检查失败和恢复失败后的再次恢复入口。

真实核心的附加测试使用合成节点，不连接真实机场：

```sh
python3 tools/check_setup.py --mihomo /path/to/mihomo --rules /path/to/test-rules
# test-rules 目录需有 cn.mrs 和 cn-ip.mrs；验证 YAML、URI、base64 与无效订阅。
```

Linux 测试主机还可以执行：

```sh
sudo python3 tools/check_setup_linux.py
```

它先创建独立网络命名空间，再验证真实 nftables/dnsmasq 解析、两次防火墙加载和删除，以及外部表保留；
不会改测试主机所在的网络命名空间。此项已加入 CI。模拟器和命名空间解析测试均不能代替真实网卡、网络管理器及下游流量验收。

上游参考：[Mihomo provider](https://wiki.metacubex.one/config/proxy-providers/)、
[TUN](https://wiki.metacubex.one/config/inbound/tun/)、[rule-provider](https://wiki.metacubex.one/config/rule-providers/)、
[v1.19.32 发布与校验值](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.32)。

### 2026-10-12 本地验证记录

| 验证层 | 结果 |
|---|---|
| 安装逻辑与故障注入 | 33 个测试通过，包括 9 个激活阶段失败、两种网络管理器和从已安装入口重复运行 |
| 真实 Mihomo 核心 | SHA256 已核对的 v1.19.32 Darwin ARM64；4 种有效订阅接受、4 种无效订阅拒绝，生成配置通过 `mihomo -t` |
| 原有项目回归 | 配置生成器、运维层、策略 overlay、订阅候选、16 个订阅更新场景、8 个规则更新场景、恢复镜像检查通过 |
| 发布检查 | ShellCheck、Python 编译、文档链接、发布隐私检查、Gitleaks 与 `git diff --check` 通过 |
| Linux 内核 / 网络管理器实跑 | 命名空间检查已接入 CI；本次在 macOS 开发机上，尚未执行 Linux 检查或真实网关安装 |
| 下游实网 | DHCP 获取、透明 TCP/UDP、重启和真实网卡行为尚待目标机验收 |
