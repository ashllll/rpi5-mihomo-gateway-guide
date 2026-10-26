# 网关自动化层（已发布源码）

这里是本指南的**可执行部分**：Mihomo / MosDNS 组合在网关上的 systemd 单元，以及健康检查、订阅与规则更新、
原生路由配置和预算准入。内容已脱敏：只包含机制代码与配置骨架，不包含订阅链接、节点名、节点凭据、
管理密钥或本部署的真实地址。脚本的地址、端口、接口都从 `/etc/mihomo-automation.env` 读取；dnsmasq 的配置不展开环境变量，
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
| `etc/NetworkManager/dispatcher.d/` | 管理无线源路由 dispatcher（读 `MANAGEMENT_INTERFACE` / `MIHOMO_MANAGEMENT_CONNECTION_UUID`） | `/etc/NetworkManager/dispatcher.d/90-wifi-management-routing`（0700；仅 `/etc/NetworkManager` 存在时安装） |
| `install.sh` | 安装器（支持 `--dry-run` / `GATEWAY_ROOT`；只 enable 真机在用的单元，其余装上但保持 disabled） | — |

## 脚本职责

| 脚本 | 作用 |
|---|---|
| `dnsmasq-safe-start` | 用 direct 基线原子安装 `/etc/dnsmasq-upstreams.conf`，先 `dnsmasq --test` 再放行，避免首次启动时 DNS 不可用 |
| `mihomo-dns-mode` | 切换 dnsmasq 上游：`auto` / `mosdns` / `mihomo` / `direct`，带锁、候选验证与原文件回退；不承诺活动文件原子替换 |
| `mihomo-healthcheck` | 只读内核状态：TUN、`rp_filter` 表、策略路由与 **prerouting 链里实际存在的 `redirect to` 规则**（表存在但规则没装全按失败处理，这是包内显式修正的旧实现盲点），每 5 分钟经代理端口做一次 204 出口抽样；**进程活着但证据连续红 3 次即自愈重启核心，每小时至多一次**；`--startup-check [秒]` 是开机门禁，`--egress` 只抽样，`--state` 打印上次状态行；按 business/egress 变化发告警 |
| `mihomo-alert` | ServerChan + Bark 通知：失败/恢复按状态迁移去重发送，`--test` 自检，无密钥时只记 journal |
| `mihomo-watch` | 全域巡检：failed 单元、温度/降频位、磁盘、内存、WAN 链路与网关可达；按边沿触发推送，恢复也发一条 |
| `mihomo-update-subscription` | 下载主订阅与额外来源 → 生成候选 → 校验与备份 → 热加载并检查；公告占位订阅视为过期，失败回滚 |
| `mihomo-subscription-candidate` | 离线生成订阅候选配置：校验来源、合并与重命名引用、应用现有 overlay、判断最终变化；不安装或重载 |
| `mihomo-update-rules` | 更新受管规则集，遇到 GitHub 403 / 配额耗尽时保留旧规则 |
| `mihomo-policy-overlay` | 合并公共 `route-defaults.yaml` 与私有 `capabilities.yaml`，生成服务分组、授权下载池、YouTube/Instagram/X 域名路由和稳定性策略 |
| `mihomo-reconcile-policy` | 按 `reconcile-targets.conf` 把指定 Selector 收敛到目标组，只影响选择器，不改池成员 |
| `mihomo-measurement-policy` | 默认暂停、原生 URLTest 健康检查所有权和持久账户账本 |
| `mihomo-measurement-budget` | 固定补丁核心的本地准入服务；原生 URLTest 发起前扣费，拒绝不污染健康历史；受管启动验证协议及账本绑定 |
| `mihomo-core-swap` | 校验新二进制并原子替换 Mihomo 核心，保留上一版本供回退 |
| `mihomo-dnsmasq-guard` | 每 30 秒分层探测：先 `127.0.0.1:53` 两个国内域名，失败后退到 `127.0.0.1:5335`。dnsmasq 静默（0 个答案）→ 重启 dnsmasq 并告警；单域名慢/无答案 → 连续两轮先重启 mosdns、仍不恢复再重启 dnsmasq；重启预算 3 次/15 分钟，超出记 `throttled` 只告警；状态写 `/run/mihomo-dnsmasq-guard/status` |
| `mihomo-download-quality` | 按能力清单采样下载类叶节点的出口质量并写 provider 评分；默认不在 timer 上跑 |
| `mihomo-social-quality` | 复用下载质量采样器，只覆盖 X 视频场景；同样默认不跑 |
| `mosdns-healthcheck` | 校验 5335 上的分流行为与 metrics，失败先重试、再选择安全回退 |
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
| `mihomo-dnsmasq-guard.timer` | 启动 30 秒后，之后每 30 秒（抖动 5 秒） | 解析分层存活探针：dnsmasq/mosdns 活着但不出答案则分级重启 |
| `mosdns-rules-update.timer` | 04:20（抖动 30min） | MosDNS 路由数据更新 |
| `mosdns-binary-update.timer` | 周日 05:10（抖动 1h） | MosDNS 二进制更新 |
| `mosdns-healthcheck.timer` | 启动 30s 后，之后每 1 分钟 | MosDNS 分流自检与回退选择 |

