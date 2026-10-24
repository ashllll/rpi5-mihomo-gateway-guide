# 原生延迟选路与检测预算

本文说明生产运维层的当前源码契约。配置模板默认暂停检测；本轮只做离线源码变更，不表示已经部署、恢复任何 timer，或完成真实节点和客户端验收。一键安装路径仍有独立默认策略。

## 路由与切换

`measurement.enabled: false` 时，生成器把自动组转为 `select`、移除探测字段并关闭 provider health-check；分流规则、节点资格和手动选择保留。开启后，各业务池使用 Mihomo 原生策略：普通网页与 Developer 使用带 tolerance 的 `url-test`；AI Research、YouTube/Instagram 所在的 Streaming、X 所在的 Social 使用保留现有顺序的 `fallback`；TW Fixed IP 保持固定 `select`。下载总组、GitHub、模型和 Package Download 使用扁平的授权叶节点 `url-test`，可以在获准候选间自动切换。它们共用相同轻量健康 URL、状态码和检测周期，生成同一健康检查所有者，避免多组重复扫描。

候选节点只来自当前配置中已获准的大流量池：STT、ByWave，以及 `roles.bulk_download.glados_explicit_allow` 明确列出的 GLaDOS 节点。普通 AI、X、NF 等 GLaDOS 节点不会因为属于同一订阅而加入下载组；STT 池仍与其他业务隔离。部署清单的节点列表必须保留在本地，仓库只包含空白骨架。

`download_routes` 和 `video_routes` 是域名到既有业务组的数据表。下载表将 GitHub Release、Git transport 和 Package 域名送入各自已有授权池。视频表将 YouTube 域名送入 Streaming，将 Instagram 的 `cdninstagram.com`、`instagr.am`、`instagram.com` 后缀送入 Streaming，将既有 X/Twitter 后缀送入 Social；X 规则不会抢先覆盖 `x.ai` 或 `grok.com` 的 AI 规则。这里的健康 URL 只选路，不证明平台登录、地区解锁、播放质量或大文件持续吞吐。

生成配置明确设置 `unified-delay: false`。它不替代固定核心补丁中的 deadline 修复。

撤空 GLaDOS 下载资格后，两个受管下载子组保留为只含 `REJECT` 的手动组，使旧规则/选择器引用明确拒绝。订阅失败回滚会将备份节点按当前权限重新生成并校验，再加载回滚配置；当前策略无效时报告回滚失败，不原样恢复旧授权。

节点名称中的控制字符会在生成阶段拒绝，防止核心与生成器的名称正则匹配差异扩大候选池。合法十进制字符串端口与整数端口在身份比对中统一；不支持或越界的原生检测端口在生成阶段报错。节点名和账户键不作截断、修剪或重命名。

## 账户预算与准入

启用原生检测必须同时配置 `measurement.enabled: true`、`native_budget: true`、账户和受管核心预算服务。模板默认 `enabled: false`、账户清单为空。账户 ID 必须是非空字符串；通过精确节点名或前缀将每个叶节点唯一映射到账户，未匹配、重复匹配和不合法身份拒绝生成或检测。`native_probes_per_hour` 用于生成阶段检查配置的名义周期成本。

本地准入服务使用原有 `/var/lib/mihomo-measurements/quality-budget.json` 账本路径和 `quality_attempts_per_hour` 账户额度键。这个历史键现在承载原生检测的总预算；读取后按一小时窗口清理到期记录，并保留已有账户键和未过期费用。变更额度或键名不会自动清空账本。每个核心原生检测在拨号前固定预扣 2 个尝试额度，失败和服务失联均不退款。健康 URL 相同的业务组共用一个 provider 所有者，减少重复检测。

启动/重启、周期检测、业务失败触发、刷新和回滚产生的每次原生检测都必须向本地 Unix socket 申请额度。准入只接受当前配置中明确的叶节点及其服务器地址/端口，不接受组名、拨号链或伪造身份。额度服务在原子账本锁内重新读取策略与配置；锁内只做本地文件校验，不循环调用 HTTP RPC。锁、账本和同一路径的符号链接别名使用同一规范文件。账本损坏、超时、超额、策略变化或服务不可用时，原生检测被拒绝，不会误写节点失败历史；业务流量本身仍按既有选路工作。

请求截止时间约束锁等待和准入。额度采用预扣，因此核心后续检测失败、连接超时或回滚时均不会退费。这个预算只控制原生健康检测尝试，不是业务连接数、DNS、订阅更新或一般 TCP 新连接的限制。

检查所有者的 URL/状态码合同绑定于具体叶节点。控制器整组或单节点复测同一 URL 时，省略或显式放宽 expected 均不能将不合格节点写成健康；不同有效所有者的条件取交集，所有者刷新/关闭分别更新自己的绑定。状态码失败仍保留预扣费用。

## 安装与验证边界

`mihomo-measurement-budget.service` 提供本地准入服务，默认不启用。可选的固定核心 drop-in 必须先核对同一个核心二进制与账本服务绑定，再由它启动核心。安装器不会启用预算服务，不会创建已退役质量采样状态目录，也不会替升级中的真实主机停用或卸载旧单元。本轮不启动服务、不调用 systemctl、不连接真实节点或公网测速。

保留的离线回归包括 `tools/check_measurement_policy.py`、`tools/check_native_budget.py`、`tools/check_overlay.py`、`tools/check_stability.py`、订阅候选/更新、安装器与发布检查。它们验证合成叶节点授权、预算锁与并发、失联拒绝、策略复验和生成路由；不代表供应商下载许可、YouTube/Instagram/X 实际播放、AI 流式会话或 LAN 验收。

尚未验证的平台播放应在有回滚点的真实客户端维护窗口中单独验收；单次原生延迟检测不能替代播放、下载完整性或长连接检查。已有 TCP/WebSocket 会话也不会随新连接选路自动迁移。
