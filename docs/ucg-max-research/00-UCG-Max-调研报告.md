# UCG Max 调研报告：UniFi Cloud Gateway Max（UCG-Max）

> 版本：v1.4（交叉核对修订 + 实机对接验证 + 本地 key 直连复核）｜ 资料访问日期：2026-10-31 ｜ 调研对象：Ubiquiti Inc. 的 UniFi Cloud Gateway Max（型号 `UCG-Max`）
> 资料口径：仅使用官方站点（ui.com / techspecs.ui.com / store.ui.com / developer.ui.com）与官方 OpenAPI 规范；所有 URL 均于 2026-10-31 实测可访问。

## 摘要

- **对象**：用户背景中的「UCG Max / unify 有线网关」对应 Ubiquiti 的 **UniFi Cloud Gateway Max**，型号 `UCG-Max`。它是运行 UniFi OS 的桌面式 Cloud Gateway：5×2.5GbE 纯有线、可选 NVMe 存储、可原生运行 Network / Protect / Access / Talk / Connect 五个应用。官方拼写是 **UniFi**（母公司 Ubiquiti Inc.），不是「unify」。
- **原理**：硬件是「5 口 2.5GbE 交换 + 四核 A53/3GB 的计算平台 + 可选 NVMe」，软件是「UniFi OS 宿主 + UniFi 应用」。转发面承担 WAN 选路、路由/NAT、防火墙与 ACL、IDS/IPS、DPI、VLAN 分段与 VPN/SD-WAN；管理面分本地直连与云端两条路径，本地走 `https://{consoleIP}/proxy/network/integration`，云端走 Site Manager 的 Cloud Connector 代理。
- **API**：官方在 developer.ui.com 公开两套 REST API，均用 API Key（`X-API-Key` / `X-API-KEY`）认证：**Site Manager API v1.0.0**（`https://api.ui.com/v1/...`，账号级盘点与指标，10 路径 / 14 操作）与 **Network API v11.0.97**（本地或云代理，站点级配置与设备动作，44 路径 / 73 操作）。分页、错误体、限流两套各不相同；**云侧 key 与 console 本机签发的本地 key 不可互换**（两者均已实测）。
- **限制**：Site Manager v1 限 10,000 请求/分钟、EA 100 请求/分钟；经 Cloud Connector 访问时每 console 限 100 请求/分钟、单请求 25 秒、响应体 10 MB。Network 应用自身官方未公布限流数值。
- **结论**：可直接按本报告第 3 章的 curl 示例发起第一次调用；但官方**没有 SDK**，只提供 OpenAPI 规范、Postman Collection 与 Ansible Collection。

## 调研方法与分工

| 角色 | 承担工作 | 产出 |
|---|---|---|
| Lead（主代理） | 检索与抓取一手官方资料；建立 OpenAPI 副本与端点清单；逐条核验草稿数字与引用；合并成稿 | `techspecs-full.md`、`network-openapi-11.0.97.json`、`network-endpoints.md`、`sm-endpoints.md`、本报告 |
| locator（子代理） | 第 1 章：候选型号区分、SKU 与区域版本、官方命名 | `01-定位.md` |
| mechanism（子代理） | 第 2 章：硬件角色、软件栈、数据流与控制流、存储分支、官方明确/归纳/未公开边界 | `02-原理.md` |
| api-writer（子代理） | 第 3 章：认证、双入口、端点表、分页与 filter、错误体、可复制示例、限制 | `03-API.md` |
| checker（子代理） | 独立交叉核对三章，重点查事实错误与「把推断写成官方」 | `04-核对.md` |

**核验方式**：规格类结论用 techspecs 页面的 `__NEXT_DATA__` JSON 逐字段比对；商店价格/库存用 store 页面 JSON（价格单位为分）；API 类结论用两份 OpenAPI 规范脚本统计（路径数、操作数、响应码、分页参数默认值与上限、可过滤属性表），并抽查 `llms.txt`、gettingstarted、filtering、error-handling、responseformat、versioncontrol 等文档页原文。


## 一、对象定位

### 1.1 候选对象与区分依据

官方 techspecs 把 Cloud Gateways 划为 5 个子类：enterprise-scale（EF-Core、EFG）、large-scale（UDM-Beast、UDM-Pro-Max、UDM-SE、UDM-Pro）、compact（UCG-Max、UCG-Fiber、UCG-Ultra）、wifi-integrated（UCG-Industrial、UDR-5G-Max、UDR7、UX7、UDW、UDR、UX）与 whole-home-wifi（UX7）[2]。同子类产品功能相近，跨子类的差别集中在吞吐、端口速率、存储介质与是否集成无线。与「UCG Max」近名的候选如下：

| 产品名 | 型号 | 子类 | IDS/IPS | 端口 | 存储 | 形态 |
|---|---|---|---|---|---|---|
| Cloud Gateway Max | UCG-Max | compact | 2.3 Gbps | 5×2.5GbE | 可选 NVMe ≤2 TB | 桌面 |
| Cloud Gateway Ultra | UCG-Ultra | compact | 1 Gbps | 1×2.5GbE + 4×1GbE | 板载 16 GB | 桌面 |
| Cloud Gateway Fiber | UCG-Fiber | compact | 5 Gbps | 4×2.5GbE + 1×10GbE + 2×10G SFP+ | 可选 NVMe ≤2 TB | 桌面，30W PoE |
| Dream Machine Pro | UDM-Pro | large-scale | 3.5 Gbps | 2×10G SFP+ + 9×1GbE | 1×3.5" HDD | 1U 机架 |
| Dream Machine SE | UDM-SE | large-scale | 3.5 Gbps | 2×10G SFP+ + 8×1GbE + 1×2.5GbE | 1×3.5" HDD + 128 GB SSD | 1U 机架，PoE |
| Dream Machine Pro Max | UDM-Pro-Max | large-scale | 5 Gbps | 2×10G SFP+ + 8×1GbE + 1×2.5GbE | 2×3.5" HDD + 128 GB SSD | 1U 机架 |
| Dream Machine Beast | UDM-Beast | large-scale | 25 Gbps | 2×25G SFP28 + 8×10GbE + 2×10G SFP+ + 2×1GbE | 2×3.5" HDD + 128 GB SSD | 1U 机架 |
| Dream Router 7 | UDR7 | wifi-integrated | 2.3 Gbps | 4×2.5GbE（1 PoE）+ 1×10G SFP+ | 64 GB microSD | 桌面，WiFi 7 |

三条分界线决定了本机的唯一性：

**一是 compact 三款的分界在吞吐与端口速率。** Ultra 为 1 Gbps、端口上限 2.5GbE，且 Application Suite 只列 Network[3]；Fiber 为 5 Gbps，带 10G SFP+ 与 30W PoE[4]；Max 居中——2.3 Gbps、5 个 2.5GbE 口，无 10G、无 PoE[1]。若需求是「多个 2.5G 口 + 不需要 10G 上行」，三者中只有 Max 符合。

**二是 Dream Machine 系列属于机架形态的另一条产品线。** UDM-Pro、UDM-SE、UDM-Pro-Max、UDM-Beast 均为 1U 机架、带 3.5" HDD 盘位，并标注 USP-RPS 冗余直流输入[5][6][7][8]；带机量从 100+ 设备升到 750+，吞吐从 3.5 Gbps 升到 25 Gbps。它们与 UCG-Max 的差别不只是性能，而是「机架 + HDD + 冗余电源」的机房形态。

**三是带无线的型号被单独归类。** Dream Router 7 明确标注整合 WiFi 7、microSD 存储与 4 个 2.5GbE 口（含 1 个 PoE）[9]，被放在 wifi-integrated 子类，不与 UCG 同列[2]。说明官方把「有无无线射频」作为一级分类维度，UCG-Max 属于不带射频的纯有线网关。

因此，同时满足「纯有线、5×2.5GbE、可选 NVR 存储、桌面形态」的只有 UCG-Max，即本次调研对象。

需要注意商店条目的重合：美国店同时列出 "Cloud Gateway Max"（$279.00）与 "Cloud Gateway Max NS"（$199.00）两个商品[10][11]。官方为两者各建了一份 techspecs 页（ucg-max 与 ucg-max-ns），两页规格除「认证清单」一项外完全相同（UCG-Max 的 Certifications 带 Anatel 编号 09004-24-08356，NS 页只写 Anatel 无编号），而各自 variants 只对应一条 SKU（UCG-Max / UCG-Max-NS）[1][17]。可见 "NS" 条目对应的是无盘存储档位，不是另一款设备；同系列里真正需要区分的是子类，而非商品名后缀。

### 1.2 存储版本与区域 SKU

techspecs 的 NVR Storage 项写 "Selectable NVMe SSD up to 2 TB"，note 原文为："No Storage option does not include NVMe SSD. Third-party SSD installation requires a tray accessory. 512 GB, 1/2 TB option includes tray accessory and NVMe SSD."[1] 即官方承认 4 个档位：无盘、512 GB、1 TB、2 TB；其中 512 GB 与 1/2 TB 档均随附托盘与 NVMe 盘，无盘版自行加装第三方盘需另购托盘。

商店在 2026-10-31 的实际售卖状态：美国店只有两个网关 SKU——UCG-Max（预装 512 GB，$279.00，在售）与 UCG-Max-NS（No Storage，$199.00，售罄，页面标注 2026-11-06 补货）[10][11]；欧店为 UCG-Max-EU / UCG-Max-UK（€250.00）与 UCG-Max-NS-EU / -NS-UK（€179.00）[12]；英店为 UCG-Max-UK（£220.00）与 UCG-Max-NS-UK（£159.00）[13]。区域差异只表现为 SKU 后缀（US/EU/UK）与结算货币、价格，官方规格页未按区域列出功能差异（未找到）。

1 TB / 2 TB 不是独立网关 SKU：techspecs 也没有 1 TB、2 TB 的规格页（ucg-max-1tb、ucg-max-2tb 均重定向至 techspecs 首页）；商店把它们作为 NVMe 配件 UACC-SSD-1TB（$390）与 UACC-SSD-2TB（$730）挂在 UCG-Max 的关联商品中，两者当前均售罄[10]；自行加装所需的 M.2 托盘 UACC-SSD-Tray 售价 $19.00、有货[14]。故本次以 UCG-Max（512 GB 预装）为主体，No Storage 版视为同型号的存储档位差异，而非另一款产品。

与同门 Ultra 对照，更能说明存储档位的意义：Ultra 的应用套件只勾选 Network，且只有板载 16 GB 存储[3]；Max 勾选 Network、Protect、Access、Talk、Connect 五项[1]，并保留 NVMe 位承担录像。也就是说，"可选 NVR 存储"并非单纯多一个插槽，它与 Protect 能力的开放是同时给出的——这也是 Max 与 Ultra 在用途上的实质区别，而不只是吞吐从 1 Gbps 提高到 2.3 Gbps。

### 1.3 确定结论

**品牌与厂商。** techspecs 的 productLine 字段为 "Unifi"[1]，商店把 manufacturer 标为 "Ubiquiti"[10]，ui.com 的组织结构化数据与页脚均署名 "Ubiquiti Inc."[15]。即产品线名为 UniFi，厂商为 Ubiquiti Inc.；本次抓取的官方页面中检索不到 "unify" 这一拼写，故判断为用户笔误。

**产品名与型号。** 官方产品名 Cloud Gateway Max，型号与 URL slug 均为 UCG-Max，美国店默认 SKU 为 UCG-Max-US[10]（techspecs 的 variants 只给 `UCG-Max`，`-US` 后缀只出现在商店 `defaultSku`）。

