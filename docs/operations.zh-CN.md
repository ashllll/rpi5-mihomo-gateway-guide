# 运维：日常看什么、出事怎么做

对象是这台 rp5。所有命令都在网关本机以 root 执行；只想看不动手时，
先读 `/run/mihomo-healthcheck/status` 与 `systemctl --failed`。
机制原理见[架构](architecture.zh-CN.md)，首次搭建见[部署](deployment.zh-CN.md)。

## 1. 一眼判断

```sh
cat /run/mihomo-healthcheck/status
systemctl --failed --no-legend
systemctl list-timers --all | grep -E 'mihomo|mosdns'
```

状态行（单行 `key=value`）里只有五个字段需要看：

| 字段 | 正常 | 含义 |
|---|---|---|
| `business` | `verified` / `idle` | 转发计数器在增长 / 链路在但没流量 |
| `tun` `redirect` `filter` `route` | 全 `1` | TUN 设备、`inet mihomo`、`inet rp_filter`、`ip rule 9000` 四要素 |
| `egress` | `ok` | 最近一次经 `127.0.0.1:7890` 取 gstatic 204 成功 |
| `action` | `none` | 本次未触发恢复动作；`restart` 表示探针重启过核心 |
| `fatal` | `none` | 非 `none` 时优先看它，再对照 `journalctl` |

`business=failed` / 任一四要素为 0 / `egress!=ok` 都代表代理链路不可用。

出口抽样固定走 `127.0.0.1:7890`：核心按 `bind-address: "*"` 监听，本机已无独立 LAN 口，环回是唯一不随链路状态变化的入口；`lan-allowed-ips` 必须含 `127.0.0.1/32`，否则核心会重置环回连接。生产拓扑是「一根网线接 UCG 交换机」：上行 VLAN1（`192.168.0.0/24`）+ 下行 VLAN20（`192.168.20.1/24`，UCG 广播的 `RP5-PC-TEST` Wi-Fi 客户端），独立下游 LAN 口与 Pi 自建 AP 已移除。

探针只在状态迁移时推送告警，所以“没新告警”不等于“此刻正常”，仍需看 `egress`。
黑障自愈：进程还活着但证据连续红 3 次，探针会重启核心一次（`restarted_at` 记录时间，
每小时至多一次）；三次仍不绿说明重启救不回来，直接查节点/订阅/上游。

## 2. 单元清单

发布层 14 个 enabled（6 个 service + 8 个 timer；安装脚本启用这些）+ 系统自带的 `nftables.service`。
其余 worker service 是 `static`：没有 `[Install]`，由 timer 或 `OnFailure` 拉起，不算 enabled。

| 单元 | 作用 | 触发 |
|---|---|---|
| `nftables.service` | 载入 `rp_filter` / `rp_nat` | 开机、手动 reload |
| `dnsmasq.service` | 环回 resolver（`/etc/dnsmasq.d/resolver.conf`，`127.0.0.1:53`）；`boot-safe.conf` 保证先于 mihomo 写 direct 基线；`restart.conf` 补 `Restart=always`／`RestartSec=2s`（Debian 原 unit 没有重启策略，崩溃后网关自身解析静默失效，而 mihomo 门禁只证明代理链路，看不到它） | 常驻，崩溃自起 |
| `dnsmasq-vlan-pilot.service` | VLAN 20 独立 DNS/DHCP | 常驻 |
| `homeassistant-access-guard.service` | 从 `wan0`/`wlan0` REJECT `tcp/8123`，上游侧访问不到 HA UI | 开机一次（oneshot） |
| `mihomo-dnsmasq-guard.timer` | 每 30 秒分层探测：先 `127.0.0.1:53` 两个国内域名，失败再退到 `127.0.0.1:5335`。dnsmasq 静默（0/2）立即重启它；只有一个域名慢/无答案记为降级，连续两轮先重启 mosdns、仍不恢复再重启 dnsmasq。重启预算 3 次/15 分钟，超出只告警不重启（`throttled`），避免重启风暴 | 定时 |
| `mihomo.service` | 核心（`--run-core` 校验后拉起；`ExecStartPre` 先 `mihomo -t`） | 常驻，`Restart=always` |
| `mihomo-measurement-budget.service` | 预算准入 broker（建 socket；账本首扣创建） | 常驻（enabled） |
| `mosdns.service` | 国内直连/代理分流 DNS | 常驻，`After=mihomo` |
| `mihomo-healthcheck.timer` | 每分钟四要素 + 计数（随机延迟 3s） | 每分钟 |
| `mihomo-watch.timer` | 单元失败/温度/磁盘/内存/WAN（边沿触发） | 开机 90s，之后每 120s |
| `mosdns-healthcheck.timer` | mosdns 健康 | 开机 30s，之后每分钟 |
| `mosdns-rules-update.timer` | 规则集每日 04:20±30min | 每日 |
| `mihomo-subscription-update.timer` | 拉取主订阅及非手动额外来源，校验、备份、业务检查及失败恢复 | 开机 10 分钟，任务结束后每 48 小时 |
| `mihomo-media-guard.timer` | 媒体速度准入，私有策略启用才采样；详见[媒体路由](wiki/x-video-routing.md) | 开机 2 分钟，之后每分钟唤醒 |
| `mosdns-binary-update.timer` | MosDNS 二进制每周日 05:10±1h | 每周 |

