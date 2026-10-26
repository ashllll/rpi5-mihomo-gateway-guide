# dnsmasq 配置（从真机抽取，已脱敏）

真机 `/etc/` 下与 dnsmasq 有关的三处配置的镜像。`install.sh` 按下表落位：

| 本目录文件 | 真机目标路径 | 说明 |
| --- | --- | --- |
| `resolver.conf` | `/etc/dnsmasq.d/resolver.conf` | 环回 resolver（`interface=lo`、`listen-address=127.0.0.1`）：只给网关自身工具与健康检查用，`servers-file` 指向动态上游文件 |
| `vlan-pilot.conf` | `/etc/dnsmasq-vlan-pilot.conf` | 独立进程（`dnsmasq-vlan-pilot.service`）上的 VLAN20 试点作用域：服务 UCG 广播的 `RP5-PC-TEST` Wi-Fi 客户端，DNS 直投 Mihomo `:1053` |
| `upstreams.conf` | `/etc/dnsmasq-upstreams.conf` | 上游解析器，真机当时生效的那份（mosdns `:5335`）；`install.sh` 只在文件不存在时种子一次 |

仓库历史上还有一份下游 LAN 作用域（`lan.conf`），已随独立 LAN 口与 Pi 自建 AP 一起退役：dnsmasq 不再承担 LAN DHCP，只剩上面两个作用域。

## 上游文件的分工

`resolver.conf` 的 `servers-file=/etc/dnsmasq-upstreams.conf` 是热切换点，`mihomo-dns-mode` 在 direct / mihomo / mosdns 三种模式间原子替换它，`dnsmasq-safe-start` 在每次开机时先装回 direct 基线。三份模式基线不在本目录：

- `automation/etc/dnsmasq-upstreams.{direct,mihomo,mosdns}` → `/etc/mihomo/dnsmasq-upstreams.*`；
- 本目录的 `upstreams.conf` 只是"真机当前生效内容"的镜像，供首次安装种子用，不参与模式切换（切换目标始终是 `/etc/mihomo/` 下的三份基线）。

VLAN20 作用域不参与这套模式切换：`vlan-pilot.conf` 固定 `server=127.0.0.1#1053`，直接交给 Mihomo fake-IP。

## 占位值对照表

真机的具体地址、主机名、MAC 从不进入本仓库，隐私检查器会在 `automation/**` 里直接拦下它们。下面的表说明每个占位值代表什么、读者该替换成什么；记录真机原值的替换关系只留在私有部署记录里。

| 占位值 / 位置 | 代表什么 | 替换依据 |
| --- | --- | --- |
| `198.51.100.1`（`vlan-pilot.conf` 的 `listen-address`、`dhcp-option`） | VLAN20 试点段的网关地址（也是 DHCP 下发的 router / DNS） | RFC 5737 TEST-NET-2 |
| `198.51.100.100-198.51.100.199` | VLAN 试点 DHCP 池，保留 /24 掩码与 12h 租期 | 同上 |
| `198.51.100.10` | 试点终端固定租约 | 同上 |
| `00-00-5e-00-53-01` | 试点终端的 MAC 固定租约（dash 记法，dnsmasq 两种记法都收） | RFC 7042 文档 MAC |
| `198.51.100.117`（`resolver.conf` 的 `host-record`）、`192.0.2.117`（`vlan-pilot.conf` 的 `host-record`） | 网关自身管理名地址，供 `gateway` / `gateway.lan` 解析 | RFC 5737 |
| `gateway` / `gateway.lan` / `gateway.localdomain` | 网关主机名三连（`host-record` 优先于 `/etc/hosts` 127.0.1.1） | 通用占位 |

其余内容（`bind-dynamic`、租期、`cache-size`、`no-resolv`、`domain-needed`、`bogus-priv`、`log-dhcp`、`servers-file` 路径、`server=127.0.0.1#1053`、接口名 `lo`/`proxy20`）与真机逐字一致。

`resolver.conf` 与 `vlan-pilot.conf` 的地址没有做成环境占位：dnsmasq 不展开环境变量，`listen-address`、`dhcp-option` 与 `host-record` 必须是字面地址。改网段时直接改这两份文件，不再有第二个环境变量落点。
