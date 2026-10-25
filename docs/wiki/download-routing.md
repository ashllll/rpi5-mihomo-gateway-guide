# 下载分流与原生延迟选择

[Wiki 首页](../README.md) · [ADR-0007](../adr/0007-native-routing-budget.md) · [运维](operations.md)

## 现状

Download、GitHub Download、Model Download、Package Download 都是**扁平的原生 `url-test`**，
候选只来自部署清单明确授权的叶节点；域名规则只决定进哪个组，不扩大节点权限。
Streaming/Social/AI Research 用原生 `fallback` 保留既有顺序，TW Fixed IP 是固定 `select`。

原生检测负责“组内此刻用哪个获准节点”，不模拟 Release 下载、Git pack、模型文件或包安装，
也不测持续吞吐。它影响新连接，不迁移已建立连接。

## 采样与预算

- 影子评分的自适应脚本已于 2026-10-24 退役并移出仓库。
- `download-quality` / `social-quality` 采样仍在仓库与真机单元里，但 timer 处于 disabled；
  它们只通过预算 broker 支取尝试额度，state 分别在
  `/var/lib/mihomo-download-quality/scores.json`、`/var/lib/mihomo-social-quality/scores.json`。
- 原生检测每次预扣 2 次尝试，账本 `/var/lib/mihomo-measurements/quality-budget.json`；
  拒绝不会记为节点失败，业务路由照旧。真机已开启检测；模板保留关闭开关。

## 验证与限制

同域名 HTTPS 无法可靠区分聊天与大文件；Git SSH、私有 registry、Git LFS 与跳转域名要单独核验。
离线检查（`check_overlay.py`、`check_measurement_policy.py`、`check_native_budget.py`）只使用
合成节点；真实下载完整性、授权与出口仍需客户端验收。
