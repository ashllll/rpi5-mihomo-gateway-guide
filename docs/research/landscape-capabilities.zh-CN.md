> **外部项目研究，非本仓库交付。** 本文是一次候选路线调研与验收计划，描述 Landscape Router 等外部项目，不代表本仓库已实现的功能、当前配置或验收结论。本机现状见 [架构](../architecture.zh-CN.md) 与 [部署](../deployment.zh-CN.md)。

# Landscape 能力范围核实报告（固定版本 v0.26.3）

本报告回答一个问题：**Landscape Router 到底能做什么、不能保证什么，以及它对当前 RPi5 + Mihomo 融合项目意味着什么。**
所有结论都标注证据层级，未实机验证的部分一律明说，不把猜测写成事实。

- 报告日期：2026-10-13
- 固定发行版：**v0.26.3**（发布 2026-10-12T12:13:13Z）
- 固定源码提交：`34845f91ed18dab5c9851e71538548c35eb2efcd`（tag `v0.26.3`，本地只读镜像核对一致，工作区干净）
- 对照 main：`e9f019b5ff3b5306a7b336ee36693ca374618480`（2026-10-12T22:24:41Z）——**main 上的新行为不计入本报告**
- 文档依据：本轮查阅的官方页面；网站与默认分支会变化，不能代替固定发行版源码
- 本报告**未执行任何现网/实机操作**：没有安装、没有拨测、没有改配置、没有连接设备

## 1. 版本与证据层

### 1.1 标签含义

| 标签 | 含义 |
| --- | --- |
| 【文】 | 官方文档明确描述（附直链） |
| 【源】 | 在固定提交的源码中确认（附文件路径） |
| 【差】 | 文档与发行版不一致，或文档未覆盖而源码已有 |
| 【未证】 | 未找到内建证据；同时给出检索范围，不等于"不存在" |
| 【未测】 | 只能靠实机验证，本报告没有验证 |

源码链接一律形如 `https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/<路径>`，下文简写为 `blob/<路径>`。

### 1.2 证据层的三个边界

1. **文档与源码存在覆盖差异。** v0.26.3 已含 WAN 健康检测、自动续证等源码，部分参考页较简略。文档未写清的能力需回到固定源码核对；当前网站的新示例也不能直接替代旧版参数。
2. **上游 README 是宣传口径。** "升级无忧……配置自动迁移，降级同样支持"是上游自我描述【文-上游声称】；本轮已在源码中核对降级的真实边界（见 §9.8），结论比口号窄得多。
3. **全部为文档与源码结论。** 性能、稳定性、故障行为、断电一致性均无实机证据。

### 1.3 许可与依赖边界

`landscape-ebpf` 为 GPLv2，其余部分为 GPLv3【源 `README.zh.md`】；`landscape-ebpf` 的 NATv4 复用了 `einat-ebpf`（GPL-2.0-or-later）的报文处理、分片跟踪与部分辅助函数【源 `landscape-ebpf/README.md`】。若要再分发融合镜像，第三方许可不因本项目自身许可是 CC0 而改变。

## 2. 网络基础：接口、区域、拨号、VLAN 与 IPv6

### 2.1 区域（Zone）是一切的入口【文】

每张网卡在参与路由前必须先归属一个区域，共三种：

| 区域 | 数据管道 | 可开启的服务 |
| --- | --- | --- |
| Wan | eBPF WAN TC 管道 | 静态 IP / DHCP Client / PPPoE / PPPD / DHCPv6-PD；NAT、防火墙、MSS 钳制、WAN 路由 |
| Lan | eBPF LAN TC 管道 | DHCP Server、LAN 路由、LAN IPv6 分配、ICMPv6 RA |
| Undefined | 无 | 不能开任何服务；**只有** Undefined 网卡能被附加到桥接上，也可开 WiFi AP 模式 |

两条硬约束：Wan 区域的网卡**不能**作为桥接成员；切换区域走 `ChangeZone` API 时 Landscape 会自动清理旧区域下所有服务并调整桥接与 eBPF 管道。文档：<https://landscape.whileaway.dev/zh/reference/interface-zone.html>

### 2.2 拨号与转发前提

- PPPoE 两种：**pppd 方式需要自己额外安装 pppd**；native 拨号直接在建 WAN 区域网卡上拨，"兼容性会稍微差点"【文 <https://landscape.whileaway.dev/zh/reference/pppoe.html>】。
- 源码侧 `WanLinkConfig.attach_iface_name` 记录底层物理口，pppd 链路的 `pppX` 设备名单独放在 `Pppd` 变体里【源 `landscape-common/src/wan_link/config.rs`】——与"外部 ppp"模型一致。
- LAN↔WAN 要能通，必须开启对应网卡的路由转发服务【文 <https://landscape.whileaway.dev/zh/features/ebpf-route.html>】。
- **IPv4 DHCP 内建**：Lan 区域可开 DHCP Server，配置结构为 `dhcpv4_services`，官方另有 `reference/dhcpv4` 与 `reference/lan-hostname` 参考页【文+源 `landscape-common/src/config/init.rs`】。

### 2.3 VLAN 是**临时方案**【文+差】

官方页面标题即"vlan 配置"，正文写明"当前为临时方案"，做法是在 `/etc/network/interfaces` 手工创建 `eth0.10` 这类子接口并设为 `manual`；页面还提醒"如果之后页面上支持创建 vlan 网卡后，需要再手动删除添加的 vlan 配置"。
→ 官方给出的是系统侧手工创建 VLAN 子接口。本轮未确证 v0.26.3 完整 UI 创建入口；这不等于数据平面不能处理 VLAN，也不应把所有 VLAN 规划写成界面内完成。
文档：<https://landscape.whileaway.dev/zh/reference/vlan-temp.html>

