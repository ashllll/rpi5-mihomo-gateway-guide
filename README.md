# Raspberry Pi 5 + Mihomo Gateway Guide

一套经过实际部署和故障验证的 Raspberry Pi 5 + Mihomo 双网口透明代理网关记录。

## 当前公开版本（2026-10-11）

当前架构以 [新版方案](docs/current-architecture.zh-CN.md) 为准。下方旧拓扑、图片和
原始长文是历史档案，不代表当前网段、链路速率或策略。新版配置示例使用虚构网段；
历史档案保留旧的私有 IPv4 拓扑示例，不包含当前设备地址或访问凭据。

- [部署与回滚](docs/deployment-current.zh-CN.md)：生成配置、检查和人工分阶段部署。
- [智能选节点设计](docs/adaptive-selection.zh-CN.md)：业务评分、隔离、恢复与会话稳定。
- [端口与故障验收](docs/operations-current.zh-CN.md)：动态重定向、PC 透明转发和双路径测试。
- [参数模板](examples/site.example.json)：接口、子网、DHCP 和内网管理范围可配置。
- [配置生成器](tools/render.py)：只生成文件，不连接设备、不安装、不重启服务。

```sh
cp examples/site.example.json site.local.json
# 先修改 site.local.json，确认接口名称、地址和现有网络不冲突。
python3 tools/render.py --site site.local.json --output rendered
python3 tools/validate_public.py
```

发布版提供基础网关配置和自适应机制设计；不包含生产订阅合并器、生产节点数据库、
完整自适应执行器、Docker 私有数据或一键部署承诺。供应商凭据需要在部署端自行配置。
生成后的配置仍需要按照部署文档检查和验证，不能直接覆盖正在工作的网关。

## 历史档案

本仓库侧重：

- Raspberry Pi 5 双网口网络规划
- Mihomo TUN、DNS、DHCP、NAT 和策略分流
- 多订阅融合与自动节点切换
- GitHub、YouTube、AI 和大文件下载专项策略
- DNS fail-open、健康检查、自动恢复和配置回滚
- AP 下载慢、客户端代理掩盖故障等真实问题的诊断方法

> This repository is a sanitized documentation archive. It does not contain
> subscription URLs, node credentials, Wi-Fi passwords, dashboard secrets, or
> private backup archives.

![Raspberry Pi 5 + Mihomo network topology](assets/network-topology.png)

## Repository contents

| Path | Description |
|---|---|
| [`docs/deployment-debugging-summary.zh-CN.md`](docs/deployment-debugging-summary.zh-CN.md) | 完整中文部署、调试、优化和故障总结 |
| [`assets/network-topology.svg`](assets/network-topology.svg) | 可编辑矢量网络拓扑图 |
| [`assets/network-topology.png`](assets/network-topology.png) | 1800×1120 PNG 预览图 |
| [`web/index.html`](web/index.html) | 响应式拓扑浏览页面 |
| [`LICENSE`](LICENSE) | CC0-1.0 公共领域奉献协议 |

## Topology

```text
Internet
  ↓
Main router · 192.168.100.1/24
  ├── Main Wi-Fi: bypasses Raspberry Pi
  ├── wlan0: emergency management path
  └── Ethernet 1 Gbps
       ↓
     wan0
Raspberry Pi 5 · Debian 13
  ├── dnsmasq
  ├── nftables
  ├── Mihomo TUN
  └── systemd health checks and rollback
       ↓
     lan0 · 192.168.1.1/24 · 2.5 Gbps
       ↓
Switch / access point
       ↓
LAN clients
```

## Important boundaries

- WAN 和 LAN 必须使用不同子网。
- 主路由 DHCP 可以保留；下游 AP 必须使用桥接/AP 模式并关闭自己的 DHCP。
- 网关无法看到 Windows 客户端进程，因此进程级分流应在终端本机完成。
- `Download` 策略只能覆盖已匹配到下载规则的域名，不能按传输字节数自动识别未知大文件。
- 在生产环境修改 NetworkManager、nftables 或 DNS 前，必须保留 Wi-Fi、串口或本地终端回退通道。

## Validation

仓库通过 GitHub Actions 检查：

- 必需文档和图像是否存在
- SVG 是否为有效 XML
- HTML 是否引用仓库内的 SVG
- 是否意外包含本机绝对路径
- 是否出现已知订阅令牌或密码模式

## License

本仓库使用 [CC0 1.0 Universal](LICENSE)。在法律允许的最大范围内，你可以复制、修改、分发和用于商业用途，无需请求许可。

第三方产品名和商标仍归其各自权利人所有。
