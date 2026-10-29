# 网关自动化层（已发布源码）

这里是本指南的**可执行部分**：Mihomo / MosDNS 组合在网关上的 systemd 单元，以及健康检查、订阅与规则更新、
原生路由配置和预算准入。内容已脱敏：只包含机制代码与配置骨架，不包含订阅链接、节点名、节点凭据、
管理密钥或本部署的真实地址。站点地址、端口、接口集中配置在 `/etc/mihomo-automation.env`；dnsmasq 的配置不展开环境变量，
随仓库发布的是 RFC 5737 文档地址占位，安装后按站点改写。

`automation/` 只描述**怎么运行**；节点能力（哪些节点允许下载、哪些属于 AI 池）属于部署数据，见下文
[部署数据](#部署数据不随仓库发布)。

## 目录

| 路径 | 内容 | 安装目标 |
|---|---|---|
| `sbin/` | 可执行脚本 | `/usr/local/sbin/`（0755） |
| `systemd/` | 单元与 drop-in 片段 | `/etc/systemd/system/`（0644） |
| `etc/dnsmasq-upstreams.*` | 三份 dnsmasq 上游基线（direct / mihomo / mosdns） | `/etc/mihomo/` |
| `etc/dnsmasq/` | 两个 dnsmasq 作用域与初始上游：`resolver.conf`（环回）、`vlan-pilot.conf`、`upstreams.conf` | `/etc/dnsmasq.d/resolver.conf`、`/etc/dnsmasq-vlan-pilot.conf`、`/etc/dnsmasq-upstreams.conf`（仅缺失时播种） |
| `etc/mosdns/` | MosDNS 配置骨架与两份自定义域名规则 | `/etc/mosdns/` |
| `etc/mihomo/config.local.yaml` | 本机覆盖段（不含凭据） | `/etc/mihomo/config.local.yaml`（已存在则保留） |
| `policy/capabilities.yaml` | 稳定性、节点能力与私有路由覆写模板（`overlay` 段为空） | `/etc/mihomo/adaptive/capabilities.yaml` |
| `policy/route-defaults.yaml` | 公共下载与视频域名路由默认表 | `/etc/mihomo/adaptive/route-defaults.yaml`（0644）|
| `env/` | 部署参数与 reconcile 目标的示例 | `/etc/mihomo-automation.env.example`、`/etc/mihomo/` |
| `env/health-probes.conf.example` | 可选的分路径 HTTP 探测示例，默认不启用 | `/etc/mihomo/health-probes.conf.example` |
| `etc/systemd/journald.conf.d/` | 有容量和保留期限的持久日志配置 | `/etc/systemd/journald.conf.d/` |
| `etc/NetworkManager/dispatcher.d/` | 管理无线源路由 dispatcher（读 `MANAGEMENT_INTERFACE` / `MIHOMO_MANAGEMENT_CONNECTION_UUID`） | `/etc/NetworkManager/dispatcher.d/90-wifi-management-routing`（0700；仅 `/etc/NetworkManager` 存在时安装） |
| `check-release.py` / `release-manifest.json` | 发布包身份、受管文件内容与已安装基线校验 | 安装记录为 `/var/lib/mihomo-automation/installed-manifest.json` |
| `install.sh` | 安装器（支持 `--dry-run` / `GATEWAY_ROOT`；只 enable 真机在用的单元，其余装上但保持 disabled） | — |

## 脚本职责

| 脚本 | 作用 |
|---|---|
| `dnsmasq-safe-start` | 用 direct 基线原子安装 `/etc/dnsmasq-upstreams.conf`，先 `dnsmasq --test` 再放行，避免首次启动时 DNS 不可用 |
| `mihomo-dns-mode` | 切换 dnsmasq 上游：`auto` / `mosdns` / `mihomo` / `direct`，带锁、候选验证与原文件回退；不承诺活动文件原子替换 |
| `mihomo-healthcheck` | 启动只检查核心进程、TUN、`rp_filter` 表、`redirect to` 规则及策略路由；出口抽样失败记 `degraded` 并告警，不停止或重启核心。进程停止立即尝试恢复，本地转发结构连续失败 3 次才尝试重启，每小时至多一次。`--observe-only` 不自愈，`--egress` 独立严格探测，`--state` 读取状态；启动或自身重启后出口缓存置 `unknown`，由下一轮 timer 重新探测 |
| `mihomo-alert` | ServerChan + Bark 通知：投递被服务端接受后才记录冷却；`--health` 冷却抑制返回 75，投递失败返回非零，健康检查保留待通知状态并重试；通知失败不影响转发。`--test` 自检，无密钥时只记 journal |
| `mihomo-watch` | 全域巡检：failed 单元、温度/降频位、磁盘、内存、WAN 链路与网关可达；按边沿触发推送，恢复也发一条 |
| `mihomo-update-subscription` | 短锁取得基线 → 无维护锁下载、规范化并准备候选 → 锁内核对输入、原子切换、重启并验收；按事务阶段恢复，见下文 |
| `mihomo-subscription-normalize` | 把 Clash YAML、base64 配置与 URI 列表规范化为 `proxies` 列表，剔除公告占位；诊断不回显 URI、凭据或原始解析值 |
| `mihomo-subscription-candidate` | 离线生成订阅候选配置：校验来源、合并与重命名引用、应用现有 overlay、判断最终变化；不安装或重载 |
| `mihomo-update-rules` | 更新受管规则集，遇到 GitHub 403 / 配额耗尽时保留旧规则 |
| `mihomo-policy-overlay` | 合并公共 `route-defaults.yaml` 与私有 `capabilities.yaml`，生成服务分组、授权下载池、YouTube/Instagram/X 域名路由和稳定性策略 |
| `mihomo-reconcile-policy` | 清理不属于当前候选池的 Fallback 固定选择，再按 `reconcile-targets.conf` 收敛 Selector；`--repair-stale` 仅清理失效固定选择，不应用 Selector 映射，不改池成员或重启核心 |
| `mihomo-measurement-policy` | 默认暂停、原生 URLTest 健康检查所有权和持久账户账本 |
| `mihomo-measurement-budget` | 固定补丁核心的本地准入服务；原生 URLTest 发起前扣费，拒绝不污染健康历史；受管启动验证协议及账本绑定 |
| `mihomo-core-swap` | 校验新二进制并原子替换 Mihomo 核心，保留上一版本供回退 |
| `mihomo-dnsmasq-guard` | 分别探测配置的客户端 DNS 的 UDP/TCP、实际上游和宿主 MosDNS；只认 `NOERROR` 中的合法 A 答案。客户端两种传输全无答案且实际上游有答案时，按 20 秒间隔、3 次/15 分钟预算恢复指定单元；单域名或单传输失败只降级。维护期继续观测但不竞争重启；`--observe-only` 只写观测，不重启、通知或写恢复计数 |
| `mihomo-download-quality` | 按能力清单采样下载类叶节点的出口质量并写 provider 评分；默认不在 timer 上跑 |
| `mihomo-social-quality` | 复用下载质量采样器，只覆盖 X 视频场景；同样默认不跑 |
| `mosdns-healthcheck` | 分别观测国内解析、代理 fake-IP 与 metrics；部分失败只降级，保留可用分支。进程停止或所有信号连续静默 3 次才尝试恢复，每小时至多一次；失败/恢复通知独立记账 |
| `mosdns-update-rules` | 原子更新 MosDNS 路由数据并校验（单元名是 `mosdns-rules-update.service`） |
| `mosdns-update-binary` | 从官方稳定版更新 MosDNS 二进制，核对后切换 |
| `mosdns-rollout-rollback` | 只接受 `/var/backups/mosdns-rollout-*.tar.gz` 的回滚点，回退单元与脚本 |
| `mosdns-emergency-direct` | 紧急旁路：停用 MosDNS 及其 timer，DNS 走直连 |
| `mosdns-resume-auto` | 恢复 MosDNS 服务与三个 timer |
| `homeassistant-access-guard` | 拒绝上游接口访问 Home Assistant UI（tcp/8123），保持容器/主机数据库与配置独立备份 |

## systemd 单元与定时任务

服务单元：`mihomo.service`（含 `hardening.conf`、`ucg-link.conf`、`dns-failover.conf`、`measurement-budget.conf`、
`zz-startup-probe.conf`、`zz-alert.conf` 六个 drop-in）、`mihomo-measurement-budget.service`、`mihomo-watch.service`、
`mosdns.service`、`dnsmasq-vlan-pilot.service`、`dnsmasq.service.d/boot-safe.conf`、`homeassistant-access-guard.service`、
`mihomo-alert@.service`，以及下表脚本对应的一次性 `.service`。
预算准入服务在真机上启用，`measurement-budget.conf` 把核心的 `ExecStart` 换成 `--run-core` 包装；
预算 drop-in 就是 `systemd/mihomo.service.d/measurement-budget.conf`，不再有单独的 `optional/` 副本。
带 `Exec*` 且依赖部署参数的单元统一加载 `EnvironmentFile=-/etc/mihomo-automation.env`；`mihomo-watch.service`
与 `mihomo-alert@.service` 读 `/etc/mihomo-alert.env`。`homeassistant-access-guard.service` 是一次性单元，从 WAN、WLAN
口 `REJECT` tcp/8123，上游侧访问不到 Home Assistant UI；脚本自己加载 `/etc/mihomo-automation.env`，单元不另设
`EnvironmentFile`。

| Timer | 周期 | 作用 |
|---|---|---|
| `mihomo-healthcheck.timer` | 每 1 分钟（抖动 3s） | 内核状态检查；出口抽样每 5 分钟一次，不计测量预算 |
| `mihomo-watch.timer` | 开机 90s 后，之后每 2 分钟（抖动 5s） | 全域异常巡检（单元/温度/磁盘/内存/WAN），按边沿触发告警 |
| `mihomo-dnsmasq-guard.timer` | 启动 30 秒后，之后每 30 秒（抖动 5 秒） | 客户端 UDP/TCP 与实际上游分层观测，只恢复满足条件的客户端解析器 |
| `mosdns-rules-update.timer` | 04:20（抖动 30min） | MosDNS 路由数据更新 |
| `mosdns-binary-update.timer` | 周日 05:10（抖动 1h） | MosDNS 二进制更新 |
| `mosdns-healthcheck.timer` | 启动 30s 后，之后每 1 分钟 | MosDNS 分流自检与回退选择 |

另有四个随仓库安装、在真机上保持 disabled 的 timer：`mihomo-rules-update.timer`、`mihomo-subscription-update.timer`、
`mihomo-download-quality.timer`、`mihomo-social-quality.timer`。订阅管线会整份重写配置、质量采样消耗预算且改 provider，
都要人工确认时机；`install.sh` 只 `enable` 上表六个，把这四个 `disable`，从旧发布升级时这步幂等。

`Persistent=true` 的 timer 可能在首次启动或补执行时立即触发一次，不能只看 `OnCalendar` 判断是否会执行。
出口探测不属于启动门禁；探测目标或代理节点不可达不应带走国内 DIRECT 转发。
MosDNS 不依赖 Mihomo 启动成功，其启动/停止 hook 使用有界 `auto` 选择，国内解析正常即可保留 MosDNS。
这里的 `mihomo-dns-mode` 只管理本机环回 dnsmasq；VLAN 客户端的 dnsmasq 仍按部署配置选择上游。
因此本机 `127.0.0.1:53` 或 `5335` 正常不能代替 VLAN 客户端的 UDP/TCP DNS 与实际流量验收。
`automation/install.sh` 默认 `enable` 12 个单元（上表六个 timer 加六个服务，不 `start`），四个休眠 timer 显式 `disable`。
`measurement.enabled` 默认 false，Mihomo 原生 URLTest 暂停；显式启用时仍须配置原生预算服务与账户，周期下限 900 秒。
下载、GitHub、模型和 Package Download 在授权叶节点间自动选择；YouTube/Instagram 的 Streaming 与 X 的 Social
保留部署顺序并使用原生 fallback。完整契约见[运维手册](../docs/operations.zh-CN.md)。
编辑 manifest 不会直接改变运行中的配置；下一次满足更新间隔且通过校验和健康门禁的订阅更新，
会重新生成并加载 overlay。随后核对运行配置的检测周期、Telegram 网段及实际业务路径。

升级自带旧选路单元的真实主机时，当前安装器只安装仓库中的现行文件并 `disable` 上述四个休眠 timer，
不会删除主机上已退役的 adaptive 单元文件。管理员应先人工核对旧单元是否在运行、是否有在途作业，
再按维护窗口停用并移除它们；本轮没有对真实主机执行删除。

## 安装

```sh
sudo automation/install.sh --dry-run        # 只打印计划，不碰文件系统
sudo automation/install.sh                  # 安装 24 个 sbin 与整棵 unit 树，enable 12 个单元（不 start）
GATEWAY_ROOT=/tmp/gw automation/install.sh  # 沙箱演练（不需要 root，也不装到真实系统）
```

安装语义：

- `sbin/` 与 `systemd/` **覆盖式**安装，保证脚本与单元始终是仓库版本；
- 部署数据（`capabilities.yaml`、`route-defaults.yaml`、MosDNS 配置与规则、`config.local.yaml`、三份 dnsmasq 上游基线、
  dnsmasq 的 `resolver.conf` / `vlan-pilot.conf`、两份示例 env）**已存在则保留**，不会覆盖运维人员改过的文件；
- `route-defaults.yaml` 独立安装为 0644 公共数据，不写入或替换已有私有 `capabilities.yaml`；旧清单缺少路由表时使用这份默认表；
- 创建 0700 权限的 `/etc/mihomo/{secrets,adaptive,providers}` 与 `/var/lib/mihomo-{measurements,extra-subscriptions,subscription-backups}`；
- 若 `/etc/dnsmasq-upstreams.conf` 不存在，用 `etc/dnsmasq/upstreams.conf` 播种，保证代理栈启动前 DNS 可用；
- 只有在运行 systemd 的主机上才 `systemctl daemon-reload`，随后 `enable` 发布层 12 个单元、`disable` 四个休眠 timer；
  `enable` 只决定开机自启，首次部署仍需手工 `start` 常驻单元；
- dispatcher 只在 `/etc/NetworkManager` 存在时装成 0700；仍不改动 sshd、路由、sysctl 或 nftables（管理无线源路由由 dispatcher 在 NetworkManager 事件里自己加）。

安装后仍需人工完成：把 `env/automation.env.example` 与 `env/mihomo-alert.env.example` 复制成 `/etc/mihomo-automation.env`、
`/etc/mihomo-alert.env`（0600）并填入真实值；放入订阅链接与面板 token；
填写 `capabilities.yaml` 的 `overlay` 段与 `reconcile-targets.conf`；改写 dnsmasq 配置里的文档占位地址；确认 dispatcher 已按 NetworkManager 连接事件生效；
按需 `systemctl start` 常驻单元（`enable` 只决定开机自启）。

安装前，`check-release.py` 先核对包内文件与 `release-manifest.json`，再按上一份安装记录检查受管文件漂移；
校验失败时不会先覆盖文件。安装完成且文件与候选清单一致后，才原子记录新的安装基线。
发布维护者在审查最终文件后生成清单，再验证；部署者不要用重生成清单或手工 `--record` 绕过差异：

```sh
python3 automation/check-release.py --write  # 发布维护者：最终审查后生成
python3 automation/check-release.py          # 校验发布包身份
sudo python3 automation/check-release.py --root /  # 只读核对已安装基线
```

首次纳管时，已有受管文件若与候选不同，也会拒绝安装；应保留差异和备份，完成有依据的迁移审查。
现有私有 env、能力清单和订阅等站点数据不会与公共模板逐字比较，也不会被模板覆盖。

## 部署参数（`/etc/mihomo-automation.env`）

`env/automation.env.example` 是发布层的 env 模板，覆盖可配置的全部部署参数；共享的 mixed/controller 端口、WAN 接口与管理无线接口
按站点填写，其余独有参数同样在这里审阅。把 env 安装为 **0600** 并排除在版本控制之外。
已有部署应按模板合并所需字段，保留现有订阅镜像、能力清单路径和管理接口等设置；不要直接覆盖已有 env。
脚本内置同样的示例默认值，因此不写 env 文件也能启动，但上线前必须改成实际值。

| 变量 | 示例 | 作用 |
|---|---|---|
| `MIHOMO_MIXED_PORT` | `7890` | Mihomo HTTP/SOCKS 混合端口 |
| `MIHOMO_CONTROLLER_PORT` | `9090` | Mihomo external controller 端口 |
| `SUBSCRIPTION_BACKUP_URL` | 空 | 供应商文档提供的镜像链接；留空时只用 `subscription.url` 单链接 |
| `MIHOMO_SUBSCRIPTION_TIMEOUT_SECONDS` | `1800` | 一次更新的工作时限；下载、规范化、校验、重启和验收均使用剩余时间 |
| `MIHOMO_SUBSCRIPTION_CLEANUP_TIMEOUT_SECONDS` | `240` | 失败后的独立恢复总时限 |
| `MIHOMO_SUBSCRIPTION_FAILURE_COOLDOWN_SECONDS` | `21600` | 相同失败来源与策略指纹的冷却期；`--force` 可明确重试 |
| `DNS_CLIENT_SERVER` / `DNS_CLIENT_PORT` | `127.0.0.1` / `53` | 实际向客户端提供 DNS 的地址与端口；VLAN 部署必须填写真实目标 |
| `DNS_CLIENT_UNIT` | `dnsmasq` | 该客户端 DNS 实例所属的 systemd 单元，不能误填成另一个解析器 |
| `DNS_UPSTREAM_SERVER` / `DNS_UPSTREAM_PORT` | `127.0.0.1` / `5335` | 客户端解析器实际使用的上游；直接接 Mihomo DNS 的站点应填写对应监听端口 |
| `DNS_PROBE_DOMAINS` | `www.baidu.com www.qq.com` | 用空格分隔的 DNS 探测域名 |
| `WAN_INTERFACE` | `wan0` | 上游（WAN）接口名 |
| `WLAN_INTERFACE` | `wlan0` | 管理无线接口 |
| `MANAGEMENT_INTERFACE` | `wlan0` | 可选 dispatcher 观察的接口 |
| `MIHOMO_MANAGEMENT_CONNECTION_UUID` | 空 | 留空表示该接口上的所有连接都接受；填值后只对指定连接动作 |
| `MIHOMO_RECONCILE_TARGETS` | `/etc/mihomo/reconcile-targets.conf` | Selector → 目标组 的映射文件 |
| `MIHOMO_AUTOMATION_ENV_FILE` | `/etc/mihomo-automation.env` | reconcile 读取的部署参数文件；覆盖此路径须通过调用环境传入 |
| `MIHOMO_RECONCILE_SECRET_FILE` | `/etc/mihomo/secrets/dashboard.secret` | reconcile 使用的控制器凭据文件 |
| `MIHOMO_RECONCILE_LOCK_FILE` | `/run/lock/mihomo-reconcile-policy.lock` | 防止 reconcile 并发执行 |
| `MIHOMO_RECONCILE_TIMEOUT_SECONDS` | `15` | reconcile 整次执行时限（秒），允许 1–30 |
| `MIHOMO_CAPABILITIES` | `/etc/mihomo/adaptive/capabilities.yaml` | 能力清单路径 |
| `MIHOMO_ROUTE_DEFAULTS` | `/etc/mihomo/adaptive/route-defaults.yaml` | 独立公共域名路由默认表路径 |
| `MIHOMO_MEASUREMENT_SOCKET` | `/run/mihomo-measurements/budget.sock` | 准入服务的 unix socket |
| `EGRESS_EVERY` | `300` | 健康检查新鲜出口抽样间隔（秒）；`0` 表示每轮 timer 都采样 |
| `EGRESS_URL` | `https://www.gstatic.com/generate_204` | 出口抽样的 204 目标 |
| `EGRESS_PROBES_FILE` | `/etc/mihomo/health-probes.conf` | 可选的分路径探测文件；不存在时沿用 `EGRESS_URL` 单目标 |
| `EGRESS_ALERT_FAILURES` / `EGRESS_RECOVERY_SUCCESSES` | `1` / `1` | 告警、恢复通知所需的连续新鲜样本数，均须为正整数；缓存读取不推进计数 |

## 订阅事务与恢复

健康检查 timer 每分钟补偿清理一次失效 Fallback 固定选择，维护锁忙时跳过，API 失败留待下轮重试。
它只处理非空 `fixed` 已不在该组 `all` 中的状态；有效固定选择和 Selector 保持原样。
清理使用原生 controller DELETE，并读回确认；此接口清除选择及缓存，不删除节点。
这项补偿不参与核心启动门禁，不因控制器或代理出口失败停止核心。它存在一个巡检周期的发现窗口；
GET 与 DELETE 不具备原子比较更新能力，维护时应避免并发面板改选。业务可用仍需客户端验证。

更新器只在取得基线和提交阶段持有共享维护锁，下载、规范化和候选生成期间释放它，避免阻挡健康恢复。
提交前在锁内重核配置、策略、订阅来源、选择器目标、相关 helper 和核心二进制等输入；有并发变化就丢弃候选。
新配置通过同目录临时文件与原子 rename 安装，再重启核心替换 provider 生命周期，不能以 reload 代替。

原子切换前失败保持原配置字节不变；切换后、验收前失败则恢复旧节点并重新应用当前策略，再检查进程与本地转发结构。
critical HTTP 验收同时要求 curl 成功退出和预期状态码，半响应即使打印了 `204` 也不能通过。
候选验收通过并持久记录 `accepted` 是提交点：之后缓存写入失败会恢复整套旧缓存，保留已验收的新核心，
返回非零并明确报告缓存尚未完成，不再为缓存故障回滚或重启核心。

事务记录和恢复快照保存在 `/var/lib/mihomo-subscription-backups/`。被中断的事务在下次调用时先执行本地恢复，
本次恢复完成后退出，不立即联网重装同一候选；已 `accepted` 的事务只修复缓存，尚未验收的事务恢复旧配置。
当前配置出现不属于该事务的变化时保留冲突和恢复记录，不覆盖独立修改。相同失败候选在冷却期内跳过，
需要再次尝试时显式使用 `--force`；日志与冷却记录不保存原始 URI 或凭据。

默认工作时限为 1800 秒，恢复另有 240 秒总时限；子进程超时会终止其进程组。
`mihomo-subscription-update.service` 配套使用 `TimeoutStartSec=2100`、`TimeoutStopSec=300`，为恢复留出时间。
调整内部时限时必须同时检查外层单元的时限，避免 systemd 在恢复完成前强制终止事务。

## DNS、出口样本与日志

客户端 guard 的 `lan` / `resolver` 字段分别对应配置的客户端 DNS 与其实际上游，另有 `client_udp`、`client_tcp`、
`host_mosdns`、`unit`、`upstream`、`maintenance` 等观测，写入 `/run/mihomo-dnsmasq-guard/status`。
宿主 MosDNS 正常不能代替实际核心 DNS 上游正常，单域名失败也不能作为重启整个解析器的依据。

可选探测文件每行使用 `label|proxy或direct|接受的HTTP状态码|URL`，状态码可用逗号分隔，每次最多 6 条小请求。
安装器只放置 `.example`，需核对站点路由后才启用活动文件。每条结果独立写入 `/run/mihomo-healthcheck/paths`，
聚合状态为 `ok`、`partial`、`fail` 或尚未采样的 `unknown`；部分失败会降级并按确认计数发送通知，保持核心运行。
这些样本证明所选路径的传输可达，不证明登录、播放或完整业务成功。

持久日志配置为 `Storage=persistent`、`SystemMaxUse=512M`、`RuntimeMaxUse=64M`、`MaxRetentionSec=14day`。
安装后应确认 journald 配置生效、跨启动日志可回读；保留时间同时受容量上限约束。

## 部署数据（不随仓库发布）

| 位置 | 内容 | 权限 |
|---|---|---|
| `/etc/mihomo/secrets/subscription.url` | 供应商订阅链接（唯一副本） | 0600 |
| `/etc/mihomo/secrets/subscriptions.d/` | 额外订阅链接 | 0600 |
| `/etc/mihomo/secrets/dashboard.secret` | 面板 / controller token | 0600 |
| `/etc/mihomo/adaptive/capabilities.yaml` 的 `overlay:` | 媒体池、AI 池、Auto / Developer、固定地区池的节点标签 | 0600 |
| `/etc/mihomo/reconcile-targets.conf` | 组名 = 目标组 | 0600 |
| `/var/lib/mihomo-measurements/quality-budget.json` | 原生 URLTest 共用的持久账户尝试账本；保留旧账户键与未过期记录 | 0600 |
| `/var/lib/mihomo-extra-subscriptions/` | 已验收的规范化额外订阅缓存 | 0700 |
| `/var/lib/mihomo-subscription-backups/` | 私有配置与缓存快照、事务恢复记录、失败指纹；不随仓库发布 | 0700；记录文件 0600 |
| `/var/lib/mihomo-automation/installed-manifest.json` | 已安装受管文件的发布身份与内容摘要 | 0600 |

仓库里的 `capabilities.yaml` 只提供稳定性规则、空账户、空 `overlay` 节点表和可选的私有路由覆写；公共下载/视频域名表由 `route-defaults.yaml` 唯一提供。旧部署清单缺少 `download_routes` / `video_routes`，或值为 YAML `null` / `{}` 时，安装器保留原文件，overlay 会加载这份独立默认表。旧 `download_quality.profiles` 中带域名的业务配置会按 profile ID 迁入下载路由：默认同 ID 的域名与旧域名合并，旧组名沿用；显式新路由表同 ID 项优先替换迁入项。自定义路由项需要至少一个域名，空域名表会拒绝候选，避免静默丢路由。节点标签是部署数据，
公开版一律留空：`mihomo-policy-overlay` 在某个池没有任何可用节点时会**直接以非 0 退出并且不写出配置**（fail-closed），
不会生成一个空池或半成品配置。

## 安全与失效边界

- **fail-closed**：没有可用订阅节点时不落盘；配置变更先语法验证再应用。订阅失败按上述提交点恢复，不能把缓存未完成报告成整笔事务成功；恢复失败保留记录并报告备份位置。
- **路由边界**：原生 `url-test` 只在已授权叶节点间选路；fallback 使用既有部署顺序。Instagram 复用 Streaming，平台规则不会扩展 AI 或下载节点权限。
- **预算边界**：核心的启动、周期、失败、刷新与回滚 URLTest 均经本地准入服务，每个原生检查固定预扣 2 次额度；锁内重读当前配置和策略，失联或超额只拒绝检测、不改变业务路由。沿用 `quality_attempts_per_hour` 账本键和原账本路径。这不是业务 TCP 总连接数上限，见[运维手册](../docs/operations.zh-CN.md)。
- **互斥**：定时任务用 `flock` 串行化（Mihomo 维护一条锁，MosDNS 各任务一条锁），避免更新与检查互相踩踏。
- **DNS 自愈**：`mihomo-dns-mode` 带锁选择并验证 dnsmasq 上游；已有活动文件可能原位写入，失败时尝试恢复原文件，不把候选临时文件等同于原子发布。
- **上线验收**：拒绝以纯 IP 保活、进程存活或 Pi 本机显式代理成功代替验收后上线。必须验证真实客户端的 UDP/TCP DNS、国内域名直连、局域网，以及需要代理的域名与实际业务；出口失败只能降级，真正的核心或本地转发结构故障仍须报告失败。
- **可信内网前提**：本方案以单点、可信内网为边界，入站只放行 `lo`、established、上游 LAN 的 SSH/8799 与 DHCP，VLAN20 的管理口单独放行；不作为访客网络的加固策略。
- **不影响宿主其它服务**：本层只管理自己的单元、`/etc/mihomo`、`/etc/mosdns`、`/etc/dnsmasq-upstreams.conf`、
  `/etc/dnsmasq.d/resolver.conf`、`/etc/dnsmasq-vlan-pilot.conf` 与自己的状态目录。

## 仓库门禁

在检出目录运行（只需 Python 3 标准库与 PyYAML）：

```sh
python3 automation/check-release.py       # 发布清单与文件摘要
python3 tools/check_release_integrity.py   # 发布身份、安装漂移与输入保护
python3 tools/check_scoped_probes.py       # 分业务出口观测与局部降级
python3 tools/check_reconcile_policy.py    # Fallback 失效固定选择修复与有效选择保留
python3 tools/check_gateway_resilience.py  # 出口失败不终止核心与本地结构门禁
python3 tools/check_dns_resilience.py      # DNS 故障边界与恢复
python3 tools/check_alert_delivery.py      # 通知重试与冷却
python3 tools/check_rollback_recovery.py   # 回滚与恢复
python3 tools/check_mihomo_policy.py       # Mihomo 分组与路由契约
python3 tools/validate_public.py    # 发布结构、隐私模式（含订阅节点标签分隔符）、图表可解析
python3 tools/check_image.py        # 恢复镜像的自检与拒绝写入磁盘
python3 tools/check_overlay.py      # 用合成配置驱动 policy-overlay：池顺序、备份池、幂等性、空 manifest 时 fail-closed
python3 tools/check_subscription_candidate.py  # 真实候选生成入口：来源合并、引用重命名、缓存、未变化与失败保护
python3 tools/check_subscription_update.py  # 完整订阅更新：21 种离线场景，包括原子切换前后故障、撤权、重启与缓存保护
python3 tools/check_update_transaction.py  # 真实CLI事务：锁、并发输入、时限、崩溃恢复、缓存持久顺序与规范化
python3 tools/check_client_dns_guard.py    # 客户端UDP/TCP、实际DNS上游、合法答案、维护互斥与恢复预算
python3 tools/check_dnsmasq_guard.py       # 宿主兼容目标与持续局部DNS故障不重启
python3 tools/check_automation.py   # 语法、ASCII 与禁值扫描、env 变量双向一致、单元加载 env、沙箱安装、运行时参数化
python3 tools/check_stability.py    # 暂停生成、进程检查与恢复不产生业务探测
python3 tools/check_measurement_policy.py # 原生检测所有权、持久账本、锁与并发
python3 tools/check_native_budget.py # 核心准入、配置复验、固定预扣和服务失联拒绝
python3 tools/check_update_rules.py # 规则更新事务与回滚
python3 tools/check_publication_privacy.py  # 隐私扫描覆盖与检查器格式回归
```

`check_automation.py` 会把 `install.sh` 装进临时目录，断言安装结果与文档一致、24 个脚本全部安装、`enable` 集合恰好是发布层 12 个单元、四个休眠 timer 保持 `disable`、dnsmasq 三个文件按 `dnsmasq.d` / pilot 独立路径落位、升级时保留既有 `capabilities.yaml`、安装并保留独立的 `route-defaults.yaml`、`--dry-run` 不产生任何文件，并验证两个清单路径都能经环境传入订阅候选 overlay。
上述合成检查不替代真实 Linux 数据面、目标机 systemd 行为与客户端验收。
安装到目标系统前，先在目标系统上做语法检查（`nft -c -f`、`dnsmasq --test`、`mihomo -t`），见
[分阶段部署](../docs/deployment.zh-CN.md)。
