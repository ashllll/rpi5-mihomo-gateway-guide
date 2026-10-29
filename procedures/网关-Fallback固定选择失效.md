# 网关-Fallback固定选择失效

> **触发**：Fallback 有健康备用却仍选坏首项；订阅换名后业务 TLS 失败；fixed 不在 all。
> **验证**：原生 DELETE 后 fixed 清空、选中健康候选，真实客户端访问恢复，核心 PID 与配置哈希不变。

## 首选

`python3 tools/check_reconcile_policy.py`

核对 controller 的组类型、fixed、all 和对应 URL 的健康历史。仅对已不属于该组的非空固定选择使用 `mihomo-reconcile-policy --repair-stale`。

## 被挡时

- 维护锁忙或 controller 不通：保留核心，由下轮巡检重试。
- 整组无健康候选：报告业务降级，检查出口；清选择不能创造可用节点。

## 别再来

| 弯路 | 为什么不通 |
|---|---|
| 只看全局代理 alive | 内联 provider 有独立健康对象；要看该组候选及测试 URL |
| 用 DELETE 204 证明持久化 | 核心缓存写错可能仅记日志；隔离核心须验证重载和重启 |
| 先装巡检入口再做单组试运行 | 下一次 timer 可能先清完全部分组，失去单组验证条件 |

## 注意

- 固定核心的缓存恢复可写入不存在的旧选择，fallback 随后可能直接返回首项。
- 当前修复为原生接口清理和每分钟补偿，存在发现窗口及 GET→DELETE 并发窗口。
- 有效固定选择、Selector 和候选池保持原样；恢复不应强制选回已坏节点。

证据：2026-10-29 分组修复任务的 `validation/repair-linux.json`、`canary-client.json`，以及仓库 CLI 回归。
