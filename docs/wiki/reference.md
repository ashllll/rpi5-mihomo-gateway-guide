# 源码、安装路径与配置所有权

[Wiki 首页](../README.md) · [部署](../deployment.zh-CN.md) · [自动化层](../../automation/README.zh-CN.md)

## 仓库到主机

| 仓库 | 安装到 | 说明 |
|---|---|---|
| [`automation/install.sh`](../../automation/install.sh) | 就地执行 | 覆盖式幂等；`--dry-run` 预览；只 enable 11 个单元 |
| [`automation/sbin/`](../../automation/sbin/) | `/usr/local/sbin/` | 探针、预算 broker、DNS 模式机、订阅/规则/换核工具 |
| [`automation/systemd/`](../../automation/systemd/) | `/etc/systemd/system/` | 单元与 6 个 `mihomo.service.d` drop-in |
| [`automation/etc/dnsmasq/`](../../automation/etc/dnsmasq/) | `/etc/dnsmasq.d/resolver.conf` 等 | 两个 dnsmasq 作用域（环回 + VLAN20）+ 上游基线 |
| [`automation/etc/mosdns/`](../../automation/etc/mosdns/) | `/etc/mosdns/` | 配置与自定义规则；大规则集由更新任务下载 |
| [`automation/env/`](../../automation/env/) | `/etc/*.env.example` | 部署参数模板，真实文件 0600 |
| [`automation/policy/`](../../automation/policy/) | `/etc/mihomo/adaptive/` | 能力清单与路由默认值骨架 |
| [`core/`](../../core/README.md) | `/usr/local/bin/mihomo` | 固定 v1.19.28 + 预算补丁，按 README 自编译 |
| [`image/`](../../image/) | 恢复介质 | 校验/写盘工具，不属于网关运行层 |
| [`tools/`](../../tools/) | 不安装 | 发布结构、隐私与合成回归检查 |

## 数据所有权

- 运行配置 `/etc/mihomo/config.yaml` 由订阅管线生成；本地覆盖段与能力清单由部署者维护。
- 订阅、dashboard token、告警 key 只在 `/etc/mihomo/secrets/` 与 `/etc/*.env`。
- 账本 `/var/lib/mihomo-measurements/quality-budget.json` 只由 broker 写。
- 仓库里的 `*.example`、空能力池和示例 JSON 都不含真实节点。

## 单元数量

发布层 enable 13 个单元（六个 service、七个 timer），订阅刷新在开机十分钟及任务结束后每 48 小时运行。
`mihomo-rules-update`、`download-quality`、`social-quality` 三个 timer 随仓库安装但保持 disabled。
清单、失败恢复与手动操作见[运维文档](../operations.zh-CN.md)。
