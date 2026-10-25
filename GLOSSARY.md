# 术语表

描述这台 rp5 网关的部署与运行词汇。括号里的路径是权威位置。

**TUN**：Mihomo 建的虚拟网卡（设备名 `Mihomo`，地址 `198.18.0.1/30`）。UDP 与部分 TCP
经它进入核心；表 `2022` 的路由指向 `198.18.0.2`。TUN 不在，代理流量就静默黑洞。

**fake-IP**：核心对代理域名返回 `198.18.0.1/16` 内的占位地址，客户端拿它建连，
核心按规则选出口后再用真实域名解析。`fake-ip-filter` 内的域名保留真实地址。
fake-IP 只证明解析成功，不证明转发成功。

**auto-redirect**：Mihomo 自动维护的 TCP 重定向入口（动态端口，本机 `:46225`），
配合 `inet mihomo` 的 `redirect` 规则把 TCP 送进核心。端口是动态的，所以防火墙
不能写死白名单，`lan0` 整体放行。

**预算准入**：显式节点拨号前的本地额度检查。核心请求
`/run/mihomo-measurements/budget.sock`，broker 校验协议、节点身份与期限，按 2 次尝试预扣，
写账本 `/var/lib/mihomo-measurements/quality-budget.json`。拒绝时不拨号、不改节点健康。
_避免_：限速、流量整形。

**门禁**：判定“可以放流量”的一组内核状态，而不是进程存活。启动门禁由
`mihomo-healthcheck --startup-check` 检查 TUN、`inet mihomo`、`inet rp_filter`、`ip rule 9000`；
常态门禁每分钟复检并写 `/run/mihomo-healthcheck/status`。

**包装器（预算 broker）**：`mihomo-measurement-budget`。`mihomo-measurement-budget.service`
（无参数）常驻建 `/run/mihomo-measurements`（0700）与 `budget.sock`（0600）；drop-in 把
`mihomo.service` 的 `ExecStart` 换成 `--run-core /usr/local/bin/mihomo`，先核验核心协议、
配置与 broker 状态一致，再 `execve` 核心并注入 `MIHOMO_MEASUREMENT_SOCKET`。
账本在首次预扣时才原子创建。

**休眠单元**：仓库安装但保持 disabled 的 timer（rules/subscription/download/social）。
它们不自动运行，只保留手动执行能力；`systemctl start` 可跑一次。
_避免_：删除的单元——自适应评分已删除，不属于休眠。

**业务候选池**：被允许承载某类业务的一组出口节点。进入候选池表示满足部署者给定的资格，
不表示业务始终可用。_避免_：全局最快池、万能节点池。

**源订阅**：供应商提供的原始配置输入，与合并、筛选、覆盖后的运行候选不同。
_避免_：最终运行规则。

**订阅候选配置**：合并主订阅、额外来源与本地保留节点并应用本地策略后的待验证配置；
候选生成不表示运行配置已更新。

**传输可达**：探测得到规定范围内的响应。不包含登录、内容展示或完整业务成功。
_避免_：平台正常、业务正常。

**业务验收**：真实客户端在预期链路中完成任务（时间线、播放、流式会话、下载完整性）。
_避免_：测速通过、首页可达。

**影子评分（已退役）**：历史观察评分机制，2026-10-24 停止并删除对应脚本与 unit；
由 download/social-quality 采样接续（现休眠，只经预算准入）。当前选路由核心原生
`url-test`/`fallback` 与预算服务负责。
