# 运维排障与恢复

[Wiki首页](../README.md) · [架构](architecture.md) · [安全](security.md)

本页是按证据逐层缩小范围的操作手册。先确认实际采用的安装路径，再处理
单一故障域；本页的预检默认只读。规则细节见[当前运维记录](../operations-current.zh-CN.md)，
部署路径见[分阶段部署](../deployment-current.zh-CN.md)与
[一键安装](../one-click-setup.zh-CN.md)。生产自动化行为以
[自动化层说明](../../automation/README.zh-CN.md)和链接的脚本/单元为准。

## 先保住管理与证据

1. 保持当前可用的 SSH / 本地控制台，不在唯一管理通道上停止服务、改防火墙
   或重启。
2. 记录故障开始、最近一次已知成功、客户端所在网络、当前安装器/服务名和
   最近变更。节点、地址和订阅值留在本地记录中。
3. 一次只测一个层次；单次探测失败不直接证明设备离线、节点失效或 DNS 故障。
4. 首轮只运行读取状态的命令。`nft -f`、服务重启/重载、禁用 timer、强制更新、
   安装和回滚都会改变状态，须先有对应授权与恢复点。

可先收集以下只读概况；接口、日志和规则输出在共享前必须脱敏：

```sh
date -Is
ip -br link
ip -br addr
ip route show table all
ip rule show
systemctl --no-pager --full status mihomo mihomo-gateway mihomo-gateway-dhcp dnsmasq mosdns
systemctl list-timers --all
ss -lntup
```

并非每台主机都有这些 unit。命令报“not found”先记为“不适用/未知”，不要
因此安装或启停服务。读取[当前运维记录](../operations-current.zh-CN.md)后，
也要确认没有把本地地址、网段、接口名或端口原样贴入公开工单。

## 按四层定位

依次验证上游、DNS、显式代理、真实 LAN 客户端。优先使用低频、短超时的
单次请求；不要在第一次排障时并发跑测速或整套业务流量。

### 1. 上游直连

网关侧低影响检查：

```sh
curl --noproxy '*' --connect-timeout 3 --max-time 8 \
  -o /dev/null -w '%{http_code}\n' https://www.baidu.com/
```

同时核对默认路由与实际 WAN。此 hostname 请求还依赖当前解析器；如果 DNS
尚未独立验证，请把失败记为“未知”，不要直接归因于上游断网。成功只说明
该时刻至少有一条网关直连 HTTP(S) 路径可达，不证明代理节点或 LAN 转发正常。
VPN/策略路由也可能改变出口。

### 2. DNS

分别在网关本机监听与 LAN 客户端实际使用的 DNS 上查询：

```sh
dig @<已核实的DNS地址> <测试域名> A +time=2 +tries=1
```

先将占位符替换为现场值。单个域名成功不等于分流、TCP/53、缓存或 fake-IP
映射整体正确。

### 3. 显式代理

在网关和一台 LAN 客户端分别测试实际 mixed-port：

```sh
curl --proxy http://<网关地址>:<mixed端口> \
  --connect-timeout 3 --max-time 10 -o /dev/null \
  -w '%{http_code}\n' https://www.gstatic.com/generate_204
```

该结果只证明这次显式代理路径可用，不证明透明 TCP/UDP、规则命中或浏览器
会话通过。占位符必须替换后再执行。

### 4. 真实 LAN 客户端

选择一台下游客户端，关闭其 VPN/TUN/系统代理；检查 DHCP 租约、DNS，再测
新建 TCP、实际 UDP 和预期业务出口。客户端流量须确认确实经过目标网关。
网关本机 curl、Ping、显式代理都不能替代此项；单一网页也不能覆盖 UDP/
长连接。

`curl --noproxy '*'` 只绕过 curl 自己的代理设置，不会关闭操作系统 VPN/TUN；
先核实客户端路由和出口。DNS 超时、HTTP 状态异常或请求被拦截也可能是目标
站点策略变化，需用第二个独立目标复核。

快速分支：

- 上游直连失败：先核对 WAN 链路、默认路由、时间与上游可达性；此时不要
  把代理节点判为坏节点。