**与「unify 有线网关」的三点差异。** ①拼写与归属：UniFi ≠ unify，厂商 Ubiquiti Inc.[15]；②定位：官方用 "Cloud Gateway"，shortDescription 为 "Compact 2.5G Cloud Gateway with 30+ UniFi device / 300+ client support, 2.3 Gbps IPS routing, and selectable NVR storage."[1]——它运行 UniFi OS（吞吐注记写明 "With UniFi OS 4.1 or later."），可加 NVMe 盘承担 NVR，并支持 Protect 等 5 个应用；③无线：官方规格未列任何射频项、端口仅 5×2.5GbE、管理仅 Ethernet 与 Bluetooth[1]，且带无线的型号另立 wifi-integrated 子类[2]，据此归纳为纯有线；注意这是归纳，官方并未直接写「无 Wi-Fi」，第 2 章同一判断也按此口径标注。

为便于后续章节统一措辞，术语对照如下：

| 用户表述 | 官方表述 | 依据 |
|---|---|---|
| unify | UniFi，产品线名 | [1][15] |
| 品牌 | Ubiquiti Inc.，厂商名 | [15] |
| 有线网关 | Cloud Gateway，类别名，运行 UniFi OS | [1] |
| UCG Max | Cloud Gateway Max，型号 UCG-Max | [1] |
| NS | No Storage，无盘档位 | [1][11] |

### 1.4 关键规格

| 项目 | 官方值 | 来源 |
|---|---|---|
| 尺寸 / 重量 | 141.8×127.6×30 mm；无 SSD 460 g，带 SSD 519 g | [1] |
| 端口 | 5×2.5GbE RJ45（2.5G/1G/100M/10M）；默认 WAN 1×2.5GbE，最大 WAN 数 4 | [1] |
| IDS/IPS 吞吐 | 2.3 Gbps（需 UniFi OS 4.1 或更新） | [1] |
| CPU | 四核 ARM Cortex-A53 @ 1.5 GHz | [1] |
| 内存 | 3 GB | [1] |
| 存储选项 | 可选 NVMe SSD 至 2 TB（无盘 / 512 GB / 1 TB / 2 TB） | [1][10] |
| 供电 / 功耗 | USB-C 5V DC/5A（含适配器）；最大 16.1 W；输入 100–240V AC | [1] |
| 散热 | 55 BTU/hr | [1] |
| 工作温度 | -10 至 40 ℃；湿度 5–95% 非凝结 | [1] |
| 认证 | CE、FCC、IC、Anatel 09004-24-08356、SRRC；NDAA 合规 | [1] |
| 管理方式 | 以太网 + 蓝牙；0.96" LCM 状态屏；1 个恢复出厂按钮 | [1] |
| 应用套件 | Network/Protect/Access/Talk/Connect 全支持；管理 30+ 设备、300+ 客户端、Access Hub 50，可带 15 路 HD / 8 路 2K / 5 路 4K 相机 | [1] |

### 1.5 网络、安全与管理能力

官方把下列能力列为该机型的标准项[1]，是理解「Cloud Gateway」定位的关键：

- **安全**：状态防火墙、应用感知七层防火墙、DPI 与流量识别、基于区域/域名/应用的 Zone-Based 高级过滤、内容过滤、IPS/IDS（签名 55,000+，官方标注 with CyberSecure）、广告拦截、按 VLAN/子网流量分段。
- **VPN 与 SD-WAN**：免许可 SD-WAN；站点互联支持 Site Magic、IPsec、OpenVPN；服务端支持 Identity Endpoint 一键 VPN、Teleport、WireGuard、OpenVPN、L2TP；客户端支持 OpenVPN 与 WireGuard。
- **路由与网络**：多 WAN 负载均衡、OSPF/BGP 动态路由、高级 QoS、mDNS、高级 NAT（SNAT/DNAT/Masquerade/NAT Pooling/1-to-1 NAT）、内置 RADIUS 与 RadSec、UniFi LTE Backup 故障切换、上网质量与中断报告、MAC 表 2,000 条、基于策略的 WAN/VPN 路由、自定义 DHCP、IPv6 ISP、IGMP 代理、虚拟网络覆盖。
- **管理**：以太网 + 蓝牙，0.96" LCM 状态屏，1 个恢复出厂按钮；移动端要求 UniFi iOS/Android 10.17.2 及以上[1]。

端口角色上，官方给出的默认 WAN 为 1 个 2.5GbE、最大 WAN 数为 4[1]，即 5 个端口按配置在 WAN 与 LAN 之间分配。供电方面，整机由 USB-C 5V/5A 适配器供电，硬件清单中没有 PoE 条目；作为对照，同门 Fiber 明确列出 30 W PoE 预算[4]。因此 UCG-Max 不能为下游 AP 或相机供电，接入侧仍需另配 PoE 交换机——这一点容易被"网关"二字掩盖，实际它是路由与录像合一的小型主机，而非带供电的交换平台。

### 1.6 检索范围与未核实项

- **发布日期**：未找到。检索范围为 techspecs 产品页、US/EU/UK 三个商店商品页、ui.com 首页与安装指南入口（https://ui.com/qig/ucg-max 为前端应用外壳，不含可引用的日期文本）、developer.ui.com/llms.txt（面向 API 的索引，不含产品发布信息）。
- **区域功能差异**：未找到。三个区域商店的规格描述一致，差异仅在 SKU 后缀与货币、价格。
- **1 TB / 2 TB 是否曾作为网关 SKU 售卖**：未找到证据。techspecs 与商店都没有对应独立页面（路径重定向或不存在），当前仅见 UACC-SSD-1TB/2TB 配件。
- **"NS" 缩写**：官方未展开说明，仅可据 techspecs 的 "No Storage option does not include NVMe SSD" 对应为无盘版[1][11]。
- **"unify" 拼写**：在 ui.com 首页、7 份 techspecs 页与 8 份商店页 HTML 中检索 "unify"，命中 0 次；官方一律写作 UniFi，故判定为用户笔误。
- **未采用 help.ui.com**：该域名存在 Cloudflare 挑战页，正文无法核验，故全文不引用。

### 1.7 官方来源（访问日期 2026-10-31）

1. Techspecs：UCG-Max — https://techspecs.ui.com/unifi/cloud-gateways/ucg-max
2. Techspecs：Cloud Gateways 分类与子类 — https://techspecs.ui.com/unifi/cloud-gateways
3. Techspecs：UCG-Ultra — https://techspecs.ui.com/unifi/cloud-gateways/ucg-ultra
4. Techspecs：UCG-Fiber — https://techspecs.ui.com/unifi/cloud-gateways/ucg-fiber
5. Techspecs：UDM-Pro — https://techspecs.ui.com/unifi/cloud-gateways/udm-pro
6. Techspecs：UDM-SE — https://techspecs.ui.com/unifi/cloud-gateways/udm-se
7. Techspecs：UDM-Pro-Max — https://techspecs.ui.com/unifi/cloud-gateways/udm-pro-max
8. Techspecs：UDM-Beast — https://techspecs.ui.com/unifi/cloud-gateways/udm-beast
9. Techspecs：UDR7 — https://techspecs.ui.com/unifi/cloud-gateways/udr7
10. Store（US）：Cloud Gateway Max — https://store.ui.com/us/en/products/ucg-max
11. Store（US）：Cloud Gateway Max NS — https://store.ui.com/us/en/products/ucg-max-ns
12. Store（EU）：Cloud Gateway Max — https://store.ui.com/eu/en/products/ucg-max
13. Store（UK）：Cloud Gateway Max — https://store.ui.com/uk/en/products/ucg-max
14. Store（US）：UACC-SSD-Tray — https://store.ui.com/us/en/products/uacc-ssd-tray
15. ui.com 首页（组织信息与页脚） — https://ui.com/
16. 安装指南入口 — https://ui.com/qig/ucg-max
17. Techspecs：UCG-Max-NS — https://techspecs.ui.com/unifi/cloud-gateways/ucg-max-ns

## 二、工作原理

UCG-Max（Cloud Gateway Max）是 Ubiquiti 的桌面式 Cloud Gateway，官方定位是「Compact 2.5G Cloud Gateway with 30+ UniFi device / 300+ client support, 2.3 Gbps IPS routing, and selectable NVR storage」[2]。它把三件事装进同一个 141.8 × 127.6 × 30 mm 的机身：一台运行 UniFi OS 的主机、一套原生运行的 UniFi 应用、以及可选的本机 NVMe 录像存储[1][3]。理解它要分三层：硬件载体（转发面与存储）、软件栈（UniFi OS 与应用）、以及管理与数据两条路径（本地 / 云端）。本章逐层拆开，凡官方文档只给出结论、没有给出实现细节的地方，一律标注为「未公开」，不做机制推测。

### 2.1 硬件组成与各自角色

### 2.1.1 五个 2.5GbE 口：转发面的物理入口与出口

官方规格给出的端口事实是：5 个 2.5 GbE RJ45，支持 2.5G / 1G / 100M / 10M 协商；默认 WAN 口为 1 个 2.5 GbE RJ45；最大 WAN 口数为 4；MAC 地址表容量 2,000[1]。

这组数字的含义是：设备出厂时只有 1 个口承担 WAN 角色，其余口作为 LAN 侧接入；但软件允许最多把 4 个口配置成 WAN[1]。因此「1 WAN + 4 LAN」是默认拓扑，而不是硬性端口划分——官方规格只承诺「默认 WAN 口数 1」与「最大 WAN 口数 4」两个数字，端口角色由 Network 应用的配置决定，这一点属于合理归纳。多 WAN 场景下官方支持负载均衡、故障切换，并可用 UniFi LTE Backup 做额外互联网故障转移[1]。

### 2.1.2 计算与内存：官方只给到 SoC 级别

处理器为四核 ARM Cortex-A53，主频 1.5 GHz；系统内存 3 GB[1]。官方规格没有公开 SoC 具体型号、内存类型与频率、是否有网络加速单元，也没有公开包处理路径；这些属于未公开项，不应从「四核 A53」外推具体转发实现。

可核实的性能口径有两个：其一是 IDS/IPS 吞吐标称 2.3 Gbps[1]，Store 页面在同一项上附注「With UniFi OS 4.1 or later」[2]，说明该数字是特定软件版本下的标称值，而不是硬件理论上限；其二是管理规模上限——30+ 台 UniFi 设备、300+ 并发客户端、50 个 Access Hub，以及按分辨率分档的摄像头数量（15 路 HD / 8 路 2K / 5 路 4K）[1]。这些上限描述的是 console 能纳管的设备规模，不等于转发性能。

### 2.1.3 存储：可选 NVMe，承担 NVR 角色

官方把这项硬件直接命名为 NVR Storage：可选 NVMe SSD，最高 2 TB[1]。它不参与路由转发，只服务于录像落盘（见 2.5）。带 SSD 时整机重量从 460 g 变为 519 g[1]，可作为「存储是内置可插拔件」的旁证。

### 2.1.4 状态屏、供电、外壳与管理接口

- 显示：0.96" 状态显示屏（官方标为 LCM Display）[1]。官方只描述为「status display」，未公开可显示的具体内容项与刷新机制。
- 供电：USB type C（5V DC / 5A），适配器随附，支持 100–240V AC 输入；最大功耗 16.1 W，散热 55 BTU/hr[1]。
- 外壳：聚碳酸酯（Polycarbonate）[1]。官方规格未列出风扇项，也未给出风道或转速参数——据此可归纳为无风扇被动散热设计，但官方并未在规格中明写「fanless」，此条属合理归纳。
- 管理接口：官方在 Management 一栏只写 Ethernet 与 Bluetooth[1]。以太网管理对应本地/云端两条路径（见 2.4）；Bluetooth 的具体用途（如首次配置、近场发现）官方规格未展开，属未公开。
- 环境：工作温度 -10 至 40 °C，湿度 5–95% 非凝结[1]。

