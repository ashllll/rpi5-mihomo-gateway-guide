> **外部项目研究，非本仓库交付。** 本文是一次候选路线调研与验收计划，描述 Landscape Router 等外部项目，不代表本仓库已实现的功能、当前配置或验收结论。本机现状见 [架构](../architecture.zh-CN.md) 与 [部署](../deployment.zh-CN.md)。

# Landscape + Mihomo 分阶段可行性验证记录

日期：2026-10-14。结论：**Mihomo 管理接口与 DNS/fake-IP 的本机隔离检查通过；Landscape 接入仍有明确门禁，尚不能宣布原规则全量等价或切换现网。**

本轮遵循“原有 Mihomo 规则全量迁移保留”：不修改生产配置、不删除规则、不替换 DNS 模式。当前仓库是发布目录，真实订阅、节点、能力池和现网配置不在其中。实验全部使用合成数据，不能代表用户实际规则已迁移。

## 1. 固定基线与证据层

| 项目 | 本次依据 |
|---|---|
| 仓库基线 | main，`045d0d0eabc4e3c6ef130b746aa86b44049ce8e2`；开始时 README 与两个 Landscape 文档已有未提交内容，保存前态后保留 |
| Landscape | v0.26.3，`34845f91ed18dab5c9851e71538548c35eb2efcd`；固定本地源码只读审计，未启动 |
| Mihomo | v1.19.32，Darwin ARM64，Go 1.26.8；本机真实核心、合成规则与本地 DNS/HTTP 应答端 |
| 发行包校验 | `mihomo-darwin-arm64-v1.19.32.gz` SHA256 `3312a6780652c622890fd4357c6a853bbf865464fd047ac7b7f52dab8de18652`，与 GitHub Release API asset digest 一致 |
| 解压程序校验 | SHA256 `94a386ec0149080deadd86b1f667363bde3c70f7489dba3258e52c56fc9a6d66` |
| 运行限制 | 所有试验监听使用 loopback 和动态高端口；TUN 关闭；不改系统 DNS/路由，不取真实订阅，不启生产 timer；正常退出、Python 异常及已处理的 SIGTERM 会停止子进程并清理临时配置，SIGKILL/断电不保证清理 |
| 缺少的环境 | 当前 PATH 未找到 Docker/Podman/Colima/Orb/Lima/QEMU；本轮没有可用的隔离 Linux 运行环境；尚未取得现网配置或确认树莓派连接目标 |

发行依据：[固定版 Release API](https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/v1.19.32)、[发行页面](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.32)。二进制校验只证明本次程序来源，不证明目标机版本相同。

## 2. 已执行：本项目原有机制回归

以下脚本本轮均以退出码 0 完成。它们测试当前发布源码和匿名夹具，不是 Landscape 的集成测试。

| 检查 | 实测结果 | 证据范围 |
|---|---|---|
| 旧生成器回归（工具已删除） | 通过 | 私有目录生成、参数传播、拒绝不安全站点 |
| `tools/check_overlay.py` | 通过 | 业务池顺序、备用池、幂等、空能力清单拒绝 |
| `tools/check_subscription_candidate.py` | 通过 | 订阅候选、引用重命名、缓存与失败保护 |
| `tools/check_subscription_update.py` | 16 场景通过 | 更新/重载/中断/回滚模拟 |
| `tools/check_update_rules.py` | 8 场景通过 | 规则更新事务与回滚模拟 |
| `tools/check_automation.py` | 53 文件检查通过 | 语法、参数、单元环境、临时根目录安装 |
| 旧安装器事务模拟（工具已删除） | 33 测试通过 | 原安装器事务模拟；不证明 Landscape 回滚 |

## 3. 已执行：Mihomo 一侧真实进程隔离验证

可复跑入口：[check_landscape_mihomo.py](../../tools/check_landscape_mihomo.py)。显式传入已核验的 Mihomo 程序：

```sh
python3 -B tools/check_landscape_mihomo.py --mihomo /path/to/verified/mihomo
```