- 上游直连成功、DNS 失败：确认实际 DNS 监听/上游模式和 dnsmasq/MosDNS/
  Mihomo 服务；不要用更换规则集代替 DNS 诊断。
- DNS 正常、显式代理失败：核对 mixed-port 监听、controller/代理配置和
  订阅候选健康状态；不要公开 controller 响应或认证头。
- 显式代理成功、真实 LAN 失败：优先核对 DHCP、客户端默认路由、透明代理
  动态监听、NAT/转发链和是否绕行；本机成功不是转发验收。

曾有仅按静态业务端口放行而漏掉 Mihomo auto-redirect 动态 TCP 监听，导致
真实客户端失败、网关本机与显式代理仍正常的案例。不要把故障当时的端口号
写入白名单。先比较当前监听和规则归属，确认安装模板，再决定是否进入有
备份的变更流程；见[运维记录](../operations-current.zh-CN.md)及
[渲染器](../../tools/render.py)。

## 多探针状态与“未知”

[健康检查脚本](../../automation/sbin/mihomo-healthcheck)由
[每分钟 timer](../../automation/systemd/mihomo-healthcheck.timer)触发，探测
并行运行，状态写入运行时目录 `/run/mihomo-healthcheck/`。`status` 记录时间戳、
整体模式、各业务探针结果、动作以及 X 探针分项。

读状态时检查时间戳；缺文件、写入时间明显落后于 timer 周期、探测未执行或
结果相互矛盾时，运维记录应标为**未知/证据过期**，不可把缺失当作 `false`。
本机可只读查看该状态文件：

```sh
sudo sed -n '1,2p' /run/mihomo-healthcheck/status
```

该文件不含 controller secret；输出仍可能暴露服务状态和探测摘要，外发前需
按最小披露原则审阅。

脚本输出的整体模式含义：

- `healthy`：本轮定义的检查均通过；不是浏览器登录、视频播放、真实 LAN
  或持续吞吐验收。
- `degraded`：存在失败的业务路径；脚本倾向对对应 Mihomo 组触发复测，不因
  单业务失败重载整个仍运行的核心。
- `upstream-down`：脚本自身对一个直连 HTTP(S) 目标的基线探测失败；由于该
  检查使用域名，它不能区分 DNS 故障与 WAN 故障。本轮动作是等待，不据此
  惩罚代理节点；需结合独立 DNS、链路和路由证据判断。
- Mihomo 进程不 active 时脚本才请求重启。先读取 systemd 与健康记录，确认
  是否反复重启及管理路径；不要把“有 restart”自动当作修复完成。

X 类探针并行检查新/旧 API 与静态、图片、视频资源，分类为
`path-reachable`、`endpoint-changed`、`restricted`、`resource-failed`、
`api-failed` 或 `unreachable`。限制/端点变化不等价于节点死亡；API 未授权
响应只证明到 API 的路径可达，静态/媒体域名也不证明媒体已播放。

记录中的 `x_timeline=unverified` 是刻意的：认证时间线必须由授权用户在客户
端单独验证。来源与判定逻辑见健康检查脚本及
[当前故障记录](../operations-current.zh-CN.md)。

## 自动更新、状态文件与锁

- 自动化订阅 timer 每日低峰触发并有随机延迟；`Persistent=true` 意味着错过的
  任务可能在开机/恢复后补跑。周期以
  [实际 timer](../../automation/systemd/mihomo-subscription-update.timer)为准，
  部署文档要求订阅刷新间隔至少 24 小时。
  避免手工高频运行；`--force` 会跳过“候选未变化”的短路，不是无害的状态查询。
- 更新脚本先尝试订阅源与已配置的供应商文档镜像；主来源不可用时保留现有
  主节点。额外来源只有通过校验的下载或 last-known-good 缓存可进入候选。
  候选语法、重载、服务状态或关键路径检查失败时，脚本尝试恢复原配置。
- `/run/lock/mihomo-maintenance.lock` 与订阅专用锁避免健康检查/更新等维护任务
  并发；锁占用时任务会退出/跳过。不要为了“让它跑起来”删除锁文件或并发
  重启任务。