### 2.2 软件栈

### 2.2.1 UniFi OS：主机操作系统与统一管理底座

官方对 UniFi OS 的定义是「a powerful and intuitive operating system enabling an end-to-end management experience」[3]。它列出的能力包括：通过 unifi.ui.com 的 UniFi Site Manager 或 UniFi 手机应用（iOS / Android）在任何地点管理；多因素认证（含 UI Verify）；以及对 UniFi OS 与 UniFi 应用做自动云备份与恢复[3]。

从角色上看，UniFi OS 是运行在 console 上的宿主系统，应用是它之上的「可原生运行的应用」（官方原文为「The UniFi Applications that can run on a Cloud Gateway are…」）[3]。手机应用的最低版本要求为 iOS / Android 10.17.2 及以上[1]，这是官方给出的客户端兼容门槛。

### 2.2.2 UniFi Network：转发策略的定义者与网关控制者

官方把 Network 描述为「The core of the network which manages UniFi gateways, switches and access points providing real-time traffic insights and a visual topology」[3]。落到 UCG-Max 的规格表，Network 承载的能力可以分成四组[1]：

- 安全：有状态防火墙、应用感知（L7）防火墙、DPI 与流量识别、基于区域的防火墙高级过滤（地区 / 域名 / 应用）、内容过滤、IPS/IDS、广告拦截、基于 VLAN/子网的流量分段；
- 路由与 NAT：多 WAN 负载均衡、OSPF 与 BGP 动态路由、高级 NAT（SNAT / DNAT / Masquerade / NAT Pooling / 1-to-1 NAT）、基于策略的 WAN 与 VPN 路由、自定义 DHCP、IPv6 ISP 支持、IGMP 代理、mDNS；
- VPN 与 SD-WAN：免许可 SD-WAN、Site Magic、IPsec / OpenVPN 站点间 VPN、WireGuard / OpenVPN / L2TP / Teleport 服务端、WireGuard / OpenVPN 客户端；
- 其他：高级 QoS、集成 RADIUS 服务器与 RadSec、UniFi LTE Backup 故障转移、互联网质量与中断上报。

由此可以归纳：Network 既是转发策略（防火墙、NAT、路由、QoS）的定义者，也是被纳管网关/交换机/AP 的控制者；但「策略如何变成设备上的转发行为」属于未公开实现。

### 2.2.3 UniFi Protect：可选应用，依赖本地存储

官方描述 Protect 为「A constantly evolving, AI-driven camera platform with smart-detection capabilities that records high-quality video to local storage」[3]。在 UCG-Max 上，「local storage」就是规格表中的 NVR Storage（NVMe SSD，最高 2 TB）[1][2]，摄像头规模上限为 15 路 HD / 8 路 2K / 5 路 4K[1]。因此 Protect 在 UCG-Max 上是可选应用：装了应用但没有可用 NVMe 时，缺少官方定义的录像落盘介质（此句为合理归纳；官方只说明 No Storage 版本不含 SSD，见 2.5）。

### 2.2.4 官方 Application Suite 支持矩阵

techspecs 的「UniFi Application Suite」段以勾选形式给出 UCG-Max 的原生应用支持[1]：

| 应用 | 官方是否支持 | 官方角色描述（摘自 ui.com）[3] |
|---|---|---|
| UniFi Network | ✓ | 网络核心，管理网关、交换机、AP，提供实时流量洞察与拓扑 |
| UniFi Protect | ✓ | AI 驱动的摄像头平台，录像写入本地存储 |
| UniFi Access | ✓ | 门禁控制，含触摸屏读卡器、实时视频、手机凭证 |
| UniFi Talk | ✓ | 企业电话系统（VoIP 订阅 + 呼叫路由） |
| UniFi Connect | ✓ | 企业物联网（Enterprise of Things）平台 |

五个应用全部为 ✓，与官方 FAQ「可在 Cloud Gateway 原生运行的应用」列表一致[3]。官方未给出多应用并发运行时的资源配额或性能分配，属未公开。

### 2.3 数据流（转发面）

官方规格与官方页面能支撑的功能环节是清楚的，但**没有公开数据包在设备内部的处理顺序、流水线级数、是否共享解析结果、以及软硬件卸载策略**。下面的顺序按功能语义排列，是本章的合理归纳，不代表官方实现顺序。

```
                    ┌──────────────────────── UCG-Max（UniFi OS console）────────────────────────┐
                    │                                                                          │
 ISP/WAN ──2.5GbE──▶│ [WAN 口 ×1（可扩至 4）] ──▶ [路由 / NAT] ──▶ [防火墙 + ACL] ──▶ [IDS/IPS] │
                    │        ▲                      │                 │              │          │
                    │   多 WAN 选路                 │                 │              ▼          │
                    │  （负载均衡/故障切换）         │                 │        [DPI 分类]        │
                    │                               ▼                 ▼              │          │
                    │                        [LAN / VLAN / 子网] ◀─────┴──────────────┘          │
                    │                               │                                          │
                    │        ┌──────────────────────┼──────────────────────┐                   │
                    │        ▼                      ▼                      ▼                   │
                    │  2.5GbE ×4（LAN）      外接 AP（无线）      VPN / SD-WAN 出口           │
                    │  （交换机/终端）        （Network 纳管）    （Site Magic/IPsec/…）        │
                    │                                                                          │
                    │  [NVMe SSD ≤2TB] ◀── Protect 录像          [0.96" LCM 状态屏]            │
                    └──────────────────────────────────────────────────────────────────────────┘
```

**逐段说明（官方可支撑的部分）：**

1. **WAN 接入与选路**：5 个 2.5GbE 口中默认 1 个为 WAN、最多 4 个可作 WAN[1]；多 WAN 支持负载均衡与故障切换，并可用 LTE Backup 做额外故障转移[1]。选路发生在进入 NAT 之前，属合理归纳。
2. **路由 / NAT**：官方列出 OSPF、BGP 动态路由与 SNAT / DNAT / Masquerade / NAT Pooling / 1-to-1 NAT[1]，以及基于策略的 WAN 与 VPN 路由[1]。NAT 会话表规模、并发连接数官方未公开。
3. **防火墙与 ACL**：官方同时提供有状态防火墙、应用感知 L7 防火墙、基于区域的防火墙（官方 FAQ 说明其把网络归入 LAN / WAN / Guest / IoT 等区域，从而按区域而非逐条规则设置策略）[3]，以及独立的内容过滤与广告拦截[1]。Network API 中存在 firewall policies、firewall zones、acl-rules 三类资源，并各自带 ordering 接口[6]，说明「策略是有序集合」这一点可由 API 结构佐证；但策略求值顺序与匹配算法未公开。
4. **IDS/IPS**：官方标称 IDS/IPS 吞吐 2.3 Gbps（UniFi OS 4.1 及以上）[1][2]，签名库规模 55,000+ 且与 CyberSecure 相关[1]。官方对能力的描述是「detects and blocks suspicious activity…using a real-time database of known threats」[3]；检测/阻断的具体模式（旁路、串联、硬件加速）未公开。
5. **DPI 与流量识别**：官方规格列出 DPI 与 Traffic Identification[1]；Network API 暴露 DPI 应用清单与分类清单两个只读资源[6]；官方页面另描述「Traffic Flow Engine」可记录应用使用、IP 会话与协议行为[3]。这说明 DPI 的分类结果既用于策略，也进入可视化与日志；特征库更新机制未公开。
6. **LAN / VLAN**：官方支持基于 VLAN/子网的分段、虚拟网络覆盖、mDNS、DHCP、IPv6 与 IGMP 代理[1]，MAC 表容量 2,000[1]。
7. **无线**：UCG-Max 本身没有无线射频（规格表端口全为 RJ45，未列 Wi-Fi 项）[1]，无线必须由外接 UniFi AP 提供，而 AP 由 Network 纳管[3]。Wi-Fi 广播对象可通过 Network API 管理[6]。此为合理归纳：官方没有直说「无 Wi-Fi」，但规格表没有任何射频项。
8. **VPN / SD-WAN 出口**：官方支持 Site Magic（免许可 SD-WAN）、IPsec 与 OpenVPN 站点间 VPN、WireGuard / OpenVPN / L2TP / Teleport 服务端、WireGuard / OpenVPN 客户端[1]。Site Magic 的站点与子网选择在 Site Manager 中完成[3]，Site Manager API 另有 SD-WAN 配置与状态资源[7]，说明 SD-WAN 编排的入口在云端而非仅本地。

**关于 2.3 Gbps 的口径**：这是整机 IDS/IPS 吞吐标称值[1][2]。若 5 个 2.5GbE 口同时满速，单向聚合为 12.5 Gbps，远高于该标称，因此开启 IPS/IDS 后整机吞吐受 2.3 Gbps 约束（算术归纳，官方未给出分场景吞吐曲线）。这也是把 UCG-Max 定位为「2.5G 接入 + 千兆级安全转发」而非「全线速 2.5G 安全网关」的依据。

### 2.4 控制流（管理面）

管理面是两条独立寻址的路径：本地直连 console，或经云端代理。

```
[本地路径]
 浏览器/脚本 ──HTTPS──▶ https://{consoleIP}/proxy/network/integration ──▶ console 上的 Network 应用
                        （官方 OpenAPI 的 Local Console server）[6]        （API 在本地每个 site 运行）[5]

[云端路径]
 unifi.ui.com（Site Manager）── 配置/监控/备份 ──┐
 手机 App（iOS/Android ≥10.17.2）[1] ───────────┤
                                                ├──▶ console（host 注册到云端）[8]
 Site Manager API  https://api.ui.com/v1  ──────┘
 Cloud Connector  https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration
                                                └──▶ 代理到 console 本地 Network API [6]

[设备侧]
 待 adopt 设备 ──adopt──▶ console 的 Network 应用 ──▶ 已 adopt 设备列表 / 设备动作 [6]
 设备与 console 状态 ──上报──▶ 云端（hosts.reportedState / ISP 指标）[8][7]
```

**（1）本地路径。** 官方 OpenAPI 把本地 console 定义为 server：`https://{consoleIP}/proxy/network/integration`[6]；Network 文档说明「每个 UniFi 应用都有自己的 API 端点，运行在每个 site 本地」[5]。认证统一走 API Key：请求头 `X-API-Key`，密钥在 unifi.ui.com 生成[4][7]。浏览器访问 console 的 UniFi OS 管理界面属于 console 的标准本地管理方式，但本次引用的官方开放文档只给出 API 前缀，未给出 UI 地址细节，故 UI 地址部分属归纳。

**（2）云端路径。** 官方把 Site Manager（unifi.ui.com）列为「在任何地点管理」的入口，并支持通过手机应用管理[3]。Site Manager API 的基址是 `https://api.ui.com`，分为 `/v1/...`（正式）与 `/ea/...`（Early Access）两套[10]；资源包括 hosts、sites、devices、ISP metrics、SD-WAN configs[4][7]。跨网络访问本地 Network API 时，官方提供 Cloud Connector：`https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration`，其中 `consoleId` 指定要代理到的 console[6]；开发者文档也明确「Network 与 Protect API 运行在每个 UniFi host 本地，可通过 Site Manager Cloud Connector 或现场直连访问」[4]。

**（3）Adoption。** 官方 API 把 adoption 做成一等概念：先有 pending 设备列表（`GET /v1/pending-devices`），再通过 `POST /v1/sites/{siteId}/devices` 执行 adopt，之后设备出现在「已 adopt 设备」列表，可按设备执行动作或移除（unadopt）[6]。多数调用以 `siteId` 为前提，站点是配置与设备的归属单位[5]。