### 2.4 IPv6：PD、LAN 分配与 Docker IPv6

- PD 前提是运营商支持前缀申请；若运营商只支持 RA 通告地址，则 PD 方案不可用【文 <https://landscape.whileaway.dev/zh/reference/ipv6/ipv6-pd.html>】。
- "预期上游 PD 前缀长度"**不影响**能否拿到上游前缀，但**影响 LAN 侧 IPv6 分配配置**；实际前缀在"网络状态 → 上游 PD 前缀"查看，长度不符会在此报错。
- LAN 分配三种：SLAAC（RA）、DHCPv6、混合（RA + DHCPv6）；前缀按"画布"分块，例如拿到 /60 就能给下游分 16 个 /64【文 <https://landscape.whileaway.dev/zh/reference/ipv6/lan-ipv6.html>】。
- Docker IPv6 需要三步：`/etc/docker/daemon.json` 打开 `ipv6` 并设 `fixed-cidr-v6`；compose 里固定 `com.docker.network.bridge.name`；网卡出现后**先把区域转为 LAN 再开 LANv6**，并配置一个 IPv6 段，否则容器拿不到地址。容器内如无 DHCPv6 客户端，一般只能靠 RA 拿地址。【文 <https://landscape.whileaway.dev/zh/reference/ipv6/docker-ipv6.html>】

### 2.5 平台前提与两个易踩的坑

- 内核 ≥ 6.9 且启用 BTF/BPF，需要 root；标准发行版建议 ≥ 2 GiB 内存，极简系统约 1.5 GiB【文 <https://landscape.whileaway.dev/zh/intro/requirements.html>】。
- **bpffs 必须正确挂载。** 需只读核对实际挂载和 BTF，不能仅凭 systemd/OpenRC 名称推断。挂载、改 fstab/内核是后续试验操作，不是本轮已执行的修复。【文 <https://landscape.whileaway.dev/zh/faq/alpine-bpffs.html>】
- **53 端口独占。** 若本机已有 DNS 服务占用 53（典型是 systemd-resolved），Landscape 的 DNS 服务起不来；官方给出的处置是 `systemctl stop/disable/mask systemd-resolved`，并给出复原命令。【文 <https://landscape.whileaway.dev/zh/faq/dns.html>】

【未测】以上全部：内核/驱动/BTF/bpffs/网卡模式/PD 实际拿到与否，都要在目标机实测。

### 2.6 无线：本机网卡能做 AP，但**不是**多 AP 控制器【源+文+未证】

- Landscape 把无线网卡当普通受管网卡管理，它能**设置**的 `WifiMode` 只有三个值：`Undefined`（默认）/ `Client` / `AP`（`landscape-common/src/config_service/iface/config.rs:91`）。
- **AP 模式 = 拉起外部 `hostapd` 进程**：`landscape/src/wifi/mod.rs:62` 的 `create_wifi_service` 把配置写进 `<home>/hostapd_tmp/<网卡>.conf`（`write_config` 在 `:151`），再 `Command::new("hostapd")`（`:96`），并监控子进程退出（`:115`）；另有按网卡的 hostapd watchdog 线程（`landscape-common/src/concurrency.rs:44`）。配置本体是**一整段原始 hostapd conf 文本**：`WifiServiceConfig { iface_name, enable, config: String }`，启用时非空且 ≤ 8192 字符，区域要求 LAN 或 Undefined（`landscape-common/src/lan_service/ap/config.rs`）。
- **Client（Station）模式 = 用 `iw` 切模式并清掉桥接主设备**（`landscape/src/config_service/iface_service.rs:152`，内部调 `using_iw_change_wifi_mode`；注意 `controller_name` 指的是**桥接主设备**，如 `br_lan`，不是无线控制器）。全树**没有 `wpa_supplicant`**，所以关联与凭据管理不在 Landscape 手里，Client 模式只是把网卡切成 station。
- ⚠️ 别被 `WLANType` 误导：`landscape-common/src/dev/wifi.rs` 列了 14 种类型（Adhoc/Station/Ap/ApVlan/Wds/Monitor/MeshPoint/P2P/Nan…），那是**从 nl80211 读到的硬件类型**，不是 Landscape 的可配置能力；连 mesh 认证都还是 `todo!()`（`landscape/src/netlink/wifi.rs:55`）。
- **官方文档没有独立 WiFi 页面**，只在 `reference/interface-zone` 里写了一行"开启 WiFi AP 模式（将网卡作为无线接入点）"，说明参考页覆盖较简略。
- 【未证】多 AP 统一管理、802.11r/k/v 漫游、信道/射频规划与漫游优化：v0.26.3 全树 grep `802.11[rkv]`、`roaming`、`漫游`、`channel plan`、`射频` 均无实现证据。结论：**"管本机一块无线网卡"支持，"管一张无线网（多 AP 控制器 + 射频优化）"不支持** —— 这两件事不要混为一谈。

## 3. 核心 Flow（分流策略）

### 3.1 语义【文】

Flow 即策略组，设备按 IP 或 MAC 加入。默认 Flow 为 **ID 0**，自定义 Flow 为 **1–255**。每个 Flow 由"入口规则 + 目的规则 + 出口"组成：

