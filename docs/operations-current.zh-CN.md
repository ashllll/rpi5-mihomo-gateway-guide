# 端口、验收与故障恢复

这是生产参考方案的运维说明，不适用于直接推断所有部署入口的防火墙或当前设备状态。
按 [RepoWiki 运维手册](wiki/operations.md)识别安装路径和故障层，安全差异见[安全边界](wiki/security.md)。

## 动态端口事件

2026-10-11 曾把 LAN INPUT 改成仅允许固定业务端口的白名单，漏掉 Mihomo
auto-redirect 的动态 TCP 监听端口，PC 因而无法上网。网关本机 curl 和显式代理正常，
仍不能证明透明转发正常。回滚 LAN 放行后，PC 的 DNS、TCP 和 UDP 均恢复双向通信，
用户确认网页恢复。动态端口值会变化，不应把事件当时的端口号写死到生产白名单。

本仓库的 nftables 基线信任下游 LAN。它只替换自身管理的表，不 flush 全局 ruleset，
保留 Mihomo 自动管理的表。部署目标是可信内网，SSH 密码登录可用，不需要公网端口映射。
LAN 全放行也意味着 LAN 客户端可以访问绑定在 LAN 或通配地址上的服务，这是明确取舍。

| 功能 | 端口 / 方向 |
|---|---|
| SSH | TCP 22，LAN 及参数指定的上游管理网段 |
| DHCP | LAN UDP 67；上游客户端 UDP 68 / DHCPv6 546 |
| DNS | LAN TCP/UDP 53；本机 Mihomo 1053 / MosDNS 5335 |
| 显式代理 | LAN TCP/UDP mixed-port，默认 7890 |
| Mihomo controller | LAN controller-port，默认 9090，须配置 secret |
| 透明代理 | 动态 auto-redirect 监听 + TUN 转发，不固定端口 |
| HA / MQTT | 可选 TCP 8123 / 1883 / 9001 |
| 发现协议 | mDNS UDP 5353、SSDP UDP 1900 及集成的动态端口 |
| 运维服务 | 可选 Node-RED 1880、Kuma 3001、ntfy 8080、指标 9091 / 9100 |

上游 WAN INPUT 默认丢弃新入站连接，保留指定管理网段的 SSH 和上游 DHCP 回包。
ICMP / ICMPv6 诊断及邻居发现报文允许接收，不限定为 SSH 的管理来源网段。
所以 HA 等服务在上游网段默认不可访问；这不是公网远程管理方案。
生成器不修改 sshd；已有密码登录可保留，使用 sshd -T 检查实际策略。

## 三层验收

1. 本机：服务 active、语法通过、路由正确，只证明基本进程与上游状态。
2. 显式代理：curl --proxy，证明 mixed-port 和对应节点可用。
3. 真实 LAN：客户端关闭本机 VPN / TUN / 系统代理，验证 DHCP、DNS 和透明 TCP/UDP。

Mac 同时连 Wi-Fi、有线和本地 TUN 时，默认路由可能绕过 Pi。先核对接口和路由，
不能根据一次 curl 成功宣布网关转发成功。恢复期间可以保留 Wi-Fi SSH 管理通道，
业务验收需要明确客户端的流量实际经过 LAN。

在真实 LAN 客户端运行（地址以部署参数为准）：

```sh
dig @10.77.0.1 www.baidu.com
dig @10.77.0.1 github.com
curl --noproxy '*' --max-time 20 -I https://www.baidu.com
curl --noproxy '*' --max-time 20 -I https://github.com
curl --noproxy '*' --max-time 20 -I https://www.youtube.com
```

curl 不关闭系统 VPN/TUN；需人工确认。之后实际播放视频、测试 AI 流式会话，
并在有回退通道时验证 Mihomo 重启后动态端口、基础 NAT、DNS 回退和真实客户端访问。
不要在唯一管理连接上直接做停服务或重启设备测试。

## 恢复

先保存当前配置，再以已经验证过的备份恢复。执行 nft -c -f 候选文件只检查语法，
不会证明端到端业务；nft -f 才应用。恢复基础表时不得删除 Mihomo 表。
若 WAN SSH 只放行某个临时 DHCP 地址，换 IP 后会失联，应按管理内网 CIDR 配置。
若只有 ping 可达而 SSH 超时，优先检查 INPUT、来源网段和 sshd，不能直接认定设备离线。

DNS 健康与规则更新是两件事。规则更新遇到 GitHub 403 / 配额耗尽时保留旧规则，
在配额恢复后重试；不得改用来源不明的镜像或关闭下载校验来强行成功。
DNS 模式由 `mihomo-dns-mode` 选择；`mosdns-healthcheck` 在分流探测失败后重启 MosDNS、复查并请求 auto 模式，
按健康情况选择 MosDNS、Mihomo 或直连 DNS。`mihomo-healthcheck` 只读内核状态，不做 DNS 模式切换：
它报告 TUN 设备、两张表和策略路由是否存在，以及重定向计数是否在增长。直连 DNS 回退不保证海外代理业务可用，细节见
[自动化层说明](../automation/README.zh-CN.md)。

## 启动门禁与内核回滚

`mihomo -t` 只校验配置，不证明 TUN 设备和 nftables 钩子存在。2026-10-25 一版未带 gVisor 的内核
通过了 `-t` 正常启动，但设备与跳转表都没有建出来：需要代理的连接全部黑洞，国内直连照常工作，
故障因此静默了一分钟以上。现在用三层证据替代“进程还在”：

