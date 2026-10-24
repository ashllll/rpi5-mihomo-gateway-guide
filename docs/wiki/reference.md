# 源码、安装路径与配置所有权

> [Wiki首页](../README.md) · 版本基线以 [docs/README.md](../README.md) 为准。本页机制说明随 checkout 源码维护，不是测试结果或发布收据。

所有源码链接均相对本页；核对特定版本时，以 Wiki 首页记录的基线为准。

## 代码地图

| 路径 | 责任边界 |
|---|---|
| [`setup.sh`](../../setup.sh) → [`gateway/setup.py`](../../gateway/setup.py) | 新主机的一键网关安装。外层脚本只准备 Python；Python 入口负责探测、依赖、候选配置、网络切换、健康门禁与回滚。 |
| [`gateway/defaults.json`](../../gateway/defaults.json) | 一键安装器使用的核心版本/摘要、端口、DNS、规则及默认服务策略；不是运维自动化的能力清单。 |
| [`tools/render.py`](../../tools/render.py) | 面向手动分阶段部署的站点参数校验与私有候选配置生成；不安装软件、不改主机服务。 |
| [`examples/site.example.json`](../../examples/site.example.json) | renderer 的无个人值参数模板；字段集合由 renderer 严格校验，部署者应另存本地站点文件。 |
| [`automation/install.sh`](../../automation/install.sh) | 已有 Mihomo/MosDNS 部署的运维脚本与 systemd 单元安装器；不是一键网络配置器。 |
| [`automation/sbin/`](../../automation/sbin/) 与 [`automation/systemd/`](../../automation/systemd/) | 运维层的原生策略/预算准入、订阅/规则更新、进程检查与 DNS/MosDNS 操作，以及对应 service/timer 模板。 |
| [`automation/env/`](../../automation/env/) 与 [`automation/policy/`](../../automation/policy/) | 安全示例和部署策略骨架；真实站点参数及订阅能力数据由网关管理员持有。 |
| [`tools/check_*.py`](../../tools/) 与 [CI 工作流](../../.github/workflows/validate.yml) | 临时目录模拟、静态/隐私检查及隔离 Linux 命名空间检查；见[验证矩阵](validation.md)。 |
| [`image/gateway_image.py`](../../image/gateway_image.py) | 独立的恢复镜像校验、清理与写盘路径；不属于上述三种安装入口。 |

## 三条不同的安装路径

| 路径 | 输入与产物 | 明确不做的事 |
|---|---|---|
| **一键网关：** [`setup.sh`](../../setup.sh)、[`gateway/setup.py`](../../gateway/setup.py) | 在 Linux/systemd 目标主机运行；探测接口与网络管理器，读取私有订阅，准备并验证核心及配置，备份后应用网关网络和三个 `mihomo-gateway*` 服务。`--dry-run` 只探测/打印计划；`--rollback` 恢复备份。 | 不使用生产运维层的 overlay/评分清单；它有独立默认值、配置目录、服务和事务回滚。 |
| **手动生成：** [`tools/render.py`](../../tools/render.py) | 读取严格匹配 [`examples/site.example.json`](../../examples/site.example.json) 字段的本地站点文件，在一个新的、权限受限的输出目录生成私有候选文件（含环境文件、Mihomo、DNS、防火墙、sysctl 与占位 provider）。 | 不覆盖已有输出目录，不安装、不 reload、不切换网卡；占位 provider 不是可用订阅。 |
| **运维层安装：** [`automation/install.sh`](../../automation/install.sh) | 将 `automation/sbin/`、`automation/systemd/` 和配置骨架安装到既有系统；支持 `--dry-run`、`GATEWAY_ROOT` 沙箱前缀与显式 `--enable-timers`。脚本/单元按仓库版本安装，已有部署数据按“存在则保留”处理。 | 不负责网卡、路由、防火墙或一键网关初始化；不自动安装 `automation/optional/`，不生成真实订阅/节点能力。 |