**（4）配置下发。** 配置写入通过本地或云端代理的 Network API 完成，可写的对象包括 networks、firewall policies 与 zones、acl-rules、dns policies、traffic matching lists、wifi broadcasts 等；`/v1/sites/{siteId}/vpn/servers` 只有 `GET`，VPN 服务端目前是只读资源，firewall 与 acl 还提供 ordering 接口用于调整用户自定义规则的顺序[6]。由于这些 API 运行在 console 本地[5]，可以归纳为「配置先落在 console 上的 Network 应用」；console 再向被 adopt 设备下发配置，但最后一跳的协议、原子性与回滚机制官方未公开。

**（5）遥测与统计。** 本地侧，Network API 提供 `GET /v1/sites/{siteId}/devices/{deviceId}/statistics/latest` 读取设备最新统计[6]。云端侧，Site Manager 的 hosts 资源返回每台 host 的注册时间（registered to cloud）、连接状态、最近一次设备备份时间、以及 `reportedState` / `userData` 字段；官方注明这些字段结构会随 UniFi OS 或 Network Server 版本变化，示例基于 UniFi OS 4.1.13[8]。devices 资源返回 host 所属设备、最后处理时间、型号代码、连接状态与固件版本[9]，另提供 ISP 指标查询[7]。hosts 还带「是否被禁止云访问」字段[8]，说明云访问可被单独阻断，而本地 API 路径独立存在（归纳）。配合官方「自动云备份与恢复」能力[3]，可以确认「console → 云端」存在状态与备份上报通道，但通道协议、心跳间隔与字段全集未公开。

**（6）API 治理。** Site Manager API 采用统一响应结构（data / httpStatusCode / traceId / nextToken）与统一错误结构[11]；分页用 pageSize（单页上限 500）与 nextToken[11]；限流按版本区分，v1 为每分钟 10,000 次、EA 为每分钟 100 次，超限返回 429 并带 Retry-After[7]；版本策略规定 EA 端点晋升为 `/v1` 后旧调用继续可用，并允许新增资源、可选参数与响应属性等向后兼容变更[10]。

### 2.5 存储分支

UCG-Max 按存储分为四个版本：No Storage、512 GB、1 TB、2 TB[2]。官方对差异的说明是：**No Storage 版本不含 NVMe SSD，第三方 SSD 安装需要托盘配件；512 GB 与 1/2 TB 版本包含托盘配件与 NVMe SSD**[2]。官方同时给出托盘配件（UACC-SSD-Tray）的规格：支持 2230 / 2242 / 2260 / 2280 / 22110 尺寸的 M.2 NVMe SSD，UCG-Max 与 UCG-Fiber、UNAS-Pro-8/4 一样，每块 NVMe 运行在 PCIe Gen3x2[2]。

由此可归纳出三条工程结论：

1. **存储上限由硬件与配件共同决定**：官方标称最高 2 TB[1][2]，同时托盘支持 22110 长尺寸，说明物理空间不是按单一短盘设计。
2. **录像落盘位置明确**：Protect 的录像写入本地存储[3]，而 UCG-Max 的本地存储就是这块 NVMe（官方命名为 NVR Storage）[1]；因此无 SSD 的 No Storage 版本不具备官方定义的录像介质（归纳）。
3. **容量规划有官方参考值**：同一份规格表给出 15 路 HD / 8 路 2K / 5 路 4K 的摄像头上限[1]，可作为「2 TB 能撑多少路」的量级参考，但官方未给出码率、保留天数与容量换算表。

### 2.6 官方明确与合理归纳的边界

| 结论 | 证据强度 | 依据 |
|---|---|---|
| 5×2.5GbE、默认 WAN 1、最大 WAN 4、MAC 表 2,000 | 官方明确 | [1] |
| 四核 A53 1.5GHz、3GB 内存 | 官方明确 | [1] |
| IDS/IPS 2.3 Gbps，且需 UniFi OS 4.1+ | 官方明确 | [1][2] |
| 55,000+ IPS/IDS 签名（CyberSecure） | 官方明确 | [1] |
| Application Suite 五项均 ✓ | 官方明确 | [1][3] |
| NVR Storage 为可选 NVMe，最高 2 TB | 官方明确 | [1][2] |
| No Storage 不含 SSD、第三方盘需托盘、托盘支持 22110、PCIe Gen3x2 | 官方明确 | [2] |
| 本地 API 基址与 Cloud Connector 路径 | 官方明确 | [6] |
| Site Manager API 基址 api.ui.com、v1/EA、限流、响应结构 | 官方明确 | [7][10][11] |
| adoption 的 pending→adopt→unadopt 流程 | 官方明确 | [6] |
| hosts 的 reportedState / 注册时间 / 最近备份字段 | 官方明确 | [8] |
| Protect 录像写本地存储 | 官方明确 | [3] |
| 1 WAN + 4 LAN 是默认拓扑 | 合理归纳 | 官方只给默认 WAN 1、最大 WAN 4 [1] |
| 无风扇被动散热 | 合理归纳 | 官方规格无风扇相关项 [1] |
| 无线必须外接 AP | 合理归纳 | 规格无射频项，AP 由 Network 纳管 [1][3] |
| NAT→防火墙→IPS→DPI 的处理顺序 | 合理归纳 | 官方只列功能，不列顺序 [1] |
| 开启 IPS 后整机吞吐受 2.3 Gbps 约束 | 合理归纳 | 标称值与端口速率的算术比较 [1][2] |
| 无 SSD 时 Protect 无本地录像介质 | 合理归纳 | NVR Storage 与 Protect 本地录像两条官方事实 [1][3] |
| console 向设备下发配置的最后一跳 | 未公开 | 仅有 API 侧写入事实 [6] |

### 2.7 官方来源

访问日期均为 2026-10-31。

1. Ubiquiti Tech Specs — Cloud Gateway Max（UCG-Max）：https://techspecs.ui.com/unifi/cloud-gateways/ucg-max
2. Ubiquiti Store — Cloud Gateway Max 产品页（含 NVR Storage 注释、M.2 SSD Tray 配件规格）：https://store.ui.com/us/en/products/ucg-max
3. ui.com — UniFi Cloud Gateways 产品族页（含 UniFi OS / 应用 FAQ）：https://ui.com/us/en/cloud-gateways
4. Ubiquiti Developer Docs — 文档索引与 API 总览（Site Manager 基址、Cloud Connector 说明）：https://developer.ui.com/llms.txt
5. Ubiquiti Developer Docs — Network API v11.0.97 Getting Started：https://developer.ui.com/network/v11.0.97/gettingstarted
6. Ubiquiti Developer Docs — Network OpenAPI v11.0.97（servers：Local Console 与 Cloud Connector；本仓库副本 `docs/ucg-max-research/network-openapi-11.0.97.json`）：https://developer.ui.com/network/v11.0.97/openapi.json
7. Ubiquiti Developer Docs — Site Manager API v1.0.0 Getting Started（限流、分页、端点清单）：https://developer.ui.com/site-manager/v1.0.0/gettingstarted
8. Ubiquiti Developer Docs — Site Manager API List Hosts（reportedState / 注册与备份字段）：https://developer.ui.com/site-manager/v1.0.0/listhosts
9. Ubiquiti Developer Docs — Site Manager API List Devices：https://developer.ui.com/site-manager/v1.0.0/listdevices
10. Ubiquiti Developer Docs — Site Manager API Version Control（/v1 与 /ea、向后兼容策略）：https://developer.ui.com/site-manager/v1.0.0/versioncontrol
11. Ubiquiti Developer Docs — Site Manager API Response Format（成功/错误结构、分页）：https://developer.ui.com/site-manager/v1.0.0/responseformat

### 2.8 未找到 / 未公开

- **转发面实现细节**：数据包在设备内的处理顺序、流水线级数、是否共享解析、软硬件卸载与队列调度策略，官方文档均未公开。
- **SoC 与平台细节**：官方只给「四核 Cortex-A53 1.5 GHz + 3 GB」，未给 SoC 型号、内存规格、是否有加密/转发加速单元；未公开所以不引用任何内核或包处理框架名称。
- **NAT 与会话规模**：官方未给 NAT 会话表容量、并发连接数、新建连接速率。
- **IPS/DPI 特征库运维**：签名更新频率、更新通道、特征库版本策略未公开；DPI 分类的具体规则与更新机制亦未公开。
- **Bluetooth 管理用途**：规格只列「Management: Ethernet / Bluetooth」，未说明 Bluetooth 用于何种管理流程。
- **LCM 状态屏内容**：官方只写「0.96" status display」，未列出可显示项与交互方式。
- **配置下发协议**：console 向被 adopt 设备下发配置的最后一跳协议、原子性、回滚与失败重试策略未公开。
- **云连接与遥测协议**：console 与云端的连接建立方式、心跳间隔、遥测字段全集与保留策略未公开；官方只给出 hosts 的 reportedState / userData 结构随版本变化这一限制。
- **多应用并发资源配额**：五个应用同时运行时 CPU/内存/存储如何分配，官方未给出配额或性能数据。
- **存储换算**：官方未给录像码率、保留天数与容量的换算表，也未给兼容 SSD 型号清单与耐久度要求。
- **吞吐分场景曲线**：仅有一个 2.3 Gbps 标称值，未给不同包长、不同功能组合（IPS+DPI+QoS）下的吞吐曲线。

## 三、API 操作指南

UCG-Max 上的自动化入口由 Ubiquiti 官方在 developer.ui.com 提供，文档公开可读、无需注册；但要真正发出请求，必须先在自己的 UI 账户下生成 API Key。官方索引（`llms.txt`）共列出 6 个 API：Network v11.0.97、Protect v7.3.74、Mobility v1.0.0、InnerSpace v1.3.23、Site Manager v1.0.0、Carrier Fabric v1.0.0；本章只覆盖与 UCG-Max 网关运维直接相关的两套：**Site Manager API**（云侧、账号级）与 **Network API**（本地、站点级）。与 NVR/录像相关的自动化还需另查 Protect API v7.3.74。两者基址、分页方式、响应包裹结构和错误体都不相同，混用是接入时最常见的错误来源。本章按「认证 → 入口与基址 → 端点 → 请求响应要点 → 可复制示例 → 已知限制 → 版本」的顺序展开，所有接口名、参数、字段与限流数字均取自官方规范或官方文档页面。

### 3.1 认证：API Key 与请求头

官方 `llms.txt` 对全部 API 的认证方式只有一句话：所有 API 都通过 `X-API-KEY` 请求头做 API Key 认证，密钥在 unifi.ui.com 生成。而 Site Manager 的 OpenAPI 规范把安全方案声明为 `type: apiKey`、`in: header`、`name: X-API-Key`。也就是说官方自己就在两种大小写之间摇摆：文档正文与 curl 示例统一写 `X-API-KEY`，规范里的 securityScheme 写 `X-API-Key`。HTTP 头字段名本身大小写不敏感，两种写法等价，客户端按团队规范任选一种即可，但不要在同一套代码里混写到让人误以为是两个头。

创建路径在 Site Manager Getting Started 页面写得很明确：登录 unifi.ui.com，进入 Settings → API Keys，选择 “Create New API Key”，复制生成的密钥并妥善保存，因为**密钥只会显示一次**。官方同时给出直达链接 <https://unifi.ui.com/settings/api-keys>，并提示要对密钥做妥善的哈希与安全存储——换成工程语言就是：生成后立刻写入环境变量或密钥管理器，不要留在 shell 历史、截图或仓库里，因为平台不再提供第二次查看。

