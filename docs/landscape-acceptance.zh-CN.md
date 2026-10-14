# Landscape + Mihomo 融合：逐步验收与回滚清单

编制日期：2026-10-13；修订日期：2026-10-14。状态：**尚未实施融合，G0–G8 整体门禁未通过**。
已完成的仓库回归与 Mihomo 本机隔离检查见[分阶段可行性验证记录](landscape-feasibility.zh-CN.md)；它们不替代下表的 Linux/树莓派透明转发和恢复验收。
本清单覆盖已识别的兼容、转发、策略、故障、恢复和发布风险；执行中出现的新问题必须追加到问题台账，不能把清单当作不存在未知问题的证明。

## 1. 适用边界与当前缺口

目标分工：Landscape 管理试验网络的接口、DHCP、NAT、Flow 与相应访问控制；Mihomo 在独立容器中通过 TProxy 接收流量，继续管理原有订阅、业务规则、分组、节点和代理 DNS。DNS 入口与 MosDNS 的位置须按原方案行为验证，不能默认交给 Landscape。
**原有 Mihomo 规则及依赖全量保留，包括顺序、rule-providers、代理组、DNS/fake-IP、hosts、嗅探与自动化。** 保持原 DNS 模式，不以改成真实 IP、删减规则或提前直连来满足迁移。真实 IP 试验只能作为独立诊断用例，不能替代原模式验收；接入元数据变化造成规则失效时阻塞迁移。

| 当前入口 | 已有能力 | 本次不能直接继承的结论 |
|---|---|---|
| `setup.sh` / `gateway/setup.py` | 独立 Mihomo TUN、dnsmasq、nftables、备份与恢复计时器 | 不包含 Landscape、容器、eBPF 或其数据库的安装与回滚 |
| `automation/` | 生产订阅更新、overlay、评分、隔离、MosDNS/DNS 自愈 | 路径、服务名、API、DNS 模式和锁需适配；不能原样启用所有 timer |
| 恢复镜像 | 无节点系统底包与写盘校验 | 不是现网完整备份，不包含个人配置和容器数据；实机启动仍待验收 |

依据：[当前安装器](../gateway/setup.py)、[一键安装与回滚](one-click-setup.zh-CN.md)、
[生产自动化](../automation/README.zh-CN.md)、[恢复镜像边界](recovery-image.zh-CN.md)。

**接口接管前必须实现并在备用环境演练恢复入口、独立 watchdog 和挂载归属清单。文档本身不是这些工具。**
`setup.py --rollback latest` 只还原原安装器自己的网络配置事务，按[一键安装与回滚](one-click-setup.zh-CN.md)的口径还会保留已安装的软件包；它不覆盖 Landscape 的二进制、数据库、容器与挂载，也不卸载已附加的 eBPF/TC 程序或归还被接管的接口，不能当作 Landscape 恢复命令。

## 2. 执行规则与参数

- 按 G0 → G1 → G2 → G3 → G4 → G5 → G6 → G7 → G8 执行；上一阶段全部必测项通过才进入下一阶段。
- 勾选框仅在附有证据后勾选；状态使用 `待测 / 通过 / 失败 / 阻塞 / 不适用`，不以服务 active 代替业务通过。
- 不适用必须写明拓扑或功能原因。管理恢复、DNS、透明 TCP/UDP、规则正确性、防泄漏、回滚与重启不能整体跳过。
- 每次只改变一个职责或参数；每个变更有唯一事务 ID、前后清单和恢复点。失败先回退，保存脱敏证据后修复重测。
- 优先使用独立测试机/备用系统介质与独立网络。**在现网网关上只选一个客户端，不能隔离主机 DNS、WAN、默认路由或 eBPF 的副作用。**
- 网络命名空间只验证部分规则；VM 不能替代树莓派驱动/XDP/BPF、接口独占及物理启动验收。
- 不盲目卸载 NetworkManager，不全局 `flush ruleset`，不批量卸载未知 BPF，不清空 Docker 网络/卷，不覆盖原盘来演练恢复。
- 故障注入限试验网络；现网切换、重启、断电、拔线、写盘和发布属于后续实施阶段，本清单不表示已经执行或获得这些操作的授权。

### 2.1 开始前冻结的参数

真实值保存于仓库外的私有验收记录。以下建议值只是试验起点，需按基线和业务要求填写后冻结，不是产品固定参数。