- 匹配顺序：优先级数值**小者优先**（0–65535），**同优先级时 DNS 规则压过 IP 规则**，每包最多命中一条目的规则，命中即停。
- 出口只是"没有目的规则选中其它动作"时流量离开的位置；**规则可以把流量送往另一个 Flow 的出口**。
- 三种特殊配置必须分别验收：
  - **仅入口（空出口）**：无规则选中其它 Flow 出口时**丢弃**；命中其它出口时按该出口正常转发。
  - **仅出口（空入口）**：无客户端直接进入，只能被其它 Flow 当作出口目标。
  - **入口出口皆空**：只能作为其它 Flow 转发流量的丢弃目标。

**空默认出口本身不能证明业务不会直连**，防直连结论必须另有客户端侧与外网侧证据。
文档：<https://landscape.whileaway.dev/zh/features/traffic-flow.html>

### 3.2 出口权重与"权重 ≠ 健康切换"【源】

`WeightedFlowTarget { target, weight: u32 }`，默认权重 **1**（`default_flow_target_weight`），且校验"不能全部为 0 权重"；WebUI 文案说明客户端默认按权重走这些出口，**除非** DNS 规则或目的 IP 规则把流量改道到另一个 Flow【源 `landscape-common/src/flow/config.rs`】。

权重只解决"出口怎么分"，不解决"出口挂了怎么办"。健康检测是另一套机制，见下。

### 3.3 WAN 健康检测：存在，但新、且探针仅 IPv4【源+差】

`landscape-common/src/wan_link/` 提供 `health.rs`、`runtime.rs`、`status.rs`：

- `WanLinkHealthState = Unknown(默认) | Healthy | Unhealthy`，是"去抖后的链路存活判定"。
- 默认参数：间隔 **10s**、超时 **1000ms**、失败阈值 **3**、恢复阈值 **3**、可靠性 **1**。
- 注释写明（`landscape-common/src/wan_link/health.rs`）：探测与评估在 `landscape::wan_link_service`，路由服务在 `landscape-core` 聚合快照，是**消费方（故障切换决策、未来的状态视图）的唯一真源**——"故障切换决策"与"未来的状态视图"并列，属规划口径，不等于已有实现。
- 探针目标类型是 `TargetProbe { target: Ipv4Addr, rtt }`——**只有 IPv4**。

相关迁移名含 2026-10-08，属于近期版本变化；迁移名不等于实机验证时间。相比参考页，固定源码提供了更具体的能力边界【差】。
→ 探测、去抖、聚合与状态翻转事件已经内建。本轮在固定源码中未找到健康事件驱动生产出口自动重选的完整闭环；subscribe_health_events 的调用集中在测试，不能由探测字段推出自动切 WAN，更不能保证长连接无损。反过来也不能说它没有健康检测。探针配置仅 IPv4【源+未测】。

### 3.4 未找到证据的部分【未证】

- **多 WAN 会话保持**：addr_binding 是出口地址绑定，不足以证明会话绑定；数据平面另有路由/连接缓存。本轮未完整验证权重选择与这些缓存的关系，不能仅凭关键词检索断言没有会话绑定，也不能保证切换后长连接无损。
- **应用层 DPI**：全树未找到应用识别；匹配维度只有 IP / MAC / 域名 / 目的 IP / 协议 / 端口。

检索范围：v0.26.3 全树 `git grep` 关键词 `session_bind`、`dpi`、`app_id`、`failover`（`failover` 仅 1 处命中，即 §3.3 的注释）。

### 3.5 标题陷阱：英文页叫 "Traffic Shaping"，但**不是**带宽整形【文+源+差】

- 英文页 H1 是 `# Traffic Shaping`，中文页 H1 是 `# 分流控制`，同一页面。
- 英文正文的第一句就自我定义："Traffic Shaping groups clients into Flows and applies destination-based DNS, routing, and egress policies to their traffic."——是**分组与策略**，不是限速。
- 源码侧：全树未找到 `cake`、`htb`、`fq_codel`、`bandwidth`、`shaping` 的限速实现；`qos` 字段（`Option<u32>`）只出现在配置/运行时声明（`landscape-common/src/flow/config.rs`、`flow/runtime.rs`）与数据库透传（`landscape-database/src/flow_rule/repository.rs:294`、`:299`、`:303` 只是原样搬运），**没有任何数据面消费点**【源】。
- 另有 UI/后端不一致：`vlan_id` 在 Rust 里被注释掉（`// pub vlan_id: Option<u32>,`），而 WebUI 仍在渲染 `rule.vlan_id ?? "N/A"`【源+差】。

**结论：本轮未找到完整带宽整形执行路径，不能依据 Flow 名称或 qos 字段宣称支持 CAKE/SQM、按设备限速或拥塞优先级。**

## 4. DNS

### 4.1 每流独立【文】

每个 Flow 拥有独立的 Hickory DNS 服务器，独立缓存、独立上游、独立规则，"无跨流泄漏"；DNS 应答填充**对应流**的 eBPF map。
顺带一个重要后果：**同一域名在不同 Flow 里可能有不同解析结果**，排障必须按流看。

### 4.2 上游类型【源】

`DnsUpstreamMode` 四个变体：`Plaintext`（默认，传统 UDP/TCP 无加密）、`Tls { domain }`（DoT）、`Https { domain, http_endpoint }`（DoH）、`Quic { domain }`（DoQ）【源 `landscape-common/src/dns/upstream.rs`】。