休眠 3 个（随仓库安装但保持 disabled；2026-10-25 已清理本机那 2 个退役评分单元，真机与仓库一致）：

| 单元 | 为什么休眠 | 手工跑一次 |
|---|---|---|
| `mihomo-rules-update.timer` | 规则由订阅管线一并生成，独立更新会与配置快照分叉 | `sudo /usr/local/sbin/mihomo-update-rules` |
| `mihomo-download-quality.timer` | 采样写 `./providers/quality-*.yaml`，但配置未声明对应 file provider，产物无人读取；开 timer 只消耗带宽 | `sudo /usr/local/sbin/mihomo-download-quality` |
| `mihomo-social-quality.timer` | 同上，仅 X 视频场景，每 2 分钟一轮 | `sudo /usr/local/sbin/mihomo-social-quality` |

`homeassistant-access-guard.service` 也在发布层（oneshot，开机跑一次）。
本机另有 1 个不在发布层的单元：`rp5-agent.service`（T-Display 监控的只读指标，tcp/8799），
由部署端单独维护，仓库不发布（2026-10-26 已移除对已退休 iKuai 的 `ikuai-wan-watch.service`）。

订阅定时任务使用树莓派本机 systemd，不依赖管理电脑。私有地址准备好后执行 `sudo systemctl enable --now mihomo-subscription-update.timer`，用 `systemctl list-timers mihomo-subscription-update.timer` 核对下一次执行。开机超过十分钟后首次启用会立即检查；任务结束（包括失败或无变化）后等待 48 小时，重启后十分钟再次检查。手动检查用 `sudo systemctl start mihomo-subscription-update.service`。

无变化不重启核心；候选有变化时现有更新器会重启 Mihomo，活动连接可能短暂中断。下载或校验失败保留当前主节点或最后可用额外缓存，不能仅凭服务退出成功认定所有来源已更新。新地址只保存在私有订阅文件中。

需要仅手动更新的额外来源，在 `/etc/mihomo-automation.env` 中用 `MIHOMO_SUBSCRIPTION_MANUAL_SOURCES` 配置其标识（空格分隔）。更新器不会请求这些地址，而是合并经过校验的本地缓存；缓存无效时保留当前配置并报错。`--force` 也不覆盖手动来源策略。

## 3. 故障对照表

| 现象 | 先查 | 处理 |
|---|---|---|
| `tun=0` | `ip link show Mihomo`、`journalctl -u mihomo -n 80` | 配置错误看 `mihomo -t -d /etc/mihomo`；核心崩溃看 `mihomo-core-swap status` |
| `redirect=0` | `nft list table inet mihomo` | 表没建：核心未完成启动；`systemctl restart mihomo` 后复跑 `--startup-check 30` |
| `filter=0` | `nft list tables`、`systemctl status nftables` | 缺表：`systemctl reload nftables`；若配置改坏，先 `nft -c -f /etc/nftables.conf` |
| `route=0` | `ip rule`、`ip route show table 2022` | 规则 9000 缺失通常是核心未完成 auto-route；重启核心并观察 |
| `egress=failed` | `curl -x socks5h://127.0.0.1:7890 -o /dev/null -w '%{http_code}' https://www.gstatic.com/generate_204` | 204 说明探针误报；否则看节点/订阅/上游 |
| DNS 全挂 | `cat /etc/dnsmasq-upstreams.conf`、`dig @127.0.0.1 -p 5335 example.com` | 上游模式错：`mihomo-dns-mode auto`；mosdns 挂：看 `mosdns-healthcheck` 与 `journalctl -u mosdns` |
| 客户端只走直连 | `cat /run/mihomo-healthcheck/status`、`nft list table inet mihomo` | 四要素正常但规则未命中：检查 UCG 的 DHCP 是否把 VLAN20 客户端的 DNS 指向 `192.168.20.1` |
| 预算拒绝一切 | `systemctl status mihomo-measurement-budget`、`ls -l /run/mihomo-measurements/` | broker 未起或 socket 权限错；账本损坏先备份再重建 |
| 告警不来 | `sudo /usr/local/sbin/mihomo-alert --test` | 看 `/etc/mihomo-alert.env`（0600）与 `journalctl -t mihomo-alert` |