另有四个随仓库安装、在真机上保持 disabled 的 timer：`mihomo-rules-update.timer`、`mihomo-subscription-update.timer`、
`mihomo-download-quality.timer`、`mihomo-social-quality.timer`。订阅管线会整份重写配置、质量采样消耗预算且改 provider，
都要人工确认时机；`install.sh` 只 `enable` 上表六个，把这四个 `disable`，从旧发布升级时这步幂等。

`Persistent=true` 的 timer 可能在首次启动或补执行时立即触发一次，不能只看 `OnCalendar` 判断是否会执行。
`automation/install.sh` 默认 `enable` 真机在用的 11 个单元（上表五个 timer 加六个常驻服务，不 `start`），四个休眠 timer 显式 `disable`。
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
sudo automation/install.sh                  # 安装 22 个 sbin 与整棵 unit 树，enable 真机在用的 11 个单元（不 start）
GATEWAY_ROOT=/tmp/gw automation/install.sh  # 沙箱演练（不需要 root，也不装到真实系统）
```

安装语义：

- `sbin/` 与 `systemd/` **覆盖式**安装，保证脚本与单元始终是仓库版本；
- 部署数据（`capabilities.yaml`、`route-defaults.yaml`、MosDNS 配置与规则、`config.local.yaml`、三份 dnsmasq 上游基线、
  dnsmasq 的 `resolver.conf` / `vlan-pilot.conf`、两份示例 env）**已存在则保留**，不会覆盖运维人员改过的文件；
- `route-defaults.yaml` 独立安装为 0644 公共数据，不写入或替换已有私有 `capabilities.yaml`；旧清单缺少路由表时使用这份默认表；
- 创建 0700 权限的 `/etc/mihomo/{secrets,adaptive,providers}` 与 `/var/lib/mihomo-{measurements,extra-subscriptions,subscription-backups}`；
- 若 `/etc/dnsmasq-upstreams.conf` 不存在，用 `etc/dnsmasq/upstreams.conf` 播种，保证代理栈启动前 DNS 可用；
- 只有在运行 systemd 的主机上才 `systemctl daemon-reload`，随后 `enable` 发布层 11 个单元、`disable` 四个休眠 timer；
  `enable` 只决定开机自启，首次部署仍需手工 `start` 常驻单元；
- dispatcher 只在 `/etc/NetworkManager` 存在时装成 0700；仍不改动 sshd、路由、sysctl 或 nftables（管理无线源路由由 dispatcher 在 NetworkManager 事件里自己加）。

安装后仍需人工完成：把 `env/automation.env.example` 与 `env/mihomo-alert.env.example` 复制成 `/etc/mihomo-automation.env`、
`/etc/mihomo-alert.env`（0600）并填入真实值；放入订阅链接与面板 token；
填写 `capabilities.yaml` 的 `overlay` 段与 `reconcile-targets.conf`；改写 dnsmasq 配置里的文档占位地址；确认 dispatcher 已按 NetworkManager 连接事件生效；
按需 `systemctl start` 常驻单元（`enable` 只决定开机自启）。

## 部署参数（`/etc/mihomo-automation.env`）

`env/automation.env.example` 是发布层的 env 模板，覆盖脚本读取的全部变量；共享的 mixed/controller 端口、WAN 接口与管理无线接口
按站点填写，其余独有参数同样在这里审阅。把 env 安装为 **0600** 并排除在版本控制之外。
已有部署应合并这四个共享字段，保留现有订阅镜像、能力清单路径和管理接口等设置；不要直接覆盖已有 env。
脚本内置同样的示例默认值，因此不写 env 文件也能启动，但上线前必须改成实际值。

| 变量 | 示例 | 作用 |
|---|---|---|
| `MIHOMO_MIXED_PORT` | `7890` | Mihomo HTTP/SOCKS 混合端口 |
| `MIHOMO_CONTROLLER_PORT` | `9090` | Mihomo external controller 端口 |
| `SUBSCRIPTION_BACKUP_URL` | 空 | 供应商文档提供的镜像链接；留空时只用 `subscription.url` 单链接 |
| `WAN_INTERFACE` | `wan0` | 上游（WAN）接口名 |
| `WLAN_INTERFACE` | `wlan0` | 管理无线接口 |
| `MANAGEMENT_INTERFACE` | `wlan0` | 可选 dispatcher 观察的接口 |
| `MIHOMO_MANAGEMENT_CONNECTION_UUID` | 空 | 留空表示该接口上的所有连接都接受；填值后只对指定连接动作 |
| `MIHOMO_RECONCILE_TARGETS` | `/etc/mihomo/reconcile-targets.conf` | Selector → 目标组 的映射文件 |
| `MIHOMO_CAPABILITIES` | `/etc/mihomo/adaptive/capabilities.yaml` | 能力清单路径 |
| `MIHOMO_ROUTE_DEFAULTS` | `/etc/mihomo/adaptive/route-defaults.yaml` | 独立公共域名路由默认表路径 |
| `MIHOMO_MEASUREMENT_SOCKET` | `/run/mihomo-measurements/budget.sock` | 准入服务的 unix socket |
| `EGRESS_EVERY` | `300` | 健康检查出口抽样间隔（秒）；`0` 关闭抽样 |
| `EGRESS_URL` | `https://www.gstatic.com/generate_204` | 出口抽样的 204 目标 |

