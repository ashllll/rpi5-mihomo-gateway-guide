# 端口、验收与故障恢复

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
