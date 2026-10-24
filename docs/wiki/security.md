# 安全边界与发布规则

[Wiki首页](../README.md) · [架构](architecture.md) · [运维](operations.md)

本页补充仓库的 [`SECURITY.md`](../../SECURITY.md)，不替代漏洞报告流程。结论区分为：源码/文档中可核对的控制、运维建议、尚未由仓库证明的现场状态。安装模板、架构报告和验收清单都不能证明目标主机当前的有效安全配置。

## 信任边界

### 下游 LAN：配置明确选择信任

渲染器生成的 nftables 只替换自身表，INPUT 默认 drop，但明确接受整个
可信 LAN 接口；这也允许 Mihomo auto-redirect 的动态监听，不依赖固定静态
代理端口白名单。依据是[`tools/render.py`](../../tools/render.py)与
[动态端口运维记录](../operations-current.zh-CN.md)。

这个假设不适用于访客或不可信 LAN。LAN 客户端可能访问绑定 LAN/通配地址的
其它服务；静态 allowlist 也可能漏掉动态监听并切断透明代理。不要在未核实
当前监听、规则归属和真实 LAN 行为前扩大或缩小放行范围。

### WAN / 上游：模板与安装器并不等价

渲染器的 INPUT 链以 drop 为基线，仅放行已建立连接、配置的管理网段 SSH、
DHCP 回包和诊断 ICMP；ICMP 规则并非都限制到 SSH 管理来源。渲染器不改
sshd。以上是模板行为，不是目标机当前防火墙的证据。

单独的[`setup.py`安装器](../../gateway/setup.py)创建自有 nftables 表，
其 INPUT policy 为 accept；它不是渲染器的 WAN drop 策略。
[`automation/install.sh`](../../automation/install.sh)也不安装或修改
nftables。现场必须先识别安装路径并检查完整有效 ruleset，不能由其中一份
模板推断机器实际已关闭 WAN 入站。

### Mihomo mixed / controller：数据面与管理面分开

渲染器生成随机 controller secret，并将 mixed/controller 绑定到部署参数；
自动化脚本从部署 env 读取参数，controller 请求使用 bearer secret。生成的
候选与运行配置都含敏感值。实现见[`tools/render.py`](../../tools/render.py)
及[`mihomo-healthcheck`](../../automation/sbin/mihomo-healthcheck)。

controller 是管理面，不是普通代理端口。保持 secret 私密，避免不必要的
WAN 暴露；但有 secret 也不能把不可信客户端放进整体信任的 LAN。检查现场
监听地址、有效 Mihomo 配置和防火墙，不要只检查模板。

### SSH：认证方式由操作者选择

当前渲染器和 automation 安装器都不改 sshd。部署文档要求保留发行版当前
认证策略，并用 `sshd -T` 核对；上游可达性还受实际防火墙与管理网段影响。
详见[部署步骤](../deployment-current.zh-CN.md)。

密码或密钥认证由运维者按接入需求选择。本项目不要求强制切换为 key-only；
需要密码 SSH 的运维应保留可用的密码登录，不要照搬会锁断管理通道的加固
建议。若确需修改 sshd，先核对生效配置、验证语法并保留第二条管理通道。

### 容器与控制平面：当前不是已部署能力

两个仓库安装器都不安装 Landscape 或容器控制面。项目的
[Landscape 能力报告](../landscape-capabilities.zh-CN.md)和
[验收计划](../landscape-acceptance.zh-CN.md)只讨论固定版本的管理监听、
Docker socket 兼容运行时及出口容器；能力报告指出上游参考账户需首次登录
更改，WAN 管理导出默认关闭。它们不是现场配置证明。

验收文档中的参考容器包含高权限能力，并明确不代表本项目批准的最小权限。
若另行部署，管理 API、宿主 Docker socket、特权容器及 NET_ADMIN/BPF 能力
可能控制或接管宿主网络。应把它们视为高权管理边界而非普通业务容器，限制
到受信任管理路径，逐项核对权限、挂载和暴露端口，并先在隔离环境演练。
这些是风险处理建议；仓库没有证据证明某现场的容器权限、绑定地址或控制面
已经安全配置。

### 安装路径不能混为一谈