注意区分两件事：这是 **Landscape 作为客户端去用**哪些上游；Landscape 同时还能**对外提供** DoH 服务，监听端口默认 **6053**（`DEFAULT_DNS_DOH_LISTEN_PORT`），LAN 后缀默认 `lan`【源 `landscape-common/src/lib.rs`】。

### 4.3 缓存与 TTL 默认值【源】

| 项 | 默认值 |
| --- | --- |
| DNS 缓存容量 | 4096 |
| 缓存存储 TTL 设置 | 86400s（24 小时），不代表所有 DNS 记录统一有效 24 小时 |
| 否定缓存 TTL | 120s |

CacheHandle::insert 还会读取应答记录的最小 TTL，无记录时使用否定缓存 TTL，TTL 为 0 不入缓存。须区分缓存存储设置、记录 TTL 与 Flow map 生命周期；上游参考页较简略，细节以固定源码为准【源+差】。

### 4.4 域名→IP、地理库、重写与拦截

- **Geo 库**：支持 URL 每日定期更新、手工录入、AdGuard 规则转换；支持 DAT 与 TXT 格式；可强制更新【文 <https://landscape.whileaway.dev/zh/reference/domain-ip-collection.html>】。配置结构上对应 `geo_ips` / `geo_sites`【源 `landscape-common/src/config/init.rs`】。
- **重写 / 广告拦截**：重定向规则**没有具体结果时即为拦截**；"禁止元数据"开关决定是只拦 A/AAAA，还是把命中该规则的 NS/SOA/TXT/MX/CAA 查询一起拦【文 <https://landscape.whileaway.dev/zh/reference/redirect.html>】。

### 4.5 边界与未证

- **CDN / DoH / ECH / 共享 IP**：DNS→IP 分流本身不能完整区分同一 IP/端口上的不同网站或应用；自带加密 DNS 可能绕过映射过程。Flow、端口规则和代理层可补充，但不能只靠 DNS map 保证分类正确；TTL 需区分 §4.3 的不同层次。
- **fake-IP / MosDNS**：全树 `mosdns`、`fake_ip` 均 **0 命中**，文档站也没有对应页面【未证】。→ **Landscape 与 MosDNS/fake-IP 的融合没有任何自动保证**，必须由本项目自己设计边界。检索范围：v0.26.3 源码及本轮查阅的官方文档页面。

## 5. 数据平面

- **默认 TC（SKB）模式**；v0.26.3 的 --try-xdp（别名 --txdp）无值时尝试所有接口，指定值接受逗号分隔的数字 ifindex（如 3,5）。当前英文网站有接口名示例，与固定版参数类型不同，不能混用。上游描述 native XDP 不支持时回退 TC，但目标驱动的实际回退仍需实测。【源 landscape-common/src/args.rs；文 <https://landscape.whileaway.dev/features/ebpf-route.html>】
- **绕过 Netfilter**：转发在 Ingress/Egress(qdisc) 层完成，在进入 Netfilter **之前**决定转发目标并 `bpf_redirect()` 到目标网卡；官方 FAQ《与 iptables 的关系》直接写"没有关系"，并说明 DNS/IP 标记在**开启了标记服务的 WAN 网卡 EGRESS** 上生效【文 <https://landscape.whileaway.dev/zh/faq/iptables.html>】。
- **性能证据有环境边界**：官方页提供其他 CPU/网卡环境的截图；本轮未提取图像数值，也未测树莓派。不能把这些结果直接换算为当前设备吞吐保证。
- **map 空间**：`--ebpf_map_space` / `LANDSCAPE_EBPF_MAP_SPACE`，默认 `default`，决定 `/sys/fs/bpf/landscape/<map_space>/` 下的 pin 目录【文】。
- **推断（非事实）**：eBPF 内核化的是**转发路径**；走容器出口的流量被 TProxy 交给容器内用户态程序处理，因此代理加密、协议栈与订阅逻辑仍在用户态，"eBPF 化"不会把代理本身搬进内核。此为原理推断，标注为推断。

【未测】XDP 是否真的挂上、降级与否、64 字节小包表现、驱动兼容性——全部需要实机。

## 6. NAT 与防火墙

### 6.1 默认 NAT 比对称型更严格（"目的锁"）【文】

该策略锁定的是**同一内网 IP、源端口、协议构成的源端点**，而不是限制整台客户端只能访问一个服务器。映射存活期内，该源端点首先联系服务端 B 后：

| 方向 | 结果 |
| --- | --- |
| 客户端 A → 服务端 B | 允许 |
| 服务端 B → 路由 A' | 允许（随后转回 A） |
| 同一源端点 → 另一个服务端 C | 直接丢弃，不创建新映射；不同源端点不据此判断 |
| 另一个服务端 C → 路由 A' | 丢弃 |

上游将限制 PCDN 端点复用列为动机，但这不证明能识别/阻止所有 PCDN，也不是上行限速。需要 Full Cone 时可按静态映射或 DNS/IP 规则显式配置，启用范围和真实行为须单独验收。【文 <https://landscape.whileaway.dev/features/nat.html>】

**这不是带宽限制，也不是传统对称 NAT**——它比对称 NAT 更严格。

### 6.2 静态映射【源】

`config_service/static_nat/`（`config4.rs` / `config6.rs`），配置结构里是 `static_nat_mappings_v4` / `static_nat_mappings_v6`；CLI 生成器也有 `--static-nat WAN_PORT:LAN_PORT` 参数【源】。

