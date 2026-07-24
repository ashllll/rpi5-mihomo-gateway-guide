# Raspberry Pi 5 + Mihomo 网关部署、调试与优化完整总结

> 文档日期：2026-07-24
> 适用设备：Raspberry Pi 5、瑞莎双 2.5GbE 扩展板、独立无线 AP
> 系统：Debian GNU/Linux 13（trixie），ARM64
> Mihomo：Meta v1.19.28，Linux ARM64
> 文档性质：本次实际安装、调试、故障修复、性能优化和验收记录

## 1. 文档说明与安全边界

本文总结本次 Raspberry Pi 5 Mihomo 透明代理网关从规划、安装、调试到自动化运维的完整过程。

文档不保存以下敏感内容：

- Wi-Fi/AP 密码
- Mihomo Dashboard 密钥
- 机场订阅完整 URL 和令牌
- 节点服务器、UUID、密码、证书等连接凭据

敏感订阅地址只保存在树莓派的 root 专用文件中，权限为 `0600`。包含敏感信息的完整备份也仅保存在本地 Mac，禁止上传到公开网盘、GitHub 或聊天平台。

本文中的 IP 地址反映本次最终网络设计。WAN 和管理 Wi-Fi 的 DHCP 地址可能随主路由租约变化，不能把某个临时地址当成永久管理地址。

---

## 2. 最终目标与结论

本次目标不是把树莓派改造成全功能路由器系统，而是建立一台稳定、可维护、可自动恢复的家庭透明代理网关：

- 主路由继续负责上游拨号、主 Wi-Fi 和上游 DHCP。
- Raspberry Pi 5 负责下游 LAN 的 DHCP、DNS、NAT 和 Mihomo 透明代理。
- 独立 AP 接入树莓派 LAN，为无线客户端提供下游网络。
- 中国大陆域名/IP 直连。
- 游戏、Steam 等流量直连，避免进入普通代理。
- GitHub、YouTube、AI 等服务使用独立策略组，提高稳定性。
- 多个订阅源合并，并自动更新、验证、回滚。
- 大流量下载不使用 GLaDOS 节点，避免触发其限速策略。
- Mihomo 或代理节点故障时自动探测、切换、重载或重启。
- Mihomo 停止时 DNS 自动切换到直连 DNS，避免整个 LAN 因 DNS 失效而断网。

最终结果：

- 网关、DHCP、DNS、NAT 和 Mihomo 服务均已运行。
- LAN 客户端关闭本机 Surge 后仍能通过树莓派访问网络。
- Mihomo 管理 104 个有效代理节点和多个专用策略组。
- 规则、订阅和健康检查均已建立定时自动化。
- 已建立多阶段本地备份和恢复路径。
- AP 偶发下载慢的主要瓶颈已定位为“代理节点到特定 CDN 的单连接吞吐和冷启动”，不是 AP 信号、TUN MTU 或 LAN 链路故障。

---

## 3. 最终网络拓扑

```text
Internet
   │
主路由
├── 上游网段：192.168.100.0/24
├── 网关：192.168.100.1
├── 保留 DHCP
├── 保留原主 Wi-Fi
│
└── Ethernet
     │
     ▼
Raspberry Pi 5
├── wan0：从主路由 DHCP 获取地址
├── wlan0：管理与应急 SSH，只连接主路由 Wi-Fi
├── lan0：192.168.1.1/24
├── dnsmasq：下游 DHCP + DNS 前端
├── nftables：转发、NAT 和基础防火墙
└── Mihomo：TUN 透明代理、策略路由和 DNS
     │
     ▼
交换机 / 独立 AP（桥接/AP 模式）
     │
     ├── Mac
     ├── iPad
     ├── 手机
     ├── 电视
     └── 其他 LAN 设备
```

### 3.1 为什么下游改为 `192.168.1.0/24`

上游主路由已经使用 `192.168.100.0/24`。树莓派工作在双网口路由模式时，WAN 和 LAN 必须使用不同的三层子网。

如果 WAN 和 LAN 同时使用 `192.168.100.0/24`，Linux 无法稳定判断同一目标应该从哪个接口发出，容易出现：

- 路由歧义
- ARP 冲突
- 回包走错接口
- NAT 和策略路由异常
- 客户端偶发断网

因此最终采用：

- 上游：`192.168.100.0/24`
- 下游：`192.168.1.0/24`
- 树莓派 LAN 网关：`192.168.1.1`

部署初期 `192.168.1.1` 曾被原下游路由器占用，导致 NetworkManager 拒绝激活 LAN 配置。关闭旧路由器后，冲突解除。

---

## 4. 硬件与软件清单

### 4.1 硬件

- Raspberry Pi 5
- 瑞莎双 2.5GbE 扩展板
- NVMe/本地可靠存储
- 主路由千兆 LAN 口
- 独立无线 AP
- 交换机及网线

### 4.2 当前链路状态

| 接口 | 用途 | 当前状态 | 协商速率 |
|---|---|---|---:|
| `wan0` | 接主路由 | UP | 1Gbps，全双工 |
| `lan0` | 接交换机/AP | UP | 2.5Gbps，全双工 |
| `wlan0` | 管理与应急 SSH | UP | 由主路由 Wi-Fi 提供 |

WAN 只协商到 1Gbps 是正常现象，因为上游主路由物理端口本身只有千兆，不属于故障。

### 4.3 当前系统

```text
操作系统：Debian GNU/Linux 13 (trixie)
架构：aarch64 / ARM64
内核：6.18.34+rpt-rpi-2712
Mihomo：Meta v1.19.28 linux arm64
```

