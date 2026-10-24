# 网关自动化层（已发布源码）

这里是本指南的**可执行部分**：Mihomo / MosDNS 组合在网关上的 systemd 单元，以及健康检查、订阅与规则更新、
原生路由配置和预算准入。内容已脱敏：只包含机制代码与配置骨架，不包含订阅链接、节点名、节点凭据、
管理密钥或本部署的真实地址。所有地址、端口、接口都从 `/etc/mihomo-automation.env` 读取，脚本自身可原样发布。

`automation/` 只描述**怎么运行**；节点能力（哪些节点允许下载、哪些属于 AI 池）属于部署数据，见下文
[部署数据](#部署数据不随仓库发布)。

## 目录

| 路径 | 内容 | 安装目标 |
|---|---|---|
| `sbin/` | 可执行脚本 | `/usr/local/sbin/`（0755） |
| `systemd/` | 单元与 drop-in 片段 | `/etc/systemd/system/`（0644） |
| `etc/dnsmasq-upstreams.*` | 三份 dnsmasq 上游基线（direct / mihomo / mosdns） | `/etc/mihomo/` |
| `etc/mosdns/` | MosDNS 配置骨架与两份自定义域名规则 | `/etc/mosdns/` |
| `policy/capabilities.yaml` | 稳定性、节点能力与私有路由覆写模板（`overlay` 段为空） | `/etc/mihomo/adaptive/capabilities.yaml` |
| `policy/route-defaults.yaml` | 公共下载与视频域名路由默认表 | `/etc/mihomo/adaptive/route-defaults.yaml`（0644）|
| `env/` | 部署参数与 reconcile 目标的示例 | `/etc/mihomo-automation.env.example`、`/etc/mihomo/` |
| `optional/networkmanager/` | 可选的管理 Wi-Fi 源路由 dispatcher，**不自动安装** | 自行审阅 |
| `install.sh` | 安装器（支持 `--dry-run` / `--enable-timers` / `GATEWAY_ROOT`） | — |

## 脚本职责

| 脚本 | 作用 |
|---|---|
| `dnsmasq-safe-start` | 用 direct 基线原子安装 `/etc/dnsmasq-upstreams.conf`，先 `dnsmasq --test` 再放行，避免首次启动时 DNS 不可用 |
| `mihomo-dns-mode` | 切换 dnsmasq 上游：`auto` / `mosdns` / `mihomo` / `direct`，带锁、候选验证与原文件回退；不承诺活动文件原子替换 |
| `mihomo-healthcheck` | 只读内核状态（TUN、两张 nft 表、策略路由、重定向计数），每 5 分钟经代理端口做一次 204 出口抽样；按 business/egress 变化发告警 |
| `mihomo-alert` | ServerChan + Bark 通知：失败/恢复按状态迁移去重发送，`--test` 自检，无密钥时只记 journal |
| `mihomo-watch` | 全域巡检：failed 单元、温度/降频位、磁盘、内存、WAN 链路与网关可达；按边沿触发推送，恢复也发一条 |
| `mihomo-update-subscription` | 下载主订阅与额外来源 → 生成候选 → 校验与备份 → 热加载并检查；公告占位订阅视为过期，失败回滚 |
| `mihomo-subscription-candidate` | 离线生成订阅候选配置：校验来源、合并与重命名引用、应用现有 overlay、判断最终变化；不安装或重载 |
| `mihomo-update-rules` | 更新受管规则集，遇到 GitHub 403 / 配额耗尽时保留旧规则 |
| `mihomo-policy-overlay` | 合并公共 `route-defaults.yaml` 与私有 `capabilities.yaml`，生成服务分组、授权下载池、YouTube/Instagram/X 域名路由和稳定性策略 |
| `mihomo-reconcile-policy` | 按 `reconcile-targets.conf` 把指定 Selector 收敛到目标组，只影响选择器，不改池成员 |
| `mihomo-measurement-policy` | 默认暂停、原生 URLTest 健康检查所有权和持久账户账本 |
| `mihomo-measurement-budget` | 固定补丁核心的本地准入服务；原生 URLTest 发起前扣费，拒绝不污染健康历史；受管启动验证协议及账本绑定 |
| `mosdns-healthcheck` | 校验 5335 上的分流行为与 metrics，失败先重试、再选择安全回退 |
| `mosdns-rules-update` | 原子更新 MosDNS 路由数据并校验 |
| `mosdns-update-binary` | 从官方稳定版更新 MosDNS 二进制，核对后切换 |
| `mosdns-rollout-rollback` | 只接受 `/var/backups/mosdns-rollout-*.tar.gz` 的回滚点，回退单元与脚本 |
| `mosdns-emergency-direct` | 紧急旁路：停用 MosDNS 及其 timer，DNS 走直连 |
| `mosdns-resume-auto` | 恢复 MosDNS 服务与三个 timer |
| `homeassistant-access-guard` | 拒绝上游接口访问 Home Assistant UI（tcp/8123），保持容器/主机数据库与配置独立备份 |

## systemd 单元与定时任务

服务单元：`mihomo.service`（含 `hardening.conf`、`dns-failover.conf`、`zz-startup-probe.conf`、`zz-alert.conf` 四个 drop-in）、`mihomo-alert@.service`、`mihomo-watch.service`、`mosdns.service`、
`dnsmasq.service.d/boot-safe.conf`、`homeassistant-access-guard.service`，以及下表脚本对应的 `.service`。
预算服务与 `optional/mihomo-native-budget.conf` 仅供固定补丁核心显式接入，默认不启用，后者不自动安装。
带 `Exec*` 且依赖部署参数的单元统一加载 `EnvironmentFile=-/etc/mihomo-automation.env`。

| Timer | 周期 | 作用 |
|---|---|---|
| `mihomo-healthcheck.timer` | 每 1 分钟（抖动 3s） | 内核状态检查；出口抽样每 5 分钟一次，不计测量预算 |
| `mihomo-watch.timer` | 开机 90s 后，之后每 2 分钟（抖动 5s） | 全域异常巡检（单元/温度/磁盘/内存/WAN），按边沿触发告警 |
| `mihomo-subscription-update.timer` | 04:30（抖动 15min） | 订阅更新（至少 24 小时间隔） |
| `mihomo-rules-update.timer` | 04:15（抖动 1h） | 规则集更新 |
| `mosdns-rules-update.timer` | 04:20（抖动 30min） | MosDNS 路由数据更新 |
| `mosdns-binary-update.timer` | 周日 05:10（抖动 1h） | MosDNS 二进制更新 |
| `mosdns-healthcheck.timer` | 启动 30s 后，之后每 1 分钟 | MosDNS 分流自检与回退选择 |

`Persistent=true` 的 timer 可能在首次启动或补执行时立即触发一次，不能只看 `OnCalendar` 判断是否会执行。
`automation/install.sh --enable-timers` 只启用上表六个维护 timer。`measurement.enabled` 默认 false，生成配置会暂停原生 URLTest；显式启用时仍须配置原生预算服务和账户，周期下限为 900 秒。下载、GitHub、模型和 Package Download 在授权叶节点间自动选择；YouTube/Instagram 的 Streaming 与 X 的 Social 保留部署顺序并使用原生 fallback。完整契约见[原生延迟选路与检测预算](../docs/adaptive-selection.zh-CN.md)。
编辑 manifest 不会直接改变运行中的配置；下一次满足更新间隔且通过校验和健康门禁的订阅更新，
会重新生成并加载 overlay。随后核对运行配置的检测周期、Telegram 网段及实际业务路径。

升级自带旧选路单元的真实主机时，当前安装器只安装仓库中的现行文件，不会自动停用或删除主机已有文件。
管理员应先人工核对旧单元是否在运行、是否有在途作业，再按维护窗口逐项停用并移除已退役的文件；本轮没有对真实主机执行这些操作。

## 安装

```sh
sudo automation/install.sh --dry-run        # 只打印计划，不碰文件系统
sudo automation/install.sh                  # 安装脚本与单元，创建运行目录
sudo automation/install.sh --enable-timers  # enable --now 六个默认 timer
GATEWAY_ROOT=/tmp/gw automation/install.sh  # 沙箱演练（不需要 root，也不装到真实系统）
```

安装语义：

- `sbin/` 与 `systemd/` **覆盖式**安装，保证脚本与单元始终是仓库版本；
- 部署数据（`capabilities.yaml`、`route-defaults.yaml`、MosDNS 配置与规则、dnsmasq 上游基线、两份示例 env）**已存在则保留**，不会覆盖
  运维人员改过的文件；
- `route-defaults.yaml` 独立安装为 0644 公共数据，不写入或替换已有私有 `capabilities.yaml`；旧清单缺少路由表时使用这份默认表；
- 创建 0700 权限的 `/etc/mihomo/secrets`、`/etc/mihomo/adaptive`、`/var/lib/mihomo-{adaptive,extra-subscriptions,subscription-backups,measurements}`；
- 若 `/etc/dnsmasq-upstreams.conf` 不存在，用 direct 基线播种，保证代理栈启动前 DNS 可用；
- 只有在运行 systemd 的主机上才 `systemctl daemon-reload`；
- **不安装** `optional/`，也不改动 sshd、NetworkManager、路由、sysctl 或 nftables。

安装后仍需人工完成：用 `tools/render.py` 生成私有配置与配套 `mihomo-automation.env`，审阅后按 0600 安装 env；放入订阅链接与面板 token；
填写 `capabilities.yaml` 的 `overlay` 段与 `reconcile-targets.conf`；单独审阅 `optional/`；按需启用 timer。

## 部署参数（`/etc/mihomo-automation.env`）

`tools/render.py` 以 `env/automation.env.example` 为模板生成配套 env，共享的 LAN 地址、mixed/controller 端口和 WAN 接口直接来自站点参数。
生成器不会把下游 LAN 接口当作无线管理接口；其余独有参数仍需审阅。把 env 安装为 **0600** 并排除在版本控制之外。
已有部署应合并这四个共享字段，保留现有订阅镜像、能力清单路径和管理接口等设置；不要直接覆盖已有 env。
脚本内置同样的示例默认值，因此不写 env 文件也能启动，但上线前必须改成实际值。

| 变量 | 示例 | 作用 |
|---|---|---|
| `GATEWAY_LAN_IP` | `10.77.0.1` | 网关 LAN 地址：dnsmasq 的 :53 / DHCP、Mihomo mixed 端口与 external controller 都监听在此，必须等于 `lan_address` |
| `MIHOMO_MIXED_PORT` | `7890` | Mihomo HTTP/SOCKS 混合端口 |
| `MIHOMO_CONTROLLER_PORT` | `9090` | Mihomo external controller 端口 |
| `SUBSCRIPTION_BACKUP_URL` | 空 | 供应商文档提供的镜像链接；留空时只用 `subscription.url` 单链接 |
| `WAN_INTERFACE` | `wan0` | 上游接口，与 `site.local.json` 的 `wan_interface` 一致 |
| `WLAN_INTERFACE` | `wlan0` | 下游 / 管理无线接口 |
| `MANAGEMENT_INTERFACE` | `wlan0` | 可选 dispatcher 观察的接口 |
| `MIHOMO_MANAGEMENT_CONNECTION_UUID` | 空 | 留空表示该接口上的所有连接都接受；填值后只对指定连接动作 |
| `MIHOMO_RECONCILE_TARGETS` | `/etc/mihomo/reconcile-targets.conf` | Selector → 目标组 的映射文件 |
| `MIHOMO_CAPABILITIES` | `/etc/mihomo/adaptive/capabilities.yaml` | 能力清单路径 |
| `MIHOMO_ROUTE_DEFAULTS` | `/etc/mihomo/adaptive/route-defaults.yaml` | 独立公共域名路由默认表路径 |

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
- **预算边界**：核心的启动、周期、失败、刷新与回滚 URLTest 均经本地准入服务，每个原生检查固定预扣 2 次额度；锁内重读当前配置和策略，失联或超额只拒绝检测、不改变业务路由。沿用 `quality_attempts_per_hour` 账本键和原账本路径。这不是业务 TCP 总连接数上限，见[预算契约](../docs/adaptive-selection.zh-CN.md)。
- **互斥**：定时任务用 `flock` 串行化（Mihomo 维护一条锁，MosDNS 各任务一条锁），避免更新与检查互相踩踏。
- **DNS 自愈**：`mihomo-dns-mode` 带锁选择并验证 dnsmasq 上游；已有活动文件可能原位写入，失败时尝试恢复原文件，不把候选临时文件等同于原子发布。
- **可信内网前提**：本方案以单点、可信内网为边界，LAN INPUT 整体放行，不使用可用于不可信访客网络的通用加固策略。
- **不影响宿主其它服务**：本层只管理自己的单元、`/etc/mihomo`、`/etc/mosdns`、`/etc/dnsmasq-upstreams.conf`
  与自己的状态目录。

## 仓库门禁

在检出目录运行（只需 Python 3 标准库与 PyYAML）：

```sh
python3 tools/validate_public.py    # 发布结构、隐私模式（含订阅节点标签分隔符）、图表可解析
python3 tools/check_render.py       # 生成器隔离、参数传播、拒绝不安全站点
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

`check_automation.py` 会把 `install.sh` 装进临时目录，断言安装结果与文档一致、升级时保留既有 `capabilities.yaml`、安装并保留独立的 `route-defaults.yaml`、`--dry-run` 不产生任何文件，并验证两个清单路径都能经环境传入订阅候选 overlay。
安装到目标系统前，先在目标系统上做语法检查（`nft -c -f`、`dnsmasq --test`、`mihomo -t`），见
[分阶段部署](../docs/deployment-current.zh-CN.md)。