`mosdns-healthcheck` 的结论在 `/run/mosdns-healthcheck/status`；它单独持锁，
与 mihomo 的健康检查互不阻塞。

## 4. 更新

### 4.1 换核心

```sh
sudo /usr/local/sbin/mihomo-core-swap status
sudo /usr/local/sbin/mihomo-core-swap install /root/mihomo-candidate <sha256>
sudo /usr/local/sbin/mihomo-core-swap activate
sudo /usr/local/sbin/mihomo-healthcheck --startup-check 30
cat /run/mihomo-healthcheck/status
```

`activate` 只交换槽位并做哈希校验；正在运行的核心保持旧 inode，`systemctl restart mihomo`
才切到新核。四要素或 `business` 不对就 `mihomo-core-swap rollback`，再复跑一次门禁。
没有“浸泡计时器”这种东西：判定依据就是启动门禁 + 状态文件。

### 4.2 换 MosDNS / 规则

| 动作 | 命令 | 回滚 |
|---|---|---|
| 二进制 | `mosdns-update-binary`（或 timer） | `mosdns-rollout-rollback /var/backups/mosdns-rollout-*.tar.gz` |
| 规则 | `mosdns-update-rules`（或 timer 04:20） | `/etc/mosdns/rules/releases/` 保留旧版本，重指 `current` |
| 手工紧急直连 | `mosdns-emergency-direct` | `mosdns-resume-auto` |

`mosdns-rollout-rollback` 先切 DNS 到 direct 再恢复备份，避免回滚过程中 DNS 悬空。

### 4.3 订阅与策略

```sh
sudo /usr/local/sbin/mihomo-update-subscription --force   # 生成候选并校验（内部会调覆盖层）
sudo /usr/local/sbin/mihomo-reconcile-policy              # 按 reconcile-targets 对齐

# 覆盖层不是手工命令：它总要 (输入, 输出) 两个参数，订阅路径已自动调用。
# 手工验证时写临时文件，别覆盖线上 config：
sudo /usr/local/sbin/mihomo-policy-overlay /etc/mihomo/config.yaml /tmp/config.overlay.yaml
sudo /usr/local/bin/mihomo -t -f /tmp/config.overlay.yaml
```

订阅数据与节点标签只存在部署端：`/etc/mihomo/secrets/subscription.url`（0600）、
`/etc/mihomo/secrets/dashboard.secret`、`/etc/mihomo/adaptive/capabilities.yaml`。
更新后必须 `mihomo -t -d /etc/mihomo` 通过再 reload；`kill -HUP` 只重载不重启，
预算 broker 与 TUN 状态不受影响。

**组健康检查是硬不变量**：每个自动组都必须被某个探测所有者覆盖，否则它永远不重选——这就是
2026-10-25「节点超时后一直不切换」的根因。生产模式（`measurement.enabled: true`）下，
启用媒体准入的 Social / Streaming 使用专属 file provider 的健康调度器；其余
`url-test`/`fallback` 组的节点被折进共享的 `measurement-<16hex>` provider 以归属计量，
但**组自己仍保留 `url`/`expected-status`/`interval`**：探测由组内调度器逐节点执行，内联 provider
的 `health-check` 没有自己的探测循环（真机实测从不触发）。组的间隔取
`measurement.native_interval_seconds` 与 `stability.max_interval` 的较小者，
改它就是改全网的故障发现时间。每次探测都要先过预算侧准入，因此
`mihomo-measurement-budget` 必须接得住一次全量扫描：它缓存已解析的输入（config/manifest 变更才重读），
监听 backlog 512、并发上限 256——按请求重解析 5000 行 config 会把一整波扫描挤到准入超时。
未开原生计量时自动组会降级成 `select`，不自动切换。
仓库侧由 `tools/check_mihomo_policy.py` 用合成订阅跑两种模式断言这条不变量（CI 每次执行），
改 `automation/policy/capabilities.yaml` 后先跑它再上线。

## 5. 账本、状态与备份位置

