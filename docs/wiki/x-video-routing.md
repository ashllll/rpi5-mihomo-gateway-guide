# YouTube、Instagram 与 X 的路由

[Wiki 首页](../README.md) · [下载分流](download-routing.md) · [ADR-0007](../adr/0007-native-routing-budget.md)

## 分组

| 业务 | 运行组 | 方式 |
|---|---|---|
| YouTube / Instagram | `Streaming` | 原生 `fallback`，可启用独立媒体准入 |
| X / Twitter | `Social` | 原生 `fallback`，可启用独立媒体准入 |
| X 的 `x.ai` / `grok.com` | AI 规则 | 避免被较宽的社交规则抢先匹配 |

域名规则只使用核对过的后缀（`googlevideo.com`、`cdninstagram.com`、`twimg.com` 等），
`video_routes` 只能指向已有的 `Streaming` / `Social`，不新增应用专属选择层。

## 媒体准入

`media_admission` 默认关闭。启用时，`mihomo-media-guard` 为两个组分别维护 file provider；
只有通过当前采样契约且资格未过期的节点才在成员名单内。保留已准入顺序，核心在其中自动
fallback；不固定单节点。AI、Telegram、下载和国内直连不受此准入控制。

模板使用完整 1 MiB MP4、8 秒截止时间，包含连接建立的平均速度低于 256 KiB/s 连续两次
即隔离；新节点或恢复节点需要两次达到 384 KiB/s，隔离冷却 15 分钟，资格最多有效 6 小时。
每 10 分钟每组采样两个候选，优先当前承载节点，另一名额轮转；每天最多约 576 MiB 样本数据，
另有协议开销。参数都在私有 capabilities 配置中；样本必须换成实际可访问的 HTTPS MP4。
403/429、错误 MIME、短文件及预算拒绝不作为速度证据，也不续期。

每次采样先在共享账本预扣重定向上限，失败不退款。名单改变时原子写文件、刷新 provider，
读回实际成员与自动选择；名单不变不重复刷新。正常采样不重载核心。旧
`mihomo-social-quality.timer` 和 `mihomo-download-quality.timer` 继续休眠。

节点完整定义和采样契约指纹一致才可跨订阅继承资格；同名换密、改名、下架或改变门槛不能
沿用旧证据。订阅安装前只读检查每组至少一个资格仍覆盖事务期限；不满足则保留旧配置和
运行核心，需先单独预热新候选。启动前清理过期文件；空池使用 `REJECT`，仅相关媒体业务
不可用，不关闭核心、国内 DIRECT 或 DNS。

```sh
sudo /usr/local/sbin/mihomo-media-guard --check-ready
sudo journalctl -u mihomo-media-guard.service -n 20
sudo systemctl list-timers mihomo-media-guard.timer
```

资格状态在 `/var/lib/mihomo-media-admission/state.json`。首次启用应在隔离候选目录使用
`--prepare --force --only-node <候选名>` 完成所需轮数，再通过配置和真实媒体访问验收，
最后安装候选、启动 timer；不能直接把空候选池上线。停止 timer 不会立即撤销已有连接。

## 验证边界

原生 URL 探针只证明可达性。媒体样本验证指定服务和时间窗口的吞吐，不验证登录、地区解锁、
所有视频 CDN 或持续播放；Google 托管样本不能替代 YouTube 实际视频验收。
刷新 provider 只影响新连接，已有 TCP/QUIC 连接不会自动迁移。
真实验收要在可回滚窗口里用客户端分别打开 YouTube、Instagram 与 X，并确认 AI 与下载
仍进入各自池；离线检查不能替代这一步。