密钥的归属范围两页措辞略有差异：Network 页写每个 key 绑定到生成它的「组织或 UI 账户」，Site Manager 页写绑定到生成它的「UI 账户」。实际含义一致——key 是账号/组织级凭证，不是某个站点或某台 UCG-Max 的凭证，因此在同一组织下换控制台不需要换 key，但把 key 交给第三方就等于交出该账号可见范围内的全部数据。

认证失败的形态是固定的：Network API 的错误示例就是 `code: api.authentication.missing-credentials`、`message: Missing credentials`、`statusName: UNAUTHORIZED`。看到这一组值说明问题在凭证（没带头、头名拼错、key 被吊销），而不是路径写错；反之若路径或站点 ID 错，`statusName` 会落在 404/`NOT_FOUND` 一类。官方文档未提供密钥有效期与自动轮换机制的说明，因此「多久换一次 key」只能由使用方自行制定策略。

一个容易被忽略的事实：Network API 的 OpenAPI 规范（v11.0.97）里**完全没有声明 `securitySchemes`**，规范只描述路径与数据结构，认证要求只出现在 `llms.txt` 与文档页中。因此不要指望从规范文件里自动生成认证代码，认证头需要手工加上。官方文档里也没有出现 OAuth、Bearer Token 或 Basic Auth 等其它认证方式的说明，API Key 是当前唯一被记录的认证手段。

### 3.2 两套入口与基址

| 维度 | Site Manager API | Network API |
|---|---|---|
| 基址 | `https://api.ui.com/v1/...` | 本地：`https://{consoleIP}/proxy/network/integration`；云端：`https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration` |
| 作用域 | 账号级，跨控制台汇总 | 单个 Network 应用（单台 UCG-Max）及其站点 |
| 版本 | v1.0.0（另有 `/ea/...` 早期访问前缀） | v11.0.97 |
| 认证 | `X-API-Key` 请求头 | 同一把 key，同一请求头 |
| 分页 | `pageSize` + `nextToken` | `offset` + `limit`（列表端点另有 `filter`） |
| 响应包裹 | `data` / `httpStatusCode` / `traceId` / `nextToken` | `count` / `data` / `limit` / `offset` / `totalCount` |
| 典型用途 | 列出组织下所有 host、device、site；查 ISP 指标、SD-WAN 配置 | 读写 VLAN/网络、SSID、防火墙策略与区域、ACL、DNS 策略、hotspot 凭证、端口动作、设备统计 |

本地基址中的 `{consoleIP}` 在规范里给出的变量默认值是 `192.168.0.1`，即 UCG-Max 在局域网的地址；`/proxy/network/integration` 这一段是所有 Network API 路径的固定前缀，规范里 44 条路径全部写作 `/v1/...`，实际请求时要拼在 `integration` 之后，最终形如 `https://192.168.0.1/proxy/network/integration/v1/sites`。云端基址多一层 `v1/connector/consoles/{consoleId}/proxy/network/integration`，其中 `consoleId` 的官方描述仅为「要代理请求到的控制台 ID」，并给出形如 `942A6FF664C0000000000964970E0000000009E710560000000068A9873F:9999999999` 的示例。**官方文档未说明该 ID 的具体获取步骤**；它的示例形态与 Site Manager `GET /v1/hosts` 返回的 `id` 字段示例（同样是 `<序列号>:<数字>`）一致，但官方并未声明两者可以直接互换，接入时应以控制台或 Site Manager 实际返回值为准。

另需指出官方文档内部的一处出入：Site Manager 规范中 `*path` 参数的示例写作 `/proxy/network/integration/v1/sites`（含 `proxy` 段），而同页正文示例写作 `/network/integration/v1/sites`（不含 `proxy`）；Network 规范给出的 Cloud Connector server 为 `https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration`（含 `proxy`）。两份官方资料在这一点上不一致，接入时以 Network 规范的 server 定义为准更稳妥，若返回 404 再尝试去掉 `proxy` 段的写法。

两者的适用场景可以这样切分：需要「一次看全组织」时走 Site Manager——例如盘点所有控制台与设备、读取站点清单、拉取 ISP 质量指标；需要「改这台网关的配置」时走 Network——UCG-Max 的 VLAN、WiFi 广播、防火墙策略、ACL 规则、DNS 策略、hotspot 凭证、端口与设备动作都在这一侧，Site Manager 没有对应写接口。落到具体任务上：查询组织内所有控制台及其设备清单用 `GET /v1/hosts`（Site Manager）；给访客网络新开一个 VLAN 与 SSID 用 `POST /v1/sites/{siteId}/networks` 加 `POST /v1/sites/{siteId}/wifi/broadcasts`（Network）；排查某台 AP 的端口协商速率则用 `GET /v1/sites/{siteId}/devices/{deviceId}/statistics/latest` 与端口动作端点（Network）。

把两者串起来的一次典型流程是：先在 Site Manager 用 `GET /v1/hosts` 找到目标控制台及其 `id`（控制台标识，形如 `70A7...:1261206302`），再用 `GET /v1/devices` 确认该控制台下有哪些设备与站点；随后切换到 Network API，用 `GET /v1/sites` 重新取 UUID 形式的站点 ID，再执行配置读写。步骤看起来绕，但这是两套 API 各自只认自己 ID 体系导致的必然结果。

还有一个必须注意的差异：**两套 API 的站点标识不是同一个东西**。Site Manager 的 `siteId` 示例是 24 位十六进制串（如 `661de833b6b2463f0c20b319`），Network 的站点 `id` 在规范中明确为 UUID 格式；用 Site Manager 拿到的 ID 去调 Network 的 `/v1/sites/{siteId}/...` 会失败，反之亦然。这属于「换 API 时忘记重新取 ID」的典型事故，排查时优先怀疑。

### 3.3 关键端点

Network API v11.0.97 共 **44 条路径、73 个操作**，其中 Firewall 13 个、UniFi Devices 8 个、Supporting Resources 8 个、ACL 7 个、Networks 6 个、Switching 6 个，其余为 DNS Policies 5 个、Hotspot 5 个、Traffic Matching Lists 5 个、WiFi Broadcasts 5 个、Clients 3 个、Application Info 与 Sites 各 1 个。下表按用途分组挑出 23 条最常用的操作：

