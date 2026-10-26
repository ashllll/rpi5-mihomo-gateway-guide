# Mihomo 本地配置骨架

这里只放 `/etc/mihomo/config.yaml` 的**本地覆盖段**，以及它怎么被订阅管线保留的说明。
节点、策略组、规则、内联 provider、管理密钥全部属于部署数据，不入库。

探测参数有唯一的生效位置，取决于是否打开原生计量：

- **生产模式（`measurement.enabled: true` + 预编译核心）**：`mihomo-measurement-policy` 的
  `configure_measurement()` 把所有 `url-test`/`fallback` 组的节点折进共享的 `measurement-<16hex>`
  内联 provider，但**组自己保留 `url`/`expected-status`/`interval`**——探测由组内调度器逐节点执行，
  内联 provider 的 `health-check` 只留作计量归属（它没有 provider 级探测循环，真机实测从不触发）。
  组的 `interval` 由 `measurement.native_interval_seconds`（真机 250s）与 `stability.max_interval`
  取小，**这就是全网的故障发现时间**。所有探测都要过预算侧准入，因此
  `mihomo-measurement-budget` 必须能接住一次全量扫描（见其缓存与监听容量注释）。
- **未开原生计量**：同一条代码路径把所有自动组降级成 `select`（不自动切换），并清掉组级健康字段。
  所以「想要全自动切换」必须开原生计量并给每个节点族配 account，否则会失败关闭
  （`native budget requires an unambiguous account`）。

`stability.max_interval`（默认 250）是覆盖层对**仍保留组级探测**的配置的夹取上限，防止退回
「钉在死节点上十几分钟」。不论哪种模式，`tools/check_mihomo_policy.py` 都会用合成订阅跑一遍覆盖层，
断言每个自动组都被某个探测所有者覆盖、且间隔不超过上限（CI 每次执行）。
`Research + AI` 始终保留 `https://api.openai.com/v1/models` + `expected-status: 401`，
只把能过 OpenAI 的专用节点判为健康。

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

- `127.0.0.1/32` 固定在 `lan-allowed-ips`：本机管理调用（mixed、controller、DNS 探测）
  全部走环回，核心按 `bind-address: "*"` 监听。上游 VLAN1 与 VLAN20 客户端各用
  `198.51.100.0/24`、`203.0.113.0/24` 占位；真机值属于部署数据，不发布。复用时替换
  这三个键：`bind-address`、`lan-allowed-ips`、`external-controller`。
- `tun.route-exclude-address` 的 `10.77.0.0/16` 是占位超网，真机排除的是它自己的上游 LAN /16。
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