脚本只使用标准库。合成规则包含 DOMAIN、RULE-SET、MATCH，包含本地文件 provider 和带空格的 Selector 名称。请求经显式 SOCKS 进入测试核心，仅用于验证 fake-IP 反向恢复与规则执行，**不能当作 TProxy 透明入站证据**。

| 编号 | 验证内容 | 结果 |
|---|---|---|
| M01 | 实际核心启动，运行配置确认 TUN 关闭 | 通过 |
| M02 | 错误控制密钥被 HTTP 401 拒绝 | 通过 |
| M03 | 合成规则类型顺序、策略目标及规则集实际条数符合预期 | 通过 |
| M04 | Selector 写入 REJECT 后回读一致 | 通过 |
| M05 | 本地 provider 内容变更，更新接口后读取到新节点名称 | 通过 |
| M06 | UDP/TCP DNS 返回同一 fake-IP | 通过 |
| M07 | 正常退出后重启，Selector 与两个 fake-IP 映射保留；倒序查询避免首地址重新分配的假通过 | 通过 |
| M08 | SOCKS 请求只携带 fake-IP，核心恢复域名并命中 DOMAIN；连接接口和本地 HTTP 应答共同验证 | 通过 |
| M09 | 另一 fake-IP 恢复域名并命中 RULE-SET，选中预期策略组并得到本地 HTTP 应答 | 通过 |
| M10 | 非法规则重载被 HTTP 400 拒绝，原规则数与 DNS 映射仍可查询 | 通过 |
| M11 | 临时运行日志级别修改、回读、恢复与回读 | 通过 |

**实验中发现并修正的夹具问题：** 规则集最初使用 JSON 文本并声明 YAML 格式，v1.19.32 的规则集解析报告缺少 `payload`；主进程与 API 仍正常，规则集连接落入 MATCH/REJECT。改为明确的 YAML `payload` 后，规则集数量与真实命中均通过。这个现象说明“API 可访问、主 YAML 加载成功、provider 名称存在”不足以证明规则集加载完成。未因此修改任何生产规则或上游核心。

以上不覆盖：实际节点测速/连通、远端订阅、所有规则类型、Landscape 管理接口、统一管理模块、Linux UDP/TProxy、IPv6、断电持久化、系统恢复。

附加工具恢复检查：审查发现原验证脚本收到 SIGTERM 时未触发清理。隔离复现得到父进程退出 `-15`、子进程仍运行、临时目录仍存在；添加可捕获终止信号处理后，复测退出 `143`、子进程已退出、临时目录已删除。这是验收工具的终止清理，不能计作网关回滚通过。SIGKILL/断电后的清理不在保证范围。

## 4. 源码验证发现：影响方案选择的门禁

### F01：Landscape DNS 前置不能默认等价

固定版本 `start_flow_dns_listener` 只创建 UDP socket，并可选启动 DoH。普通 TCP DNS 监听未在该生产路径中发现。因此优先验证 Mihomo 直接承担原 DNS 入口的部署方式；若必须由 Landscape 前置，先验证 TCP53、大响应 UDP 截断后的 TCP 回退，不能把 DoH 的 TCP listener 当普通 DNS/TCP。

此外，Landscape 的解析链顺序为 Redirect → Local → Cache → Upstream。仅配置 Mihomo 为上游，不能保证原 hosts、内网域名和 DNS 策略全部执行；缓存也不是共享映射。IP 校验默认关闭，固定版 IPv4 helper 未过滤常见 `198.18.0.0/15`，所以不能声称 fake-IP 天然不兼容；启用校验时的私网答案和 IPv6 ULA 必须另测。