## 部署数据（不随仓库发布）

| 位置 | 内容 | 权限 |
|---|---|---|
| `/etc/mihomo/secrets/subscription.url` | 供应商订阅链接（唯一副本） | 0600 |
| `/etc/mihomo/secrets/subscriptions.d/` | 额外订阅链接 | 0600 |
| `/etc/mihomo/secrets/dashboard.secret` | 面板 / controller token | 0600 |
| `/etc/mihomo/adaptive/capabilities.yaml` 的 `overlay:` | 媒体池、AI 池、Auto / Developer、固定地区池的节点标签 | 0600 |
| `/etc/mihomo/reconcile-targets.conf` | 组名 = 目标组 | 0600 |
| `/var/lib/mihomo-measurements/quality-budget.json` | 原生 URLTest 共用的持久账户尝试账本；保留旧账户键与未过期记录 | 0600 |
| `/var/lib/mihomo-extra-subscriptions/` | 额外订阅生成的 provider 文件 | 0700 |

仓库里的 `capabilities.yaml` 只提供稳定性规则、空账户、空 `overlay` 节点表和可选的私有路由覆写；公共下载/视频域名表由 `route-defaults.yaml` 唯一提供。旧部署清单缺少 `download_routes` / `video_routes`，或值为 YAML `null` / `{}` 时，安装器保留原文件，overlay 会加载这份独立默认表。旧 `download_quality.profiles` 中带域名的业务配置会按 profile ID 迁入下载路由：默认同 ID 的域名与旧域名合并，旧组名沿用；显式新路由表同 ID 项优先替换迁入项。自定义路由项需要至少一个域名，空域名表会拒绝候选，避免静默丢路由。节点标签是部署数据，
公开版一律留空：`mihomo-policy-overlay` 在某个池没有任何可用节点时会**直接以非 0 退出并且不写出配置**（fail-closed），
不会生成一个空池或半成品配置。