| 参数 | 记录内容 / 建议起点 |
|---|---|
| `BASELINE_ID` / `CHANGE_ID` | 原方案快照 ID / 本次唯一事务 ID；不能只记录随重试变化的 `latest` |
| `TEST_HOST` / `TEST_CLIENT` | 试验主机、试点客户端、管理终端；稳定身份与实际接口映射 |
| `WAN_IF` / `LAN_IF` / `MGMT_IF` | 现场探测的接口和管理路径；明确管理接口不被接管 |
| `TEST_CIDR` / `DHCP_POOL` | 不与生产、Docker、VPN、管理网段及全部路由表重叠 |
| `VERSIONS` | 系统、内核、固件/驱动、Docker、Landscape 后端/前端/relay、Mihomo 的版本/源码提交/摘要 |
| `RECOVERY_ENTRY` / `RECOVERY_MEDIA` | 实际可离线运行的恢复入口、备用介质身份与启动记录 |
| `RTO` / `BOOT_BUDGET` | 最长恢复时间 / 开机恢复预算；可先以各 5 分钟为试验目标，实测后调整 |
| `BASELINE_WINDOW` / `SOAK_WINDOW` | 同条件基线采样 / 稳定观察；可从 30 分钟 / 24 小时起步，覆盖 timer、租约更新和晚高峰 |
| `PERFORMANCE_BUDGET` | 直连/代理分别填写吞吐下限、p95 延迟/抖动、丢包、CPU/内存/温度/磁盘余量；不能只写不卡 |
| `FAILURE_POLICY` | 各业务失败时 `阻断 / 已批准备用代理 / 允许直连`；默认不为必须代理业务增加 DIRECT 回退 |
| `DNS_POLICY` / `IPV6_POLICY` | DNS 上游/出口/缓存/失败动作；首版 IPv4-only 时写明 RA、DHCPv6、AAAA 和转发边界 |
| `PROBE_SET` | 受控直连/代理 TCP、UDP、DNS、长连接和真实业务目标；含预期出口与节点能力，控制探测负载 |
| `RELAY_PARAMS` | 出口容器实测的 relay 程序路径与注册 socket、worker 程序路径与启动脚本、`LAND_PROXY_HANDLE_MODE`、route mark/表号、worker 监听端口、ICMP mark；按固定版本核对上游默认值后填写，不作为本项目常量 |

缺恢复入口、RTO、失败策略或预期出口时判定阻塞。不能看到结果后放宽阈值来通过。

### 2.2 每项证据最少包含

`用例 ID、时间、主机/客户端、版本/配置摘要、变更前状态、动作、预期、实际、测量值、证据路径、回滚 ID、恢复耗时、状态`。
原始日志、抓包、API 数据、备份与实际地址留在本地私有目录；公开文档只写匿名结果。
抓包限制接口、过滤条件、包数/时间，避免收集无关设备或正文；订阅、认证头、节点名、token 不进入共享输出。

### 2.3 只读预检命令（不改状态）

本节供 G0/G1/G2 的预检条目取用，只读取状态。需要 root 的加 `sudo`，但不得顺手执行 `nft flush ruleset`、`iptables -F`、`systemctl stop/disable`、`nmcli con up/down`、`ip link set`、`bpftool prog unload` 等任何写操作。证据是命令加原始输出（按 §2.2 脱敏），只看退出码不算证据；`<...>` 用 §2.1 冻结后的真实值替换。

```bash
# 仓库身份（G0-01）
git rev-parse --show-toplevel; git rev-parse --abbrev-ref HEAD
git remote -v; git status --porcelain=v1; git rev-parse HEAD

# 平台、内核与 BPF 能力（G1-01）
uname -m; uname -r; nproc; free -m; timedatectl
ls -l /sys/kernel/btf/vmlinux
mount | grep -w bpf
zgrep -E 'CONFIG_(BPF|BPF_SYSCALL|BPF_JIT|CGROUP_BPF|NET_CLS_BPF|NET_ACT_BPF|LWTUNNEL_BPF|BPF_EVENTS)=' /proc/config.gz
# 没有 /proc/config.gz 时改读 /boot/config-<内核版本>
systemctl show <候选服务名> -p LimitMEMLOCK -p LimitNOFILE

# 现有网络与数据平面对象（G1-02/G1-04/G3-01，只列不改）
ip -br link; ip -br addr; ip route show table all; ip rule show
ip -6 addr show; ip -6 route show; sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
bpftool -f prog show                     # 程序 id/name/tag/type，-f 显示已有 pin 路径
bpftool -f map show                      # map id/name/type 与已有 pin 路径
bpftool -f link show                     # link 挂载及已有 pin 路径，关联到程序 id
nft list ruleset
ss -lntup

# TC/XDP 归属：对 §2.1 冻结的每个受管接口（被接管接口，不含 MGMT_IF）逐个执行，入向/出向分别记录
for i in <受管接口...>; do
  ip -d link show dev "$i"                 # 接口 kind/模式、veth 对端等归属细节
  tc qdisc show dev "$i"                   # qdisc 种类与 handle（clsact/ingress 等）
  tc filter show dev "$i" ingress          # 入向 filter：pref/protocol/动作
  tc filter show dev "$i" egress           # 出向 filter：pref/protocol/动作
  bpftool net show dev "$i"                # 该接口 XDP/TC 挂载点、程序 id 与 XDP 模式
done
tc qdisc show; tc filter show             # 全局兜底视图，不能替代上面的逐接口记录

# resolv.conf 归属与原始形态（G2 / R2 恢复依据）
ls -ld /etc/resolv.conf                   # 普通文件还是符号链接
stat /etc/resolv.conf                     # 类型、大小、属主、权限、时间戳
if [ -L /etc/resolv.conf ]; then
  readlink /etc/resolv.conf               # 原始链接目标；读取失败须记录为阻塞
elif [ -f /etc/resolv.conf ]; then
  printf '%s\n' '普通文件，无原始链接目标'
else
  printf '%s\n' 'resolver 缺失或类型异常：阻塞' >&2
fi
readlink -f /etc/resolv.conf              # 解析后的最终目标
cat /etc/resolv.conf                      # 当前生效内容
systemctl is-enabled systemd-resolved; systemctl is-active systemd-resolved
nmcli -t dev status

# 容器、挂载与权限归属（G1-06/G4-01）
docker ps --format '{{.Names}}\t{{.Image}}'
docker inspect --format '{{.HostConfig.Privileged}} {{.HostConfig.CapAdd}} {{.HostConfig.Sysctls}}' <容器名>
docker inspect --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ro={{not .RW}}{{end}}' <容器名>
docker volume ls; docker network ls

# 服务、定时器与日志预算（G5-12/G1-06）
systemctl list-unit-files --state=enabled
systemctl list-timers --all
df -h; df -i; journalctl --disk-usage
```

