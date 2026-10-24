# 分阶段部署公开模板

## 发布内容与生产实现的区别

本仓库提供配置生成器、DNS 分层说明与运维设计，并发布网关上实际运行的自动化层源码
（[`automation/`](../automation/README.zh-CN.md)）。仓库没有复制生产环境的订阅链接、节点名、
完整规则库、动态评分状态和管理密钥；节点标签、订阅链接与真实地址由部署端提供。模板是可验证的基础，
不是一键还原镜像。旧长文中的性能结果是历史测试，不是每台设备的保证。

## 准备

使用 Debian 13 ARM64 / Raspberry Pi OS 的匹配发行版，安装 nftables、dnsmasq、
iproute2、curl、jq、ethtool、dnsutils 和 Python 3。Mihomo 和 MosDNS 从各自官方
发布渠道取得，选择 ARM64 构建并核对发布校验信息。仓库不捆绑第三方二进制或规则库。

先按实际永久 MAC 固定 WAN/LAN 名称，确认两侧不重叠。上游 DHCP；LAN 固定地址、
不配置默认网关；AP 桥接且关闭 DHCP。关闭 LAN IPv6 RA/DHCPv6，确认上游 IPv6
不会被误分发到下游。接口、NetworkManager 与 IPv6 配置需要按本机环境配置，
生成器不会擅自修改它们。

## 生成

```sh
cp examples/site.example.json site.local.json
# 编辑接口、网段、LAN 地址和 DHCP 范围。
python3 tools/render.py --site site.local.json --output rendered
```

输出目录必须尚不存在，避免覆盖旧配置。全部文件使用私有权限，controller secret
每次生成随机值；不要提交 rendered 或 site.local.json。JSON 是 YAML 的子集，
所以生成的 .yaml 可被 Mihomo 解析，无需额外 Python YAML 依赖。

输出包含配套 `mihomo-automation.env`，其 LAN 地址、mixed/controller 端口与 WAN 接口直接来自同一份站点参数。
无线管理接口、订阅镜像与能力清单路径等自动化独有参数仍需审阅；这些参数不会从下游 LAN 接口推导。

将 mihomo/providers 下的无效占位节点换成自己的文件型 provider（顶层 proxies 列表）。
general-a / general-b **只能放允许大流量下载的节点**；ai-only 放专用 AI 节点；
download-only 放明确授权的下载节点。供应商原始订阅存放在私有位置，另行转换，
不要把整份包含 rules / proxy-groups 的订阅当作 proxy-provider 文件直接使用。

模板提供有限示例规则：没有完整国内、游戏、YouTube CDN、AI 和模型下载域名覆盖。
特别是默认 MATCH 会把未知目的地送到 Browsing。正式上线前按供应商能力和实际业务
补齐规则；一般节点不能承载大流量时不要将其放入 general-a / general-b。
不以 DOMAIN-KEYWORD,google 抢先覆盖 googlevideo，否则视频可能误入 AI 池。

## 验证后安装

保留上游 Wi-Fi 管理终端或本地控制台，备份目标文件。先只验证候选：

```sh
sudo nft -c -f rendered/nftables.conf
sudo dnsmasq --test --conf-file=rendered/dnsmasq-lan.conf
mihomo -t -d rendered/mihomo
```

nftables 需要支持 destroy table 的版本；旧版本应先升级兼容版本或调整幂等加载方式，
不要为了绕过错误使用 flush ruleset。语法检查须在安装目标系统进行。

人工对照后按文件安装：nftables.conf → /etc/nftables.conf，dnsmasq-lan.conf →
/etc/dnsmasq.d/lan.conf，gateway-sysctl.conf → /etc/sysctl.d/90-gateway.conf，
mihomo 目录 → /etc/mihomo。先检查已有 dnsmasq include、server 和 listen 选项冲突。
Mihomo 的 systemd 单元可由 `automation/install.sh` 安装，也可按发行版自行准备
（ExecStart 使用实际安装二进制和 -d /etc/mihomo），再分阶段应用 sysctl、DHCP/DNS、NAT 和代理。

自动化层（脚本、单元与 MosDNS 骨架）的安装：

```sh
sudo automation/install.sh --dry-run   # 先看计划：只打印，不碰文件系统
sudo automation/install.sh             # 覆盖脚本与单元，保留已存在的部署数据
```

安装后审阅生成的 env，再按 0600 安装到 /etc/mihomo-automation.env；已有部署只合并共享字段并保留独有设置。
随后放入 secrets、填写 capabilities.yaml 的
overlay 段与 reconcile-targets.conf。安装器会独立补入公共 `route-defaults.yaml`，已有旧 capabilities.yaml 保持原字节；如需覆写域名路由，再在私有清单填写非空 `download_routes` / `video_routes` 表。最后按需 `--enable-timers`。详见
[自动化层说明](../automation/README.zh-CN.md)。

controller 有随机 secret；SSH 保留发行版当前认证策略。若需要密码登录，用 sshd -T
确认 PasswordAuthentication yes，修改后先 sshd -t 再 reload，并从第二连接验证。
不需要在上游路由器创建公网映射，也不需要按单台管理终端 IP 限制内网 SSH。

## MosDNS 第二阶段

生产方案的 MosDNS 使用国内 / 代理 / 自定义域名集合和 CN IP 集合，在 127.0.0.1
监听，海外交给 Mihomo。配置骨架、自定义域名规则以及 mosdns-update-rules /
mosdns-update-binary / mosdns-rollout-rollback 等脚本已随 automation/ 发布，规则数据的
来源与摘要仍需你确认。详见当前架构。
确认候选 DNS 实例、规则来源和摘要后，才把 dnsmasq server 改为 MosDNS 的本机端口。
不要同时保留两个不受控的全局 DNS 上游，否则会随机绕过分流。切换后清理相关缓存，
确认国内解析和 fake-IP 映射均正常。

## 上线门禁

完成 [真实 LAN 验收](operations-current.zh-CN.md)，再配置自动更新。
更新任务需要互斥、候选验证、回滚和业务检查，至少 24 小时更新订阅。
启用 Persistent 的 timer 前评估补执行行为。原生节点检测默认关闭；如部署者显式开启，必须配置账户授权、固定核心预算补丁和本地准入服务。
六个维护 timer 的周期与启用方式见[自动化层说明](../automation/README.zh-CN.md)，先用默认安装让单元就位，确认参数与 secrets 后再按需启用。