### 6.3 防火墙是**黑名单模式**，不是 NGFW【文+源】

- 官方页只有两句："自 0.16.0 版本起，防火墙改为黑名单模式"【文 <https://landscape.whileaway.dev/zh/reference/firewall.html>】。
- 源码结构：`FirewallRuleConfig { id, name, index(优先级), enable, remark, items, mark: FlowMark, update_at }`，`FirewallRuleConfigItem { ip_protocol, local_port, address, ip_prefixlen }`；另有独立的 `firewall_blacklists: Vec<FirewallBlacklistConfig>`【源 `landscape-common/src/wan_service/firewall/config.rs`】。
- 也就是说：规则的形态是"匹配地址/前缀 + 协议 + 本地端口 → 打流量标记"，配合黑名单段使用。

固定源码的黑名单过滤在 WAN 入向检查源 IP、出向检查目的 IP；未命中该黑名单则进入后续路径。这只是该层行为，不能据此推导整机所有入站都允许或都拒绝，NAT、Flow 与主机服务保护仍需分别核对。未发现完整 NGFW/IDS/IPS 或通用区域默认拒绝策略的内建证据。

## 7. 容器与扩展

### 7.1 出口容器要**两个**程序【文】

一是 relay 程序 `redirect_pkg_handler`（来自 Releases），二是 worker 程序——**任意 TProxy 兼容程序**，在本项目里就是 Mihomo。worker 放在镜像的 `/app/server`，由 `/app/server/run.sh` 启动；relay 负责注册到 Unix socket。
文档：<https://landscape.whileaway.dev/zh/features/traffic-flow.html>

### 7.2 挂载、端口与模式【文】

- 注册 socket 目录**必须只读挂载**进容器 `/ld_unix_link/`：私有部署是 `/root/.landscape-router/unix_link/`，lkit 部署是 `/root/.lkit/landscape/data/unix_link/`。
- relay 默认转发端口 **12345**（`LAND_PROXY_SERVER_PORT` 可改），**必须与 worker 的监听端口一致**。
- `-m/--mode` / `LAND_PROXY_HANDLE_MODE`：`tproxy`（默认）/ `route` / `multiple_tproxy`。
- **多网络容器**：容器接多个网络时，出口 Flow 选中的那个网桥必须与实际承载流量的接口一致，模式（单网卡 `tproxy` 还是 `multiple_tproxy`）要与真实网卡数量匹配；官方 overlay 示例反复强调固定 `com.docker.network.bridge.name`，正是为了避免重启后接口名变化导致选错网卡或 LAN 服务起不来【文+判断】。
- 其他参数：`-s/--saddr`（默认 `0.0.0.0`）、`--saddr6`（默认 `::`）、`--sock_path`、`--log-level`。
- 自定义镜像需要在容器内加 `ip rule add fwmark 0x1/0x1 lookup 100` 与 `ip route add local default dev lo table 100`；官方参考镜像用 `privileged: true` 加 `NET_ADMIN/BPF/PERFMON` 与 `--sysctl net.ipv4.conf.lo.accept_local=1`——**这是上游参考配置，不是本项目批准的最小权限**，实际授予权限要逐项记录。

### 7.3 ICMP 的真相【文】

TProxy 只处理 TCP 和 UDP，**relay 默认丢弃进入的 ICMP，所以"经 Flow 出口容器转发的路径" ping 不通是预期默认行为**。
打开 `--enable-icmp-passthrough` 后，报文按 `--icmp-mark-value`（默认 `2`）打标并交给本机协议栈；但**只开这个开关不够**：容器内还要开 IPv4/IPv6 转发，并加上匹配该 mark 的 `MASQUERADE`（官方镜像的 `start.sh` 已含这些行，**默认是注释状态**）。

### 7.4 注册与恢复【源+文】

源码侧有 `landscape-common/src/flow/flow_socket_registrar.rs` 负责 socket 注册；容器重建、veth/ifindex 变化后必须重新注册成功，旧注册要失效【源】。这是融合项目最容易出问题的一环，必须逐项留证。

### 7.5 Podman 边界【文】

Landscape **通过 `docker.sock` 与容器运行时通信**。用 Podman 时官方做法是：启用 `podman.socket`，再建一个开机执行的 systemd 服务把 `/var/run/docker.sock` 链接到 `/run/podman/podman.sock`，并给 `landscape-router.service` 加 `After=` / `Requires=`，否则 Landscape 可能在 socket 就绪前启动。验证命令 `ls -l /var/run/docker.sock`。【文 <https://landscape.whileaway.dev/zh/faq/podman.html>】

**结论**：Docker 对 Landscape 本体是可选的，但**"容器出口"这条集成路线必须有一个 docker.sock 兼容的运行时**。

## 8. VPN / 组网

### 8.1 官方示例以外部容器 + Flow 出口集成为主【源+文】

源码检索：`tailscale`、`zerotier`、`easytier`、`netbird`、`dn42` 在 v0.26.3 全树 **0 命中**【未证内建实现】。
官方文档给出的是一条统一的搭建路径，以 Tailscale/ZeroTier 为例：

1. 使用 `landscape-router/landscape-apps` 仓库编译的镜像启动容器；
2. compose 里**必须固定** `com.docker.network.bridge.name`（如 `tail-br0` / `zero-br0`），否则重启后动态网桥名变化会导致 LAN 服务起不来；
3. 容器所属网桥要开启"LAN 路由转发服务"，否则不生效；
4. 配**静态 NAT**（内网目标端口 = 容器端口，IP = 容器 IP）；
5. **以该容器为出口创建一个 Flow**，再在客户端所属 Flow 的"目标 IP 规则"里把目的网段（例如 `100.64.0.0/10`）指向这个容器出口的 Flow。

