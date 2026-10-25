# Mihomo 本地配置骨架

这里只放 `/etc/mihomo/config.yaml` 的**本地覆盖段**，以及它怎么被订阅管线保留的说明。
节点、策略组、规则、内联 provider、管理密钥全部属于部署数据，不入库。

`capabilities.yaml` 的 `stability.groups` 是覆盖层唯一接受的组调参入口（`type/url/interval/timeout/lazy/tolerance/max-failed-times/expected-status`），
每次配置重新生成都会重新套用：真机对 `Auto` 用 `interval: 120`（默认 300s），让死节点被发现的时间从 5 分钟缩到 2 分钟；
`Streaming`/`Social`/`Research + AI` 仍是 900s，因为它们切换会断当前连接。

## 真机上的 config.yaml 从哪来

`config.yaml` 不是手写文件，也不是本仓库直接拷过去的。真机的每轮订阅更新是：

1. `mihomo-update-subscription.timer` 触发更新脚本，把新订阅写进临时 `candidate`。
2. `mihomo-subscription-candidate merge_sources(current, subscription, manifest)` 以**当前生效的
   `config.yaml`** 为底，合并订阅带来的 `proxies` 与 `proxy-groups`；当前文件里的本地键因此原样保留。
3. `mihomo-policy-overlay` 重建策略组与规则，写死 `unified-delay: false`，退役旧的 quality
   provider，调用 `configure_measurement` 重建 7 个 `measurement-<16hex>` 内联 proxy-provider，
   再叠加稳定性与节点能力，最后 `yaml.safe_dump` 原子替换 `/etc/mihomo/config.yaml`。
4. 任一步失败，保留原文件与回滚备份；不会写入半成品。

所以本目录的 [`config.local.yaml`](config.local.yaml) 是**第 2 步要保留的那部分本地键**的参考快照，
不是能单独启动的完整配置，也不是 `install.sh` 会覆盖上去的模板。

## 保留哪些键

`allow-lan`、`mode`、`log-level`、`mixed-port`、`bind-address`、`lan-allowed-ips`、
`external-controller`、`external-ui`、`ipv6`、`interface-name`、`find-process-mode`、
`unified-delay`、`tcp-concurrent`、`profile`、`tun`、`dns`、`sniffer`、`rule-providers`。

订阅管线不会改写这些键；运维改站点参数时改的是真机上的当前配置（或先改本快照再人工核对），
不是让生成器从环境变量注入。`secret`、`proxies`、`proxy-groups`、`rules`、`proxy-providers`
由生成器/部署端负责，骨架里刻意没有。

## 占位与脱敏

- `10.77.0.0/24`、`10.77.0.1` 是占位站点 LAN（与 `automation/env/automation.env.example`
  的 `GATEWAY_LAN_IP` 一致）。真机值属于部署数据，不发布；复用时替换这三个键：
  `bind-address`、`lan-allowed-ips`、`external-controller`。
- `tun.route-exclude-address` 的 `10.77.0.0/16` 是同一占位超网，真机排除的是它自己的 LAN /16。
- `sniffer.skip-src-address` 由部署端 WireGuard 对端地址计算，骨架不发布该列表，真机配置里存在。
- `dns.nameserver` 里的 `#Default Proxy` 指向生成器创建的策略组，所以本地段只能在组已存在的配置上叠加。

## 规则文件不在仓库

`rule-providers` 的 6 个条目都是 `type: file` + `format: mrs` + `path: ./rules/*.mrs`：

| provider | behavior | 文件 |
|---|---|---|
| `private-ip` | ipcidr | `rules/geoip-private.mrs` |
| `cn-ip` | ipcidr | `rules/geoip-cn.mrs` |
| `cn-domain` | domain | `rules/geosite-cn.mrs` |
| `game-domain` | domain | `rules/dustinwin-games.mrs` |
| `game-cn-domain` | domain | `rules/dustinwin-games-cn.mrs` |
| `steam-domain` | domain | `rules/geosite-steam.mrs` |

这些 `.mrs` 由 `mihomo-rules-update.timer` 下载/更新到 `/etc/mihomo/rules/`，仓库不带二进制规则，
也不带下载地址。新机器首次部署需要先跑一次规则更新，否则 `mihomo -t` 会因 provider 文件缺失失败。

## 与预算准入的关系

`measurement-<16hex>` 内联 provider 由 `configure_measurement` 生成，不是本地段的一部分；它们与
`/var/lib/mihomo-measurements/quality-budget.json` 的账户额度配套，准入逻辑和 systemd 接线见
[`core/README.md`](../../core/README.md#预算外壳接线)。`unified-delay: false` 必须保留：
开启后一次检测会打两次 HEAD，预算按两次预扣，且真机策略依赖单次语义。

## 安装

`automation/install.sh` 只把 [`config.local.yaml`](config.local.yaml) 装到 `/etc/mihomo/config.local.yaml`
（0644）作参考；`*.md` 不随安装器上机，`tools/check_automation.py` 的 `shipped_files()` 也排除文档。
运行中的 `config.yaml` 仍由订阅管线生成，安装器不会拿骨架覆盖它。
