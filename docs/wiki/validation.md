# 验证矩阵与现场验收边界

> [Wiki首页](../README.md) · 版本基线以 [docs/README.md](../README.md) 为准。本页机制说明随 checkout 源码维护，不是测试结果或发布收据。

## CI 执行矩阵

权威命令序列见 [`.github/workflows/validate.yml`](../../.github/workflows/validate.yml)。除最后的 Linux 命名空间任务外，工作流在 Ubuntu 上先确保 PyYAML 可导入，再运行以下命令：

| 命令 | 由当前测试实际覆盖 | 不能据此声称 |
|---|---|---|
| `python3 tools/validate_public.py` | 必要发布文件、SVG/HTML 可解析性及工作树文件的隐私模式扫描。 | 不扫描 Git 历史，也不证明部署数据正确。 |
| `python3 tools/check_render.py` | renderer 在临时目录生成候选、共享参数传播、拒绝危险输入/覆盖、文件权限和环境模板一致性。 | 没有把候选安装到主机。 |
| `python3 tools/check_overlay.py` | 合成配置下的池权限、Download 系列扁平 native URLTest、域名路由、AI/X 与 Instagram 业务边界、空能力清单 fail-closed。 | 不含真实订阅、节点标签或供应商能力验证。 |
| `python3 tools/check_subscription_candidate.py` | 生产候选入口的合并、引用重命名、缓存、未变化及错误时不留下成功候选。 | 不更新运行中的主配置。 |
| `python3 tools/check_subscription_update.py` | 以 stub 命令和临时目录运行 16 种成功、锁定、校验/写入/重载/健康失败与回滚场景。 | 不访问真实订阅、controller 或 systemd。 |
| `python3 tools/check_automation.py` | automation 源语法、变量与模板双向匹配、systemd 环境文件约定、能力清单键/空节点列表、沙箱安装保留本地数据及运行参数传播。需要 PyYAML。 | 沙箱安装不管理宿主 systemd，也不是目标机部署。 |
| `python3 tools/check_stability.py` | 按 checkout 源码执行的离线 stub 检查：健康检查只读进程状态，恢复动作不启动业务 HTTP 探测；禁用策略生成稳定选择器。 | 不请求真实 X 时间线、AI 网页、视频或节点；不是现场验收。 |
| `python3 tools/check_measurement_policy.py` | 原生所有权合并、账户映射、保留账本键/历史、锁等待、原子账本、并发与暂停行为。 | 不调用真实 controller、核心或公网节点。 |
| `python3 tools/check_native_budget.py` | Unix socket 准入、每次固定预扣、锁内策略复验、截止时间、失联/坏账本拒绝与 checked socket 绑定。 | 使用匿名 loopback 临时套接字和合成核心，不证明实际部署服务或真实路由。 |
| `python3 tools/check_update_rules.py` | stub/临时目录中的 8 种规则更新事务、失败恢复和 reload 门禁。 | 不下载或应用实时规则。 |
| `python3 tools/check_publication_privacy.py` | 隐私扫描器规则及脱敏报告格式的合成回归。 | 不等同于 `validate_public.py` 的全工作树扫描或 Git 历史扫描。 |
| `python3 tools/check_image.py` | 合成镜像流、摘要/布局拒绝、引导数据清理与写盘拒绝路径；写入使用临时普通文件和模拟调用。 | 不写入真实磁盘，也不证明恢复介质能在目标板启动。 |
| `python3 tools/check_setup.py` | 一键安装器默认 unittest/FakeHost：接口选择、配置、重复安装、故障注入、恢复与权限。 | 不变更当前主机网络。默认工作流未传真实核心参数；带 `--mihomo/--rules` 的核心解析是额外本地测试，不是此 CI 命令。 |

### 隔离 Linux 网络命名空间

