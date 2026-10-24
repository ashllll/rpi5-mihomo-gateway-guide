# YouTube、Instagram 与 X 稳定路由

> [Wiki 首页](../README.md) · 本页说明规则和原生组策略，不是平台播放、登录或机场性能验收记录。

## 现有业务池

YouTube 和 Instagram 域名规则进入现有 `Streaming` 池，X/Twitter 域名规则进入现有 `Social` 池。两组使用 Mihomo 原生 `fallback`，按照部署清单中的既有节点顺序优先，并在当前出口不可用时切换到下一授权节点。AI Research、下载、Telegram 和节点池权限不随视频平台规则改变。

当前规则表只加入经核对的域名后缀：

- YouTube：`youtube.com`、`youtu.be`、`googlevideo.com`、`ytimg.com`、`youtube-nocookie.com`，以及 `youtubei.googleapis.com`、`youtube.googleapis.com`、`youtubeembeddedplayer.googleapis.com`。
- Instagram：`cdninstagram.com`、`instagr.am`、`instagram.com`。没有验证的域名不加入表中。
- X/Twitter：保留现有 `x.com`、`twitter.com`、`twimg.com`、`t.co`、`twittercdn.com` 后缀。

X 的 `x.ai`、`grok.com` 仍由 AI 规则处理，避免较宽的社交规则抢先匹配。`video_routes` 映射是数据驱动的，目标只允许使用已有 `Streaming` 和 `Social` 组；Instagram 复用 Streaming，不增加应用专属选择层或新测速器。

## 原生健康检查边界

Streaming 使用 YouTube 的小型可达性响应，Social 使用 X API 的通用响应范围。它们用来维持稳定的 fallback 顺序，不请求真实视频片段、不做吞吐评分，也不验证账号登录或内容解锁。探针成功不表示 YouTube、Instagram 或 X App 的播放链路一定可用。

`measurement.enabled` 默认关闭；显式开启时，所有原生 URLTest 通过同一本地预算账本。失败、限流、额度不足和预算服务失联不能被当成已验证的业务故障。账本路径、保留的历史账户额度键和准入流程见[原生延迟选路与检测预算](../adaptive-selection.zh-CN.md)。

## 验证

`tools/check_overlay.py` 使用合成节点检查三类平台规则进入正确的既有组、X/Grok 仍归 AI、分流数据没有改变 AI 或下载池权限。`tools/check_native_budget.py` 检查原生检测准入和持久账本；`tools/check_stability.py` 检查进程恢复不额外发起业务探测。

这些离线检查不代表真实节点可用、平台登录成功或客户端播放流畅。真实验收需要在可回滚的维护窗口中用实际客户端分别验证 YouTube、Instagram 与 X 内容播放；同时确认 AI Research 及获准下载仍进入各自既有池。本轮没有执行实机、节点或公网测试。
