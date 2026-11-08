# 全设备分流、流量统计与 GitHub API 出口

当前运行策略对所有经过透明网关的设备生效：AI 聊天/API 仅使用 GLaDOS；模型、软件和开发依赖下载使用 STT 与 ByWave，GLaDOS 不进入下载池。没有针对某台电脑的源地址例外。YouTube、社交媒体使用正常健康检查与 fallback，不再因吞吐量准入池为空而整组 REJECT。

公开策略段见 [`global-routing.example.yaml`](../automation/policy/global-routing.example.yaml)，包含实际使用的域名、机场约束和全设备源范围。将其字段合并到私有 `capabilities.yaml` 时，保留其他字段以及 measurement 账户预算、overlay 节点池；`stability.groups` 按组名合并，不能覆盖整个 stability。示例的 GLaDOS AI Pool 匹配规则需要与自己的订阅对应。生成器校验 AI 池只能包含指定机场的实际节点，池为空时拒绝发布。

开发下载默认表见 [`route-defaults.yaml`](../automation/policy/route-defaults.yaml)，覆盖 Python/Conda、npm、Homebrew、Rust/Go、Java、Ruby、NuGet、Docker、编辑器、SDK 和构建工具。私有清单中的同名条目优先；下载规则优先于较宽的 AI 域名规则。未提供域名的连接使用当前现场保留的通用直连规则，避免把 BT IP 连接送进机场。

## 流量查询

`mihomo-traffic.service` 每 0.5 秒采集连接累计值，按 5 分钟桶持久化设备、机场、节点的上传和下载量，保留 90 天，每 15 秒提交。DHCP 身份绑定到连接，采集器重启不会重复累计已记录字节；主核心重启与采样缺口单独记录。

```sh
sudo mihomo-traffic report
sudo mihomo-traffic report --since 2026-11-08 --airport GLaDOS
sudo mihomo-traffic report --since 2026-11-08 --device 192.0.2.10 --json
systemctl status mihomo-traffic.service
```

SQLite 位于 `/var/lib/mihomo-traffic/traffic.sqlite3`，只保留本地。采样间隔内已结束的短连接可能无法分配给设备；报告保留全局总量和未归属差额。开始采集前的流量不能追溯，也不等同于机场按倍率计费的账单。采集器不保存访问 URL 或目标域名。

## GitHub API 自动换出口

`api.github.com` 单独使用 `GitHub API` select 组，候选仅限 STT/ByWave。`mihomo-github-api-guard.timer` 在启动 30 秒后运行，每次完成后间隔 60 秒检查匿名配额。剩余配额不大于 5 时，使用隔离探测最多验证 4 个备选节点；遵守重置时间和 Retry-After，只有确认有额度后才切换。全部不可用时保留当前出口并记录原因。

```sh
systemctl status mihomo-github-api-guard.timer
journalctl -u mihomo-github-api-guard.service --since today
sudo cat /var/lib/mihomo-github-api/state.json
```

只清理已耗尽出口上、连续一秒字节数未变化的 API 连接；GitHub Release、npm 等下载连接保持运行。TLS 内部请求状态不可见，切换时个别 API 请求仍可能需要客户端重试。登录令牌的账号限额、搜索专项限额和二级限流不能保证通过换 IP 解决；应用仍需遵守 GitHub 的退避要求。

## 仓库与现场的一致性

发布清单对脚本和 systemd 单元按字节及权限校验；`check-release.py --root / --dry-run` 可检查现场是否漂移。订阅 URL、令牌、真实节点、设备身份、数据库及运行状态不进入公开仓库。公开模板与私有部署输入因此不会逐字相同。

旧 `wg-ucg-pilot` 依赖已经退役。`ucg-link.conf` 保留为空的迁移占位，升级会覆盖旧依赖，不再拉起隧道。其他文档中的 WireGuard pilot 图是历史拓扑，不代表当前转发路径。

安装器启用新增服务和 timer，但不主动启动；在私有配置准备好后运行 `systemctl start mihomo-traffic.service mihomo-github-api-guard.timer`。升级前保留完整回滚点并获取维护锁；不得通过删除锁或忽略漂移校验强行覆盖现场。