主要组件：

- NetworkManager
- nftables
- dnsmasq
- Mihomo
- systemd
- curl、jq 等维护工具

---

## 5. 基础安装与预处理

### 5.1 系统准备

使用 64 位 Raspberry Pi OS/Debian 系统，优先放在 NVMe 上。完成系统更新并安装基础组件：

```bash
sudo apt update
sudo apt install -y \
  nftables \
  dnsmasq \
  curl \
  ca-certificates \
  jq \
  ethtool \
  iproute2 \
  iperf3
```

确认系统架构：

```bash
uname -m
```

预期结果：

```text
aarch64
```

### 5.2 固定 WAN/LAN 接口名

扩展板上的两个接口原本可能显示为 `eth1`、`eth2`，重启或驱动初始化顺序变化时存在交换风险。

最终通过 systemd `.link` 文件按网卡永久 MAC 地址命名：

```text
/etc/systemd/network/10-radxa-wan.link  -> wan0
/etc/systemd/network/11-radxa-lan.link  -> lan0
```

设计目的：

- 避免重启后 WAN/LAN 对调。
- 避免 nftables、dnsmasq 和 Mihomo 绑定错误接口。
- 让配置文件只依赖 `wan0`、`lan0` 这两个稳定名称。

网口识别阶段使用了网卡 LED 闪灯命令确认物理口，随后停止闪灯，再根据实际连线固定用途。

### 5.3 NetworkManager 网络职责

最终职责如下：

#### `wan0`

- 连接主路由 LAN。
- IPv4 使用 DHCP。
- 默认路由由主路由提供。
- DNS 不直接分发给下游客户端。

#### `lan0`

- 固定地址：`192.168.1.1/24`
- 不设置上游网关。
- 不从外部获取 DHCP。
- 作为下游 DHCP、DNS 和网关入口。

#### `wlan0`

- 继续连接主路由 Wi-Fi。
- 仅作为管理与 SSH 应急通道。
- 通过管理路由脚本避免它抢占 WAN 数据出口。

管理 Wi-Fi 的价值是：即使 LAN 配置错误，仍可从上游 Wi-Fi 登录树莓派回滚配置。

### 5.4 主路由与 AP 应如何配置

主路由：

- 保留上游 DHCP。
- 保留主 Wi-Fi。
- 无需安装代理插件。
- 无需关闭 DHCP。
- 给 `wan0` 和 `wlan0` 提供上游地址。

下游 AP：

- 使用 AP/桥接模式。
- 关闭 AP 自己的 DHCP。
- 不执行二次 NAT。
- LAN 口连接树莓派 `lan0` 下游交换网络。

下游只能有一个 DHCP 权威服务器，即树莓派上的 dnsmasq。否则会出现客户端随机获得错误网关或 DNS。

---

## 6. Linux 转发与基础网络配置

### 6.1 IPv4 转发

核心配置：

```conf
net.ipv4.ip_forward=1
```

### 6.2 反向路径与重定向

透明代理和策略路由需要关闭严格反向路径过滤：

```conf
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
net.ipv4.conf.wan0.rp_filter=0
net.ipv4.conf.lan0.rp_filter=0
```

同时禁用 ICMP Redirect：

```conf
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
```

验证：

```bash
sysctl net.ipv4.ip_forward
sysctl net.ipv4.conf.all.rp_filter
sysctl net.ipv4.conf.all.accept_redirects
sysctl net.ipv4.conf.all.send_redirects
```

### 6.3 nftables

当前 nftables 主要表：

```text
inet rp_filter
ip   rp_nat
inet mihomo
```

职责划分：

- `rp_filter`：树莓派基础输入/转发控制。
- `rp_nat`：LAN 到 WAN 的基础 IPv4 masquerade。
- `mihomo`：由 Mihomo `auto-redirect` 自动管理透明代理规则。

必须避免在服务运行期间执行无条件 `flush ruleset`。该命令会同时删除 Mihomo 自动生成的表和链，导致透明代理突然失效。

检查：

```bash
sudo nft list ruleset
```

基础 NAT 的作用不仅是给直连流量上网，也为 Mihomo 停止后的 fail-open 直连路径提供兜底。

---

## 7. DHCP 与 DNS 架构

### 7.1 dnsmasq 的职责

dnsmasq 只负责：

- LAN DHCP
- LAN DNS 入口
- 本地 DNS 缓存
- 在 Mihomo DNS 和直连 DNS 之间切换

关键 LAN 配置：

```conf
interface=lan0
listen-address=127.0.0.1,192.168.1.1
bind-dynamic

dhcp-range=192.168.1.100,192.168.1.199,255.255.255.0,12h
dhcp-option=3,192.168.1.1
dhcp-option=6,192.168.1.1

cache-size=1000
servers-file=/etc/dnsmasq-upstreams.conf
```

客户端应获得：

```text
IP：192.168.1.100-199
网关：192.168.1.1
DNS：192.168.1.1
```

第一阶段 LAN 只启用 IPv4，避免 IPv6 绕过透明代理或形成无法解释的双栈路径。

### 7.2 Mihomo 正常时的 DNS

```text
客户端
  ↓ 53
dnsmasq：192.168.1.1
  ↓
Mihomo DNS：127.0.0.1:1053
  ↓
按 Mihomo DNS 策略解析
```

Mihomo 正常时，dnsmasq 上游配置为：

```conf
server=127.0.0.1#1053@lo
```

