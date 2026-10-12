# 网关自动化层（已发布源码）

这里是本指南的**可执行部分**：Mihomo / MosDNS 组合在网关上的 systemd 单元，以及健康检查、订阅与规则更新、
自适应评分与受门禁保护的应用脚本。内容已脱敏：只包含机制代码与配置骨架，不包含订阅链接、节点名、节点凭据、
管理密钥或本部署的真实地址。所有地址、端口、接口都从 `/etc/mihomo-automation.env` 读取，脚本自身可原样发布。

`automation/` 只描述**怎么运行**；节点能力（哪些节点允许下载、哪些属于 AI 池）属于部署数据，见下文
[部署数据](#部署数据不随仓库发布)。

## 目录

| 路径 | 内容 | 安装目标 |
|---|---|---|
| `sbin/` | 16 个可执行脚本 | `/usr/local/sbin/`（0755） |
| `systemd/` | 24 个单元与 drop-in 片段 | `/etc/systemd/system/`（0644） |
| `etc/dnsmasq-upstreams.*` | 三份 dnsmasq 上游基线（direct / mihomo / mosdns） | `/etc/mihomo/` |
| `etc/mosdns/` | MosDNS 配置骨架与两份自定义域名规则 | `/etc/mosdns/` |
| `policy/capabilities.yaml` | 服务定义与能力清单（`overlay` 段为空） | `/etc/mihomo/adaptive/capabilities.yaml` |
| `env/` | 部署参数与 reconcile 目标的示例 | `/etc/mihomo-automation.env.example`、`/etc/mihomo/` |
| `optional/networkmanager/` | 可选的管理 Wi-Fi 源路由 dispatcher，**不自动安装** | 自行审阅 |
| `install.sh` | 安装器（支持 `--dry-run` / `--enable-timers` / `GATEWAY_ROOT`） | — |

## 脚本职责

| 脚本 | 作用 |
|---|---|
| `dnsmasq-safe-start` | 用 direct 基线原子安装 `/etc/dnsmasq-upstreams.conf`，先 `dnsmasq --test` 再放行，避免首次启动时 DNS 不可用 |
| `mihomo-dns-mode` | 切换 dnsmasq 上游：`auto` / `mosdns` / `mihomo` / `direct`，带锁与原子替换 |
| `mihomo-healthcheck` | 分类连通性检查与保守恢复；失败时调用 `mihomo-dns-mode` 回退，恢复后回到自动模式 |
| `mihomo-update-subscription` | 下载主订阅与额外来源 → 生成候选 → 校验与备份 → 热加载并检查；公告占位订阅视为过期，失败回滚 |
| `mihomo-subscription-candidate` | 离线生成订阅候选配置：校验来源、合并与重命名引用、应用现有 overlay、判断最终变化；不安装或重载 |
| `mihomo-update-rules` | 更新受管规则集，遇到 GitHub 403 / 配额耗尽时保留旧规则 |
| `mihomo-policy-overlay` | 按 `capabilities.yaml` 生成服务分组、curated 池与规则覆盖（`direct_prefix`、`download_overrides`、`service_overrides`） |
| `mihomo-reconcile-policy` | 按 `reconcile-targets.conf` 把指定 Selector 收敛到目标组，只影响选择器，不改池成员 |
| `mihomo-adaptive-score` | **只观察**：采样候选节点延迟，写入 EWMA、失败连击、冷却与评分，不修改任何分组 |
| `mihomo-adaptive-apply` | 受门禁的第一执行阶段：只把已进入冷却的持续失败节点移出候选池，冷却结束后恢复；每次改动先语法验证再应用 |
| `mosdns-healthcheck` | 校验 5335 上的分流行为与 metrics，失败先重试、再选择安全回退 |
| `mosdns-rules-update` | 原子更新 MosDNS 路由数据并校验 |
| `mosdns-update-binary` | 从官方稳定版更新 MosDNS 二进制，核对后切换 |
| `mosdns-rollout-rollback` | 只接受 `/var/backups/mosdns-rollout-*.tar.gz` 的回滚点，回退单元与脚本 |
| `mosdns-emergency-direct` | 紧急旁路：停用 MosDNS 及其 timer，DNS 走直连 |
| `mosdns-resume-auto` | 恢复 MosDNS 服务与三个 timer |
| `homeassistant-access-guard` | 拒绝上游接口访问 Home Assistant UI（tcp/8123），保持容器/主机数据库与配置独立备份 |

## systemd 单元与定时任务

服务单元：`mihomo.service`（含 `hardening.conf`、`dns-failover.conf` 两个 drop-in）、`mosdns.service`、
`dnsmasq.service.d/boot-safe.conf`、`homeassistant-access-guard.service`，以及下表脚本对应的 `.service`。
带 `Exec*` 且依赖部署参数的单元统一加载 `EnvironmentFile=-/etc/mihomo-automation.env`。

| Timer | 周期 | 作用 |
|---|---|---|
| `mihomo-healthcheck.timer` | 每 2 分钟（抖动 20s） | 连通性与 DNS 模式自愈 |
| `mihomo-adaptive-score.timer` | 每 5 分钟（偏移 `*:0/5`，抖动 45s） | 影子评分采集 |
| `mihomo-adaptive-apply.timer` | 每 5 分钟（偏移 `*:1/5`，抖动 30s） | 受门禁的隔离执行 |
| `mihomo-subscription-update.timer` | 04:30（抖动 15min） | 订阅更新（至少 24 小时间隔） |
| `mihomo-rules-update.timer` | 04:15（抖动 1h） | 规则集更新 |
| `mosdns-rules-update.timer` | 04:20（抖动 30min） | MosDNS 路由数据更新 |
| `mosdns-binary-update.timer` | 周日 05:10（抖动 1h） | MosDNS 二进制更新 |
| `mosdns-healthcheck.timer` | 启动 30s 后，之后每 1 分钟 | MosDNS 分流自检与回退选择 |

`Persistent=true` 的 timer 可能在首次启动或补执行时立即触发一次，不能只看 `OnCalendar` 判断是否会执行。
评分采集与执行分离：基础联网未稳定前不要启用 `mihomo-adaptive-apply.timer`。

## 安装

```sh
sudo automation/install.sh --dry-run        # 只打印计划，不碰文件系统
sudo automation/install.sh                  # 安装脚本与单元，创建运行目录
sudo automation/install.sh --enable-timers  # 同时 enable --now 八个 timer
GATEWAY_ROOT=/tmp/gw automation/install.sh  # 沙箱演练（不需要 root，也不装到真实系统）
```

安装语义：

- `sbin/` 与 `systemd/` **覆盖式**安装，保证脚本与单元始终是仓库版本；
- 部署数据（`capabilities.yaml`、MosDNS 配置与规则、dnsmasq 上游基线、两份示例 env）**已存在则保留**，不会覆盖
  运维人员改过的文件；
- 创建 0700 权限的 `/etc/mihomo/secrets`、`/etc/mihomo/adaptive`、`/var/lib/mihomo-{adaptive,extra-subscriptions,subscription-backups}`；
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

## 部署数据（不随仓库发布）

| 位置 | 内容 | 权限 |
|---|---|---|
| `/etc/mihomo/secrets/subscription.url` | 供应商订阅链接（唯一副本） | 0600 |
| `/etc/mihomo/secrets/subscriptions.d/` | 额外订阅链接 | 0600 |
| `/etc/mihomo/secrets/dashboard.secret` | 面板 / controller token | 0600 |
| `/etc/mihomo/adaptive/capabilities.yaml` 的 `overlay:` | 媒体池、AI 池、Auto / Developer、固定地区池的节点标签 | 0600 |
| `/etc/mihomo/reconcile-targets.conf` | 组名 = 目标组 | 0600 |
| `/var/lib/mihomo-adaptive/` | `scores.json`、`base-pools.json`、备份 | 0700 |
| `/var/lib/mihomo-extra-subscriptions/` | 额外订阅生成的 provider 文件 | 0700 |

仓库里的 `capabilities.yaml` 只提供 `services`（服务名、分组、探测 URL）与空的 `overlay` 段。节点标签是部署数据，
公开版一律留空：`mihomo-policy-overlay` 在某个池没有任何可用节点时会**直接以非 0 退出并且不写出配置**（fail-closed），
不会生成一个空池或半成品配置。

## 安全与失效边界

- **fail-closed**：没有可用订阅节点时不落盘；配置变更先语法验证再应用。订阅写入、重载、健康检查或信号中断失败时恢复旧配置；恢复失败会报告备份位置，额外订阅缓存只在健康门禁通过后更新。
- **有界改动**：执行阶段只隔离连续失败且已进入冷却的节点，并为每个池保留保底候选；上游整体故障时暂停节点惩罚。
- **互斥**：定时任务用 `flock` 串行化（Mihomo 维护一条锁，MosDNS 各任务一条锁），避免更新与检查互相踩踏。
- **DNS 自愈**：`mihomo-dns-mode` 原子替换 dnsmasq 上游文件；健康检查失败先重试、再回退、再恢复自动模式。
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
python3 tools/check_update_rules.py # 规则更新事务与回滚
python3 tools/check_publication_privacy.py  # 隐私扫描覆盖与检查器格式回归
```

`check_automation.py` 会把 `install.sh` 装进临时目录，断言安装结果与文档一致、二次安装保留运维人员改过的
`capabilities.yaml`、`--dry-run` 不产生任何文件，并注入测试用的地址与端口验证脚本确实从环境读取参数。
安装到目标系统前，先在目标系统上做语法检查（`nft -c -f`、`dnsmasq --test`、`mihomo -t`），见
[分阶段部署](../docs/deployment-current.zh-CN.md)。