## 安全与失效边界

- **fail-closed**：没有可用订阅节点时不落盘；配置变更先语法验证再应用。订阅写入、重载、健康检查或信号中断失败时恢复旧配置；恢复失败会报告备份位置，额外订阅缓存只在健康门禁通过后更新。
- **路由边界**：原生 `url-test` 只在已授权叶节点间选路；fallback 使用既有部署顺序。Instagram 复用 Streaming，平台规则不会扩展 AI 或下载节点权限。
- **预算边界**：核心的启动、周期、失败、刷新与回滚 URLTest 均经本地准入服务，每个原生检查固定预扣 2 次额度；锁内重读当前配置和策略，失联或超额只拒绝检测、不改变业务路由。沿用 `quality_attempts_per_hour` 账本键和原账本路径。这不是业务 TCP 总连接数上限，见[运维手册](../docs/operations.zh-CN.md)。
- **互斥**：定时任务用 `flock` 串行化（Mihomo 维护一条锁，MosDNS 各任务一条锁），避免更新与检查互相踩踏。
- **DNS 自愈**：`mihomo-dns-mode` 带锁选择并验证 dnsmasq 上游；已有活动文件可能原位写入，失败时尝试恢复原文件，不把候选临时文件等同于原子发布。
- **可信内网前提**：本方案以单点、可信内网为边界，入站只放行 `lo`、established、上游 LAN 的 SSH/8799 与 DHCP，VLAN20 的管理口单独放行；不作为访客网络的加固策略。
- **不影响宿主其它服务**：本层只管理自己的单元、`/etc/mihomo`、`/etc/mosdns`、`/etc/dnsmasq-upstreams.conf`、
  `/etc/dnsmasq.d/resolver.conf`、`/etc/dnsmasq-vlan-pilot.conf` 与自己的状态目录。

## 仓库门禁

在检出目录运行（只需 Python 3 标准库与 PyYAML）：

```sh
python3 tools/validate_public.py    # 发布结构、隐私模式（含订阅节点标签分隔符）、图表可解析
python3 tools/check_image.py        # 恢复镜像的自检与拒绝写入磁盘
python3 tools/check_overlay.py      # 用合成配置驱动 policy-overlay：池顺序、备份池、幂等性、空 manifest 时 fail-closed
python3 tools/check_subscription_candidate.py  # 真实候选生成入口：来源合并、引用重命名、缓存、未变化与失败保护
python3 tools/check_subscription_update.py  # 完整订阅更新：16 种离线场景，包括写入、中断、重载、回滚与缓存保护
python3 tools/check_automation.py   # 语法、ASCII 与禁值扫描、env 变量双向一致、单元加载 env、沙箱安装、运行时参数化
python3 tools/check_stability.py    # 暂停生成、进程检查与恢复不产生业务探测
python3 tools/check_measurement_policy.py # 原生检测所有权、持久账本、锁与并发
python3 tools/check_native_budget.py # 核心准入、配置复验、固定预扣和服务失联拒绝
python3 tools/check_update_rules.py # 规则更新事务与回滚
python3 tools/check_publication_privacy.py  # 隐私扫描覆盖与检查器格式回归
```

`check_automation.py` 会把 `install.sh` 装进临时目录，断言安装结果与文档一致、`enable` 集合恰好是发布层 11 个单元、四个休眠 timer 保持 `disable`、dnsmasq 三个文件按 `dnsmasq.d` / pilot 独立路径落位、升级时保留既有 `capabilities.yaml`、安装并保留独立的 `route-defaults.yaml`、`--dry-run` 不产生任何文件，并验证两个清单路径都能经环境传入订阅候选 overlay。
安装到目标系统前，先在目标系统上做语法检查（`nft -c -f`、`dnsmasq --test`、`mihomo -t`），见
[分阶段部署](../docs/deployment.zh-CN.md)。