### 7.3 Mihomo 停止时的 DNS 回退

Mihomo 停止或启动失败时，systemd 钩子自动把 dnsmasq 上游切换到直连公共 DNS：

```conf
server=223.5.5.5@wan0
server=119.29.29.29@wan0
```

切换使用原子文件替换并重新加载 dnsmasq，防止出现半写入状态。

这项修复解决了本次最关键的问题：

> 客户端关闭 Surge 后，接入树莓派 LAN 就完全没网。

原始截图中客户端 DNS 曾显示为 `198.18.0.2`。该地址属于 Fake-IP 使用范围，不应由 DHCP 直接作为客户端 DNS 服务器下发。客户端依赖 Surge 时，Surge 掩盖了网关 DNS 故障；关闭 Surge 后，真实 DNS 链路失效，表现为“有 IP、有网关，但网页全部打不开”。

最终通过以下方式修复：

1. DHCP 只向客户端下发 `192.168.1.1` 作为 DNS。
2. dnsmasq 固定监听 LAN 53 端口。
3. Mihomo DNS 改为本机回环地址的非 53 端口。
4. Mihomo 启停时自动切换 dnsmasq 上游。
5. 客户端关闭 Surge 后重新获取 DHCP/DNS并验收。

---

## 8. Mihomo 安装与系统服务

### 8.1 安装原则

Mihomo 使用官方 ARM64 二进制，原生运行，不使用 Docker。

原因：

- 减少容器网络层。
- TUN、nftables 和 systemd 权限更清晰。
- 启动顺序和故障定位更简单。
- 不依赖 Docker bridge 与透明代理规则的兼容性。

安装后确认：

```bash
mihomo -v
```

配置检查：

```bash
sudo mihomo -t -d /etc/mihomo
```

### 8.2 systemd 服务

Mihomo 由 systemd 管理：

```bash
sudo systemctl enable --now mihomo
sudo systemctl status mihomo --no-pager
sudo journalctl -u mihomo -n 100 --no-pager
```

服务具有自动重启能力，并通过 hardening override 限制运行权限。

### 8.3 权限与加固

Mihomo 仅保留必要能力：

```text
CAP_NET_ADMIN
CAP_NET_RAW
CAP_NET_BIND_SERVICE
```

同时启用：

- `NoNewPrivileges`
- `PrivateTmp`
- `ProtectHome`
- `ProtectSystem=full`
- `UMask=0077`

订阅文件、缓存和备份中的敏感配置使用 root 权限和 `0600`。

---

## 9. Mihomo TUN 配置逻辑

当前核心配置：

```yaml
tun:
  enable: true
  stack: mixed
  device: Mihomo
  auto-route: true
  auto-redirect: true
  auto-detect-interface: false
  strict-route: true
  dns-hijack:
    - any:53
    - tcp://any:53
  include-interface:
    - lan0
  route-exclude-address:
    - 192.168.0.0/16
    - 224.0.0.0/4
```

配置逻辑：

- 只接管 `lan0` 的下游流量。
- 不接管 `wlan0` 管理流量。
- `auto-route` 建立 TUN 路由。
- `auto-redirect` 使用 nftables 接管透明流量。
- `strict-route` 降低漏流和错误回程风险。
- 劫持 LAN 客户端的 TCP/UDP 53 请求，统一经过本机 DNS。
- 排除私网和组播地址，保证管理设备、打印机和局域网服务可访问。
- 关闭自动出口检测，避免 Wi-Fi 管理接口和有线 WAN 同时在线时选错出口；出口由明确的网络策略控制。

---

## 10. 订阅合并与节点管理

### 10.1 当前订阅结构

当前合并三个订阅源：

| 来源 | 有效节点数量 | 主要用途 |
|---|---:|---|
| GLaDOS | 56 | 默认、AI、流媒体、开发等 |
| BWB | 19 | 备份和下载 |
| InfraAPI | 29 | 备份和下载 |
| 合计 | 104 | 合并后统一管理 |

订阅 URL 不直接写入公开配置，而是保存在：

```text
/etc/mihomo/secrets/subscriptions.d/*.url
```

权限：

```text
root:root
0600
```

订阅缓存：

```text
/var/lib/mihomo-extra-subscriptions/
```

同样使用 `0600`。

### 10.2 合并时的清洗

自动更新脚本执行以下处理：

1. 下载主订阅和额外订阅。
2. 首选经过当前代理下载，失败时尝试直连。
3. 限制下载文件大小，防止异常响应占满磁盘。
4. 检查 YAML 结构和代理节点字段。
5. 删除公告、套餐提示等伪节点。
6. 给不同来源增加名称前缀，避免同名节点冲突。
7. 合并为统一配置。
8. 应用本地策略覆盖层。
9. 执行 `mihomo -t`。
10. 保存旧配置快照。
11. 重载 Mihomo。
12. 验证关键访问路径。
13. 失败则自动回滚到上一份可用配置。

已从 InfraAPI 订阅中过滤两个公告型伪节点，防止它们进入策略组并造成假健康。

### 10.3 自动更新周期

```text
订阅更新：每 24 小时一次，附加随机延迟
规则更新：每天约 04:15，附加随机延迟
健康检查：每 2 分钟一次，附加约 20 秒抖动
```

随机延迟用于避免所有任务同时访问订阅源或集中重启服务。

---

## 11. 策略组设计

当前配置包含 19 个代理组。主要组如下：