1. 单元门禁：`mihomo.service.d/zz-startup-probe.conf` 在启动后运行
   `mihomo-healthcheck --startup-check 20`，检查 `Mihomo` 设备、`inet mihomo`、`inet rp_filter`
   和 `ip rule 9000`，缺失即让这次启动失败（不再用 `-` 前缀吞错）。
2. 部署门禁：候选部署先过同一检查（默认等 25 秒），成功后浸泡 45 秒复检并读取控制器
   `/connections`；任一失败自动回滚——配置与单元从备份包恢复，内核通过 `mihomo-core-swap rollback`
   换回旧 inode，然后再验证一次。结果写入部署目录的 `deploy-result.json`
   （`post_start`、`post_soak`、`rollback_exercised` 可查）。
3. 常态观测：`mihomo-healthcheck.timer` 每分钟只读内核状态，写
   `/run/mihomo-healthcheck/status`（`business=verified|idle|failed`）。`rp5-agent` 的 `gate`
   字段汇总该状态、TUN 速率和预算账本余量，`degraded=true` 即需要处理。告警只能消费这些
   字段，不得用生成流量的探针去换业务预算。

替换内核用 `mihomo-core-swap`：`install <binary> <sha256>` 预置候选，`activate` 用
`renameat2(RENAME_EXCHANGE)` 单系统调用换入（运行中的进程保持旧 inode，重启才切换），
`rollback` 换回，`status` 只读。内核必须用 `-tags "with_gvisor no_tailscale"` 构建；
安装器也会拒绝 `-v` 不含 `with_gvisor` 的二进制。

### 告警与出口抽样（2026-10-25 起）

- **通道**：`mihomo-alert` 同时支持 ServerChan（`https://sctapi.ftqq.com/<sendkey>.send`）与
  Bark（`<BARK_URL>`，JSON 带 `level=timeSensitive`）。密钥只放 `/etc/mihomo-alert.env`
  （0600，模板 `automation/env/mihomo-alert.env.example`，安装器只装模板）。两个通道都未配置时
  只写 journal，`--test` 以 1 退出；配置后 `sudo /usr/local/sbin/mihomo-alert --test` 会真的送到手机。
- **成功判定**：ServerChan 即使 SendKey 错误也回 HTTP 200，真正的结果在响应 JSON 里——
  只认 `code==0`（错误码如 `40001` 会连同 `data.error` 写进 journal）；Bark 同理只认 `code==200`。
  `--test` 要求**每个已配置通道都成功**才返回 0，部分成功也报 1，避免“一半静默”。
- **报文约束**：ServerChan 用表单 POST（`--data-urlencode`），`title` 上限 32 字符（脚本按 `cut -c1-32`
  截断），`desp` 按 Markdown 解释且上限 32KB——Markdown 里单换行不生效，要多行正文必须空行分隔；
  当前告警正文是单行时间戳 + 一句话，不受影响。Bark 走 JSON（`title/body/group/level=timeSensitive`）。
- **只走 Bark 的配置**（本机现状）：两种都行，但只选一种，否则同一条告警到两次——
  ① 直连 Bark：只填 `BARK_URL=https://api.day.app/<DeviceKey>`，不放 SendKey；
  ② 经 ServerChan 转发 Bark：填 `SERVERCHAN_SENDKEY` 与 `SERVERCHAN_CHANNEL=8`（不填 channel
  就用 sct.ftqq.com 后台的默认通道），`BARK_URL` 留空。`--test` 只要求已配置的通道全部成功。
- **去重**：同一 `key` 在 `ALERT_COOLDOWN`（默认 600 秒）内只发一次，状态在
  `/run/mihomo-alert.state`，被抑制也留 journal。
- **触发**：`mihomo.service` 的 `OnFailure=mihomo-alert@%N.service` 接单元失败；健康检查只在状态
  迁移时告警（`failed→verified|idle|degraded`、`verified|idle|degraded→failed`、
  `verified→degraded`），不会每分钟刷屏。
- **全域巡检**：`mihomo-watch.timer` 每 2 分钟检查 failed 单元、温度与降频位、磁盘使用率、可用内存、
  `wan0` 链路/默认路由/网关可达。条件成立发一条、恢复再发一条，状态在 `/run/mihomo-watch/`；
  阈值用 `WATCH_TEMP_C`（80）、`WATCH_DISK_PCT`（90）、`WATCH_MEM_MB`（512）、`WATCH_WAN_IF`（wan0）
  覆盖。`sudo /usr/local/sbin/mihomo-watch --self-test` 按五类各发一条，用于确认每种推送都能到手机。
- **出口抽样**：timer 每 5 分钟经 `socks5-hostname 192.168.1.1:7890` 请求
  `https://www.gstatic.com/generate_204`，要 204。这是普通代理流量，**不经过测量准入**，实测前后账本
  积分不变；失败只把 `business` 降为 `degraded`，不触发内核回滚。
- **开机策略**：`zz-alert.conf` 同时设 `StartLimitIntervalSec=600`、`StartLimitBurst=3`，连续失败
  三次后停止重试（不再无限重启掩盖问题）；`--startup-check` 在开机 300 秒内自动放宽到 75 秒
  （冷启动 TUN 建立慢），但窗口过后仍必须通过门禁。

### 接管预算内核

安装器 `gateway/setup.py` 不再只认官方发布：`--core <path> --core-sha256 <hex>` 安装自编内核，
同一套 `require_capabilities()` 拒绝不含 `with_gvisor` 的内核；若本机存在
`/etc/systemd/system/mihomo.service.d/measurement-budget.conf`，还会要求 `-v` 声明
`mihomo-budget-v1`，避免“装了预算 drop-in、内核却不说协议”的半吊子状态。官方内核与预算内核
因此走同一条下载/校验/落盘路径，区别只在 `--core` 参数。