来源：[生产 DNS listener](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-dns/src/listener.rs#L153)、[解析链](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-dns/src/server/chain.rs#L160)、[IP 校验](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-dns/src/server/rule.rs#L142)。

### F02：LAN 路由与缓存可能先于 Mihomo 处理

LAN 入口先查路由缓存、做 LAN 重定向，再判断 Flow 与出口。若原 Mihomo 规则包含内网拒绝、跨网段代理或本机目标，原样复制规则仍可能失去命中机会。需要对照真实基线逐类证明行为；不能把内网绕行自动认作允许例外。旧连接、热缓存与新连接必须分别测。

来源：[LAN 入口顺序](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-ebpf/src/bpf/tc_chain/tc_lan_ingress_intro.bpf.c#L168)。

### F03：TProxy 五元组具备接口基础，协议范围仍需实测

`handle_pkg` 使用原五元组查 socket，仅在查找监听 socket 时替换查找结构的目的地址/端口，本函数没有改写报文 IP/端口。这支持“接入段设计上保留原地址”的判断，不能证明完整容器链路和 Mihomo 元数据保真。TCP/UDP 双栈需在 Linux 实测。

ICMP/ICMPv6 默认丢弃；开启 passthrough 后走内核路径，需要配套路由/转发。PMTU、大包、UDP 分片与回程必须测试，不能用网页访问代替。

来源：[TProxy socket 分配](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-ebpf/src/bpf/tproxy.bpf.c#L29)、[协议入口](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-ebpf/src/bpf/tproxy.bpf.c#L117)。

### F04：注册日志成功不等于出口可用

relay 启动时选第一个有 peer 的非 lo 接口，只采集一次 ifindex；之后每 60 秒发送同一注册信息，没有服务端确认读回。热换接口后不能假定自动重新发现。应保持试验容器接口明确，重建/热换后验证新 ifindex 和实际流量；正常 stop、异常退出、强杀要分别验收。

来源：[接口选择和注册循环](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape-ebpf/src/bin/redirect_pkg_handler.rs#L167)、[服务端注册与事件](https://github.com/ThisSeanZhang/landscape/blob/34845f91ed18dab5c9851e71538548c35eb2efcd/landscape/src/docker/mod.rs#L386)。

## 5. 下一阶段顺序、通过标准与回滚

本轮是在不接管网络的前提下提前验证独立接口；不表示跳过 [G0–G8 清单](landscape-acceptance.zh-CN.md) 的部署门禁。

| 阶段 | 当前状态 | 必须交付的证据 | 失败/恢复 |
|---|---|---|---|
| P0 原配置基线 | 阻塞：缺现网有效配置/依赖及连接目标 | 规则顺序、引用、provider 文件摘要、规则集条数、DNS 模式、原命中/出口；识别元数据依赖 | 只读，不修改；按 R0 保存证据 |
| P1 本机接口 | 11 项通过，合成数据限定 | M01–M11 真实进程输出 | 停本次核心，清理临时目录；不涉及系统恢复 |
| P2 隔离 Linux 接入 | 阻塞：缺隔离 Linux 与恢复入口 | 固定版本 Landscape/relay/Mihomo；先演练 G2，再测五元组、TCP/UDP、DNS TCP、fake-IP、ICMP/MTU | R1/R2；生产接口不接入 |
| P3 原规则行为等价 | 待 P0/P2 | 全量静态差异＋规则族与优先级正反例；实际命中/组/出口一致；内网与缓存单列 | R3；不删规则、不放宽失败策略 |
| P4 管理与自动化 | 一侧接口具备；适配未实现 | 双方版本/鉴权读回，单写入者、并发锁、坏候选恢复、原评分/订阅行为 | R3；分别恢复配置与状态 |
| P5 故障、重启、回退 | 待 P2–P4 | 容器重建/kill、DNS 故障、节点故障、启动顺序、备用盘启动、RTO | R2/R4/R5；恢复须客户端证明 |
| P6 单客户端与扩大范围 | 未开始 | 对照业务与性能、长时间观察；G7 通过且具有现网切换授权 | 按批撤回；整机共享资源故障整机恢复 |

P0 输入应保存在仓库外：有效配置与加载来源、rule/proxy provider 数据、实际 DNS 链路、能力清单、selector 状态、自动化状态，以及设备/接口/内核的只读盘点。无需把订阅 URL、节点凭据或管理密钥贴到对话或发布目录。

本轮未连接树莓派、未安装 Landscape、未修改系统 DNS/路由、未执行故障注入或物理恢复、未提交或推送。

交付检查：发布结构与隐私检查通过（101 文件），隐私检查器回归通过；新增文件语法、末尾换行与空白检查通过。原 README 与能力报告的开始时内容保持一致。