预检输出只说明当前状态，不构成任何验收通过；`ss -lntup` 在 53 端口有其它 DNS 服务时即判定 G2 阻塞。

逐接口记录必须能支撑 R2 的归属恢复：接口名与 kind、qdisc handle、滤器的方向/`pref`/`protocol`/动作、挂载的 BPF 程序 `id`/`name`/`tag` 与 XDP 模式（native/generic/offload）、已有 `pinned` 路径（通过带 `-f` 的 prog/map/link 视图关联）。没有 pin 的对象明确记为无；程序未直接 pin 时还需核对关联 link，不能把缺少输出当作没有挂载。不带接口名的 `tc filter show` 无法说明接口和方向，不能单独作为恢复依据。

`readlink -f` 只给出解析后的最终目标，无法区分「本来就是普通文件」和「本来是指向该文件的符号链接」，也不保留原始链接目标；因此 `/etc/resolv.conf` 要同时留证原始类型、原始链接目标（若有）、解析后目标、内容与权限，缺任一项不满足 §3 的「原文件或符号链接、目标、权限」恢复要求。

命令读取失败必须记录原始错误并阻塞对应门禁，不能用兜底成功消息掩盖权限不足、目标缺失或工具错误。

预检缺少 `bpftool`/`tc`/`ip`，或内核对某项不支持时，在问题台账中记为阻塞；确属计划内不适用（例如该接口本就不挂 XDP、无 clsact）时写明理由与判断依据，不能留空或凭猜。需要时改用等价只读命令留证；不得在预检阶段顺带安装软件、加载模块或改动任何状态。

## 3. 统一回滚流程

阶段表中的 R0～R5 引用以下流程。执行前落成针对目标机的可运行恢复入口，并在独立控制台保存副本。
恢复必须固定到对应快照；成功需真实下游客户端证明，不能只比较文件。

| 编号 | 触发范围 | 操作顺序与恢复判据 |
|---|---|---|
| R0 | 只读预检/离线生成 | 终止本次操作、保存证据，只清理本事务临时目录；网络状态与基线无差异 |
| R1 | 独立试验主机/容器/网络 | 退出故障注入，停止本次写入者/重启策略，恢复试验快照；只移除本事务容器/挂载/规则；验证生产无变化、试验基线可复现 |
| R2 | 接管接口、DNS 或数据平面 | 冻结更新/timer/自动重启/API 写入 → 将试点客户端撤回旧网关并保持失败策略 → 停新方案 → 按归属清单清除本次 TC/XDP/BPF、策略路由、veth/bridge、规则和监听 → 恢复原管理器、地址/路由/sysctl、防火墙、DNS、服务/timer → 验证旧方案。禁止新旧 DHCP/DNS 争用同一链路 |
| R3 | 策略/订阅/规则/自动化 | 停本事务写入者，恢复上一份完整配置、provider/规则、评分/冷却/池状态，语法检查、重载并验证业务；失败升级 R2。不能只恢复主 YAML 留下新缓存/池状态 |
| R4 | 系统不可达/开机失败/恢复失败 | 用独立管理链路/本地控制台执行 R2；仍失败则关机切回已验证原系统/备用介质；核对启动盘、管理与旧网络。保留失败盘，禁止覆盖原盘 |
| R5 | 程序/容器/数据库升级 | 停新版本及写入者，离线恢复成套旧后端、前端、relay、Mihomo、容器摘要、数据库/卷/配置、服务定义；验证旧版重启和业务。旧版不能读数据库时不能硬启动，升级 R4 |

R2 特别要求：

- 恢复 `/etc/resolv.conf` 的原文件或符号链接、目标、权限和管理器关系；只把内容改成 DNS 地址不算恢复。
- BPF/qdisc/filter 以接口稳定身份、方向、程序 ID、attach 模式、handle/priority、pin 路径和事务归属定位；保留原程序/qdisc。未知归属停止自动清理，转控制台恢复。
- nftables/Docker 只恢复本事务对象；原 DROP/REJECT、容器、端口发布、Home Assistant 等访问保护必须保持。
- 恢复原 timer 与服务 enabled/masked 状态；新方案不能在后续重启/定时触发时重新夺回接口。
- 客户端可能保留新缓存/租约/连接：撤销新 RA（若启用），重新获取正确地址/DNS，验证新连接；记录旧长连接是否需重建。
- 独立 watchdog 从本地已校验备份运行，不依赖 Landscape、Docker、在线下载、DNS 或当前 SSH；覆盖被杀、SSH 中断、切换重启和已提交事务不误回退。

### 3.1 必须保存的恢复集合

| 类别 | 保存和核实的状态 |
|---|---|
| 网络 | 网口与永久身份、地址、全部路由表/规则、桥/VLAN/MTU、网络管理器配置与状态、sysctl |
| 数据平面 | 相关 nft/iptables 规则及归属、TC/qdisc/XDP/BPF 挂载/pin 清单；现有外部对象重建方式 |
| DNS/DHCP | resolv.conf 类型/目标、resolved 状态、dnsmasq/MosDNS/Mihomo 配置、租约、缓存策略 |
| 服务 | systemd 单元/drop-in、enable/mask/active 状态、timer、容器重启策略、依赖和锁 |
| 应用数据 | Landscape 实际配置目录/数据库、证书/认证、Mihomo provider/规则/secret、评分/隔离、必要 Docker 卷 |
| 程序与系统 | 成套版本/摘要、包变更记录、原系统/内核和可启动介质；离线恢复工具与依赖 |