| 策略组 | 类型 | 节点/成员数 | 用途 |
|---|---|---:|---|
| `Default Proxy` | select | 35 | 普通海外访问 |
| `Research + AI` | select | 18 | AI、研究服务 |
| `Streaming` | fallback | 11 | YouTube 等流媒体 |
| `Developer` | url-test | 59 | GitHub、开发服务 |
| `Download` | url-test | 49 | 大流量下载 |
| `Steam` | select | 10 | Steam 相关策略 |
| `Auto` | url-test | 60 | 通用自动选路 |
| `TW Fixed IP` | fallback | 8 | 需要固定地区/出口的 AI 服务 |
| `BWB Backup` | fallback | 19 | BWB 订阅备份 |
| `InfraAPI Backup` | fallback | 29 | InfraAPI 订阅备份 |

### 11.1 为什么使用不同组

不同服务对网络质量的要求不同：

- GitHub 更依赖稳定 TLS、Release/CDN 和长连接。
- YouTube 更依赖持续带宽和流媒体 CDN。
- AI 服务可能对地区和出口 IP 稳定性敏感。
- 大文件下载更依赖吞吐，不应只看延迟。

把所有网站都放进一个 `url-test` 组，会出现“延迟最低节点并不适合某个服务”的问题。因此建立 Developer、Streaming、Research + AI 和 Download 等专用组。

### 11.2 自动切换

- `url-test`：周期性测试成员并自动选择低延迟可用节点。
- `fallback`：优先使用前面的节点，失效后自动切换。
- `select`：允许稳定锁定特定策略，同时保留自动组作为选项。

现有连接默认不强制中断，避免看视频、下载或远程连接在节点切换时立即断开。

---

## 12. 分流规则

### 12.1 当前规则集

当前启用 6 个远程/本地规则集：

| 标签 | 文件 | 用途 |
|---|---|---|
| `private-ip` | `geoip-private.mrs` | 私网地址直连 |
| `cn-ip` | `geoip-cn.mrs` | 中国大陆 IP 直连 |
| `cn-domain` | `geosite-cn.mrs` | 中国大陆域名直连 |
| `game-domain` | `dustinwin-games.mrs` | 游戏域名直连 |
| `game-cn-domain` | `dustinwin-games-cn.mrs` | 国内游戏域名直连 |
| `steam-domain` | `geosite-steam.mrs` | Steam 相关规则 |

### 12.2 规则顺序

Mihomo 规则从上到下匹配，因此优先级是核心设计：

```text
1. 私网和局域网地址 -> DIRECT
2. 游戏与游戏加速器相关流量 -> DIRECT
3. Steam 等游戏平台 -> DIRECT/Steam 策略
4. 中国大陆域名与 IP -> DIRECT
5. GitHub/开发服务 -> Developer
6. YouTube/流媒体 -> Streaming
7. AI 服务 -> Research + AI / TW Fixed IP
8. 大流量下载域名 -> Download
9. 其他海外流量 -> Default Proxy
```

### 12.3 删除进程规则

曾考虑加入 Windows 进程规则，但树莓派作为三层网关无法看到客户端 Windows 上的进程名称，只能看到：

- 源/目标 IP
- 域名
- 端口
- TCP/UDP 协议

因此网关上的 `PROCESS-NAME` 规则不会按预期工作，最终已删除，避免形成无效配置和错误安全感。

### 12.4 游戏流量

需求是：

- 国内游戏直连。
- 国外游戏不走普通代理，由专用游戏加速器处理。

当前使用 DustinWin 游戏规则、国内游戏规则和 Steam 规则作为网关级直连依据。

必须理解其限制：游戏规则主要根据域名/IP 分类，无法识别客户端进程。若未来同一台 PC 上出现特定游戏漏匹配，应增加具体域名/IP规则，或在 PC 本机使用进程级策略，而不是把所有 UDP 流量一律直连。

---

## 13. 大流量下载策略

### 13.1 设计要求

GLaDOS 对大流量下载存在限速或使用策略风险，因此 Download 组必须完全排除 GLaDOS 节点。

当前 Download 组共 49 个成员：

```text
InfraAPI：29
BWB：19
DIRECT：1
GLaDOS：0
```

### 13.2 已覆盖的常见大文件域名

包括但不限于：

- GitHub Release、Git LFS、GitHub 容器资源
- Docker 镜像与下载资源
- Hugging Face、Civitai、Kaggle
- PyTorch、TensorFlow、NVIDIA、Ollama
- Microsoft、Apple、VS Code、JetBrains
- Mozilla、Adobe

### 13.3 重要限制

Mihomo 在连接建立时只能依据域名、IP、端口等条件选路，无法在同一个连接下载到一定字节数后再判断“这是大文件”并切换线路。

因此：

- 已知下载站点通过域名规则进入 Download。
- 未知站点仍可能进入默认策略。
- 发现新的大文件站点后应补充域名规则。
- `url-test` 主要按延迟选择节点，低延迟不等于大文件单连接吞吐最高。

未来若追求下载性能，可为 Download 组建立专门的吞吐探测逻辑，而不是只依赖 HTTP 延迟。

---

## 14. 自动化、自恢复和回滚

### 14.1 健康检查

健康检查每 2 分钟运行一次，并分别测试：

- 上游直连
- 默认代理
- Developer
- Streaming
- Research + AI
- Download

采用分级恢复，避免短暂抖动触发频繁重启：

```text
连续 1 次失败：记录，不动作
连续 2 次失败：触发相关策略组重新测试
连续 3 次失败：重载 Mihomo
持续多类别失败：重启 Mihomo
上游直连本身失败：等待上游恢复，不反复重启代理
```

