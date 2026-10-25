# 验证矩阵与现场验收边界

[Wiki 首页](../README.md) · [部署](../deployment.zh-CN.md) · [运维](../operations.zh-CN.md)

## 仓库检查

```sh
python3 tools/validate_public.py
python3 tools/check_publication_privacy.py
```

`validate_public.py` 检查必需文件、无非白名单二进制、SVG 可解析、工作树文本不命中隐私模式；
`check_publication_privacy.py` 回归隐私规则本身。其余合成检查按改动范围选用：

| 命令 | 覆盖 |
|---|---|
| `check_automation.py` | 脚本语法、env 变量与模板双向匹配、沙箱安装保留本地数据 |
| `check_overlay.py` | 业务池权限、扁平 URLTest、域名路由与空池拒绝 |
| `check_measurement_policy.py` | 原生检测所有权、账户映射、账本键与锁 |
| `check_native_budget.py` | socket 准入、预扣、截止时间、失联拒绝 |
| `check_subscription_candidate.py` / `check_subscription_update.py` | 候选生成与事务更新/回滚 |
| `check_update_rules.py` | 规则更新事务与 reload 门禁 |
| `check_stability.py` | 恢复动作不额外发起业务探测 |
| `check_image.py` / `check_landscape_mihomo.py` | 恢复镜像与外部方向的离线检查 |

CI 在 [`.github/workflows/validate.yml`](../../.github/workflows/validate.yml) 里跑这些命令。

## 目标机门禁（真机做，离线检查代替不了）

```sh
/usr/local/bin/mihomo -t -d /etc/mihomo
sudo /usr/local/sbin/mihomo-healthcheck --startup-check 30
cat /run/mihomo-healthcheck/status
sudo /usr/local/sbin/mihomo-healthcheck --egress
```

前三步证明配置、四要素与运行状态；`--egress` 证明经代理端口能取到 204（不花预算）。
`systemctl is-active` 不算通过——2026-10-25 的 gVisor 事故就是进程活着而 TUN 缺失。

## 仍需人工验收

真实手机接入后的直连/代理、YouTube/Instagram/X 播放、AI 流式响应、授权下载的完整性与
续传、重启恢复、回滚演练。离线回归只使用合成节点与 stub，不证明供应商授权、平台解锁、
吞吐或长连接稳定性。现场记录必须带日期，并且不得写入订阅、节点标签或凭据。
