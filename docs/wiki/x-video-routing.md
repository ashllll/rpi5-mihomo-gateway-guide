# YouTube、Instagram 与 X 的路由

[Wiki 首页](../README.md) · [下载分流](download-routing.md) · [ADR-0007](../adr/0007-native-routing-budget.md)

## 分组

| 业务 | 运行组 | 方式 |
|---|---|---|
| YouTube / Instagram | `Streaming` | 原生 `fallback`，按部署清单既有顺序 |
| X / Twitter | `Social` | 原生 `fallback`；`x-video` 是社媒采样器的唯一 profile |
| X 的 `x.ai` / `grok.com` | AI 规则 | 避免被较宽的社交规则抢先匹配 |

域名规则只使用核对过的后缀（`googlevideo.com`、`cdninstagram.com`、`twimg.com` 等），
`video_routes` 只能指向已有的 `Streaming` / `Social`，不新增应用专属选择层。

## 采样（当前休眠）

`mihomo-social-quality` 是 X-only adapter：要求候选组为 `Social` 且 profile 只有 `x-video`，
复用下载采样的有界请求与预算准入，state 在 `/var/lib/mihomo-social-quality/scores.json`。
对应 timer 处于 disabled；影子评分体系已于 2026-10-24 退役，采样只作为可选补充。

## 验证边界

探针只维持 fallback 顺序，不请求真实视频片段，也不验证登录、播放或地区限制。
真实验收要在可回滚窗口里用客户端分别打开 YouTube、Instagram 与 X，并确认 AI 与下载
仍进入各自池；离线检查不能替代这一步。