### 14.2 当前自恢复实例

2026-07-24 检查时曾发现 Streaming 类别连续两次失败，系统自动触发组检查；下一轮检测恢复：

```text
mode=healthy
direct=true
default=true
developer=true
streaming=true
ai=true
download=true
failed_categories=0
action=none
```

这证明现有自动化能够区分单类别故障并完成恢复，而不是直接导致整个网关断网。

### 14.3 订阅更新回滚

更新脚本具有以下保护：

- 互斥锁，防止多个更新任务并发。
- 下载失败使用已知良好缓存。
- 配置语法检查失败不替换正式配置。
- 重载后检查 GitHub、YouTube、AI 和下载路径。
- 验证失败自动恢复上一份配置。
- 保留最近约 20 份快照。

### 14.4 DNS fail-open

Mihomo 故障时：

```text
LAN -> 基础 NAT -> WAN
DNS -> dnsmasq -> 直连公共 DNS
```

因此代理故障不应导致整个 LAN 完全失去国内网络。海外代理网站可能不可用，但基本网络、DHCP 和 DNS 仍应工作。

---

## 15. 本次遇到的问题、根因与修复

| 阶段 | 现象 | 根因 | 修复 | 经验 |
|---|---|---|---|---|
| 方案选择 | Landscape 功能强但部署风险高 | 目标是稳定代理而不是研究型路由数据面 | 改用 Debian/Raspberry Pi OS + Mihomo | 方案应服务于目标，不追求技术复杂度 |
| 网口识别 | 不确定 ETH1/ETH2 对应哪个物理口 | 接口名与物理口缺少固定映射 | LED 闪灯识别，再按 MAC 固定为 `wan0`/`lan0` | 先识别物理接口再写网络规则 |
| LAN 地址 | `192.168.1.1` 无法激活 | 原下游路由器仍占用该地址 | 关闭旧路由器，消除地址冲突 | 改地址前先检查 ARP 和冲突设备 |
| 子网规划 | 希望 WAN/LAN 都保持 `100.x` | 双网口路由不能在两侧使用同一子网 | WAN 保持 `192.168.100.0/24`，LAN 改为 `192.168.1.0/24` | WAN/LAN 必须三层隔离 |
| DHCP | 担心要关闭主路由 DHCP | 混淆了上游和下游广播域 | 主路由 DHCP 保留；树莓派只服务下游 LAN；AP DHCP 关闭 | DHCP 是否冲突取决于是否处于同一广播域 |
| LAN 无网 | 插 LAN 后客户端有地址但打不开网页 | DNS 链路不完整，客户端依赖 Fake-IP/Surge | dnsmasq 统一监听 53；下发 `192.168.1.1`；增加 DNS 自动回退 | “有 IP”不代表 DNS 和转发可用 |
| Surge 掩盖故障 | 开 Surge 有网，关 Surge 立即断网 | Mac 本机代理绕过了错误的网关 DNS/代理路径 | 所有验收必须关闭本机 Surge 后执行 | 客户端代理会制造假阳性 |
| nftables | 担心 Mihomo 规则被破坏 | 基础配置若 `flush ruleset` 会删除自动链 | 基础表和 Mihomo 表分离，不在运行中全量 flush | 自动生成规则不应被静态脚本清空 |
| 代理订阅下载慢 | 订阅和依赖下载速度不足 | 直连到订阅/CDN路径慢 | 更新脚本先用当前代理，失败再直连，并缓存已知良好版本 | 安装阶段可临时使用代理，但正式系统不能依赖 Mac |
| 订阅合并 | 多订阅有重名和公告节点 | 不同机场格式不一致，包含伪节点 | 添加来源前缀、过滤公告节点、验证结构 | 订阅不能直接拼接后投入生产 |
| 规则 | 进程规则无效 | 网关看不到客户端进程 | 删除 PROCESS-NAME 规则 | 进程级分流必须放在终端本机 |
| 游戏 | 游戏不能进入普通代理 | 单靠 CN 规则不能覆盖所有海外游戏 | 启用 DustinWin games、games-cn 和 Steam 规则并直连 | 游戏规则仍需按实际连接补漏 |
| AI 稳定性 | AI 服务可能因出口变化异常 | 自动组切换会改变出口 IP/地区 | 增加 Research + AI 和 TW Fixed IP 组 | AI 服务需要地区和会话稳定性 |
| GitHub/YouTube 卡顿 | 通用自动组无法兼顾所有服务 | 节点到不同 CDN 的质量不同 | 建立 Developer、Streaming 专用组和分类健康检查 | 延迟最低不等于业务体验最好 |
| 下载限速 | 大下载可能走 GLaDOS | 默认组包含 GLaDOS | Download 组明确排除全部 GLaDOS 节点 | 线路策略要表达供应商限制 |
| AP 下载慢 | 无线下载偶发慢、转圈 | 代理节点到特定 CDN 的单连接吞吐和冷启动 | 进行有线/无线、单/多连接、不同订阅和显式代理对照测试 | 不能看到 Wi-Fi 就先归因信号 |
| iPad LOL 延迟 | 手游固定约 108ms | iPad 当时连接主路由 Wi-Fi，未经过树莓派 | 先确认客户端实际网关和路径，再定位游戏/运营商/加速器 | 不经过 Pi 的流量不能归因 Mihomo |
| 测试脚本 | 某次结果误报 RED | 临时解析器分隔符处理错误 | 重跑并交叉验证原始输出 | 自动测试也要验证测试工具本身 |
| 临时端口 | 临时测试服务残留占端口 | 测试结束未完整清理 PID | 按监听端口查进程并终止，后续使用随机端口 | 临时诊断工具也要有清理步骤 |