- [`tools/render.py`](../../tools/render.py)生成的基础防火墙模板有 WAN INPUT drop 与可信 LAN 全放行，并保留 Mihomo 自己管理的动态 redirect 规则；“生成了模板”不表示已应用。
- [`setup.sh`](../../setup.sh)调用[`gateway/setup.py`](../../gateway/setup.py)，仅创建并替换 `inet mihomo_gateway` 自有表。其 input chain 是 accept 策略；安装器不执行全局 `flush ruleset`，但这不等于具有 WAN deny 策略，也不能仅由该表判断主机的有效过滤结果。
- [`automation/install.sh`](../../automation/install.sh)安装脚本与 systemd 单元/配置骨架，不改 sshd、NetworkManager、路由、sysctl 或 nftables；其正常安装会覆盖自有脚本/unit，部署数据存在时保留。

因此任何“WAN 已关闭”“LAN 已隔离”“SSH 只允许密钥”“容器最小权限”的结论，都要同时给出安装路径、当前配置/规则证据和实际验证。不要从某一份模板推断另一部署方式或当前机器状态。

## 凭据与公开仓库

以[`SECURITY.md`](../../SECURITY.md)为权威发布清单：订阅 URL/token、节点
凭据与节点清单/标签、Wi-Fi 或 dashboard 密码、私钥/证书、完整 Mihomo
目录归档、未脱敏路由器备份都不能提交。真实配置、provider、日志、运行状态、
抓包和生成候选属于私有部署数据；即使文件名看起来普通，也要检查内容和
Git 历史。

- 渲染器生成新的 controller secret；输出目录必须不存在且为私有权限。
  不要把生成目录加入仓库或贴到 issue。
- automation 部署环境、订阅源、dashboard secret 和节点能力标签存放在主机
  私有位置；示例文件只作无真实值模板。安装时保留已有部署数据不代表它们
  适合发布。
- 不用 `cat`、`env`、`set -x`、带 URL 的命令行参数或完整 `journalctl` 输出
  展示 secret。检查权限时用 `stat`/`namei` 等不显示内容的方式；分享日志前
  逐行脱敏地址、标识、URL、Authorization、token 和节点名。
- 公开提交前按[`SECURITY.md`](../../SECURITY.md)执行仓库已有的结构、渲染、
  automation 检查，并对工作树与将发布历史运行 Gitleaks 或等效扫描。扫描
  输出要脱敏；发现已泄露凭据，先撤销/轮换，再处理公开内容，不能只删当前文件。
- 私网示例并不等于凭据，但本 Wiki 刻意不复述实际网段、接口映射、节点标签、
  设备标识、主机名或本地路径。事故材料同样按最小披露原则处理。

## 控制、建议与未验证项

### 源码中可确认

renderer 随机生成 controller secret；输出目录拒绝覆盖并设私有权限。
automation 安装器的 dry-run 不写文件，已有部署数据默认保留。订阅事务保存
候选回滚点，并在语法、加载和关键路径验证后才接受配置。

这些实现事实不等于现网文件权限、secret 保管、实际 timer 状态或整机备份
已经合格。安装器保留部署文件，也不替运维人员判断其内容是否可公开。

### 文档中的部署要求

部署文档要求保留发行版 SSH 认证策略和用户所需的登录方式、私有化 env/
secrets、不创建公网端口映射，并完成真实 LAN 验收与独立恢复准备。
这是文档要求，不是自动强制的运行时策略，也不是已完成现场验收的证明。

### 建议性控制

管理 API 限定可信管理路径；不可信访客网络与网关运维 LAN 分离；容器权限、
挂载和端口逐项最小化；定期抽样验证私有备份能恢复。这些是建议，不能写成
仓库已经部署或测试通过。

### 仍需目标机核验

实际 nftables 全 ruleset、sshd 生效认证、controller 绑定与可达面、订阅和
secret 文件权限、Docker socket/容器能力、Landscape 暴露情况目前都不能由
公开源码确认。需要目标机只读盘点和受控验证；静态检查、模板或模拟测试均
不能替代。

## 报告疑似泄露

若疑似凭据已进入公开记录，不要在公开 issue、聊天或 Wiki 重复贴值。按[`SECURITY.md`](../../SECURITY.md)先撤销/更换凭据，再通过仓库维护者的私密 GitHub 渠道联系。报告中只提供受影响凭据类别、发现时间、公开位置（不含值）、撤销状态和最小必要证据；私有原始证据另存受限位置。