| 路径 | 内容 |
|---|---|
| `/run/mihomo-healthcheck/` | `status`、`packets`、`egress`、`egress_at`、`fail_streak`、`restarted_at`（0700，重启清空） |
| `/run/mosdns-healthcheck/status` | mosdns 健康结论 |
| `/var/lib/mihomo-measurements/quality-budget.json` | 预算账本（账户、尝试次数、`last_reservation_at`） |
| `/var/lib/mihomo-download-quality/scores.json` | 下载采样（休眠） |
| `/var/lib/mihomo-social-quality/scores.json` | X 视频采样（休眠） |
| `/var/lib/mihomo-media-admission/state.json` | Social / Streaming 资格、节点指纹及采样时间 |
| `/var/backups/mosdns-rollout-*.tar.gz` | MosDNS 回滚点 |
| `/etc/mosdns/rules/releases/` | 规则集历史版本 |
| `/var/lib/mihomo-swap/state.json`、`/usr/local/lib/mihomo-swap/{a,b}` | 双槽核心与状态 |

预算账本由 broker 与媒体 guard 经同一锁和持久预扣接口原子写回；手工编辑会让预扣记录失真。
备份账本用 `install -m0600` 拷走，不要 `cp` 丢权限。

## 6. 锁与互斥

| 锁 | 持有者 | 拿不到时 |
|---|---|---|
| `/run/lock/mihomo-maintenance.lock` | 无参 `mihomo-healthcheck`、订阅/规则更新、质量采样 | 探针直接退出 0；更新脚本退出 0 或跳过 |
| `/run/lock/mosdns-healthcheck.lock` | mosdns 健康检查 | 跳过本轮 |
| `/run/lock/mihomo-download-quality.lock` | 下载采样 | 跳过本轮 |

手动跑重活（换核、订阅、规则）前确认没有定时任务在跑：`flock -n /run/lock/mihomo-maintenance.lock true`。
这些任务都不长驻，锁是互斥而不是排队，被跳过的一轮由下一个周期补上。

## 7. 巡检节奏

- **每天**：看 `status` 一行、`systemctl --failed`；DNS 有异常时 `dig` 一次国内域名。
- **每周**：看 `mihomo-watch` 是否报过温度/磁盘；检查 `/var/log` 与 `journalctl -p err --since '7 days ago'`。
- **换核/改配置后**：`mihomo -t` → 启动门禁 → 状态文件 → 一次 egress 抽样 → 出问题直接 rollback。
- **告警演练**：`sudo /usr/local/sbin/mihomo-alert --test`，确认手机能收到；
  `mihomo-watch --self-test` 逐类发一条。通知器坏了只记日志，不会拖垮网关。
- **会推送的事件**（同一 key 默认 600 秒内去重，ServerChan 免费额度有限，所以只推移位级事件）：
  单元失败（`OnFailure`）、gate `business` 迁移、`mihomo-watch` 的五类巡检
  （WAN/温度/磁盘/内存/单元）、`mihomo-dnsmasq-guard` 的分级重启（dnsmasq 静默 / MosDNS 降级重启 /
  重启预算耗尽只推一次）、MosDNS 连续失败切兜底模式、**设备绕过网关 DNS**（`mihomo-watch` 的
  `dns-bypass` 探针：谁的 IP、DHCP 主机名、MAC，向哪个上游解析器查询；只记日志和推送，不拦流量）。
  `dns-bypass` 探针的观测规则装在独立表 `inet dns_bypass`（prerouting priority -151），
  与 `inet mihomo` 无关，重启 Mihomo 不会丢；接口与上游地址由 `WATCH_DNS_BYPASS_IF` /
  `WATCH_DNS_BYPASS_TARGET` 指定。

## 8. 两条真实事故的教训

- **2026-10-24 影子评分退役**：旧的定时评分与自动应用被下载/社交质量采样取代，
  相关脚本与 unit 已从仓库删除；真机上禁用的 unit、可执行文件与 92 MB 历史数据
  也已在 2026-10-25 清理干净。看到评分相关告警，先确认它是不是历史单元，不要重新启用。
- **2026-10-25 换核漏编 gVisor**：进程活着但 TUN 没起来，代理流量静默黑洞。当时的出口抽样走
  mixed-port、绕开 TUN/redirect，所以门禁仍绿；发现耗时未记录。
  之后启动门禁强制检查四要素，`systemctl is-active` 不再当作“已就绪”。
  换核后必须跑 `--startup-check` 并读 `status`，而不是只看单元绿色。
