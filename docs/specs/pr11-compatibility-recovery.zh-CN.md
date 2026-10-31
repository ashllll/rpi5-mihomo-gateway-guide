# PR #11 兼容性与恢复修复规格

本规格定义订阅兼容性、失败冷却和安装恢复的有效契约。验收通过公开 CLI 及隔离文件系统执行；实机服务和断电验收另行进行。

## 问题与目标

订阅刷新不能因旧 STT 引用、合法 SS URI 转换错误或过期失败指纹而被错误阻挡；安装中断后必须有可验证的恢复路径，不能靠绕过漂移校验继续。

## 行为规格与验收

| ID | 用户需要的行为 | 验收接口与判据 |
| --- | --- | --- |
| R1 | 旧 STT 组没有显式 selections 配置时，仍能跟随当前 STT 节点改名或部分下架；显式配置优先；仅由用户 provider 提供成员的组保留其所有权 | candidate prepare：显式成员无旧引用，保持当前配置不变；用户 provider 的 use/filter 不变；沿用能力清单中的来源前缀，不扩展其他业务池；全部候选不可用时保持安全拒绝 |
| R2 | 标准 SS 链接的地址、凭据和受支持插件参数正确转换 | normalize INPUT OUTPUT：尾部斜线、百分号编码、URL-safe Base64、IPv6、插件映射的输出符合 SIP002 与 Mihomo 字段契约；不支持或错误的插件不能静默降级为无插件节点；错误消息不含凭据 |
| R3 | 冷却只阻止实际相同的失败候选 | update-subscription CLI：同一失败候选跳过；修改候选继承的 TUN/DNS 字段后普通重试重新验证并应用；保留策略与 helper 身份校验、维护锁、失败回滚和 --force |
| R4 | 安装中断后能恢复一致版本，并继续正常升级 | install.sh + GATEWAY_ROOT：复制中失败恢复旧文件和安装清单；不可捕获中断由下一次调用恢复；新建文件正确清理，人工漂移仍拒绝；dry-run 不写入、不执行恢复；系统服务操作不在沙箱执行 |

## 安装事务边界

- 维护锁覆盖恢复、文件安装与提交；沙箱使用根目录内的锁。dry-run 不创建锁或目录，发现待恢复事务时非零退出。
- 修改前持久保存旧文件、权限及缺失状态；逐文件原子替换。失败恢复旧文件集合，初装产生的文件和目录按记录清理。
- 恢复前校验日志结构、快照完整性及全部受管状态；写入或恢复期间发现人工漂移时拒绝覆盖并保留恢复记录。既存私有配置继续保留，敏感目录权限仍收紧至 `0700`。
- 新文件集合与安装清单共同提交后，才进行 systemd 配置。服务操作失败必须非零退出，并明确文件已提交，不宣称服务或文件已回滚。
- 该锁协调本项目的维护操作；不能保证不使用此锁的外部进程在最后一次检查与文件操作之间没有竞争。

## 验收入口

| 规格 | 持续回归 |
| --- | --- |
| R1 | `tools/check_subscription_candidate.py`、`tools/check_overlay.py`、`tools/check_legacy_stt.py` |
| R2 | `tools/check_subscription_normalize.py`、`tools/check_subscription_update.py` |
| R3 | `tools/check_update_transaction.py` |
| R4 | `tools/check_release_integrity.py`、`tools/check_automation.py` |

发布检查使用 `.github/workflows/validate.yml` 的入口；受管文件变更后必须重新生成并验证发布清单。

## 测试约定

沿用已用于复现问题的公开 CLI 接口及现有临时目录夹具。真实执行候选生成、转换、锁和文件操作；只在操作系统命令、下载、核心进程等外部接口注入故障。测试不依赖生产凭据，不执行线上故障注入。保留逐项 red/green 证据。

## 不在本轮范围

不调整节点授权、业务分组偏好或真实订阅数据；不新增供应商协议；不自动部署、推送或合并。离线与沙箱通过不替代真实客户端、systemd 时序和断电后的实机验收。

## 协议依据

- [Shadowsocks SIP002](https://shadowsocks.org/doc/sip002.html)
- [Mihomo Shadowsocks 配置](https://wiki.metacubex.one/config/proxies/ss/)