---

## 16. AP 下载慢的专项诊断

### 16.1 无线链路结果

AP 当前工作在：

```text
5GHz
Wi-Fi 6
80MHz
信号约 -39dBm
PHY 速率约 960Mbps
```

LAN 内 iperf3 多轮测试：

```text
无线 8 轮平均：约 526.9Mbps
最低：约 418.8Mbps
波动系数：约 9.1%
有线 LAN：约 2.30Gbps
```

这说明：

- AP 信号质量正常。
- 无线局域网本身没有固定在几十 Mbps。
- LAN 口、交换链路和 AP 基础能力正常。

### 16.2 外网对照

国内直连 Wi-Fi 平均约 `135.6Mbps`，基础外网路径可用。

GitHub 相同目标测试：

```text
Wi-Fi 单连接：约 22.2Mbps
有线单连接：约 25.8Mbps
多连接下载：约 60-90Mbps
```

不同 BWB/InfraAPI 节点对 Cloudflare 测试在暖连接时可达到约 `130-160Mbps`，但同一节点访问 GitHub 目标仍可能只有 `15-27Mbps`。

显式 HTTP 代理和透明 TUN 的结果接近，因此以下因素不是主要根因：

- TUN MTU
- `auto-redirect`
- AP 无线信号
- 单纯的无线驱动

最终判断：

> 慢点主要位于“代理节点/机场出口 -> GitHub 或特定 CDN”的路径，尤其是单 TCP 连接吞吐、冷启动、跨境拥塞和运营商/CDN 对等互联。

### 16.3 为什么会表现为卡顿和转圈

浏览器访问 GitHub、YouTube 和 AI 服务时会并发加载许多不同域名：

- 主站
- API
- 静态资源
- 图片/CDN
- 视频分片
- WebSocket/流式响应

只要其中某个域名落到质量较差的节点或连接冷启动慢，页面就可能表现为：

- 点击无响应
- 图片迟迟不加载
- 页面转圈
- 视频首帧慢
- AI 流式回答中断或停顿

因此健康检查必须按业务类别测试，而不能只探测一个通用 `generate_204` URL。

### 16.4 当前优化与后续建议

已经实施：

- Developer、Streaming、Research + AI 分组。
- 多订阅自动切换。
- 按类别独立健康检查。
- Download 组排除 GLaDOS。

可进一步优化：

1. 为 GitHub Release、Git LFS 和容器域名单独测试真实小文件。
2. 为 Streaming 使用 YouTube/Google Video 代表性探测。
3. 为 AI 服务使用对应服务的轻量 HTTPS/API 健康检查。
4. Download 组增加吞吐采样，不只按延迟选节点。
5. 监控 24 小时内节点切换次数，避免组过度抖动。
6. Mac 测试时只保留一条有效网络路径，或显式绑定 Wi-Fi/有线接口。

---

## 17. 验收与检查流程

### 17.1 每次改配置前

```bash
sudo cp -a /etc/mihomo "/etc/mihomo.backup-$(date +%Y%m%d-%H%M%S)"
sudo mihomo -t -d /etc/mihomo
```

同时确认管理 Wi-Fi SSH 可用，不要在唯一连接路径上直接修改网络。

### 17.2 服务状态

```bash
systemctl is-active NetworkManager
systemctl is-active nftables
systemctl is-active dnsmasq
systemctl is-active mihomo
systemctl --failed
```

预期：

```text
四项服务均为 active
failed units = 0
```

### 17.3 接口与路由

```bash
ip -br address
ip route
ethtool wan0
ethtool lan0
```

检查：

- `wan0` 有 `192.168.100.0/24` 地址。
- 默认路由指向 `192.168.100.1`。
- `lan0` 是 `192.168.1.1/24`。
- `wan0` 和 `lan0` 均为 UP。

### 17.4 DHCP

客户端接入 LAN/AP 后应获得：

```text
地址：192.168.1.100-199
网关：192.168.1.1
DNS：192.168.1.1
```

如果仍看到 `198.18.x.x` 作为系统 DNS，应释放 DHCP 租约、关闭终端代理并重新连接。

### 17.5 DNS

```bash
dig @192.168.1.1 baidu.com
dig @192.168.1.1 github.com
```

分别验证国内和海外域名。

### 17.6 直连与代理

在客户端完全关闭 Surge/系统代理后测试：

```bash
curl -I https://www.baidu.com
curl -I https://github.com
curl -I https://www.youtube.com
```

AI 服务应使用浏览器实际登录和流式请求测试，不能只以 DNS 或 TCP 建连作为验收。

### 17.7 fail-open

在有 Wi-Fi 管理通道和回滚准备的条件下：

1. 停止 Mihomo。
2. 确认 dnsmasq 自动切到直连上游。
3. 确认 LAN 仍能打开国内网站。
4. 重新启动 Mihomo。
5. 确认 dnsmasq 自动切回 `127.0.0.1:1053`。
6. 验证 GitHub、YouTube 和 AI 路径恢复。

### 17.8 断电重启验收

```text
1. 安全关机或断电模拟重启。
2. 确认接口仍命名为 wan0/lan0。
3. 确认 lan0 地址仍为 192.168.1.1。
4. 确认 DHCP 和 DNS 自动启动。
5. 确认 nftables 基础 NAT 存在。
6. 确认 Mihomo 自动启动并建立 TUN。
7. 客户端关闭本机代理后完成国内、GitHub、YouTube 和 AI 测试。
8. 查看 systemd 是否有失败单元。
```

