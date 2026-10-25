# 运维：定位与恢复

[Wiki 首页](../README.md) · [运维全文](../operations.zh-CN.md) · [架构](architecture.md)

本页是按层缩小的只读排查顺序；状态字段、timer 清单与更新回滚的权威内容在
[运维文档](../operations.zh-CN.md)。

## 先保住管理通道

保持 SSH/本地控制台可用，不在唯一管理通道上改防火墙、重启或停服务。
一次只动一层，先只读；`nft -f`、reload、restart 之前先确认恢复点。

```sh
date -Is
ip -br addr; ip rule show; ip route show table 2022
systemctl --no-pager --failed
cat /run/mihomo-healthcheck/status
ss -lntup
```

## 四层定位

| 层 | 只读检查 | 判定 |
|---|---|---|
| 上游 | `ip route`、`curl --noproxy '*' https://www.baidu.com/` | 单次成功只说明这一刻直连可达 |
| DNS | `dig @192.168.1.1 example.com +time=2`、`dig @127.0.0.1 -p 5335 example.com` | 两个入口分别验证 dnsmasq 与 mosdns |
| 显式代理 | `curl -x socks5h://192.168.1.1:7890 .../generate_204` | 204 只证明该端口，不证明透明转发 |
| 真实 LAN | 下游终端关闭 VPN/系统代理后重新取租约再测 | 唯一能验收透明代理与 VLAN 的层 |

故障对照、DNS 模式回退、换核与账本位置见[运维全文](../operations.zh-CN.md)。
