# 待核验经验

- 2026-10-31：固定核心 v1.19.28 的 file provider 启动可落入 empty-fallback，但热刷新 proxies: [] 返回 503 并保留旧成员；必须发布 name: REJECT/type: reject 哨兵并回读 provider 与 fallback 的 all/now，避免最后一个慢节点无法移除。Mac 与 Pi 隔离实核均验证节点→REJECT→节点且 PID 不变；证据：tools/check_media_core.py。媒体样本事实需在维护锁尝试与 provider 发布前持久化，否则锁忙或第二组发布失败会让已知慢节点在下轮不可判定结果后重新准入；13 项 guard 回归覆盖。

- 2026-10-30：准入修复后仍出现一次视频代理服务器连接 EOF，随后恢复；上游传输波动的来源尚未定位。证据：本地 `mihomo-live-20261030/validation/admission-observation.jsonl` 与 `recent-runtime-errors.json`。不能据此扩池、放宽健康判据或宣称持续稳定。
- 2026-10-30：Telegram 13 个候选均无健康状态时选回失败首项；单个 STT 原生复测后自动切换且网关 HTTP 200，但随后真实 VLAN 客户端仍遇到代理服务器 TCP 建连超时。新 BWB 订阅入口已验证并备份替换；其首项新参数及两枚地址变化的 STT 媒体候选均未通过隔离连接验证，未替换运行节点。15 分钟周期会延迟恢复发现，但不是全部故障原因。证据：本地 `mihomo-live-20261030/validation/telegram-final-diagnosis.json`；底层链路原因仍待核实。

- 2026-10-30：新 Meta 订阅增加 AnyTLS，但旧媒体名称规则漏掉新协议节点。仅在站点配置扩展同来源、同地区的名称匹配，先验证生成候选的成员边界与实际核心，再四文件备份、定时回滚和热加载；真实 VLAN20 五路径连续三轮通过。不能把下载成功或全局节点数当作业务入池证据。新节点中仍有连接失败，旧线路底层原因未证实。证据：本地 `mihomo-live-20261030/TELEGRAM.md` 与 `validation/stt-meta-final.json`。

- 2026-10-30：临时订阅首次下载成功、五分钟后 HTTP 410；用户确认有效期且将该来源排除自动刷新。使用 MIHOMO_SUBSCRIPTION_MANUAL_SOURCES 保留已校验缓存并完全跳过下载，缺缓存拒绝应用，--force 不绕过；实际 systemd 日志验证手动来源未下载、其他来源刷新成功。证据：本地 `mihomo-live-20261030/SUBSCRIPTIONS.md`，CLI 24 场景及事务 27 项通过。

- 2026-10-31：macOS 系统 tar 携带 AppleDouble 元数据，Linux 候选包出现 `._*` 额外文件，使 release manifest 校验拒绝安装。改用 Python tarfile 生成无扩展属性的包，定向清理本次暂存目录中确认由归档生成的元数据后，实机包校验通过；未对已安装目录清理。证据：本地 `mihomo-notifications-20261031/package.tgz` 和 `deploy.py`。

- 2026-10-31：Mac 本地订阅事务 6 秒用例在慢请求前耗尽时间，日志明确为 before apply，未提交候选；相同未改测试在两轮 Linux CI 均通过。仅作补充验证，Mac 配置 12 秒/模拟慢请求 20 秒后进入探测并超时拒绝，旧节点和缓存保留（总耗时约 16.2 秒含恢复）；未调整仓库断言或生产超时。证据：`mihomo-notifications-20261031/ship/mac-deadline.json`、`pr-validation.log`。