备份限管理员可读，记录摘要并保存主机外副本。SQLite 使用对应版本支持的一致性备份，或停止全部写入后成套备份数据库及必要 WAL/相关文件；不能复制运行中的单个数据库冒充一致备份。
配置备份不能回滚包安装、内核/驱动升级和系统盘损坏；这些变更需独立系统快照/备用介质。恢复的是实际持久化状态，不能假定改回初始化 TOML 就恢复数据库。

## 4. 分阶段验收

### G0：冻结需求、原方案基线

本阶段以只读采集和离线生成为主，不改变被测/生产系统状态。仅有两处例外需要单独记录：向仓库外介质写入恢复集合（G0-05），以及在试验主机上验证停掉试验网络服务后控制台仍可达（G0-06，失败处理为 R1）。

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G0-01 | 核实 Git 根目录/分支/remote/脏文件，记录提交与试验文件清单，识别原一键部署还是生产自动化部署 | R0；归属不明阻塞 |
| [ ] G0-02 | 核实 WAN/LAN/管理路径、启动盘、上游/交换机/AP、DHCP 来源、VPN/容器/VLAN/bridge；不用旧接口名代替探测 | R0 |
| [ ] G0-03 | 冻结参数、业务预期出口、下载许可、阻断/回退及未知域名/IP 兜底 | R0 |
| [ ] G0-04 | 同一真实客户端测原方案 DHCP/DNS、直连/代理 TCP/UDP、出口、管理、吞吐/延迟/资源与主要业务，保存可比较基线 | R0；原方案异常先处理 |
| [ ] G0-05 | 建完整恢复集合，校验摘要、解密/读取、主机外副本/空间；样本还原核对权限/所有权 | R0 |
| [ ] G0-06 | 试验环境证明独立 SSH/控制台不依赖待接管接口/DNS/容器；停试验网络服务后仍可管理 | R1；失败禁止接管 |
| [ ] G0-07 | 从现网有效配置及实际 provider/规则文件冻结全量基线：保留规则顺序、引用、代理组/能力池、DNS/hosts/嗅探、Geo 数据与自动化；记录摘要、版本、规则集加载数量及加载错误，区分模板和现网；列出源地址/入站/进程/内网规则的运行条件 | R0；缺真实基线不得宣称全量迁移 |

门禁：基线可复现、备份可读、恢复链路实际连通；不得从现网直接开始安装 Landscape。

### G1：平台、版本、部署兼容预检

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G1-01 | 实测架构、内核、BTF/BPF/JIT/cgroup/bpffs、权限与内存（官方建议标准发行版至少 2 GiB，精简系统约 1.5 GiB），并实测 bpffs 是否真挂载、服务单元 `LimitMEMLOCK` 是否够用；对照固定版本官方要求，不能只看发行版名 | R0；缺能力先在备用系统验证 |
| [ ] G1-02 | 核实网卡驱动、链路速率/双工、MTU/错误、TC/XDP attach 能力及实际模式；先验收基础路径，再把 XDP 作为单独变更，验证不支持时行为 | R1；generic 模式不冒充 native 加速 |
| [ ] G1-03 | 固定后端/前端/relay、ARM64 容器与 Mihomo 摘要，检查来源、运行库、协议和配置兼容 | R0 |
| [ ] G1-04 | 列出管理器、端口、route mark/表号、nft、TC/BPF、Docker 网络竞争；试验网段/对象不重叠 | R0；歧义不猜测 |
| [ ] G1-05 | 主机 DNS、订阅/镜像/规则下载和时间同步不依赖未启动代理；预存启动/恢复必需文件 | R0 |
| [ ] G1-06 | 检查磁盘、日志上限、内存/OOM；列明容器权限/挂载，不用扩大权限掩盖未知失败 | R0 |
| [ ] G1-07 | 候选 Mihomo 语法通过；WAN 接口、监听、API、路径适配容器命名空间，不照搬主机接口名 | R0 |
| [ ] G1-08 | 输出接管与恢复清单，明确接口逐一交接；不绕过原安装器冲突门禁强行安装 | R0 |

