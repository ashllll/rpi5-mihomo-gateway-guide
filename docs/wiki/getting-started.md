# 入门：这台机器怎么用

[Wiki 首页](../README.md) · [部署](../deployment.zh-CN.md) · [运维](../operations.zh-CN.md)

本仓库是**一台 rp5 的部署镜像**，不是一键安装器。它提供运行层脚本、systemd 单元、
DNS/MosDNS 配置骨架与自编译核心的补丁说明；网络事务、订阅与凭据由部署者持有。

## 最短路径

1. 按[部署文档](../deployment.zh-CN.md)重建网络身份（`wan0` VLAN1 上联、
   `proxy20` VLAN20、`wg-ucg-pilot`）与 `/etc/nftables.conf`。
2. `sudo automation/install.sh --dry-run` 看计划，再去掉 `--dry-run` 安装。
   它只 enable 这台机器在用的 11 个单元，其余装上但保持 disabled 或 static。
3. 用 `core/README.md` 的命令构建核心，`mihomo -t -d /etc/mihomo` 通过后再启动。
4. 读 `/run/mihomo-healthcheck/status`，四要素全 1 才算成功。

## 接手一台已存在的网关时

只把脱敏结果写进项目文档：

- 源码提交、核心版本、操作系统与 NetworkManager 连接；
- 接口角色、存在的 TUN/VPN、哪些 timer 处于 enabled；
- 私有配置与备份的位置、最近一次成功验收日期、回退通道；
- 已知业务限制与未完成验收项。

订阅地址、节点标签、controller secret、WireGuard 私钥和真实 MAC 不进入仓库或工单。
遇到故障先看[运维对照表](../operations.zh-CN.md)，不要先重装。
