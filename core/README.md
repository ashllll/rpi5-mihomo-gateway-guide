# 固定核心的统一检测准入

上游为 MetaCubeX/mihomo v1.19.28，固定 commit、协议见 [upstream.json](upstream.json)。
[补丁](mihomo-v1.19.28-budget.patch)保留原生 fallback，在所有 `adapter.Proxy.URLTest` 发起前请求本地预算服务。
原版核心没有这一接口，不能作为本机制的替代。上游源码保留其 [GPLv3 LICENSE](https://github.com/MetaCubeX/mihomo/blob/v1.19.28/LICENSE)；本目录补丁基于该源码。

## 离线运行合同

- `MIHOMO_MEASUREMENT_SOCKET` 默认 `/run/mihomo-measurements/budget.sock`，与预算服务使用相同 env。
- `--run-core --socket` 校验的路径会显式写入子进程环境，覆盖继承的 socket 值。
- 单行 JSON 请求：`protocol/name/addr/dialer/deadline_ms`；回复为 `{"protocol":"mihomo-budget-v1","allowed":true}` 加换行，最大 4 KiB。
- 请求 I/O 最多一秒，整个 URLTest 最多 30 秒，保留调用方更短的期限。准入阶段无 socket、拒绝、坏响应或过期均不拨号、不改 alive/history。
- 每次原生检测由服务按最多两个 HEAD 预扣；启动、周期、业务故障、控制器及回滚重试经过同一入口。费用写入原持久账本，失败或丢失回复不返还。
- 原生检测在拿到账本锁后重验当前政策绑定，等待期间发生变更则拒绝旧请求；已准入请求不追溯撤销。锁内只读本地策略和节点清单，不调用 RPC。
- 准入后实际 HEAD 仍受调用方总截止约束；`unified-delay` 的第二次 HEAD 超时或取消，不能沿用第一次响应计成功。预算拒绝不改健康，已准入的超期检测记录失败。
- provider 的检查状态码按其生命周期绑定到叶节点和 URL；整组或单节点控制器复测只能收紧条件，省略、通配或更宽范围均不能放宽已有合同。多个有效所有者取交集；刷新、关闭或重载清理各自的旧绑定。状态码不符返回失败，不写成功延迟。
- provider URL 在创建时统一去除首尾空白，避免同址额外任务覆盖原状态码合同。
- 生成器也在比较状态码、规划预算和生成共享 owner 前去除 URL 首尾空白，同址空白变体不能重复探测或隐藏状态码冲突。
- 明确启用的 provider 检查 URL 归一化后不能为空；生成阶段拒绝空值，避免核心静默停止自动检测。明确关闭 provider 检查或全局暂停仍可使用空 URL。
- 原生只接纳当前配置中的明确叶节点和匹配的 server/port。组名或中转链不猜测归属，直接拒绝。
- `-measurement-budget-protocol` 只输出协议并退出；`/version` 提供 `measurement_budget` 和 `measurement_socket`。受管启动核对协议与绑定。

## 可复现构建

在独立目录获取上游源码，先核对 SHA，再应用补丁。以下 `GUIDE_ROOT` 指向本指南 checkout；源码及二进制输出目录由操作者选择，避免覆盖运行核心。
依赖首次获取需要开发环境网络，运行测试只使用匿名本地夹具。

```sh
git clone --depth 1 --branch v1.19.28 https://github.com/MetaCubeX/mihomo.git mihomo-budget-src
test "$(git -C mihomo-budget-src rev-parse HEAD)" = cbd11db1e13a75d8e680e0fe7742c95be4cba2be
git -C mihomo-budget-src apply "$GUIDE_ROOT/core/mihomo-v1.19.28-budget.patch"
cd mihomo-budget-src
git diff --check
go test -race ./adapter ./component/measurementbudget ./hub/route
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath \
  -tags "with_gvisor no_tailscale" \
  -ldflags "-X github.com/metacubex/mihomo/constant.Version=v1.19.28-budget" \
  -o ../mihomo-budget-linux-arm64 .
```

`with_gvisor` is not optional: the unit file's TUN stack needs the gVisor graph
driver, and a core built without it still passes `mihomo -t` and then starts with
no TUN device, dropping every proxied connection silently (2026-10-25 incident).
`no_tailscale` only trims the binary. Check the result before installing:

```sh
./mihomo-budget-linux-arm64 -v    # must print both with_gvisor and no_tailscale
./mihomo-budget-linux-arm64 -measurement-budget-protocol   # must print mihomo-budget-v1
```

未注入 release 构建变量时，`-v` 会显示上游默认开发版本；验明来源应核对固定 commit、补丁与二进制 SHA256，不能只凭版本字符串。

可在 macOS ARM64 运行匿名 loopback/Unix socket 回归，并交叉构建 Linux ARM64；Linux 服务运行、生产接入和实际播放仍需单独验收。构建产物保留在私有验证目录，未放入此仓库。

## 接入与回退边界

`automation/install.sh` 安装预算服务和脚本，但不启用它；可选 core drop-in 不自动安装。
后续经授权接入时，必须先备份原二进制/单元/配置/账本，核验固定源码和二进制，再审阅
`automation/optional/mihomo-native-budget.conf`。该片段先启动预算服务，调用同一个 helper 的 `--run-core`，
核对将执行的二进制及服务的配置/账户/账本，再 exec 核心。单纯把 manifest 改为 true 不完成接入。
运行中服务失联只拒绝新探针；使用 Wants 而非 Requires，维护预算服务不联动停止普通业务核心。

回退到原版前需先生成暂停配置，保持测速关闭，再恢复二进制和相应单元；保留持久账本，不能靠删除账本清额度。
本轮仅交付源码与离线证据，不安装补丁核心，不启用服务或 timer。