文档：<https://landscape.whileaway.dev/zh/overlay/tailscale.html>、<https://landscape.whileaway.dev/zh/overlay/zerotier.html>，另有 easytier / netbird / dn42 与 site-to-site 指南。

**判断（非事实）**：这意味着"组网"不是菜单式一键功能，而是需要自己写 compose、管镜像、配 NAT 与规则的一套手工集成。

### 8.2 未发现内建证据的项【未证】

BGP、OSPF、IPsec、VRRP/Keepalived、HA 状态同步：本轮未发现完整内建管理能力。WireGuard 有设备类型识别，但未发现完整配置管理面；DN42 官方示例使用外部 WireGuard 与 BIRD 2 容器，不能算 Landscape 内建 BGP/VPN 控制器。
按报告口径：只说"未发现内建证据"，不断言"不存在"。检索范围：v0.26.3 源码及本轮查阅的官方文档页面。

## 9. 管理面

### 9.1 UI、REST API 与类型库【文】

"你在 UI 上的所有操作都可以通过 API 完成"；部署后访问 `https://<router>:6443/api/docs` 打开 REST API 文档；另提供 npm 库 `@landscape-router/types` 与浏览器分流追踪插件【文 <https://landscape.whileaway.dev/zh/features/api.html>】。源码侧 OpenAPI/utoipa 命中充分【源】。
监听默认 HTTP 6300、HTTPS 6443；`-e/--export-manager` 允许从 WAN IP 访问管理界面（**默认关闭，开启即扩大暴露面**）；默认口令是 `root/root`，官方要求首次登录立即修改【文】。

**设备管理**：存在设备（enrolled device）概念，配置结构中为 `enrolled_devices`，官方页面 `reference/device-management` 只有 3 行加一张截图，**未文档化具体字段与语义**【文+源+差】。设备层面能做什么，需要在实机 UI 里逐项确认。

### 9.2 配置目录与初始化语义【文+源】

`<HOME>` 默认 `/root/.landscape-router`，可用 `-c` / `LANDSCAPE_CONF_PATH` 更改。目录内：

| 文件/目录 | 作用 |
| --- | --- |
| `landscape.toml` | 进程级配置，**每次启动都读** |
| `landscape_init.toml` | 全量配置（含所有规则），**仅首次启动读一次** |
| `landscape_init.lock` | 普通文件存在时跳过初始化；缺失才尝试读取 init 配置，异常类型会报错 |
| `landscape_api_token` | API JWT，**每次重启重新签发** |
| `landscape_db.sqlite` | SQLite 数据库 |
| `logs/`、`metric/`、`static/`、`unix_link/register.sock` | 日志、指标、前端静态资源、容器注册 socket |

`landscape_init.toml` 的定位是"一份文件重建整台机器"，可通过 API 导出：`GET https://<router>:6443/api/v1/system/config/export` 带 `Authorization: Bearer <token>`【文 <https://landscape.whileaway.dev/zh/configuration/init-config.html>】。

**两个必须写进运维手册的坑**【文 <https://landscape.whileaway.dev/zh/configuration/>】：

- 固定源码启动时调用 dotenvy::dotenv()，且 DATABASE_URL 与 LANDSCAPE_CONF_PATH 是独立设置。不能据此推出 .env 必然覆盖显式 -c；应记录最终 config-dir 和 database URL，防止数据库与配置目录分离到意料之外的位置。
- metric/ 需要空间预算；固定版同时有保留天数、行数、数据库容量等设置，不能仅凭旧说明断言只按天数清理，也不能把容量配置当成所有时刻严格不超限的实测保证。

### 9.3 指标范围与保留默认值【源】

| 项 | 默认值 |
| --- | --- |
| 连接指标模式 | Persistent |
| 连接 1m / 1h / 1d 保留 | 1 天 / 7 天 / 30 天 |
| 连接汇总上限 | 500,000 行 |
| 连接库上限 | 512 MiB |
| DNS 指标保留 / 1m 保留 | 7 天 / 30 天 |
| DNS 指标库上限 | 1 GiB |
| 写批大小 / flush 间隔 | 20,000 / 30s |

监控内容：活跃连接、按源/目的 IP 的流速与 PPS、历史流量统计【文 <https://landscape.whileaway.dev/zh/monitoring/connection-info.html>】；DNS 指标含总查询量、缓存命中率、拦截率【文 <https://landscape.whileaway.dev/zh/monitoring/dns-metrics.html>】。

### 9.4 未发现内建 Prometheus/OpenMetrics 或 syslog 导出【未证】

本轮对固定源码检索这些关键词未发现对应导出实现；这不等于没有连接/DNS 指标查询 API，也不排除系统日志由 journald 等外部设施采集。
→ **不要把"接 Prometheus"或"转发 syslog"写进方案**，除非自己写采集适配。检索范围：v0.26.3 源码及本轮查阅的官方文档页面。

### 9.5 DDNS、证书与反代【文】