工作流随后用 `sudo` 安装 `iproute2`、`nftables`、`dnsmasq-base` 并运行 `sudo python3 tools/check_setup_linux.py`。该脚本要求 Linux root，并通过 `unshare --net` 创建独立 network namespace；用合成接口和配置检查 nftables 语法、dnsmasq 语法、防火墙重复加载/停止以及保留无关表。脚本明确拒绝在宿主网络命名空间里执行。

这层是真实 Linux 解析器/命名空间证据，不是树莓派物理网卡、真实路由器、Wi-Fi、下游客户端、上游互联网或重启恢复的证据。其前置条件不满足时应标记“未运行”，不能把静态检查替代成通过。

## 分层证据

1. **静态与发布结构**：语法、模板、路径、隐私规则；能够发现仓库内格式或契约回归，不触碰设备。
2. **合成/临时目录模拟**：stub 外部命令、伪造配置和故障注入；用于检查事务和 fail-closed 行为，不证明外部服务实际可用。
3. **隔离 Linux namespace**：由 CI 测试真 nftables/dnsmasq 解析和本 namespace 中的应用/停止；不经过物理 LAN 或 Wi-Fi。
4. **目标机本地门禁**：部署前的 Mihomo、dnsmasq、nftables 配置检查与本机服务/显式代理检查；只证明相应本机门禁。参考[一键配置指南](../one-click-setup.zh-CN.md)与[运维指南](../operations-current.zh-CN.md)。
5. **真实 LAN 与应用验收**：必须由实际下游终端完成并单独记录，不能由以上任何一层推断。本任务没有执行这一层。

## 现场验收清单（待执行，不是已通过结果）

在可恢复的目标网关、具备上联管理通道的维护窗口内逐项记录；不得把地址、订阅、凭据或节点标签写入公开记录。

- [ ] 网关安装后确认服务状态、LAN DHCP/DNS、直连/代理路径；确认测试终端没有本机 VPN/TUN 代替网关转发。
- [ ] 用真实手机接入下游网络，重新获取网络租约；分别核对常用直连服务与代理服务，并记录终端类型/测试时间，不公开标识。
- [ ] 在已登录的真实 X 客户端打开并滚动时间线，检查文本、图片和视频媒体；分别记录 API/静态资源/时间线结果，不把 HTTP 探针成功写成时间线已验收。
- [ ] 在真实 AI 网页或应用完成一次持续流式响应；区分 API 可达、账号/网页状态和流式正文实际返回。
- [ ] 实际播放目标流媒体；再执行经授权的 GitHub/模型文件下载，检查完整性、持续传输与中断/续传表现，不用单次探测码代替下载验收。
- [ ] 在保留带外/上联管理通道的条件下重启网关；确认服务自动恢复、手机可重新联网、DNS 与透明转发恢复，再记录未通过项和回滚点。
- [ ] 若要验证失败回滚，先在可恢复环境演练并保留本地备份；不要在唯一远程管理路径上试验。

## 已知覆盖缺口

- CI 没有真实家庭 LAN、路由器/AP、手机无线客户端或物理网卡组合；命名空间使用合成接口且不代表目标网络拓扑。
- 自动回归使用合成节点/假命令；真实供应商套餐权限、平台解锁、登录态、地区限制、吞吐和大文件连续性仍需人工验证。
- 原生健康 URL 仅用于检测/选路；YouTube、Instagram、X 播放和 AI 流式会话未由离线回归验证。
- 稳定性回归使用 stub；其断言明确保留 `x_timeline=unverified`，因此不能据此报告真实 X 时间线验收。
- 默认 CI 没有执行带真实 Mihomo 核心的可选订阅解析路径，也没有运行 `tools/check_landscape_mihomo.py`；这些不在 [`validate.yml`](../../.github/workflows/validate.yml) 的命令清单中。
- 自动化隐私扫描检查当前文件集，不检查提交历史；公开发布前仍须单独审查暂存内容与历史，且不能把含真实凭据的日志/备份加入仓库。
