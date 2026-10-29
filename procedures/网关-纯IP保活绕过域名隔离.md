# 网关-纯IP保活绕过域名隔离

> **触发**：Mihomo 停止后国内保活；用 DNS 返回 IP 动态放行；fake-IP 旧连接恢复。
> **验证**：共用 CDN IP 的受限域名仍不可达，且旧 fake-IP 连接能恢复；任何一项失败都不能上线。

## 首选

在独立 Linux mount/net/PID namespace 内，以真实 Mihomo mixed、MosDNS、dnsmasq 和 nftables 重现：国内域名与受限域名共享一个 IP，先解析国内域名再直接向该 IP 发受限 Host 请求；另用不同 IP 的受限目标作对照。

## 被挡时

- 无法创建完整隔离网络时保留方案未验证，不在生产关闭核心或临时放宽转发规则。
- 保留已有“出口探测只降级、不停止核心”的修复；核心完全退出的保活须另行设计与验证。

## 别再来

| 弯路 | 为什么不通 |
|---|---|
| DNS 拒绝代理域名，国内解析 IP 入短 TTL 集合 | 共享 IP 上仍可用受限 Host 访问，客户端、端口和 TTL 限定不能恢复域名边界 |
| 停核心后把 DNS 换成真实 IP，就认为旧连接恢复 | 应用仍复用旧 fake-IP 时无映射执行方；新 DNS 成功不代表旧地址成功 |
| 实验脚本 exit 0 当作上线通过 | 脚本可以成功复现反例；必须读取 candidate_deployment_accepted 判定 |

## 注意

- 实验实际 fake-IP TTL 为 1 秒；失败证明的是应用复用旧地址，不代表遵循 TTL 的所有客户端都会长期断网。
- 2026-10-29 实验判定为拒绝纯 IP 方案上线，未改生产 nftables 或 DNS 上游。

证据：本地 hardening 交付目录 `validation/core-independence-run2/results.json`、`lab/core_independence_design.md`。