- **DDNS**：把 WAN 网卡或内网设备获得的动态公网 IPv4/IPv6 更新到指定运营商的 DNS 记录；LAN 设备可选使用哪条 WAN 的 PD 前缀拼接。<https://landscape.whileaway.dev/zh/reference/ddns.html>
- **证书**：支持 ACME、自签与上传；固定源码 CertService::new 启动自动续期检查，check_auto_renewals 按 auto_renew、有效状态与到期阈值重新入队，还会恢复启动前处于 Processing 的 ACME 任务。自动续期机制已确认，真实 DNS 验证/授权/证书更新仍未测。<https://landscape.whileaway.dev/zh/reference/certificates.html>
- **HTTP 网关/反代**：固定源码支持 Host/路径分组、SNI 代理、多 upstream target、RoundRobin/Random/Consistent 负载均衡、可选上游健康检查和请求头策略。这是 HTTP 网关健康检查，不是 WAN 故障切换；业务兼容性仍需实测。<https://landscape.whileaway.dev/zh/reference/proxy.html>
- **系统级**：`[time]` 时间同步、`[gateway]` 反代默认关闭、日志开关与文件数上限、WiFi AP 模式（`lan_service/ap` 与 `wifi_configs`）【文+源】。

### 9.6 服务依赖【文】

官方 systemd 单元示例：`Restart=always`、`User=root`、`LimitMEMLOCK=infinity`。另外两个依赖必须显式处理：Podman 场景需 `After=`/`Requires=`（§7.5）；Alpine 场景需先挂 bpffs（§2.5）。

### 9.7 升级【文-上游声称】

上游口径是"替换二进制即升级，配置自动迁移"，且声称"降级同样支持"。文档站的升级步骤只有：下载新的可执行文件与静态资源 → 停服务 → 替换 → 重启，并提示"启动失败先看服务日志再考虑重启主机"。

### 9.8 降级的真实边界【源+文+差】——本节最重要

- **现代命令已官方文档化**：`landscape-webserver db rollback`（别名 `db rb`），"交互式回滚数据库到某个发布边界"【文 <https://landscape.whileaway.dev/zh/configuration/>，子命令小节】。
- **旧的隐藏参数**：`db -r/--rollback` 与 `-t/--times` 在源码里是 `hide = true`，官方文档只在 `advanced/settings-export` 的一个**被注释掉的**"系统降级"小节里出现过【差】。
- **源码里只有两个发布边界**：`RELEASE_BOUNDARIES` 仅含 `0.25.1` 与 `0.26.0` 两项，且注释明确写：**"回滚只保证上一个版本"**，更旧的 schema 状态走"手工逐步回滚路径"；`v0.26.3` 自身没有登记边界【源 `landscape-database/src/rollback.rs`】。流程是交互式：列目标 → 选目标 → 打印计划预览 → 确认（否则打印 `Rollback cancelled.`）→ 执行并打印 `Rollback complete. Current schema target is now …`。
- **配置导出的硬限制**：官方警告"导出的配置只能用于**相同**版本的恢复"；若导出后要升级，需**先在导出时的版本启动、完成 init 数据恢复，再启动最新版本**【文 <https://landscape.whileaway.dev/zh/advanced/settings-export.html>】。

**结论：降级不是任意版本组合的保证，更没有断电一致性保证。** 上游 README 的"降级同样支持"只能理解为"存在这样一个机制"，不能作为任何具体版本组合的承诺。备份、原盘保留与备用盘启动必须由本项目自己负责。

### 9.9 它不是什么【未证】

本机 Wi-Fi 支持外部 hostapd AP 管理与接口模式切换；Client/Station 模式切换不能等同于完整 Wi-Fi 关联/凭据管理器。本轮未发现 UniFi 式多 AP 统一管理、跨 AP 自动射频/漫游编排或完整认证门户，不能把本机 AP 能力与这些混为一谈。

## 10. 与当前项目的映射

前提：本项目现有形态是 RPi5 上的 Mihomo 网关（订阅、规则、评分、回滚由本项目与 Mihomo 负责），Landscape 承担的是 L3/L4 转发与分流编排。

| 现有组件 | 判定 | 依据 | 优先验证项 |
| --- | --- | --- | --- |
| dnsmasq（LAN DNS/DHCP） | **可替代，需迁移** | 自带 DHCPv4/每流 DNS，不能因无 fake-IP 就否定其分流解析能力 | 地址池/租约/主机名、53 端口、resolver 与恢复交接 |
| MosDNS | **按需保留或替代** | 依据国内解析、未知域名校验、缓存/故障回退等实际需求比较，不因源码无 mosdns 字样强制保留 | DNS 单一入口、映射一致、避免双缓存双写入 |
| nftables | **部分可替代** | 转发绕过 Netfilter，DNS/IP 标记在 WAN egress 生效；官方 FAQ 直接写"没有关系" | 容器出口路径仍需要容器内 MASQUERADE/转发；现有 nft 规则逐条归属，不能整段删 |
| 主机 TUN（Mihomo） | **目标 TProxy 模式中替换/停用** | Landscape 导流到容器 TProxy；原 native 模式可作为互斥部署方案保留 | 关闭重复 auto-route/auto-redirect/DNS 劫持，核对节点出站无环路 |
| 生产评分 / 订阅更新 | **保留逻辑，必须适配验收** | provider、业务池与节点能力仍由 Mihomo/本项目负责 | 容器路径/API/鉴权、锁/timer、热加载/回滚、评分隔离门禁全部重验 |
| 回滚 | **需适配** | `setup.py --rollback` 只还原原安装器的网络配置事务；Landscape 侧是 `db rollback`（只保证上一版本）+ 相同版本配置导出 | 自建备份/还原编排，保留原盘与备用盘启动演练 |

### 10.1 重点新风险（按严重度）

