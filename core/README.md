# 固定核心的统一检测准入

上游为 MetaCubeX/mihomo v1.19.28，固定 commit 与协议见 [upstream.json](upstream.json)。
[补丁](mihomo-v1.19.28-budget.patch)共 1552 行、11 个文件，保留原生 fallback，在所有
`adapter.Proxy.URLTest` 发起前请求本地预算服务。原版核心没有这一接口，不能作为本机制的替代。
上游源码保留其 [GPLv3 LICENSE](https://github.com/MetaCubeX/mihomo/blob/v1.19.28/LICENSE)；本目录补丁基于该源码。

上游已发布 v1.19.32，但补丁只对应 v1.19.28（cbd11db）的 adapter/provider 结构且尚未移植：
1.19.32 没有 `-measurement-budget-protocol`，也没有准入钩子，换上去预算执行会静默失效，所以这里钉死 1.19.28。

## 真机事实（2026-10-25 只读采样）

| 项 | 值 |
|---|---|
| 二进制 | `/usr/local/bin/mihomo`，51 305 696 字节，2026-10-25 02:13:14 +0800 |
| SHA256 | `34e79a49e181446a5665012a6e7135b9a90c5ba20d819daa2f7de3267620ccf5` |
| 版本串 | `Mihomo Meta v1.19.28-budget-status-contract-v3 linux arm64 with go1.27.2 unknown time` |
| 构建 tags | `Use tags: no_tailscale, with_gvisor` |
| 协议入口 | `mihomo -measurement-budget-protocol` 输出 `mihomo-budget-v1`（rc=0） |
| 运行态 | `mihomo.service` active（经预算 drop-in 启动）；`mihomo-measurement-budget.service` active |

`/version` 返回 `version=v1.19.28-budget-status-contract-v3`、`measurement_budget=mihomo-budget-v1`、
`measurement_socket=/run/mihomo-measurements/budget.sock`。设备的 `go1.27.2` 只是它自己的工具链，
自建产物只要版本串与 tags 匹配、补丁一致即可，不要求 go 版本相同。

配置侧（部署数据，不入库）：`/etc/mihomo/config.yaml`（168 774 字节、23 个顶层键）含 7 个内联
`measurement-<16hex>` proxy-provider，payload 节点数依次为 24/17/31/88/17/17/52，被 18 个生成的
proxy-group 以 `use` 引用；它们由 `mihomo-measurement-policy` 的 `configure_measurement` 在策略叠加时
重建，并与 `/var/lib/mihomo-measurements/quality-budget.json` 的账户额度配套。账本账户属于部署数据，
本仓库只发布机制。该配置的**本地覆盖段**见 [`automation/etc/mihomo/config.local.yaml`](../automation/etc/mihomo/config.local.yaml)。

## 构建与产物校验

在独立目录获取上游源码，先核对 commit，再应用补丁。`GUIDE_ROOT` 指向本指南 checkout；源码与二进制输出
目录由操作者选择，避免覆盖运行核心。首次获取依赖需开发环境网络，测试只用匿名本地夹具。

```sh
git clone --depth 1 --branch v1.19.28 https://github.com/MetaCubeX/mihomo.git mihomo-budget-src
test "$(git -C mihomo-budget-src rev-parse HEAD)" = cbd11db1e13a75d8e680e0fe7742c95be4cba2be
git -C mihomo-budget-src apply "$GUIDE_ROOT/core/mihomo-v1.19.28-budget.patch"
cd mihomo-budget-src
git diff --check
go test -race ./adapter ./component/measurementbudget ./hub/route
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath \
  -tags "with_gvisor no_tailscale" \
  -ldflags "-X github.com/metacubex/mihomo/constant.Version=v1.19.28-budget-status-contract-v3" \
  -o ../mihomo-budget-linux-arm64 .
```

`with_gvisor` 不是可选项：单元文件的 TUN stack 需要 gVisor 图驱动，缺它的核心仍能通过 `mihomo -t`，
启动后却没有 TUN 设备，所有代理连接静默黑洞（2026-10-25 故障）。`no_tailscale` 只是裁体积。
装前必须验产物，版本串和协议是两个独立门禁：

```sh
artifact=./mihomo-budget-linux-arm64
version=$("$artifact" -v) || exit 1
case "$version" in
  "Mihomo Meta v1.19.28-budget-status-contract-v3 linux arm64 with go"*) ;;
  *) echo "unexpected version: $version" >&2; exit 1 ;;
esac
case "$version" in
  *"Use tags: no_tailscale, with_gvisor"*) ;;
  *) echo "missing build tags: $version" >&2; exit 1 ;;
esac
test "$("$artifact" -measurement-budget-protocol)" = mihomo-budget-v1 || {
  echo "measurement budget protocol missing" >&2; exit 1
}
sha256sum "$artifact"   # 发布产物应与 34e79a49…620ccf5 一致；重建不一致先查工具链与补丁
```

未注入 release 构建变量时，`-v` 会显示上游默认开发版本；验明来源要同时核对固定 commit、补丁和二进制
SHA256，不能只凭版本字符串。可在 macOS ARM64 跑匿名 loopback/Unix socket 回归并交叉构建 Linux ARM64；
Linux 服务运行与真机接入需单独验收。

## 预算外壳接线

真机用一份 drop-in 把核心的 `ExecStart` 换成包装器，单元本体（`automation/systemd/mihomo.service`）
保持不变。仓库里这份 drop-in 与真机 `/etc/systemd/system/mihomo.service.d/measurement-budget.conf`
逐字节相同（[`automation/systemd/mihomo.service.d/measurement-budget.conf`](../automation/systemd/mihomo.service.d/measurement-budget.conf)）：

```ini
# Explicit opt-in: stage only after building and verifying the pinned budget core.
# Install as /etc/systemd/system/mihomo.service.d/measurement-budget.conf.
[Unit]
# Runtime broker maintenance must not stop ordinary proxy traffic.
Wants=mihomo-measurement-budget.service
After=mihomo-measurement-budget.service

[Service]
EnvironmentFile=-/etc/mihomo-automation.env
ExecStart=
ExecStart=/usr/local/sbin/mihomo-measurement-budget --run-core /usr/local/bin/mihomo
```

`Wants` 而非 `Requires`：预算服务重启或维护不联动停掉普通代理流量；但预算服务不在时，所有原生检测
都会被拒绝（fail-closed），代理转发本身不受影响，只是失去新的测速准入。

包装器 `/usr/local/sbin/mihomo-measurement-budget`（python3 + PyYAML，加载同目录
`mihomo-measurement-policy`）有两种模式：

- **broker（无参数，`mihomo-measurement-budget.service` 运行）**：`serve()` 建
  `/run/mihomo-measurements`（0700）与 `budget.sock`（0600），用 `.owner.lock` 防双实例，线程上限 32，
  每请求 1 秒超时、单行 ≤4 KiB。账本 `/var/lib/mihomo-measurements/quality-budget.json` 不在启动时创建，
  首次预扣（`reserve_native`）才以原子替换写入，父目录 0700。
- **`--run-core /usr/local/bin/mihomo`（核心的 ExecStart）**：先跑 `<core> -measurement-budget-protocol`
  并要求在 5 秒内输出 `mihomo-budget-v1`；再读配置与 manifest，经 socket 发
  `{"protocol":"mihomo-budget-v1","op":"status"}`，把应答中的 `protocol`/`config_id`/`measurement_id`/
  `budget_path` 与本地重算结果逐项比对；全部通过才 `execve` 核心，参数
  `-d /etc/mihomo -f /etc/mihomo/config.yaml`，并把 `MIHOMO_MEASUREMENT_SOCKET` 显式写进子进程环境。
  任一步失败直接退出，不 exec。

核心随后经同一个 socket 做准入，协议是 `mihomo-budget-v1`；broker 的 `authorize` 校验
`protocol/name/addr/dialer/deadline_ms`，拒绝组名/中转链等非叶目标，锁内重验策略指纹后才预扣。

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

## 接入与回退边界

仓库提供源码、补丁和可选 drop-in；真机已按上面的接线安装并运行（`systemctl show -p ExecStart
mihomo.service` 显示包装器 argv）。复现顺序：建产物 → 过 `## 构建与产物校验` 的三道断言 → 备份原二进制/
单元/配置/账本 → 安装包装器与 broker 单元 → 安装 drop-in → `systemctl daemon-reload` 后重启
`mihomo.service` 并确认两个单元都 active、`/version` 带 `measurement_budget`。

预算服务失联只拒绝新探针，不阻断已有连接与转发。回退到原版前先生成暂停配置并保持测速关闭，再恢复原二进制
与单元、移除 drop-in；持久账本必须保留，删除账本不等于重置额度。