---

## 18. 日常运维命令

### 18.1 查看整体状态

```bash
ip -br address
ip route
systemctl --failed
systemctl status mihomo dnsmasq nftables NetworkManager --no-pager
```

### 18.2 查看 Mihomo 日志

```bash
sudo journalctl -u mihomo -n 200 --no-pager
sudo journalctl -u mihomo -f
```

### 18.3 检查配置

```bash
sudo mihomo -t -d /etc/mihomo
```

### 18.4 查看 nftables

```bash
sudo nft list ruleset
```

### 18.5 查看 dnsmasq

```bash
sudo dnsmasq --test
sudo systemctl status dnsmasq --no-pager
sudo journalctl -u dnsmasq -n 100 --no-pager
```

### 18.6 查看定时任务

```bash
systemctl list-timers --all | grep -E 'mihomo|subscription|rule|health'
```

### 18.7 手动执行维护任务

实际脚本位于：

```text
/usr/local/sbin/mihomo-healthcheck
/usr/local/sbin/mihomo-update-subscription
/usr/local/sbin/mihomo-update-rules
/usr/local/sbin/mihomo-reconcile-policy
/usr/local/sbin/mihomo-dns-mode
```

手动执行前应先查看脚本帮助/内容并确认当前 SSH 通过管理 Wi-Fi 可回退。

---

## 19. 本地维护文件

本地应保存一份不含订阅凭据的维护脚本副本，例如：

```text
<local-maintenance-root>/rpi-mihomo-maintenance/
```

主要内容：

- Wi-Fi 管理路由
- 管理 ARP 参数
- dnsmasq LAN 配置
- Mihomo systemd 加固
- DNS failover
- Mihomo DNS 模式切换
- 策略覆盖
- 健康检查
- 策略校准
- 规则更新
- 订阅更新
- systemd service/timer 文件

这些文件用于审计、对照和灾后恢复，不应盲目整目录覆盖在线系统。恢复时必须逐项确认接口名、地址、路径和当前系统版本。

---

## 20. 备份记录

### 20.1 完整网关基线

```text
<local-backup-root>/rp5-gateway-complete-20260717-151811/
```

包括：

- `/etc`
- 引导/固件配置
- `/usr/local`
- 用户和 root 相关配置
- NetworkManager
- nftables
- dnsmasq
- systemd
- Mihomo 状态
- 更新和回滚点
- SHA256 校验文件

### 20.2 优化后 Mihomo

```text
<local-backup-root>/rp5-mihomo-optimized-20260717-232008/
```

### 20.3 多订阅版本

```text
<local-backup-root>/rp5-mihomo-multisub-20260722-072815/
```

### 20.4 三订阅与下载安全版本

```text
<local-backup-root>/rp5-mihomo-3providers-download-safe-20260722-075938/
```

这是当前较完整的三订阅、Download 排除 GLaDOS 版本，包含：

```text
README
SHA256SUMS
rpi-complete-config.tar.gz
```

### 20.5 变更前快照

```text
<local-backup-root>/mihomo-pre-glados-opt-20260717-230925
<local-backup-root>/mihomo-pre-multisub-20260722-072433
<local-backup-root>/mihomo-pre-infraapi-20260722-074424
```

### 20.6 备份安全

完整备份包含订阅 URL、节点凭据或 Dashboard 密钥的可能性很高：

- 保持目录权限 `0700`。
- 敏感文件保持 `0600`。
- 不上传 GitHub。
- 不通过聊天工具直接发送。
- 对外分享前必须制作脱敏副本。

---

## 21. 恢复方式

### 21.1 推荐的分层恢复

不要一开始全量覆盖 `/etc`。推荐顺序：

```text
1. 先恢复 /etc/mihomo 和维护脚本。
2. 执行 mihomo -t 验证。
3. 重启 Mihomo，验证代理与 DNS。
4. 必要时恢复 dnsmasq 配置。
5. 必要时恢复 nftables 和 sysctl。
6. 最后才恢复 NetworkManager 和接口命名。
```

### 21.2 为什么网络配置最后恢复

恢复以下内容可能立即中断 SSH：

- NetworkManager 连接
- systemd `.link` 接口命名
- 默认路由
- LAN 地址
- nftables 输入规则

因此恢复网络层之前必须具备：

- 主路由 Wi-Fi 管理通道，或
- 本地显示器/键盘/串口，或
- 可直接挂载磁盘修改配置的方案

### 21.3 恢复后检查

```bash
sudo mihomo -t -d /etc/mihomo
sudo systemctl restart mihomo
sudo systemctl restart dnsmasq
sudo nft list ruleset
systemctl --failed
```

随后按照第 17 节完成关闭终端代理后的完整验收。

---

## 22. 当前运行状态快照

检查日期：2026-07-24。

### 22.1 网络

```text
wan0：UP，主路由 DHCP，当前检查时为 192.168.100.30/24
lan0：UP，192.168.1.1/24
wlan0：UP，管理 Wi-Fi，当前检查时为 192.168.100.28/24
默认上游网关：192.168.100.1
```

上述 WAN/Wi-Fi 地址会随 DHCP 变化。

### 22.2 服务

```text
NetworkManager：active
nftables：active
dnsmasq：active
mihomo：active
failed units：0
```

### 22.3 配置规模

