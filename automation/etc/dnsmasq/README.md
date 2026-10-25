# dnsmasq 配置（从真机抽取，已脱敏）

真机 `/etc/` 下与 dnsmasq 有关的三处配置的镜像。`install.sh` 按下表落位：

| 本目录文件 | 真机目标路径 | 说明 |
| --- | --- | --- |
| `lan.conf` | `/etc/dnsmasq.d/lan.conf` | 下游 LAN（`lan0`）唯一作用域：DHCP + 本机 DNS，`servers-file` 指向动态上游文件 |
| `vlan-pilot.conf` | `/etc/dnsmasq-vlan-pilot.conf` | 独立进程（`dnsmasq-vlan-pilot.service`）上的 PC 专用 VLAN 试点作用域 |
| `upstreams.conf` | `/etc/dnsmasq-upstreams.conf` | 上游解析器，真机当时生效的那份（mosdns `:5335`）；`install.sh` 只在文件不存在时种子一次 |

## 上游文件的分工

`lan.conf` 的 `servers-file=/etc/dnsmasq-upstreams.conf` 是热切换点，`mihomo-dns-mode` 在 direct / mihomo / mosdns 三种模式间原子替换它，`dnsmasq-safe-start` 在每次开机时先装回 direct 基线。三份模式基线不在本目录：

- `automation/etc/dnsmasq-upstreams.{direct,mihomo,mosdns}` → `/etc/mihomo/dnsmasq-upstreams.*`；
- 本目录的 `upstreams.conf` 只是"真机当前生效内容"的镜像，供首次安装种子用，不参与模式切换（切换目标始终是 `/etc/mihomo/` 下的三份基线）。

## 占位值对照表

真机的具体地址、主机名、MAC 从不进入本仓库，隐私检查器会在 `automation/**` 里直接拦下它们。下面的表说明每个占位值代表什么、读者该替换成什么；记录真机原值的替换关系只留在私有部署记录里。

| 占位值 / 位置 | 代表什么 | 替换依据 |
| --- | --- | --- |
| `192.0.2.1`（`lan.conf` 的 `listen-address`、`dhcp-option`） | 下游 LAN 的网关地址（也是 DHCP 下发的 router / DNS） | RFC 5737 TEST-NET-1 |
| `192.0.2.100-192.0.2.199` | LAN DHCP 池，保留 /24 掩码与 12h 租期 | 同上 |
| `192.0.2.117`（`host-record`） | 网关自身管理网地址，供 `gateway.lan` 解析 | 同上 |
| `gateway` / `gateway.lan` / `gateway.localdomain` | 网关主机名三连（`host-record` 优先于 `/etc/hosts 127.0.1.1`） | 通用占位 |
| `198.51.100.1`（`vlan-pilot.conf`） | VLAN 试点段的网关地址 | RFC 5737 TEST-NET-2 |
| `198.51.100.100-198.51.100.199` | VLAN 试点 DHCP 池，10m 租期 | 同上 |
| `198.51.100.10` | 试点终端固定租约 | 同上 |
| `00-00-5e-00-53-01` | 试点终端的 MAC 固定租约（dash 记法，dnsmasq 两种记法都收） | RFC 7042 文档 MAC |

其余内容（`bind-dynamic`、租期、`cache-size`、`no-resolv`、`domain-needed`、`bogus-priv`、`log-dhcp`、`servers-file` 路径、`server=127.0.0.1#1053`、接口名 `lan0`/`proxy20`）与真机逐字一致。

`lan.conf` 里的地址没有做成环境占位：dnsmasq 不展开环境变量，`listen-address` 与 `dhcp-option` 必须是字面地址。改网段时它和 `/etc/mihomo-automation.env` 里的 `GATEWAY_LAN_IP` 要一起改，两者是同一份事实的两个落点。
