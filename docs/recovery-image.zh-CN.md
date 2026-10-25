# 无代理节点的树莓派恢复镜像

这是可写入 SD 卡或 USB NVMe 的 Raspberry Pi 5 ARM64 系统恢复底包：保留操作系统、网关程序和公开自动化源码，
个人账号、网络、节点和订阅在恢复时重新配置。下载入口为
[recovery-2026.10.12](https://github.com/ashllll/rpi5-mihomo-gateway-guide/releases/tag/recovery-2026.10.12)。

它采用与原部署同批次的官方 Raspberry Pi OS Lite / Debian 13 底包重新制作文件系统，不读取原系统的个人配置、
日志、容器数据或磁盘空闲块。删除过的旧密钥和订阅不会因整盘克隆而进入公开镜像。

| 内容 | 镜像行为 |
|---|---|
| 操作系统 | 官方 2026-06-18 Raspberry Pi OS Lite ARM64；输入 SHA-256 固定在 `image/sources.json` |
| Mihomo / MosDNS | 已验收版本的独立程序，输入文件必须匹配固定 SHA-256；版本和来源记入 manifest |
| 网关源码 | 本仓库公开源码放在 `/opt/rpi5-mihomo-gateway-guide`，自动化层已安装 |
| 节点与订阅 | 零节点、零订阅；Mihomo 配置为本机 direct 模式，无远程控制端口 |
| 账号与凭据 | 不分发可登录个人账号、密码哈希、SSH 主机密钥、授权公钥或 controller secret |
| 网络身份 | 不带真实 MAC 绑定、SSID、Wi-Fi 密码、连接文件、原网段或静态 IP |
| 初次联网 | 上游有线 DHCP；站点配置前不提供下游 DHCP、NAT 或透明代理 |
| 自动化 | Mihomo / MosDNS / dnsmasq / nftables 与八个自动化 timer 默认禁用 |
| 原部署私有数据 | 不包含容器、家目录、业务数据、节点评分、缓存、历史配置和原备份 |

默认镜像约 4 GiB，实际压缩大小与完整校验值以 Release 和 `image-manifest.json` 为准。使用容量至少 8 GB 的备用介质。
首次启动由 Raspberry Pi OS 的 cloud-init / growpart 扩展根分区到目标介质容量，并重新生成机器与 SSH 身份。
本次为了维持现网服务未重刷原树莓派，`physical_boot_tested` 保持 `false`；Release 标为预发布。

## 下载和核验

在电脑上从 Release 下载下面四个文件到同一目录，或使用 GitHub CLI：

```sh
gh release download recovery-2026.10.12 --repo ashllll/rpi5-mihomo-gateway-guide \
  --pattern rpi5-gateway-node-free.img.xz --pattern image-manifest.json \
  --pattern SHA256SUMS --pattern gateway_image.py --dir gateway-recovery
cd gateway-recovery

# macOS；Linux 可使用 sha256sum -c SHA256SUMS。
shasum -a 256 -c SHA256SUMS
```

校验值来自你信任的仓库 Release。更换来源下载时应对照原 Release 校验，不能把任意第三方提供的 manifest 当作可信证明。
二进制镜像发布到 Release 附件，Git 源码仓库不保存镜像；单个附件必须小于 2 GiB。
这是 [GitHub 官方 Release 限制](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

## 一条命令还原

恢复工具需要 Python 3.9 或更高版本。准备好 SSH 公钥，先查看磁盘列表并确认备用介质。工具不会替你自动选择磁盘：

```sh
# macOS
diskutil list external physical

# Linux
lsblk -o NAME,SIZE,MODEL,TYPE,MOUNTPOINTS
```

macOS 使用实际整盘路径 `/dev/diskN`，不能使用分区路径或内置磁盘；Linux 使用实际整盘路径，并先卸载全部分区、
关闭该介质上的 swap。镜像、工具与 manifest 必须保存在另一块磁盘，不能放在待清空介质上。

```sh
# 先演练；将 /dev/diskN 替换为核实过的目标盘。
python3 gateway_image.py restore \
  --image rpi5-gateway-node-free.img.xz --manifest image-manifest.json \
  --device /dev/diskN --user myadmin --hostname rpi-gateway \
  --ssh-key "$HOME/.ssh/id_ed25519.pub" --dry-run

# 真正还原：会要求输入 ERASE 加完整设备路径；整盘原有数据会被清空。
sudo python3 gateway_image.py restore \
  --image rpi5-gateway-node-free.img.xz --manifest image-manifest.json \
  --device /dev/diskN --user myadmin --hostname rpi-gateway \
  --ssh-key "$HOME/.ssh/id_ed25519.pub"
```

顺序为：压缩文件 SHA-256 → 解压后大小 / 分区表 / SHA-256 → 目标盘身份与容量 →
在私有镜像副本中写入首次启动资料 → 人工确认 → 再次核对目标盘 → 写盘 → 对个性化镜像全量回读 → 卸载。
下载目录需再预留一份解压镜像的空间。工具通过 macOS 原生镜像挂载或 Linux loop 设备处理副本，不依赖写盘后的分区重新识别。
镜像损坏、目标盘不合规、写盘或回读失败时返回错误，不能把该介质当作成功还原的系统启动。
本地首次启动资料只包含你选择的用户名、主机名与公钥；SSH 密码登录关闭，sudo 通过管理员公钥登录后使用。
它们写入你的备用介质，不上传 GitHub。公钥不会赋予其他下载者对你机器的访问权。

也可使用 [Raspberry Pi Imager](https://www.raspberrypi.com/software/) 的自定义镜像功能写入 `.img.xz`，
并在系统自定义选项中配置新用户名、SSH 公钥与主机名。未填写首次启动账号的公开镜像没有通用默认密码。

## 首次启动后

1. 将恢复介质装入 Pi 5，连接上游网线。从上游路由器的 DHCP 列表查找新地址，使用新账号和 SSH 公钥登录。
2. 确认 `findmnt /` 指向恢复介质；检查 `df -h /`、`cloud-init status --wait` 与 `systemctl status regenerate_ssh_host_keys`。
3. 按[部署文档](deployment.zh-CN.md)准备本机参数（`automation/env/automation.env.example` 列出的地址、接口与网段），写入部署端私有文件。
4. 按[部署文档](deployment.zh-CN.md)安装自动化层与网络配置，确认独立管理连接可用后再启用网关服务。
5. 按[自动化说明](../automation/README.zh-CN.md)添加自己的订阅、规则数据与能力清单，验收通过后按需启用 timer。

真实 MAC 绑定与网络连接均需重新建立；不能假设新介质上的接口仍叫原来的 `wan0` / `lan0`。
一键工具让 NetworkManager 自行为有线网卡建立 DHCP 连接；cloud-init 负责账号和扩容，避免把网卡通配符渲染为错误接口名。
保持上游 SSH 或本地控制台，先确认管理链路再改网络。公开镜像不能恢复已经脱敏删除的原账号、节点或私有业务数据。

## 重新制作镜像

构建器使用 Python 3.9+ 标准库，运行端需要 ARM64 Linux、root、git、curl、rsync、xz、util-linux、dosfstools 和 e2fsprogs。
依赖包只安装在隔离的镜像根目录，`policy-rc.d` 禁止在构建阶段启动服务；不挂入宿主的磁盘设备或 systemd 总线。
构建期间额外预留约 12 GB 空间。输入文件哈希不匹配、已有输出目录或不足容量都会停止构建。
上游源码下载可以通过构建进程的 `HTTPS_PROXY` 环境变量使用现有代理；该变量不会写入镜像配置。

```sh
# 从 sources.json 的官方地址下载底包；核对其 sha256。
# 两个程序路径必须是经过来源核实、匹配 sources.json 哈希的 ARM64 二进制。
sudo nice -n 19 ionice -c 3 python3 image/gateway_image.py build \
  --base /path/to/official.img.xz \
  --mihomo /path/to/mihomo --mosdns /path/to/mosdns \
  --output /path/to/new-image-output --size-mib 4096
```

只有经过筛选的 Git 发布文件会进入镜像，构建前先运行仓库隐私门禁，并核对未跟踪文件。
构建器保留官方启动文件、安装公开脚本、清空初始身份和状态，再将文件复制到全新 FAT / ext4 文件系统，
不会把构建阶段删除的密钥或缓存块带入最终镜像。压缩阶段只使用一个 worker。

产物为 `.img.xz`、`image-manifest.json`、独立恢复脚本和 `SHA256SUMS`。manifest 包含输入来源、软件包版本、
镜像大小与双重 SHA-256，以及物理启动验收标记。发布前需重新挂载最终镜像检查配置、身份、文件系统和已知凭据无回显匹配。

```sh
python3 tools/check_image.py
python3 tools/validate_public.py
```

系统与第三方程序遵循各自许可证，不因本仓库使用 CC0 而改变；许可证和对应上游源码归档在
`/usr/share/doc/gateway-image/`，其余系统组件版权说明位于 `/usr/share/doc/*/copyright`。