```text
代理节点：104
代理组：19
规则：约 1200
规则集：6
```

### 22.4 自动化

```text
健康检查：2 分钟
订阅更新：24 小时
规则更新：每天
配置失败：自动回滚
代理故障：分级自恢复
DNS：Mihomo/直连自动切换
```

---

## 23. 当前仍存在的限制

### 23.1 IPv6

当前下游以 IPv4 为主。正式启用 IPv6 前必须同时解决：

- 上游前缀获取
- LAN RA/DHCPv6
- IPv6 防火墙
- Mihomo IPv6 接管
- IPv6 DNS
- 防止 IPv6 绕过代理

在这些项目未一起完成前，不应只在 AP 或 LAN 上单独开启 IPv6。

### 23.2 游戏识别

网关无法按客户端进程识别游戏。DustinWin 和 Steam 规则能覆盖常见域名，但不能保证所有动态游戏服务器。

### 23.3 下载组选路

Download 当前使用 `url-test`，主要优化延迟和可用性，不等同于实时测量大文件吞吐。

### 23.4 机场/CDN 波动

即使 LAN 和 AP 正常，机场节点到 GitHub、YouTube 或 AI 服务的跨境路径仍会随时间变化。自动切换可降低影响，但不能消除运营商、机场和 CDN 上游故障。

### 23.5 主路由 Wi-Fi 与树莓派 LAN 是两条路径

连接主路由 Wi-Fi 的设备默认不经过树莓派。诊断延迟或代理问题前，必须先确认客户端：

- 当前连接哪个 SSID
- 获得哪个网段
- 默认网关是谁
- DNS 是谁

---

## 24. 后续优化优先级

### P0：保持稳定

- 每次改配置前备份并运行 `mihomo -t`。
- 保留管理 Wi-Fi，不在唯一 SSH 路径上冒险。
- 每周查看 systemd 失败单元和健康检查日志。
- 定期验证备份 SHA256。

### P1：改善业务体验

- 为 GitHub、YouTube、AI 增加更贴近真实业务的探测目标。
- 记录策略组切换历史，识别经常抖动的节点。
- 给 Download 组增加吞吐测试。
- 观察 GLaDOS、BWB、InfraAPI 各自的长期可用率。

### P2：可观测性

- 保存按类别的成功率、延迟和切换次数。
- 建立轻量状态页或日报。
- 记录订阅更新、规则更新、回滚和恢复事件。

### P3：高级网络

- 在充分测试后再评估 IPv6。
- 如需游戏 PC 精确按进程分流，在 PC 本机配置，而不是继续堆叠网关规则。

---

## 25. 最终经验总结

1. **稳定网关首先要保证基础网络独立可用。**
   DHCP、DNS、转发、NAT 应在终端代理关闭时单独验收。

2. **不要让 Surge 掩盖网关故障。**
   本次最初“开 Surge 有网、关 Surge 没网”就是典型假阳性。

3. **WAN 与 LAN 必须使用不同子网。**
   双网口路由设计不能为了保留旧地址而牺牲三层路由正确性。

4. **接口名必须固定。**
   任何依赖 `eth1`/`eth2` 枚举顺序的生产配置都不可靠。

5. **Mihomo 和 dnsmasq 应明确分工。**
   客户端只认识树莓派 DNS，Mihomo DNS 作为本机上游，并提供直连回退。

6. **自动化必须包含验证和回滚。**
   自动下载订阅不等于自动运维；只有经过结构检查、配置测试、业务验收和失败回滚才可靠。

7. **低延迟节点不一定适合下载。**
   GitHub/CDN 的单连接吞吐、冷启动和对等互联会显著影响体验。

8. **不同业务应使用不同策略组和探测目标。**
   GitHub、YouTube、AI 和下载对线路的要求不同。

9. **网关看不到客户端进程。**
   游戏规则可以按域名/IP补充，但精确进程分流必须放在 PC 本机。

10. **诊断前先确认真实流量路径。**
    连接主路由 Wi-Fi 的 iPad 并未经过树莓派，不能把其游戏延迟直接归因于 Mihomo。

11. **备份必须可验证、可分层恢复。**
    完整压缩包之外，还需要 README、SHA256 和明确的恢复顺序。

12. **应急管理通道非常重要。**
    保留 `wlan0` 管理 Wi-Fi，使网络配置出错后仍能安全回滚。

---

## 26. 参考资料

- Mihomo 官方文档：<https://wiki.metacubex.one/>
- Mihomo TUN 配置：<https://wiki.metacubex.one/config/inbound/tun/>
- Mihomo DNS 配置：<https://wiki.metacubex.one/config/dns/>
- Mihomo Rule Provider：<https://wiki.metacubex.one/config/rule-providers/>
- Mihomo Proxy Provider：<https://wiki.metacubex.one/config/proxy-providers/>
- Raspberry Pi 官方文档：<https://www.raspberrypi.com/documentation/>
- NetworkManager 文档：<https://networkmanager.dev/docs/>
- nftables Wiki：<https://wiki.nftables.org/>
- dnsmasq 项目文档：<https://thekelleys.org.uk/dnsmasq/doc.html>

---

## 27. 文档与备份位置速查

本文：

```text
docs/deployment-debugging-summary.zh-CN.md
```

维护脚本副本：

```text
<local-maintenance-root>/rpi-mihomo-maintenance/
```

完整备份根目录：

```text
<local-backup-root>/
```

在任何重大修改前，应先创建新的时间戳备份，并在修改后更新本总结或新增变更记录。