| 分组 | 方法与路径 | 官方摘要 | 官方文档 |
|---|---|---|---|
| 应用信息 | `GET /v1/info` | Get Application Info | [getinfo](https://developer.ui.com/network/v11.0.97/getinfo) |
| 站点 | `GET /v1/sites` | List Local Sites | [getsiteoverviewpage](https://developer.ui.com/network/v11.0.97/getsiteoverviewpage) |
| 设备 | `GET /v1/pending-devices` | List Devices Pending Adoption | [getpendingdevicepage](https://developer.ui.com/network/v11.0.97/getpendingdevicepage) |
| 设备 | `GET /v1/sites/{siteId}/devices` | List Adopted Devices | [getadopteddeviceoverviewpage](https://developer.ui.com/network/v11.0.97/getadopteddeviceoverviewpage) |
| 设备 | `GET /v1/sites/{siteId}/devices/{deviceId}/statistics/latest` | Get Latest Adopted Device Statistics | [getadopteddevicelateststatistics](https://developer.ui.com/network/v11.0.97/getadopteddevicelateststatistics) |
| AP 与交换机 | `POST /v1/sites/{siteId}/devices/{deviceId}/interfaces/ports/{portIdx}/actions` | Execute Port Action | [executeportaction](https://developer.ui.com/network/v11.0.97/executeportaction) |
| 客户端 | `GET /v1/sites/{siteId}/clients` | List Connected Clients | [getconnectedclientoverviewpage](https://developer.ui.com/network/v11.0.97/getconnectedclientoverviewpage) |
| 客户端 | `POST /v1/sites/{siteId}/clients/{clientId}/actions` | Execute Client Action | [executeconnectedclientaction](https://developer.ui.com/network/v11.0.97/executeconnectedclientaction) |
| 网络 | `GET /v1/sites/{siteId}/networks` | List Networks | [getnetworksoverviewpage](https://developer.ui.com/network/v11.0.97/getnetworksoverviewpage) |
| 网络 | `POST /v1/sites/{siteId}/networks` | Create Network | [createnetwork](https://developer.ui.com/network/v11.0.97/createnetwork) |
| WiFi 广播 | `GET /v1/sites/{siteId}/wifi/broadcasts` | List Wifi Broadcasts | [getwifibroadcastpage](https://developer.ui.com/network/v11.0.97/getwifibroadcastpage) |
| WiFi 广播 | `POST /v1/sites/{siteId}/wifi/broadcasts` | Create Wifi Broadcast | [createwifibroadcast](https://developer.ui.com/network/v11.0.97/createwifibroadcast) |
| 防火墙策略 | `GET /v1/sites/{siteId}/firewall/policies` | List Firewall Policies | [getfirewallpolicies](https://developer.ui.com/network/v11.0.97/getfirewallpolicies) |
| 防火墙区域 | `GET /v1/sites/{siteId}/firewall/zones` | List Firewall Zones | [getfirewallzones](https://developer.ui.com/network/v11.0.97/getfirewallzones) |
| ACL | `GET /v1/sites/{siteId}/acl-rules` | List ACL Rules | [getaclrulepage](https://developer.ui.com/network/v11.0.97/getaclrulepage) |
| DNS 策略 | `GET /v1/sites/{siteId}/dns/policies` | List DNS Policies | [getdnspolicypage](https://developer.ui.com/network/v11.0.97/getdnspolicypage) |
| 流量匹配列表 | `GET /v1/sites/{siteId}/traffic-matching-lists` | List Traffic Matching Lists | [gettrafficmatchinglists](https://developer.ui.com/network/v11.0.97/gettrafficmatchinglists) |
| AP 与交换机 | `GET /v1/sites/{siteId}/switching/switch-stacks` | List Switch Stacks | [getswitchstackpage](https://developer.ui.com/network/v11.0.97/getswitchstackpage) |
| AP 与交换机 | `GET /v1/sites/{siteId}/switching/lags` | List LAGs | [getlagpage](https://developer.ui.com/network/v11.0.97/getlagpage) |
| VPN 与 WAN | `GET /v1/sites/{siteId}/vpn/servers` | List VPN Servers | [getvpnserverpage](https://developer.ui.com/network/v11.0.97/getvpnserverpage) |
| VPN 与 WAN | `GET /v1/sites/{siteId}/vpn/site-to-site-tunnels` | List Site-To-Site VPN Tunnels | [getsitetositevpntunnelpage](https://developer.ui.com/network/v11.0.97/getsitetositevpntunnelpage) |
| VPN 与 WAN | `GET /v1/sites/{siteId}/wans` | List WAN Interfaces | [getwansoverviewpage](https://developer.ui.com/network/v11.0.97/getwansoverviewpage) |
| Hotspot 凭证 | `GET /v1/sites/{siteId}/hotspot/vouchers` | List Vouchers | [getvouchers](https://developer.ui.com/network/v11.0.97/getvouchers) |

这套端点的设计规律很统一，掌握后可以猜路径：列表是 `GET` 集合（带分页与 filter），详情是 `GET {id}`，创建是 `POST` 集合，更新是 `PUT {id}`（只有防火墙策略额外提供 `PATCH {id}`，规范未说明两者字段级差异），删除是 `DELETE {id}`；顺序调整走独立的 `/ordering` 资源（ACL 规则与防火墙策略各有 `GET` 取序、`PUT` 改序）；设备与端口的即时操作走 `/actions` 子路径，请求体的必填字段只有 `{"action": "..."}`（规范里 `required` 仅 `action`，但官方文字同时要求带上该动作适用的输入参数，可选参数按动作而定）。hotspot 凭证特殊：除按 ID 删除外，还有集合级 `DELETE /v1/sites/{siteId}/hotspot/vouchers`，用 `filter` 批量吊销，适合「一次性作废某批访客码」的场景；该端点的官方描述原文是 "Remove Hotspot vouchers based on the specified filter criteria."，即删除动作由 filter 条件而不是 ID 列表决定。

写操作的必填字段规范里都有明确约束：创建网络至少需要 `name`、`vlanId`、`management`、`enabled`；创建 ACL 规则需要 `name`、`action`（枚举 `ALLOW`/`BLOCK`）、`type`、`enabled`；采纳设备需要 `macAddress` 与 `ignoreDeviceLimit`；创建设备/客户端/端口动作都需要 `action`。WiFi 广播的必填项最多（`IntegrationStandardWifiBroadcastCreateUpdateDto` 的 `required` 为 13 项：`advertiseDeviceName`、`arpProxyEnabled`、`broadcastingFrequenciesGHz`、`bssTransitionEnabled`、`channel2gLockedTo6`、`clientIsolationEnabled`、`dtimPeriod2gLockedTo3`、`enabled`、`hideName`、`multicastToUnicastConversionEnabled`、`name`、`securityConfiguration`、`uapsdEnabled`；另有判别字段 `type`，IOT_OPTIMIZED 子类型另有自己的必填集合），其中 `securityConfiguration` 自身仍要求 `type`，因此新建 SSID 前最好先 `GET` 一个同类型的现有广播做模板，再改字段提交。

落地前建议先打一次 `GET /v1/info` 做连通性与版本自检：它是不带 `siteId` 的少数端点之一（同类的还有 `/v1/sites`、`/v1/pending-devices`、`/v1/countries` 等），返回体只有一个 `applicationVersion` 字段（规范示例 `9.1.0`），能通说明基址、前缀、认证三件事同时正确，比直接调用业务端点更容易定位问题。

Site Manager API v1.0.0 只有 **10 条路径、14 个操作**，其中 5 个是同一条 `{id}/*path` 模板上的 GET/POST/PUT/PATCH/DELETE 透传（cloud-connector），另外 9 个是业务读接口：`GET /v1/hosts`（[listhosts](https://developer.ui.com/site-manager/v1.0.0/listhosts)）、`GET /v1/hosts/{id}`、`GET /v1/sites`（[listsites](https://developer.ui.com/site-manager/v1.0.0/listsites)）、`GET /v1/devices`（[listdevices](https://developer.ui.com/site-manager/v1.0.0/listdevices)）、`GET /v1/isp-metrics/{type}`、`POST /v1/isp-metrics/{type}/query`、`GET /v1/sd-wan-configs`、`GET /v1/sd-wan-configs/{id}`、`GET /v1/sd-wan-configs/{id}/status`。可以看到 Site Manager 是「只读盘点 + 指标查询」，写操作全部留给 Network API。

### 3.4 请求与响应要点

**分页。** Network API 的列表端点统一使用 `offset` 与 `limit`，规范中 23 个带分页参数的 GET 端点里有 22 个是默认 25、上限 200；唯一的例外是 hotspot 凭证列表，默认 100、上限 1000。响应是带统计信息的包裹体：`count`（本页实际返回条数，规范示例 10）、`data`（数组）、`limit`（本页上限，示例 25）、`offset`（示例 0）、`totalCount`（符合条件的总数，示例 1000），翻页要靠客户端自己累加 `offset`。Site Manager 则完全不同：用 `pageSize` 取页、用响应里的 `nextToken` 取下一页，文档写明单页最多 500 条；规范中 `pageSize` 被声明为字符串类型且未写上界，实际限值以文档的 500 为准。文档未给出续页终止条件的明文说明，工程上以响应中不再出现 `nextToken` 作为结束；它也不提供总条数。

**filter 语法。** Network API 的 `filter` 是结构化、URL 安全的表达式，分三类：属性表达式 `<property>.<function>(<arguments>)`、复合表达式 `<logical-operator>(<expressions>)`（`and` / `or` / `not`）、以及否定表达式 `not(<expression>)`。官方给出的真实例子包括 `id.eq(123)`、`createdAt.in(2025-01-01, 2025-01-05)`、`and(name.isNull(), createdAt.gt(2025-01-01))`、`or(name.isNull(), expired.isNull(), expiresAt.isNull())` 与 `not(name.like('guest*'))`。字面量类型有严格写法：字符串必须单引号包裹、内部单引号用两个单引号转义（`'Hello, ''World''!'`）；整数与小数必须以数字开头；时间戳用 ISO 8601（`2025-01-29` 或 `2025-01-29T12:39:11Z`）；布尔写 `true`/`false`；UUID 必须是 8-4-4-4-12；集合写成 `[1, 2, 3, 4, 5]`。可用函数包括 `isNull`、`isNotNull`、`eq`、`ne`、`gt`、`ge`、`lt`、`le`、`like`、`in`、`notIn`、`isEmpty`、`contains`、`containsAny`、`containsAll`、`containsExactly`，其中 `like` 支持 `*` 通配（`type.like('type.')`、`name.like('guest*')`）。每个端点允许的过滤属性不同，设备端点允许对 `name` 用 `like`、对 `firmwareVersion` 用 `lt`，客户端端点则有 `access.type`、`access.authorized` 这类带点号的属性名，因此下面三条过滤表达式都是合法写法（以 `curl --data-urlencode` 片段形式给出）：

```bash
# 1) 设备名以 AP 开头
--data-urlencode "filter=name.like('AP*')"
# 2) 在线且名为 AP 开头的设备
--data-urlencode "filter=and(state.eq('ONLINE'), name.like('AP*'))"
# 3) 固件版本低于 7.0.0 的设备
--data-urlencode "filter=firmwareVersion.lt('7.0.0')"
```

由于表达式里含单引号、括号和逗号，直接用 `curl -G --data-urlencode` 让 curl 完成百分号编码，比手工拼 URL 可靠得多。过滤与分页可以叠加：先用 `filter` 缩小集合，再翻页读取，用 `totalCount` 判断是否漏读——注意 `totalCount` 是「符合过滤条件」的总数，不是全量总数。

**请求体与内容类型。** 所有写操作的请求体都是 `application/json`，规范把它们标记为必填（`requestBody.required: true`）。更新语义按 HTTP 常规理解：`PUT` 为整体更新、`PATCH` 为局部更新，但只有防火墙策略同时提供两者，其余资源只有 `PUT`，因此修改单个字段也要提交完整对象。动作类端点（设备动作、客户端动作、端口动作）的请求体结构极简，只有一个 `action` 字符串；官方对设备动作页的说明是「请求体必须包含动作名称以及适用的输入参数」，具体可用动作名由设备类型决定，规范未枚举全部取值。

**错误响应。** Network API 的错误体是固定 7 字段结构：`code`（如 `api.authentication.missing-credentials`）、`message`（如 `Missing credentials`）、`requestId`（UUID，仅 500 类内部错误时用于在服务端日志中定位）、`requestPath`（如 `/integration/v1/sites/123`）、`statusCode`（int32，如 400）、`statusName`（如 `UNAUTHORIZED`）、`timestamp`（date-time，如 `2024-11-27T08:13:46.966Z`）。值得注意的是，v11.0.97 规范对每个操作只声明成功响应（73 个操作中 65 个为 200、8 个创建类为 201），没有任何错误响应定义，具体错误码也没有逐端点列出，排障要以错误体里的 `statusName` 与 `requestPath` 为准——`requestPath` 会告诉你服务端实际收到的路径，拼接前缀出错时一眼可见。

Site Manager 的错误体只有 4 个字段：`code`、`httpStatusCode`、`message`、`traceId`。官方文档列出的常见码为 400 `BAD_REQUEST`、401 `UNAUTHORIZED`、403 `FORBIDDEN`、404 `NOT_FOUND`、429 `RATE_LIMIT`、500 `SERVER_ERROR`、502 `BAD_GATEWAY`，示例消息分别是 `invalid request parameters`、`unauthorized`、`insufficient permissions`、`thing not found: {id}`、`rate limit exceeded, retry after 5.372786998s`、`failed to get resource by ID`、`bad gateway`。处理建议也被写进文档：先看 HTTP 状态码，再解析 `code`，记录 `traceId`，实现重试，并从响应里取出重试时间。

**429 与 Retry-After。** Site Manager 文档明确：超出速率限制返回 429，客户端应解析响应头里的重试时间，并至少等待那么久再重试；其规范中 429 响应示例带有 `Retry-After: 5`。**Network API 的官方文档没有提供 429 与 `Retry-After` 的说明**，规范里也没有 429 响应定义——这一点在写重试策略时必须区别对待，不能把 Site Manager 的限流语义套到本地 API 上。

**未鉴权时的响应形状（本地实测）。** 2026-10-31 对一台 UniFi OS console（`https://192.168.0.1`）做连通性预检：不带 API Key 访问 `/proxy/network/integration/v1/info`、`.../v1/sites`，甚至不存在的路径，均由 console 前端（nginx 层）统一返回 `HTTP/2 401` 与 `{"error":{"code":401,"message":"Unauthorized"}}`，`content-type` 为 `application/json`、带 `x-content-type-options: nosniff`、无 `WWW-Authenticate`。这与规范中 Network API 的 7 字段错误体（`code/message/requestId/requestPath/statusCode/statusName/timestamp`）不同：未鉴权请求在到达 Integration API 之前就被拦下，因此不该按文档 schema 去解析这类响应。排障含义是——看到 `{"error":{...}}` 这种外壳，先检查 key 与请求头是否真的发出（`curl` 可用 `-v` 或 `--trace-ascii` 确认），而不是去查端点是否存在。该 console 的型号在未鉴权状态下无法确认，本节预检只证明该地址提供 UniFi OS 与 Network Integration API 入口。

### 3.5 完整调用示例

下面这段 shell 可直接复制执行，先用 `GET /v1/sites` 取 `siteId`，再列设备。它同时给出本地与云侧两种 base URL，实际用哪个由注释切换。

```bash
#!/usr/bin/env bash
set -euo pipefail

KEY="${UI_API_KEY:?请先导出 UI_API_KEY}"
HOST="192.168.0.1"                      # UCG-Max 的 LAN 地址
BASE="https://${HOST}/proxy/network/integration"   # 方式 A：本地直连

# 方式 B：经 Cloud Connector（无需内网可达），把上面一行换成
# CONSOLE_ID="942A6FF664C0000000000964970E0000000009E710560000000068A9873F:9999999999"
# BASE="https://api.ui.com/v1/connector/consoles/${CONSOLE_ID}/proxy/network/integration"

HDR=(-H "X-API-Key: ${KEY}" -H "Accept: application/json")

# 1) 取站点：data[] 里每项是 { id, internalReference, name }
SITE_ID=$(curl -sS -k "${HDR[@]}" "${BASE}/v1/sites" \
  | jq -r '.data[0].id')
echo "siteId = ${SITE_ID}"

# 2) 列设备（默认 25 条/页，最多 200 条/页），并只取在线设备
curl -sS -k -G "${HDR[@]}" "${BASE}/v1/sites/${SITE_ID}/devices" \
  --data-urlencode "limit=50" \
  --data-urlencode "filter=state.eq('ONLINE')" \
  | jq '{total:.totalCount, shown:.count, devices:[.data[]|{name,model,state,ipAddress,firmwareVersion}]}'
```

响应外壳与字段含义：外层 `count`/`limit`/`offset`/`totalCount` 即上面说的分页统计；`data[]` 每一项是设备概览对象，包含 `id`（UUID）、`macAddress`（如 `94:2a:6f:26:c6:ca`）、`ipAddress`（如 `192.168.1.55`）、`name`（如 `IW HD`）、`model`（如 `UHDIW`）、`state`、`supported`（布尔）、`firmwareVersion`（如 `6.6.55`）、`firmwareUpdatable`（布尔）、`features`（枚举 `switching`/`accessPoint`/`gateway` 的数组）与 `interfaces`（枚举 `ports`/`radios` 的数组）。`state` 的取值是封闭枚举：`ONLINE`、`OFFLINE`、`PENDING_ADOPTION`、`UPDATING`、`GETTING_READY`、`ADOPTING`、`DELETING`、`CONNECTION_INTERRUPTED`、`ISOLATED`、`U5G_INCORRECT_TOPOLOGY`。若 UCG-Max 自身的设备对象出现在列表里，`features` 会含 `gateway`，这是判断「网关是否被该站点纳管」的直接依据。

示例里对本地直连加了 `-k`：UCG-Max 默认使用自签证书，curl 会因证书链不受信而失败。这是部署经验而非官方文档条文；若已导入受信证书，应去掉 `-k`。云端方式不需要 `-k`。

执行这套脚本时最容易踩的四个坑，基本都能从响应结构直接定位：一是 401 且 `code` 为 `api.authentication.missing-credentials`，说明头名或 key 有问题，而不是路径问题；二是 404 且路径里带着 24 位十六进制的站点 ID，说明把 Site Manager 的 `siteId` 拿来调 Network API 了；三是错误体提示表达式非法（一般落在 4xx），多半是 `filter` 里的字符串忘了单引号，或把 `state.eq('ONLINE')` 直接裸拼进 URL 后被 shell 吞掉了引号；四是本地直连时 curl 报证书错误，属于自签证书的预期行为，而不是 API 不可用。

如果只想先确认账号级视角是否正常，可以用 Site Manager 的一行命令做对照——它不碰本地控制台，也不依赖内网可达：

```bash
curl -sS -H "X-API-Key: ${KEY}" -H "Accept: application/json" \
  'https://api.ui.com/v1/hosts?pageSize=10' | jq '.data[]|{id,type,ipAddress,owner}'
```

返回体是 `data`/`httpStatusCode`/`traceId`/`nextToken` 四件套，`data[]` 每项是一个控制台对象，字段包括 `id`、`hardwareId`、`type`、`ipAddress`、`owner`、`isBlocked`、`registrationTime`、`latestBackupTime`、`reportedState` 等；其中 `type` 的示例值是 `ucore`。注意 `hostId` 与 `hostName` 是 `GET /v1/devices` 返回里的字段，不在 hosts 响应中，两者不要混用。它能验证 key 与云端链路是否正常，但验证不了 Network API 的 `/proxy/network/integration` 前缀有没有拼对——这两类问题必须分开排查。

### 3.6 已知限制

**速率限制。** 官方只在 Site Manager 文档中给出数值：Early Access（`/ea/...`）为 100 请求/分钟，v1 stable 为 10,000 请求/分钟，超限返回 429 与 `Retry-After`。**Network API 的官方文档未提供任何限流数值**，规范中也没有 429 定义；把它当作「没有限流」并不安全，工程上应按有上限处理（串行化写入、指数退避），否则批量建 SSID 或批量吊销凭证时可能被静默截断或偶发失败。

**经 Cloud Connector 的附加限制。** 如果走云侧代理而不是本地直连，官方对代理层还有一组独立限制：每台 console 限 100 请求/分钟；单个代理请求必须在 25 秒内完成，超时会被终止并返回超时错误；上游响应体上限 10 MB；被代理的 console 固件版本必须 ≥ 5.0.3；非组织级 API Key 只能访问 key 所有者自己的 console，组织级 key 才能访问组织内任意 console。这些限制写在 Site Manager 规范的 `*path` 操作说明里，与 Site Manager API 自身的 10,000 次/分钟是两套口径，排障时要分开看。

**分页上限。** Network 列表端点 `limit` 上限 200（hotspot 凭证 1000），默认 25，翻页靠 `offset` 累加，没有游标——这意味着在翻页期间集合若有增删，会出现漏项或重复，盘点类任务要么接受一次快照误差，要么用 `totalCount` 做交叉校验。Site Manager 单页最多 500 条、必须用 `nextToken` 续页，且不返回总数，因此无法预判要翻多少页，只能循环到响应不再返回 token 为止。

**密钥权限范围。** 官方只说明 key 绑定生成它的组织或 UI 账户，**未提供端点级权限细分清单**。但读写能力要分两套看：**Site Manager 的 API Key 目前是只读的**——Getting Started 原文为「The API key is currently read-only. When write endpoints become available, you'll be able to enable write access as needed, and the existing key won't be updated automatically.」；Network API 的 key 则在站点内可读可写。最小权限只能靠「一个用途一把 key、定期轮换」在流程上实现。这同时意味着审计只能追到 key 级别，追不到「这条防火墙策略是谁的 key 改的」。

**版本演进。** Site Manager 有独立的 Version Control 页：提供 `https://api.ui.com/v1/...` 与 `https://api.ui.com/ea/...` 两套版本；当某个 EA 端点晋升为正式版（`/ea/` → `/v1/`）时，既有 EA 集成「继续按原样工作，无需立即修改」；EA 期间 `response.data` 里的字段可能是可选的。官方同时声明以下变更属于向后兼容、可能不预先通知：新增资源或端点、新增可选请求参数、新增响应属性、调整响应属性顺序、改变不透明字符串（对象 ID、错误消息等）的长度或格式。Network API 没有单独的版本演进页，它用带版本号的 URL 固化版本，`llms.txt` 里列出 v11.0.97 以及 v10.6.109、v10.6.106、v10.6.102 等历史版本入口。实践含义是：不要对响应做严格字段校验或依赖字段顺序，也不要解析对象 ID 的内部结构。

**可达性。** Network API 要么能直连控制台的 LAN 地址，要么经 Site Manager 的 Cloud Connector 代理；`llms.txt` 的原话是 Network 与 Protect 的 API 各自跑在 UniFi 主机本地，通过 Cloud Connector 或现场直连访问。跨 NAT、无公网映射的部署只能走云端代理，此时多一层网络跳转，且 `consoleId` 的获取方式官方未给出步骤。

**规范与文档的四处不一致（已核实）。** 写集成时把下面几点当已知偏差，不必反复怀疑自己看错：一是认证头，`llms.txt` 与 Site Manager 的 curl 示例写 `X-API-KEY`，Site Manager 规范写 `X-API-Key`；二是 Network 规范完全没有 `securitySchemes`，无法据规范自动生成认证代码；三是 Site Manager 的 `pageSize` 在规范中是字符串类型且无上界，真实上限 500 只见于文档正文；四是 Network 规范只声明成功响应（200 与 201），既没有逐端点错误码，也没有 429 定义。

**官方配套工具。** 官方为 Network API 提供 Ansible Collection：`ubiquiti.unifi_api`，按 Quick Start 页的说明通过 `ansible-galaxy collection install ubiquiti-unifi_api-latest.tar.gz` 安装，Python 依赖为 `httpx` 与 `urllib3`，模块入口是 `ubiquiti.unifi_api.network`（另有 `ubiquiti.unifi_api.common`），但官方同时明确：该模块**不保证所有操作幂等**，对 create/update/delete 这类写操作，建议在 playbook 中自行做存在性检查，避免重复执行产生副作用。除此之外只提供 OpenAPI 规范与 Postman Collection（每个服务各一份）。**结论：官方没有提供 SDK**——在 developer.ui.com 的全站索引 `llms.txt` 中不存在任何 SDK 类页面，只列出 OpenAPI 规范、Postman Collection 与 Ansible 模块三类配套。因此「用某语言的官方 SDK」这条路不存在，只能基于 OpenAPI 生成客户端或直接发 HTTP 请求。

### 3.7 版本与访问日期

本章对应两个规范版本：**UniFi Network API v11.0.97**（OpenAPI 3.1.0，44 条路径 / 73 个操作，本地与 Cloud Connector 两个 server 定义）与 **UniFi Site Manager API v1.0.0**（OpenAPI 3.0.3，10 条路径 / 14 个操作，server 为 `https://api.ui.com`）。规范与文档页面访问日期：**2026-10-31**。

### 参考来源

- <https://developer.ui.com/llms.txt>（全站索引；认证头 `X-API-KEY`、Site Manager 基址、Network/Protect 本地与 Cloud Connector 说明）
- <https://developer.ui.com/network/v11.0.97/openapi.json>（Network API v11.0.97 规范：servers、44 条路径、分页与字段定义）
- [Network API Getting Started](https://developer.ui.com/network/v11.0.97/gettingstarted)、[Filtering](https://developer.ui.com/network/v11.0.97/filtering)、[Error Handling](https://developer.ui.com/network/v11.0.97/error-handling)
- <https://developer.ui.com/network/v11.0.97/quick_start.ansible>（Ansible Collection 安装与幂等性说明）
- <https://developer.ui.com/site-manager/v1.0.0/openapi.json>（Site Manager API v1.0.0 规范：securityScheme `X-API-Key`、分页参数、429 与 `Retry-After` 示例）
- [Site Manager API Getting Started](https://developer.ui.com/site-manager/v1.0.0/gettingstarted)、[Response Format](https://developer.ui.com/site-manager/v1.0.0/responseformat)、[Version Control](https://developer.ui.com/site-manager/v1.0.0/versioncontrol)

### 3.8 实机验证记录（2026-10-31）

本节是对上述文档结论的实测复核。测试对象是本机所在网络中一台在 UniFi Site Manager 里登记为 **Cloud Gateway Max** 的 console（下文以 `<console>` 代指其地址与标识，MAC、IP、站点/设备 UUID 均已隐去）。测试用 API Key 在 `https://unifi.ui.com/settings/api-keys` 创建（勾选 UniFi Applications → Network，站点范围 All Sites，有效期 1 个月），测试结束后已删除。

**① 对象确认。** Site Manager `GET /v1/hosts` 返回的 `reportedState.name` 为 `Cloud Gateway Max`，`userData.controllers` 含 `network / protect / access / talk / connect / innerspace`，与「五应用全支持」一致；随后 Network API 的 `GET /v1/info` 返回 `{"applicationVersion":"10.6.109"}`，设备列表里该机 `model` 为 `UCG Max`、`firmwareVersion` 为 `5.1.33`、`state` 为 `ONLINE`。

**② 云侧 key 不能直连本地，本地 key 可以。** 在 unifi.ui.com 创建的 key 请求 `https://<console>/proxy/network/integration/v1/sites` 返回 `401 {"error":{"code":401,"message":"Unauthorized"}}`；换成云侧 Cloud Connector 路径后立即成功，说明 **在 unifi.ui.com 创建的 key 是云侧凭据**。随后用 console 本机签发的 API Key 直连 `https://<console>/proxy/network/integration` 实测通过：`GET /v1/info` → 200 `{"applicationVersion":"10.6.109"}`，`GET /v1/sites` → 200 且返回与 Cloud Connector 一致的 UUID `88f7af54-…`。结论：**云侧与本地是两套独立凭据**，同一把 key 不能跨侧使用；两套入口指向同一份站点数据。

**③ Cloud Connector 路径可用。** 实测基址形如 `https://api.ui.com/v1/connector/consoles/{hostId}/proxy/network/integration`，其中 `hostId` 取自 `GET /v1/hosts` 的 `id`（形如 `6C63…:365382398`）。经此路径访问 `/v1/sites`、`/v1/sites/{siteId}/devices`、`/v1/sites/{siteId}/clients`、`/v1/sites/{siteId}/devices/{deviceId}/statistics/latest` 均返回 200。

**④ 两套站点 ID 确认不同。** Site Manager `GET /v1/sites` 返回 24 位十六进制 `siteId`（示例已隐去），Network API `/v1/sites` 返回 UUID 形式的 `id`（如 `88f7af54-…`），`name` 为 `Default`、`internalReference` 为 `default`。跨 API 混用会得到 404/参数错误。

**⑤ 筛选与分页行为符合规范。** `filter=state.eq('ONLINE')` 与 `filter=name.like('Cloud*')` 均正确过滤；`limit=500` 被服务端夹到 `200`（响应里 `limit` 回显 200），与文档「上限 200」一致；响应包裹字段为 `offset / limit / count / totalCount / data`。`clients` 端点同样返回该结构，`totalCount` 为 26。

**⑥ 错误体与文档一致。** 传入不存在的过滤函数会得到 400，字段与 3.4 节所述的 7 字段完全对应（值已脱敏）：

```json
{"statusCode":400,"statusName":"BAD_REQUEST","code":"api.request.invalid-filter",
 "message":"failed to parse filter expression: unknown filter function 'bogus' at 1:7",
 "timestamp":"2026-10-31T02:56:28.978857846Z",
 "requestPath":"/integration/v1/sites/<siteId>/devices","requestId":"<uuid>"}
```

**⑦ 统计端点可用。** `statistics/latest` 返回 `uptimeSec`、`lastHeartbeatAt`、`loadAverage1Min/5Min/15Min`、`cpuUtilizationPct`、`memoryUtilizationPct` 与 `uplink.{txRateBps,rxRateBps}`；示例值 CPU 36.2%、内存 69.6%。

**⑧ 限流不可从响应观测。** 响应头只有 `x-request-id`、`x-content-type-options`、`x-frame-options` 等，**没有** `x-ratelimit-*` 或 `retry-after`；这与「Network API 官方未公布限流数值、规范无 429」一致，实际阈值只能靠压测或官方补充。

**未验证项（如实记录）：** 429 触发与 `Retry-After` 行为；Cloud Connector 每 console 100 次/分钟的实际上限；本地 key 的权限范围（是否可写）与轮换策略；任何写操作（本次全部只读）。

## 四、参考资料

以下链接均为官方来源，访问日期 2026-10-31。各章末尾另附该章自己的来源编号（与本章行文中的 `[N]` 对应），本节是全报告的统一链接清单。

### 4.1 产品、规格与商店

1. Tech Specs — UniFi Cloud Gateway Max（UCG-Max）：<https://techspecs.ui.com/unifi/cloud-gateways/ucg-max>
2. Tech Specs — Cloud Gateways 分类页（子类与在售型号清单）：<https://techspecs.ui.com/unifi/cloud-gateways>
3. Tech Specs — UCG-Ultra：<https://techspecs.ui.com/unifi/cloud-gateways/ucg-ultra>
4. Tech Specs — UCG-Fiber：<https://techspecs.ui.com/unifi/cloud-gateways/ucg-fiber>
5. Tech Specs — UDM-Pro-Max：<https://techspecs.ui.com/unifi/cloud-gateways/udm-pro-max>
6. Tech Specs — UDR7：<https://techspecs.ui.com/unifi/cloud-gateways/udr7>
7. UniFi Cloud Gateways 产品族页（含 UniFi OS 与五个应用的官方说明、Zone-Based Firewall、Site Magic、Traffic Flow Engine）：<https://ui.com/us/en/cloud-gateways>
8. Store（US）— Cloud Gateway Max：<https://store.ui.com/us/en/products/ucg-max>
9. Store（US）— Cloud Gateway Max NS：<https://store.ui.com/us/en/products/ucg-max-ns>
10. Store（US）— M.2 SSD Tray（UACC-SSD-Tray）：<https://store.ui.com/us/en/products/uacc-ssd-tray>
11. Store（EU）— Cloud Gateway Max：<https://eu.store.ui.com/eu/en/products/ucg-max>
12. Store（UK）— Cloud Gateway Max：<https://uk.store.ui.com/uk/en/products/ucg-max>

### 4.2 开发者文档与规范

13. Developer 文档索引（认证头、各 API 基址、OpenAPI/Postman 清单）：<https://developer.ui.com/llms.txt>
14. Network API v11.0.97 — Getting Started：<https://developer.ui.com/network/v11.0.97/gettingstarted>
15. Network API v11.0.97 — Filtering：<https://developer.ui.com/network/v11.0.97/filtering>
16. Network API v11.0.97 — Error Handling：<https://developer.ui.com/network/v11.0.97/error-handling>
17. Network API v11.0.97 — Ansible Quick Start：<https://developer.ui.com/network/v11.0.97/quick_start.ansible>
18. Network API v11.0.97 — OpenAPI 规范：<https://developer.ui.com/network/v11.0.97/openapi.json>
19. Network API v11.0.97 — Postman Collection：<https://developer.ui.com/network/v11.0.97/postman-collection.json>
20. Site Manager API v1.0.0 — Getting Started（API Key、限流）：<https://developer.ui.com/site-manager/v1.0.0/gettingstarted>
21. Site Manager API v1.0.0 — Response Format（统一响应、错误码、分页）：<https://developer.ui.com/site-manager/v1.0.0/responseformat>
22. Site Manager API v1.0.0 — Version Control（/v1 与 /ea 的兼容策略）：<https://developer.ui.com/site-manager/v1.0.0/versioncontrol>
23. Site Manager API v1.0.0 — List Hosts：<https://developer.ui.com/site-manager/v1.0.0/listhosts>
24. Site Manager API v1.0.0 — List Devices：<https://developer.ui.com/site-manager/v1.0.0/listdevices>
25. Site Manager API v1.0.0 — OpenAPI 规范（含 Cloud Connector 的限流与超时说明）：<https://developer.ui.com/site-manager/v1.0.0/openapi.json>

### 4.3 二次核对与修正记录

本轮在草稿完成后做了一次全量回查，合并前的 5 处修正/澄清（独立核对代理随后又发现 9 处问题，处置见 4.5）：

| # | 位置 | 原表述 | 核对结果与修正 |
|---|---|---|---|
| 1 | 第 3 章 Ansible 段 | 「官方声明所有操作具备幂等性」 | **错误**。官方 Ansible 页原文为「the `ubiquiti.unifi_api.network` module does not guarantee idempotency for all operations」，并建议对写操作自行检查。已改为「不保证所有操作幂等」。 |
| 2 | 第 3 章 Cloud Connector 基址 | 只给 `/proxy/network/integration` 一种写法 | 官方两份资料不一致：Site Manager 规范的 path 示例含 `proxy`，同页正文示例不含；Network 规范的 server 含 `proxy`。已补注差异与取舍。 |
| 3 | 第 3 章 filter 示例 | 初核时曾怀疑 `state.eq`、`firmwareVersion.lt` 无官方依据 | **误判已撤回**。设备端点在 OpenAPI 的 operation description 里有「Filterable properties」表，明确 `name` 支持 `like`、`state` 支持 `eq`、`firmwareVersion` 支持 `lt`，示例成立。 |
| 4 | 第 2 章控制流 | 分页参数引用标注为 [7]（gettingstarted） | 分页（`pageSize` 上限 500、`nextToken`）实际写在 Response Format 页，已改为 [11]。 |
| 5 | 第 3 章限制清单 | 未单列 Cloud Connector 的代理层限制 | 已补：每 console 100 请求/分钟、单请求 25 秒、响应体 10 MB、console 固件 ≥ 5.0.3、非组织 key 仅限本账号 console。 |

### 4.4 关键结论核验清单

| 结论 | 依据 | 状态 |
|---|---|---|
| 型号 `UCG-Max`、产品名 Cloud Gateway Max、厂商 Ubiquiti | techspecs product 字段、store SKU | 已核验 |
| 5×2.5GbE、默认 WAN 1、最大 WAN 4、MAC 表 2000 | techspecs Overview / Networking | 已核验 |
| IDS/IPS 2.3 Gbps，需 UniFi OS 4.1+ | techspecs 注记 | 已核验 |
| 四核 A53 1.5GHz、3GB 内存、16.1W、USB-C 5V/5A | techspecs Hardware | 已核验 |
| NVR 存储可选 NVMe ≤2TB；无盘版不含 SSD，第三方盘需托盘 | techspecs 注记 + store 配件页 | 已核验 |
| 五个应用均支持 | techspecs Application Suite 五项 True | 已核验 |
| 纯有线、无 Wi-Fi 射频 | techspecs 端口/射频项为空、wifi-integrated 子类另列 | 已核验 |
| US SKU：UCG-Max $279 在售、UCG-Max-NS $199 缺货（补货 2026-11-06） | store JSON `status`/`restockEtaAt` | 已核验（时点数据） |
| EU €250/€179、UK £220/£159 | eu./uk.store JSON | 已核验（时点数据） |
| API Key 认证、`X-API-Key`/`X-API-KEY` 两种写法 | llms.txt + Site Manager securityScheme | 已核验 |
| Network API 44 路径 / 73 操作；Site Manager 10 路径 / 14 操作 | 两份 OpenAPI 脚本统计 | 已核验 |
| Network 分页 22/23 端点默认 25 上限 200，vouchers 为 100/1000 | OpenAPI 参数统计 | 已核验 |
| Site Manager 分页 pageSize ≤500 + nextToken | Response Format 页 | 已核验 |
| 错误体结构（Network 7 字段 / Site Manager 4 字段） | Error Handling 页 + 规范示例 | 已核验 |
| 限流：SM v1 10,000/min、EA 100/min；Connector 100/min/console | Getting Started + 规范描述 | 已核验 |
| Cloud Connector：25s、10MB、固件≥5.0.3、key 作用域 | SM OpenAPI `*path` 描述 | 已核验 |
| 官方无 SDK，仅有 OpenAPI/Postman/Ansible | llms.txt 索引 + Ansible 页 | 已核验（索引无 SDK 条目） |


### 4.5 交叉核对（checker 代理）问题与处置

独立核对代理对 01–03 三章逐条回查后列出 9 个问题（2 个高严重度：照抄会直接失败），本报告已全部处置：

| # | 原问题 | 严重度 | 处置 |
|---|---|---|---|
| 1 | 把 `vpn/servers` 列入 Network API 可写对象 | 高 | 已改：该资源只有 `GET`，明确标为只读 |
| 2 | WiFi 广播创建必填项写成 10 个 | 高 | 已改：规范 `required` 为 13 项，逐项列全 |
| 3 | 01 把「机身无 Wi-Fi 射频」写成事实、02 却标为归纳 | 中 | 已统一：两处都表述为「官方未直接声明，依据规格归纳」 |
| 4 | 「同一把 key 可读可写」 | 中 | 已拆分：Site Manager key 目前只读（原文引用）；Network key 可读写 |
| 5 | 称官方能力只有两个 API | 中 | 已改：`llms.txt` 共 6 个 API，本章只覆盖两套，点明 Protect v7.3.74 |
| 6 | 「两页规格逐项相同」 | 低 | 已改：仅认证清单不同（Anatel 编号） |
| 7 | 候选表漏 UDM-Pro-Max / Beast 的内置 128 GB SSD | 低 | 已补 |
| 8 | 用 techspecs 支持 `UCG-Max-US` | 低 | 已改：该后缀只在商店 `defaultSku` |
| 9 | 「请求体只需一个字段」与官方原话冲突 | 低 | 已改为「必填字段只有 action，另需带适用输入参数」 |

核对代理同时确认了 12 条关键结论成立（含 44 路径/73 操作、22/23 分页默认值与上限、Site Manager 10 路径/14 操作、7/4 字段错误体、限流数值、全部价格与补货日期、三条 filter 示例），这些结论在本报告中保留原样。
