# 验证矩阵与现场验收边界

[Wiki 首页](../README.md) · [部署](../deployment.zh-CN.md) · [运维](../operations.zh-CN.md)

## 本地唯一入口

```sh
scripts/preflight.sh              # 全量：所有门禁检查 + 发布/公开面步骤
scripts/preflight.sh --list       # 打印这一轮会跑什么，不执行
scripts/preflight.sh --staged     # pre-commit：按暂存改动映射必跑检查
scripts/preflight.sh --profile core  # 真核二进制专属检查（CI core-build）
```

`scripts/preflight.sh` 是薄壳，逻辑在 `scripts/preflight.py`；hook、开发机和 GitHub Actions 的两个 job 都调用
同一个脚本，不再各自维护检查清单。检查清单与「改动→必跑检查」映射的唯一来源是
[`tools/verification-matrix.yaml`](../../tools/verification-matrix.yaml)：

- 新增 `tools/check_*.py` 会自动进入全量门与 CI；`--staged` 通过组映射找到受影响检查，
  未映射的路径回落全量，不会出现「改了这个、却没跑那个」。
- `check_media_core.py` 需要真实核心二进制，单独放在 `--profile core`；CI 的 `core-build`
  构建 pinned 核心后调用同一个入口。没有二进制时它非 0 退出并说明缺依赖，不再静默 SKIP。
- 检查以 `exit 77` 声明 SKIP。preflight 会打印 `SKIP` 并把门判红：没跑的断言不是通过。
- `scripts/preflight-baseline.txt` 与 `scripts/preflight-advisory.txt` 默认必须为空。非空条目
  必须写成 `名字 YYYY-MM-DD 原因`；过期、缺日期或指向不存在的检查都会让门红。
- `check_release_integrity.py` 等检查在受限环境需要的临时目录从 `AUDIT_TMP_ROOT` / `TMPDIR`
  读取；路径过长时回落到仓库内短路径 `.preflight-tmp/`，不再写死 `/tmp`、`/var/tmp`。

门禁自身由 `tools/check_gate_integrity.py` 保护：它验证矩阵覆盖、CI 是否仍调用同一入口、
Gitea token 是否离开 URL，以及 SKIP / 过期白名单在合成小仓库里确实会变红。发布闸门现在是
GitHub Actions：[`.github/workflows/validate.yml`](../../.github/workflows/validate.yml) 的
`preflight` 与 `core-build` 两个 job 都走同一个入口。

## 检查的分类

全量门包含三类入口：

1. `tools/check_*.py`：逻辑层回归（脚本决策、状态机、安装事务、隐私/发布门、agent/HA/nftables
   工件）。大多数用真脚本 + 桩替代外部系统，能证明「决策正确」，不证明目标机上的 systemd、
   nftables、dnsmasq、真核或真实网络。
2. `automation/check-release.py`：发布包身份与受管文件清单。
3. `tools/validate_public.py`：公开树扫描（设备标识、凭据、订阅端点、节点标签、本地路径、
   历史基线之后的作用域）。

具体某一轮跑哪些检查，以 `scripts/preflight.sh --list` 的输出为准；不要手抄清单到文档里。

## 目标机门禁（真机做，离线检查代替不了）

```sh
/usr/local/bin/mihomo -t -d /etc/mihomo
sudo /usr/local/sbin/mihomo-healthcheck --startup-check 30
cat /run/mihomo-healthcheck/status
sudo /usr/local/sbin/mihomo-healthcheck --egress
sudo scripts/preflight.sh --profile core   # 需要 MIHOMO_TEST_BINARY 指向真核
sudo systemctl show nftables.service -p ExecStop
sudo tools/check_nftables_override.py --live
```

前三步证明配置、四要素与运行状态；`--egress` 证明经代理端口能取到 204（不花预算）；
`nftables` 的 loaded ExecStop 必须是两条 `destroy table`，不能出现 `flush ruleset`。
`systemctl is-active` 不算通过——2026-10-25 的 gVisor 事故就是进程活着而 TUN 缺失。

## 仍需人工验收

真实手机接入后的直连/代理、YouTube/Instagram/X 播放、AI 流式响应、授权下载的完整性与
续传、重启恢复、回滚演练。离线回归只使用合成节点与 stub，不证明供应商授权、平台解锁、
吞吐或长连接稳定性。现场记录必须带日期，并且不得写入订阅、节点标签或凭据。