上游当前要求 Linux ≥ 6.9、BTF/BPF 等能力；见[系统要求](https://landscape.whileaway.dev/intro/requirements.html)，执行按固定版本复核。

### G2：隔离首次启动与恢复演练

使用独立试验主机/备用系统，LAN 只连测试客户端。官方说明明确：即使不带任何配置，启动仍会把 `/etc/resolv.conf` 指向 `127.0.0.1`，所以停止后必须先恢复可用 resolver，再谈其它验证；启动前用 `ss -lutp` 确认 53 端口没有其它 DNS 服务占用，否则 Landscape 的 DNS 服务起不来。Landscape 要求独占其管理的接口，官方指南会删除 NetworkManager，但同时警告不要在有其它接口仍由其管理的机器上执行该命令。
[官方部署说明](https://landscape.whileaway.dev/intro/manual-deployment.html)

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G2-01 | 启动前记录试验宿主/生产网络摘要；无配置/候选启动后比较 resolver、地址、路由、监听、BPF，无未申报改动 | R1 |
| [ ] G2-02 | 只接管试验接口；原管理、上游 DHCP、其他容器/生产客户端不变，无额外 DHCP/RA 外溢 | R1；外溢立即停止 |
| [ ] G2-03 | 正常停止后按 R2 恢复 DNS 类型、默认路由、BPF/TC/qdisc、端口/服务/旧客户端业务；不接受 stop 成功 | R2 |
| [ ] G2-04 | 试验中 TERM、强杀激活进程、断执行会话、未提交重启分别测；watchdog 在 RTO 内恢复，逐项记录 | R2/R4 |
| [ ] G2-05 | 已提交事务不被旧 watchdog 回退；重复回滚、中断后续跑幂等；坏备份/空间不足明确阻塞而非成功 | R1/R4 |
| [ ] G2-06 | 清理只作用本事务；预置无关 nft 表、容器和可恢复外部挂载作对照，恢复后仍有效 | R1 |
| [ ] G2-07 | 停试验服务后实际从备用介质启动，核对启动盘、管理、旧网络，测完整恢复时间 | R4；未物理启动不得通过 |

门禁：正常/异常回滚、备用启动均实测通过，无残留/误清理，才能进入业务试验。

### G3：直连基础网络（暂不导入代理）

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G3-01 | 接口只有一个管理者，LAN 地址、上联路由、源地址/NAT 回程正确，无重复地址/ARP 抖动/环路 | R2 |
| [ ] G3-02 | DHCP 首次获取、续租、重连均正确，地址池/掩码/网关/DNS/租期符合计划，只有预定 DHCP 来源 | R2 |
| [ ] G3-03 | UDP/TCP DNS、国内/未知/不存在域名、局域网名、缓存命中/TTL 到期/上游恢复符合计划 | R2 |
| [ ] G3-04 | 主机 DNS、软件源、规则源/时钟正常；DNS 上游故障执行既定策略，无循环转发 | R2 |
| [ ] G3-05 | 下游 TCP/UDP 双向直连、多连接、长响应/大包通过；WAN 抓包/受控日志证明 NAT/出口，不依赖 ping | R2 |
| [ ] G3-06 | 按实际使用测 NAS/HA/打印/投屏；跨子网 mDNS/广播不默认可达，逐项记录需求与结果 | R2；无该业务注明不适用 |
| [ ] G3-07 | IPv4-only 时无意外 RA/DHCPv6、IPv6 默认路由/公网绕行；AAAA/SVCB/HTTPS 响应符合既定兼容策略 | R2 |
| [ ] G3-08 | 管理 API/端口允许来源可达、禁止来源拒绝；实际跨区测试证明新路径保留 ACL，不能只看旧 nft 规则存在 | R2 |

门禁：无代理也有稳定、隔离正确的基础网络，直连性能符合冻结预算。

### G4：单客户端透明代理、DNS/规则一致性

客户端关闭手工 HTTP/SOCKS、PAC 和测试 VPN，避免显式代理掩盖透明路径失败。
出口容器需要两个程序：relay `redirect_pkg_handler`，以及 worker（本项目为 Mihomo）。worker 挂到 `/app/server` 并由其中的 `run.sh` 启动，relay 的转发端口与 worker 监听端口一致；注册用的 Unix socket 目录必须以只读方式挂进容器的 `/ld_unix_link/`。官方参考镜像使用 `privileged: true` 加 `NET_ADMIN/BPF/PERFMON` 和 `net.ipv4.conf.lo.accept_local=1`：这是上游参考配置，不是本项目已批准的最小权限，实际授予的权限必须逐项记录。
上游默认值仅作核对起点，执行时按固定版本复核，不作为本项目常量：worker 监听端口 `12345`；`LAND_PROXY_HANDLE_MODE` 取 `tproxy`/`route`/`multiple_tproxy`；容器侧 `ip rule add fwmark 0x1/0x1 lookup 100` 与 `ip route add local default dev lo table 100`；ICMP mark `2`。
依据：[Landscape 容器出口](https://landscape.whileaway.dev/features/traffic-flow.html)、[Mihomo TProxy](https://wiki.metacubex.one/en/config/inbound/port/)。

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G4-01 | relay 注册成功、注册 socket 路径与只读挂载正确、权限匹配；固定并记录 `LAND_PROXY_HANDLE_MODE`，多网络容器所选模式与实际接口一致；容器重建/veth/ifindex 变化后旧注册失效、新注册可用 | R1/R2 |
| [ ] G4-02 | TProxy TCP/UDP 与 relay 目标一致，关闭旧主机 TUN/auto-route/auto-redirect/DNS 劫持重复接管；节点连接不再进入自身 | R2 |
| [ ] G4-03 | 同一客户端分别测直连 TCP、代理 TCP、直连 UDP、代理 UDP；受控出口回显、抓包与 Mihomo 日志吻合 | R2；任一失败阻塞 |
| [ ] G4-04 | QUIC/HTTP3、UDP 回程/超时/分片、业务 NAT 通过；先确认节点支持 UDP，不以 TCP 回退代替 UDP 通过 | R2/R3 |
| [ ] G4-05 | IP/MAC/设备匹配、随机 MAC/地址漂移、入口冲突、DNS/IP 优先级/兜底符合预期，并逐条核对官方匹配语义：默认 Flow 为 ID 0、自定义 Flow 为 1–255、优先级数值小者优先、同优先级时 DNS 规则压过 IP 规则、每包最多命中一条目的规则；出口只是没有目的规则选中其它动作时流量离开的位置，规则可以把流量送往另一个 Flow 的出口。三种特殊配置分别验收：仅入口（空出口）——无规则选中其它 Flow 出口时丢弃，命中时按所选出口和 §2.1 冻结的失败策略正常转发；仅出口（空入口）——无客户端直接进入，只能被其它 Flow 当作出口目标；入口出口皆空——只能作为其它 Flow 转发流量的丢弃目标。空出口本身不能证明业务不会直连，也不是整个 Flow 的永久 fail-closed；代理故障不泄漏只能按 G4-14/G4-15 单独验收，本用例不下该结论；trace 与真实连接一致 | R3 |
| [ ] G4-06 | 两试验客户端使用不同 Flow：同域名按预期走不同上游/出口，缓存不串流；重连/地址变化匹配正确 | R3 |
| [ ] G4-07 | DNS 与最终出口一致；共享 CDN IP、多 A/CNAME、TTL 变化、IP 直连、缓存过期/连接复用不误分或留旧路由 | R3 |
| [ ] G4-08 | 自带 DoH/DoT、硬编码 DNS、VPN、HTTPS/ECH 按需求允许/限制/兜底；不假定所有域名经过网关 DNS | R3；必要规则无法保证则阻塞 |
| [ ] G4-09 | 保持原 DNS 模式，比较原域名分类、nameserver-policy/hosts/过滤与内网解析；若 Landscape 前置，必须测普通 TCP DNS、UDP 截断回退、重写/本地回答优先级和 IP 校验；若依赖嗅探，记录 TLS/QUIC/ECH 边界与 IP 兜底 | R3 |
| [ ] G4-10 | 原方案启用 fake-IP/MosDNS 时属于必测：映射唯一归属、地址池不与 LAN 路由冲突、Flow map、缓存寿命、重启/故障/回退后旧 fake-IP；持久化用多个域名倒序查询及真实连接验证，避免重新分配相同首地址造成假通过；原方案未启用才可说明不适用 | R3/R2 |
| [ ] G4-11 | AI、媒体、Telegram、GitHub 控制/下载、模型下载、普通浏览逐类测出口/地区/许可；大流量不进入受限池，未知目标兜底明确 | R3 |
| [ ] G4-12 | 订阅初装/刷新/增删、空列表/HTML/超时/摘要错误/失效凭据有明确行为；坏候选保留旧配置、不落空池/回显秘密 | R3 |
| [ ] G4-13 | controller/provider/规则路径、锁、secret 正确；UI/API 选中节点与真实出口一致 | R3 |
| [ ] G4-14 | 必须代理/阻断业务在无节点、拒绝/超时、未知协议时不意外直连；允许直连业务正常，以 WAN 侧证据确认 | R2/R3；泄漏立即停止 |
| [ ] G4-15 | 长连接、上传/下载、WebSocket、TLS/证书、登录/媒体持续通过；按实际需求测内网访问/源客户端识别 | R3 |
| [ ] G4-16 | 单列 ICMP：经 Flow 出口容器转发的路径上，relay 默认丢 ICMP，官方明确这是预期默认行为，`ping` 不通不等于故障；若启 passthrough，mark 必须与 `--icmp-mark-value` 一致，容器内转发/NAT 要自行开启（官方镜像的启动脚本默认把这几行注释掉），并验证出口/权限、PMTU 与 IPv6 错误消息；不以 ping 代替 TCP/UDP 验收 | R3 |
| [ ] G4-17 | 对 G0-07 全量基线逐项核对配置与文件摘要、规则顺序、引用和加载数量；每个规则族/优先级边界做正反例，对照实际命中、策略组与出口；单列 LAN/本机目标、冷/热路由缓存及入站类型变化，证明没有在到达 Mihomo 前绕行 | R3；不以配置数量相同代替行为等价 |
| [ ] G4-18 | 统一管理入口分别验证读状态、选组、测速、provider 更新、重载拒绝与恢复；坏鉴权被拒；更新任务与面板共享写入锁；Mihomo/Landscape 各自重启后持久状态与鉴权恢复正确 | R3；单侧 API 通过不算统一管理完成 |

门禁：四种 TCP/UDP 路径、业务策略、防泄漏均有真实客户端证据；API healthy、trace、本机 curl 不足以通过。

### G5：故障注入与自动化（逐项恢复再测下一项）

统一动作：检查点 → 一个故障 → 记录检测时间/影响/失败策略 → 撤销注入 → 记录恢复时间 → 验证新连接、出口、无残留。
超过 RTO、影响管理/非试点设备、意外直连或数据损坏，立即执行相应回滚。

| 勾选 / ID | 注入 / 必须证明的结果 | 失败处理 |
|---|---|---|
| [ ] G5-01 | 停 Mihomo、停 relay、停容器分别测；直连不扩散故障，代理遵守失败策略，恢复无旧注册占用 | R1/R2 |
| [ ] G5-02 | 单节点、整个供应商、全部节点故障分别测；不绕许可，上游整体故障不误惩罚全部节点，恢复不震荡 | R3 |
| [ ] G5-03 | 逐个中断 DNS 上游、Landscape DNS、可选 MosDNS；缓存到期/未缓存均测，无 DNS 旁路泄漏/循环，恢复出口正确 | R2/R3 |
| [ ] G5-04 | 停/强杀 Landscape，检查真实数据平面/管理/resolver；不假定进程死后 BPF 自动卸载；重注册恢复时间在预算内 | R2/R4 |
| [ ] G5-05 | Docker 重启、容器退出重启、镜像缺失/下载失败、socket 断开、卷只读/权限不足分别测；可诊断、不误清理其他业务 | R1/R2 |
| [ ] G5-06 | 试验 WAN/LAN 断链重连、上游 DHCP/地址变化/暂时失联；路由/地址恢复，节点和主机 DNS 无启动闭环 | R2/R4 |
| [ ] G5-07 | 恶化延迟/丢包、MTU/PMTU/大包；能定位小请求成功但上传下载卡住，不以全局关 offload/降 MTU 掩盖未知失败 | R2/R3 |
| [ ] G5-08 | 试验中分别模拟坏 YAML/规则/数据库、坏备份、磁盘满、内存/OOM；不覆盖好配置、不无限重启/涨日志 | R1/R3/R5 |
| [ ] G5-09 | 先只读健康/影子评分，核对 API/路径/锁；健康脚本不调用旧 DNS 模式夺回 53 端口 | R3 |
| [ ] G5-10 | 单独启订阅更新、规则更新、selector 收敛、隔离；坏候选/健康失败完整恢复、池有保底、能力限制保持 | R3 |
| [ ] G5-11 | UI 与更新/评分/隔离并发、任务中断/锁超时；事务串行，无覆盖新状态/半配置，旧写入者禁用 | R3 |
| [ ] G5-12 | timer 首次启动、Persistent 补执行、随机延迟/冷却边界正确，不在开机/切换时集中高负载或过早隔离 | R3 |
| [ ] G5-13 | 未授权 API/旧 secret/默认密码/禁止来源被拒，默认管理员口令在暴露到任何网络前已更换；`landscape_api_token` 每次重启都会重签，自动化必须重新读取而不是缓存旧值；重启或轮换后自动化正确重新鉴权；日志/进程/镜像/备份不暴露秘密 | R3；泄漏阻塞上线 |

故障注入必须限制资源与范围；不能在现网填满磁盘、耗尽内存或全局屏蔽网络制造故障。

### G6：开机顺序、升级降级、完整回退

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G6-01 | 试验系统正常重启/冷启动重复测，网络/DNS/Landscape/Docker/relay/Mihomo 按依赖恢复，BOOT_BUDGET 内客户端业务正确 | R4 |
| [ ] G6-02 | 上游/DNS 延迟、时钟未同步、镜像仓库不可达/旧租约开机；本地依赖可用，无需先手工联网才能启动 | R2/R4 |
| [ ] G6-03 | 激活未提交重启与提交后重启分别测，watchdog 前者恢复/后者保留，新旧方案无竞态 | R2/R4 |
| [ ] G6-04 | 可弃置试验介质做意外断电恢复，数据库/文件/租约一致，失败可切原盘；禁止原生产盘注入 | R4/R5 |
| [ ] G6-05 | 固定旧版→候选版升级，配置迁移/UI/API/relay/挂载/规则通过，升级前一致备份可读 | R5 |
| [ ] G6-06 | 实际降级至原套件、恢复旧数据库/卷，第二次重启仍通过；替换旧二进制不能代替降级验收 | R5/R4 |
| [ ] G6-07 | 完整撤除 Landscape 模式，恢复原管理器、dnsmasq/MosDNS/Mihomo/nft/自动化/客户端设置；测新旧连接和 RTO | R2/R4 |
| [ ] G6-08 | 回退后经重启/timer 窗口再查 BPF/TC/qdisc/resolver/路由/Docker/端口/业务，新方案不会复活 | R2/R4 |

上游 README 宣称配置迁移和支持降级，但没有据此证明本次固定版本组合、数据完整性和断电迁移恢复通过；需成套快照与实际演练。

### G7：负载、稳定性、受控扩大范围

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G7-01 | 同客户端/上游/目标/节点比原方案与新方案直连/代理吞吐、p95 延迟/抖动/UDP 丢包，符合冻结预算 | R2/R3 |
| [ ] G7-02 | 测单/双向、多客户端、小/大包、连接数/长下载；记 CPU 每核/softirq、RSS/BPF 内存、温度/降频、网卡错误/磁盘增长 | R2；链路速率不代表吞吐 |
| [ ] G7-03 | SOAK_WINDOW 覆盖真实业务、续租、缓存过期、订阅/规则 timer/故障恢复，无持续内存/FD/map/连接表增长、重启/震荡 | R2/R3 |
| [ ] G7-04 | 必测门禁全过才规划现网维护窗口、管理/快照；同主机共享 WAN/DNS 接管按整机变更评估，不冒充单客户端隔离 | R0；未授权仅保留计划 |
| [ ] G7-05 | 现网获准后先一台非关键设备再逐批增加，每批核对 DHCP/DNS、TCP/UDP、业务/保护与未迁移客户端 | R2；异常撤批，整机异常全回退 |
| [ ] G7-06 | 每批保留可用原方案至观察窗通过，记录实际 RTO/失败策略，关键业务/其他容器无性能或访问回归 | R2/R4 |

eBPF 更快不是验收条件；Mihomo 加密代理仍有用户态工作，性能结论只来自可比测量。

### G8：文档、发布、签收

| 勾选 / ID | 验收动作与通过证据 | 失败处理 |
|---|---|---|
| [ ] G8-01 | 实现后文档明确两模式互斥、内核/架构/权限/目录、安装/升级/回退/诊断；只输入订阅正常路径与歧义退出均测试 | R0/R3 |
| [ ] G8-02 | 仅模板/匿名证据入仓；扫描订阅/secret/身份、镜像层/构建产物/日志/备份；README/架构图同步 | R0；未脱敏不发布 |
| [ ] G8-03 | 若分发第三方程序/镜像，核对版本/来源/摘要、许可证与相应源码说明；上游声明 `landscape-ebpf` 为 GPLv2、其余部分为 GPLv3，融合镜像按实际内置组件逐项核对；本仓库 CC0 不改变第三方许可证 | R0 |
| [ ] G8-04 | 若制作融合镜像，无个人凭据重建，备用盘全量回读/实机启动，再验收管理/网络；未经实机启动只能标记为预发布，不得进入正式发布 | R4 |
| [ ] G8-05 | 汇总用例、失败重测、N/A 理由、遗留/测量；P0/P1 清零、必测无缺口、恢复证据全才签收 | R0；文档检查不替代实机 |

## 5. 停止条件与问题台账

| 级别 | 示例 | 处理 |
|---|---|---|
| P0 | 管理失联恢复超时、误接管生产接口、数据损坏/秘密泄露、禁止业务意外直连、误删外部对象 | 立即停止，R2/R4/R5 恢复，保留证据，阻塞扩大范围/上线 |
| P1 | DHCP/DNS/透明 TCP 或 UDP 失败、选路/下载池错误、降级/重启失败、超过性能预算 | 当前阶段失败，回退修复并重测相关路径 |
| P2 | 不影响访问/策略的展示或日志问题 | 记录影响/处理条件；错误状态显示仍需验证是否掩盖故障 |
| 未知 | 无法解释的挂载/路由/缓存变化、偶发丢包、影响不明 | 阻塞并调查，不能直接降 P2 或勾选通过 |

每个问题记：`问题 ID / 用例 ID / 现象 / 重现步骤 / 影响 / 证据 / 回滚结果 / 修复版本 / 重测 / 遗留限制`。
版本、DNS 模式、内核/驱动、权限、路由方式或池能力变化后，重评相关门禁，不沿用旧通过结论。

## 6. 可复制的验收记录

填写后留在仓库外；公开只发匿名摘要。

```text
CHANGE_ID：
BASELINE_ID / 源码提交 / 版本与配置摘要：
环境：独立主机 / 备用系统 / 现网已授权窗口
参数表已冻结：是 / 否
独立恢复入口与备用启动证据：
G0 / G1 / G2 / G3 / G4 / G5 / G6 / G7 / G8：待测
用例 ID / 时间 / 主机 / 客户端 / 状态：
变更前状态：
动作：
预期 / 实际 / 测量值：
证据路径（私有）：
故障注入及撤销动作（如有）：
回滚 ID / 恢复耗时 / 恢复后客户端证据：
问题台账行（问题 ID / 现象 / 影响 / 回滚结果 / 修复版本 / 重测 / 遗留限制）：
未解决 P0 / P1 / P2 / 未知：
不适用项及原因：
允许下一阶段：是 / 否
签收范围：设计 / 模拟 / 隔离运行 / 实机业务 / 现网稳定
```

当前记录：已执行本机合成配置下的 Mihomo API/DNS/SOCKS 隔离检查及正常进程重启，详见可行性记录。未安装 Landscape、未改系统网口/DNS/防火墙/容器，未做树莓派故障注入、系统重启或物理恢复；上表运行门禁保持待测。

## 7. 上游依据与执行前复核

门禁、参数表、失败策略和恢复要求属于本项目验收设计，不是上游已提供的保证。
实施时固定源码/发行版与对应文档，不混用 main/master 文档和旧发行包；源码细节不能作为跨版本保证。

- [README：DNS/eBPF 架构、迁移/降级声明、许可证](https://github.com/ThisSeanZhang/landscape)
- [系统要求](https://landscape.whileaway.dev/intro/requirements.html)
- [手动部署：resolver、53 端口、接口独占、恢复地址、升级](https://landscape.whileaway.dev/intro/manual-deployment.html)
- [Flow：设备匹配、DNS/IP 优先级、默认出口、trace](https://landscape.whileaway.dev/getting-started/flow-setup.html)
- [容器：relay、权限/挂载、TProxy、ICMP](https://landscape.whileaway.dev/features/traffic-flow.html)
- [eBPF 路由路径](https://landscape.whileaway.dev/features/ebpf-route.html)、[iptables 关系](https://landscape.whileaway.dev/faq/iptables.html)
- [持久化目录与配置](https://landscape.whileaway.dev/configuration/home-path.html)
- [relay 源码：注册恢复、接口选择（执行时固定提交）](https://github.com/ThisSeanZhang/landscape/blob/main/landscape-ebpf/src/bin/redirect_pkg_handler.rs)
- [TC hook 源码：清理路径（执行时固定提交）](https://github.com/ThisSeanZhang/landscape/blob/main/landscape-ebpf/src/landscape.rs)
- [bpftool 官方说明：`-f` 的 pin 路径及关联 link](https://github.com/libbpf/bpftool/blob/main/docs/bpftool-prog.rst)
- [Mihomo TProxy 入站](https://wiki.metacubex.one/en/config/inbound/port/)、[DNS](https://wiki.metacubex.one/en/config/dns/)
- [ARM64 参考容器](https://github.com/ThisSeanZhang/landscape/pkgs/container/landscape-edge)