这三者不可互换：一键入口拥有网络事务；renderer 只产出文件；automation installer 只部署运维层。对应细化操作见[一键配置指南](../one-click-setup.zh-CN.md)、[部署指南](../deployment-current.zh-CN.md)和[自动化层说明](../../automation/README.zh-CN.md)。

## 配置契约与数据所有者

| 配置 | 契约与消费者 | 所有者/发布边界 |
|---|---|---|
| `gateway/defaults.json` | `gateway/setup.py` 的版本、摘要、端口和一键策略默认值；可通过 `--defaults` 指定替代文件。安装器将所用默认值随程序保存。 | 项目维护者维护无个人值默认策略；部署者决定是否使用经审阅的覆盖文件。 |
| `examples/site.example.json` → 本地站点文件 | `tools/render.py` 要求字段集合与模板完全一致，并校验接口、网段、地址、端口、DHCP 范围和 DNS 输入。当前没有单独的 JSON Schema 文件；执行校验的契约在 renderer 与 [`tools/check_render.py`](../../tools/check_render.py)。 | 仓库只放示例模板；真实站点文件与生成目录由部署者保存在本地，不提交。 |
| `automation/env/automation.env.example` → `/etc/mihomo-automation.env.example` | 运维脚本读取的部署参数模板；installer 安装的是 `.example`。renderer 生成候选运行文件 `mihomo-automation.env`，只回填站点与自动化共同使用的 LAN 地址、mixed/controller 端口和 WAN 接口，其余自动化参数仍需审阅。 | 示例值不代表部署值；管理员审阅后才将私有运行文件安装为 `/etc/mihomo-automation.env`。`check_automation.py` 对脚本读取项与模板变量做双向核对。 |
| `automation/policy/capabilities.yaml` → `/etc/mihomo/adaptive/capabilities.yaml` | 消费者约定包括稳定性设置、download/video 域名到既有业务组的映射、账户预算和 overlay 节点列表。`mihomo-policy-overlay` 消费规则/overlay，`mihomo-measurement-policy` 规划原生所有权，`mihomo-measurement-budget` 执行准入；测试核对键、分组白名单和仓库骨架中的空节点/账户数据。 | 仓库文件是无订阅节点数据的策略骨架；部署副本及节点标签归管理员。installer 采用“存在则保留”，不会覆盖已编辑的能力清单。字段由消费者和检查脚本共同约束；当前未发现独立的正式 YAML Schema。 |
| `automation/etc/`、`automation/env/reconcile-targets.conf.example` | DNS 上游、MosDNS 配置/规则及 selector 目标映射的安全基线或模板，由相应 DNS/更新脚本读取。 | 可发布的机制/模板与运行时配置分开；运维者负责准备和审阅目标机数据。 |

不要把一键安装器的 `gateway/defaults.json` 与运维层的 `capabilities.yaml` 合并理解：前者定义独立入口的默认策略；后者为既有运维栈提供部署侧能力和服务策略输入。不得将订阅地址、密钥、真实站点参数或节点标签写入仓库、Wiki、截图或日志。

## systemd 默认与可选任务

`automation/install.sh` 会复制 `automation/systemd/` 下的现行单元文件；只有加 `--enable-timers` 时，安装器才执行以下六个 `enable --now`：

- `mihomo-healthcheck.timer`
- `mihomo-subscription-update.timer`
- `mihomo-rules-update.timer`
- `mosdns-healthcheck.timer`
- `mosdns-rules-update.timer`
- `mosdns-binary-update.timer`

升级已有主机时，安装器不会停用或删除旧版本遗留文件；管理员需在维护窗口核对其实际状态并按退役流程人工处理，本轮没有对真实主机执行这些操作。`automation/optional/` 也不会由该安装器复制。

这组六项属于运维层；[`gateway/setup.py`](../../gateway/setup.py) 另行生成并管理 `mihomo-gateway.service`、`mihomo-gateway-dhcp.service`、`mihomo-gateway-firewall.service`，并在网络事务期间动态建立 rollback timer。不要将事务 watchdog 当作运维层的默认 timer。