1. **目的锁 NAT 对 UDP 应用**：一个端口默认只属于首个服务端，语音/游戏/P2P/QUIC 可能受影响；需要按域名/IP 放通或开 NAT1。
2. **IPv6 绕行**：LANv6/RA 打开后，客户端可能直接走 IPv6 出网，绕过 IPv4 侧的分流与 NAT 策略；PD 前缀长度与"首选时长"（默认 300s）会影响切换表现。
3. **黑名单不等于默认拒绝**：防火墙是黑名单模式，不能承诺"默认安全"。
4. **权重 ≠ 健康切换**：探测、去抖、聚合与事件已内建；本轮未找到生产出口自动重选的完整闭环，不能承诺自动切换或无损会话；探针仅 IPv4。
5. **VLAN 创建边界**：官方给系统侧手工子接口方案，未确证完整 UI 创建入口；区分创建管理与数据面支持。
6. **QoS / HA 证据不足**：无带宽整形实现；无 VRRP/热备。
7. **每流独立 DNS 缓存**：同一域名在不同流可能解析不同，排障与验收都要按流记录。
8. **API token 每次重启重签**：自动化必须重读 token，不能缓存。
9. **磁盘预算**：`metric/` 持续增长，连接库 512 MiB、DNS 库 1 GiB 上限。
10. **配置与数据库路径分离**：环境中的独立 DATABASE_URL 可改变数据库位置，需记录实际生效路径。

### 10.2 与验收清单的关系

本报告是**能力边界参考**，不替代验收：[Landscape + Mihomo 融合验收清单](landscape-acceptance.zh-CN.md) 负责逐项执行与留证，其全部运行验收项仍保持**待测**。
本报告为只读研究产物，未修改该清单，也未在其中新增任何结论。

## 11. 结论

**它是什么。** 标准 Linux 上的 DNS 驱动 eBPF 分流路由器：用户态 DNS 平面按 Flow 维护解析/规则并更新内核 map，符合条件的普通转发在 TC/XDP 中导向。容器代理、DNS、HTTP 网关仍有用户态处理，不能把整个系统称为没有用户态数据路径。

**它能做什么（本报告已证实）。**

- 按 IP/MAC 把客户端编入 Flow，按域名/目的 IP/优先级分流，出口可以是 WAN 或容器。
- 每流独立 DNS（UDP/DoH/DoT/DoQ 上游）、独立缓存与规则，Geo 库支持 DAT/TXT 与 AdGuard 转换。
- 静态 NAT 映射、比对称型更严格的目的锁 NAT、按域名/IP 放通 NAT1。
- 把流量导进任意 TProxy 兼容容器（本项目即 Mihomo），支持 Podman（经由 docker.sock 兼容链接）。
- REST API/OpenAPI、DDNS、ACME 证书与自动续期、HTTP 网关上游负载均衡/健康检查、连接与 DNS 指标。
- 本机 hostapd AP 管理与无线接口模式切换（源码确认，实机未测）。
- WAN 链路健康状态评估（去抖，IPv4 探针）。

**它做不到、或不能保证什么。**

- 未找到完整 CAKE/SQM/带宽整形或应用 DPI 实现；Flow 名称与 qos 字段不能作为支持证据。
- 黑名单过滤不是完整 NGFW/IDS/IPS；未命中黑名单不代表整机 WAN 入站保护已证明。
- 不内建 fake-IP/MosDNS 融合；不内建 Tailscale/ZeroTier/EasyTier/NetBird/DN42 控制器（都是容器 + Flow 出口的手工集成）。
- 不内建 BGP/OSPF/WireGuard/IPsec/VRRP/HA 热备（未发现证据）。
- 未发现内建 Prometheus/OpenMetrics 或 syslog 导出，仍可评估 API/外部采集适配。
- 降级只保证上一版本，配置导出只能同版本恢复；无断电一致性保证。
- 性能、稳定性、故障行为与所有实机结论：**未测**。

**对当前项目的价值。** 它适合承担分流编排、每流 DNS 与出口管理，Mihomo 保留代理协议、订阅和业务节点选择。网络交接、NAT、IPv6、容器注册、鉴权和恢复边界需重新验收；新发现的具体风险还需补充用例，不能把一般条目当作已测。本轮未修改或执行验收清单。

## 12. 关键源码直链

以下固定到本文发行版提交，便于复核；源码存在不代表目标机已通过验收。

- [发行说明](https://github.com/ThisSeanZhang/landscape/releases/tag/v0.26.3)
- [WAN健康配置/IPv4 ICMP](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-common/src/wan_link/config.rs)
- [健康探测执行器](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape/src/wan_link_service/health_check.rs)
- [健康状态聚合/事件](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-core/src/route/health.rs)
- [Flow及qos字段](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-common/src/flow/config.rs)
- [DNS记录TTL/缓存/map更新](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-dns/src/server/cache.rs)
- [TC黑名单入向/出向](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-ebpf/src/bpf/tc_chain/tc_firewall.bpf.c)
- [本机hostapd AP管理](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape/src/wifi/mod.rs)
- [ACME自动续期调度/执行](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape/src/cert/order_service.rs)
- [HTTP网关匹配/负载均衡/健康配置](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-common/src/sys_service/gateway/config.rs)
- [初始化锁实际判断](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape/src/boot/mod.rs)
- [启动参数/ifindex/环境读取](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-common/src/args.rs)
- [数据库回退边界](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-database/src/rollback.rs)