- 健康探针与失败计数位于 `/run/mihomo-healthcheck/`，主机重启会清空这些易失
  证据。订阅候选回滚点保存在 `/var/lib/mihomo-subscription-backups/`，属于
  root 私有配置备份；脚本仅保留最近 20 份。以
  [更新脚本](../../automation/sbin/mihomo-update-subscription)为准。
- 一键安装器配置的原生 provider 刷新 interval 为一天，见
  [`gateway/defaults.json`](../../gateway/defaults.json)与
  [`gateway/setup.py`](../../gateway/setup.py)。这是不同于 automation systemd
  timer 的部署路径，不要把两套 timer/目录混为一谈。

只读查看 timer/近期执行记录可用：

```sh
systemctl list-timers --all
systemctl --no-pager --full status mihomo-subscription-update.timer mihomo-subscription-update.service
journalctl -u mihomo-subscription-update.service --since '-24 hours' --no-pager
```

日志可能包含额外订阅标识、路径、地址或错误上下文。仅保存在本地受限记录中；
共享前先审阅并脱敏。绝不要用 `cat`、`env`、shell tracing 或命令参数显示
订阅 URL、token、密钥或完整私有配置。

## 备份和回滚：先识别安装路径

不要假定存在一个覆盖所有部署的“自动化回滚”。先通过运行服务、已安装文件
和本地变更记录确定系统属于哪种安装：

### `setup.sh` / `/opt/mihomo-gateway/setup.py`

安装事务备份它管理的配置、网络文件、服务状态和 sysctl；健康门禁失败时
有 watchdog。已安装软件包不随回滚卸载。备份保存在
`/var/lib/mihomo-gateway/backups/`。

只有确认确属此安装器并选对备份后，才使用它提供的
`sudo python3 /opt/mihomo-gateway/setup.py --rollback latest`。若安装报
“rollback incomplete”，使用它打印的精确恢复入口和备份，不猜路径、不换成
`latest`。详见[一键部署恢复说明](../one-click-setup.zh-CN.md)及
[安装器源码](../../gateway/setup.py)。

### `automation/install.sh`

`--dry-run`只打印计划；正常安装覆盖其拥有的脚本/unit，但已存在的部署数据
文件会保留。它没有整套备份事务或通用`--rollback`命令。重装前须按本机真实
变更清单，另行准备并核验所有将覆盖文件的恢复副本；不能套用上面的
`setup.py`命令。见[安装器](../../automation/install.sh)。

### automation 订阅更新 / DNS 切换

订阅脚本在更新门禁失败时自动尝试恢复刚保存的旧 `config.yaml`；DNS 模式脚本
在校验/重载失败时尝试恢复上一个 upstream 文件。这是单次脚本事务，不是整机
恢复，也不代表回滚本身成功。先保留报错与备份标识，再按该脚本和当前系统
状态决定人工恢复；见[订阅更新](../../automation/sbin/mihomo-update-subscription)
与[DNS 模式切换](../../automation/sbin/mihomo-dns-mode)。

**执行任何恢复前**：保持独立管理通道；确认备份来自当前安装、范围/时间
正确且可读；保存当前配置摘要和服务状态；确认不会覆盖其它防火墙、容器或
新部署数据。`nft -c -f` 仅语法检查，不证明端到端恢复。

恢复后从真实 LAN 客户端重新验证 DHCP、DNS、TCP/UDP 与管理连接。具体候选
文件与步骤因安装路径而异，本页不提供通用 `cp`/`nft -f` 回滚配方。

## 事件记录模板

每次事件至少记录：

```text
时间（含时区）/发现方式：
安装路径与变更编号（匿名化）：
影响范围（网关本机 / DNS / 显式代理 / 真实 LAN；客户端角色匿名）：
只读基线及时间戳：
四层探针结果（HTTP 状态/超时；未知要明确写未知）：
健康状态模式、动作、状态新鲜度：
最近变更、自动任务/锁状态、已核实的备份编号：
已做的动作及授权依据：
结果、仍未验证的路径、下一项低风险检查：
证据保存位置（本地私有；共享版已脱敏）：
```

命令输出、抓包、控制器 API、备份及日志都可能带出私网拓扑、订阅、节点名、
认证头或密钥。原始证据留在受限位置；对外报告只保留必要的匿名状态与可
复现结论。
