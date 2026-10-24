# 入门与部署选择

[Wiki 首页](../README.md) · [架构](architecture.md) · [配置参考](reference.md)

## 项目解决什么问题

让下游设备使用一台 Linux 双网口主机作为网关，由 Mihomo 处理透明代理及业务分流。
上游路由器负责上联，桥接 AP / 交换机连接下游；没有 iKuai 依赖。
公开仓库提供机制和模板，不提供可直接使用的机场订阅、真实节点及私有部署数据。

在接线或安装前，准备独立管理通道、备份和明确的 WAN/LAN 归属。桥接 AP 链路不能同时存在竞争 DHCP。
当前公开基线面向 IPv4 下游；双栈、访客 VLAN、容器网关和单网口旁路由需单独设计。

## A：新主机，选择一键入口

先阅读[支持范围与回滚](../one-click-setup.zh-CN.md)，在目标 Linux 主机运行：

```sh
sudo sh setup.sh --dry-run
```

它只预览，不下载、不安装、不切换。正式安装命令 `sudo sh setup.sh` 会改变网络和系统服务，
应从上联 SSH 或本地控制台执行。不要在已经运行另一套生产 Mihomo / MosDNS 的主机上直接接管。

一键入口采用项目默认规则，只导入订阅节点，不保留供应商完整规则/分组。
如果你必须保留原始策略、多个供应商能力边界和专用 AI 池，不能把此入口当作现有生产方案的等价替代。

完成安装门禁后，仍需按[真实客户端验收](validation.md)验证手机、视频和会话。

## B：自己规划网络，先生成候选

在仓库根目录：

```sh
cp examples/site.example.json site.local.json
# 按实际环境编辑私有站点参数。
python3 tools/render.py --site site.local.json --output rendered
```

输出目录必须不存在。生成器创建 9 个私有文件，包括随机 controller secret 与故意不可用的 provider 占位节点。
生成成功不代表能联网；替换节点、审阅规则、目标机语法检查、备份和部署均需另外执行。
详见[分阶段部署](../deployment-current.zh-CN.md)。不要把生成目录提交到 Git。

## C：维护已有网关，安装自动化层

```sh
sudo automation/install.sh --dry-run
```

正式安装覆盖受管脚本和单元，既有能力清单、DNS 配置等部署数据保留。
这条路径不会自动配置接口、路由、sysctl 或 nftables，也不会装齐核心及私有订阅。
按[自动化层说明](../../automation/README.zh-CN.md)补齐 env、secret、capabilities 与 reconcile 映射，
再有选择地启用 timer。默认集合含七个 timer；自动隔离执行保持显式启用。

## 接手已有部署时的第一份记录

只把脱敏结果写入项目文档：

- 部署路径、源码提交、核心版本、操作系统与网络管理器。
- 上联/下联角色、是否存在本机 VPN/TUN、服务归属与已启用 timer。
- 私有配置和备份的存放位置、最近一次成功验收日期、回退通道。
- 已知业务限制、供应商下载授权的核验日期、未完成的验收项。

不要把账号信息、订阅、真实节点标签、Cookie、完整配置和真实设备清单放到公开交接记录。
遇到故障进入[运维手册](operations.md)，不要先重装。
