# 第 3 章 官方 API 操作方式

UCG-Max 上的自动化入口由 Ubiquiti 官方在 developer.ui.com 提供，文档公开可读、无需注册；但要真正发出请求，必须先在自己的 UI 账户下生成 API Key。官方索引（`llms.txt`）共列出 6 个 API：Network v11.0.97、Protect v7.3.74、Mobility v1.0.0、InnerSpace v1.3.23、Site Manager v1.0.0、Carrier Fabric v1.0.0；本章只覆盖与 UCG-Max 网关运维直接相关的两套：**Site Manager API**（云侧、账号级）与 **Network API**（本地、站点级）。与 NVR/录像相关的自动化还需另查 Protect API v7.3.74。两者基址、分页方式、响应包裹结构和错误体都不相同，混用是接入时最常见的错误来源。本章按「认证 → 入口与基址 → 端点 → 请求响应要点 → 可复制示例 → 已知限制 → 版本」的顺序展开，所有接口名、参数、字段与限流数字均取自官方规范或官方文档页面。

## 3.1 认证：API Key 与请求头

官方 `llms.txt` 对全部 API 的认证方式只有一句话：所有 API 都通过 `X-API-KEY` 请求头做 API Key 认证，密钥在 unifi.ui.com 生成。而 Site Manager 的 OpenAPI 规范把安全方案声明为 `type: apiKey`、`in: header`、`name: X-API-Key`。也就是说官方自己就在两种大小写之间摇摆：文档正文与 curl 示例统一写 `X-API-KEY`，规范里的 securityScheme 写 `X-API-Key`。HTTP 头字段名本身大小写不敏感，两种写法等价，客户端按团队规范任选一种即可，但不要在同一套代码里混写到让人误以为是两个头。

创建路径在 Site Manager Getting Started 页面写得很明确：登录 unifi.ui.com，进入 Settings → API Keys，选择 “Create New API Key”，复制生成的密钥并妥善保存，因为**密钥只会显示一次**。官方同时给出直达链接 <https://unifi.ui.com/settings/api-keys>，并提示要对密钥做妥善的哈希与安全存储——换成工程语言就是：生成后立刻写入环境变量或密钥管理器，不要留在 shell 历史、截图或仓库里，因为平台不再提供第二次查看。

密钥的归属范围两页措辞略有差异：Network 页写每个 key 绑定到生成它的「组织或 UI 账户」，Site Manager 页写绑定到生成它的「UI 账户」。实际含义一致——key 是账号/组织级凭证，不是某个站点或某台 UCG-Max 的凭证，因此在同一组织下换控制台不需要换 key，但把 key 交给第三方就等于交出该账号可见范围内的全部数据。

认证失败的形态是固定的：Network API 的错误示例就是 `code: api.authentication.missing-credentials`、`message: Missing credentials`、`statusName: UNAUTHORIZED`。看到这一组值说明问题在凭证（没带头、头名拼错、key 被吊销），而不是路径写错；反之若路径或站点 ID 错，`statusName` 会落在 404/`NOT_FOUND` 一类。官方文档未提供密钥有效期与自动轮换机制的说明，因此「多久换一次 key」只能由使用方自行制定策略。

一个容易被忽略的事实：Network API 的 OpenAPI 规范（v11.0.97）里**完全没有声明 `securitySchemes`**，规范只描述路径与数据结构，认证要求只出现在 `llms.txt` 与文档页中。因此不要指望从规范文件里自动生成认证代码，认证头需要手工加上。官方文档里也没有出现 OAuth、Bearer Token 或 Basic Auth 等其它认证方式的说明，API Key 是当前唯一被记录的认证手段。

## 3.2 两套入口与基址

| 维度 | Site Manager API | Network API |
|---|---|---|
| 基址 | `https://api.ui.com/v1/...` | 本地：`https://{consoleIP}/proxy/network/integration`；云端：`https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration` |
| 作用域 | 账号级，跨控制台汇总 | 单个 Network 应用（单台 UCG-Max）及其站点 |
| 版本 | v1.0.0（另有 `/ea/...` 早期访问前缀） | v11.0.97 |
| 认证 | `X-API-Key` 请求头 | 同一把 key，同一请求头 |
| 分页 | `pageSize` + `nextToken` | `offset` + `limit`（列表端点另有 `filter`） |
| 响应包裹 | `data` / `httpStatusCode` / `traceId` / `nextToken` | `count` / `data` / `limit` / `offset` / `totalCount` |
| 典型用途 | 列出组织下所有 host、device、site；查 ISP 指标、SD-WAN 配置 | 读写 VLAN/网络、SSID、防火墙策略与区域、ACL、DNS 策略、hotspot 凭证、端口动作、设备统计 |

本地基址中的 `{consoleIP}` 在规范里给出的变量默认值是 `192.168.0.1`，即 UCG-Max 在局域网的地址；`/proxy/network/integration` 这一段是所有 Network API 路径的固定前缀，规范里 44 条路径全部写作 `/v1/...`，实际请求时要拼在 `integration` 之后，最终形如 `https://192.168.0.1/proxy/network/integration/v1/sites`。云端基址多一层 `v1/connector/consoles/{consoleId}/proxy/network/integration`，其中 `consoleId` 的官方描述仅为「要代理请求到的控制台 ID」，并给出形如 `942A6FF664C0000000000964970E0000000009E710560000000068A9873F:9999999999` 的示例。**官方文档未说明该 ID 的具体获取步骤**；它的示例形态与 Site Manager `GET /v1/hosts` 返回的 `id` 字段示例（同样是 `<序列号>:<数字>`）一致，但官方并未声明两者可以直接互换，接入时应以控制台或 Site Manager 实际返回值为准。

两者的适用场景可以这样切分：需要「一次看全组织」时走 Site Manager——例如盘点所有控制台与设备、读取站点清单、拉取 ISP 质量指标；需要「改这台网关的配置」时走 Network——UCG-Max 的 VLAN、WiFi 广播、防火墙策略、ACL 规则、DNS 策略、hotspot 凭证、端口与设备动作都在这一侧，Site Manager 没有对应写接口。落到具体任务上：查询组织内所有控制台及其设备清单用 `GET /v1/hosts`（Site Manager）；给访客网络新开一个 VLAN 与 SSID 用 `POST /v1/sites/{siteId}/networks` 加 `POST /v1/sites/{siteId}/wifi/broadcasts`（Network）；排查某台 AP 的端口协商速率则用 `GET /v1/sites/{siteId}/devices/{deviceId}/statistics/latest` 与端口动作端点（Network）。

把两者串起来的一次典型流程是：先在 Site Manager 用 `GET /v1/hosts` 找到目标控制台及其 `id`（控制台标识，形如 `70A7...:1261206302`），再用 `GET /v1/devices` 确认该控制台下有哪些设备与站点；随后切换到 Network API，用 `GET /v1/sites` 重新取 UUID 形式的站点 ID，再执行配置读写。步骤看起来绕，但这是两套 API 各自只认自己 ID 体系导致的必然结果。

还有一个必须注意的差异：**两套 API 的站点标识不是同一个东西**。Site Manager 的 `siteId` 示例是 24 位十六进制串（如 `661de833b6b2463f0c20b319`），Network 的站点 `id` 在规范中明确为 UUID 格式；用 Site Manager 拿到的 ID 去调 Network 的 `/v1/sites/{siteId}/...` 会失败，反之亦然。这属于「换 API 时忘记重新取 ID」的典型事故，排查时优先怀疑。

## 3.3 关键端点

Network API v11.0.97 共 **44 条路径、73 个操作**，其中 Firewall 13 个、UniFi Devices 8 个、Supporting Resources 8 个、ACL 7 个、Networks 6 个、Switching 6 个，其余为 DNS Policies 5 个、Hotspot 5 个、Traffic Matching Lists 5 个、WiFi Broadcasts 5 个、Clients 3 个、Application Info 与 Sites 各 1 个。下表按用途分组挑出 23 条最常用的操作：

| 分组 | 方法与路径 | 官方摘要 | 官方文档 |
|---|---|---|---|
| 应用信息 | `GET /v1/info` | Get Application Info | [getinfo](https://developer.ui.com/network/v11.0.97/getinfo) |
| 站点 | `GET /v1/sites` | List Local Sites | [getsiteoverviewpage](https://developer.ui.com/network/v11.0.97/getsiteoverviewpage) |
| 设备 | `GET /v1/pending-devices` | List Devices Pending Adoption | [getpendingdevicepage](https://developer.ui.com/network/v11.0.97/getpendingdevicepage) |
| 设备 | `GET /v1/sites/{siteId}/devices` | List Adopted Devices | [getadopteddeviceoverviewpage](https://developer.ui.com/network/v11.0.97/getadopteddeviceoverviewpage) |
| 设备 | `GET /v1/sites/{siteId}/devices/{deviceId}/statistics/latest` | Get Latest Adopted Device Statistics | [getadopteddevicelateststatistics](https://developer.ui.com/network/v11.0.97/getadopteddevicelateststatistics) |
| AP 与交换机 | `POST /v1/sites/{siteId}/devices/{deviceId}/interfaces/ports/{portIdx}/actions` | Execute Port Action | [executeportaction](https://developer.ui.com/network/v11.0.97/executeportaction) |
| 客户端 | `GET /v1/sites/{siteId}/clients` | List Connected Clients | [getconnectedclientoverviewpage](https://developer.ui.com/network/v11.0.97/getconnectedclientoverviewpage) |
| 客户端 | `POST /v1/sites/{siteId}/clients/{clientId}/actions` | Execute Client Action | [executeconnectedclientaction](https://developer.ui.com/network/v11.0.97/executeconnectedclientaction) |
| 网络 | `GET /v1/sites/{siteId}/networks` | List Networks | [getnetworksoverviewpage](https://developer.ui.com/network/v11.0.97/getnetworksoverviewpage) |
| 网络 | `POST /v1/sites/{siteId}/networks` | Create Network | [createnetwork](https://developer.ui.com/network/v11.0.97/createnetwork) |
| WiFi 广播 | `GET /v1/sites/{siteId}/wifi/broadcasts` | List Wifi Broadcasts | [getwifibroadcastpage](https://developer.ui.com/network/v11.0.97/getwifibroadcastpage) |
| WiFi 广播 | `POST /v1/sites/{siteId}/wifi/broadcasts` | Create Wifi Broadcast | [createwifibroadcast](https://developer.ui.com/network/v11.0.97/createwifibroadcast) |
| 防火墙策略 | `GET /v1/sites/{siteId}/firewall/policies` | List Firewall Policies | [getfirewallpolicies](https://developer.ui.com/network/v11.0.97/getfirewallpolicies) |
| 防火墙区域 | `GET /v1/sites/{siteId}/firewall/zones` | List Firewall Zones | [getfirewallzones](https://developer.ui.com/network/v11.0.97/getfirewallzones) |
| ACL | `GET /v1/sites/{siteId}/acl-rules` | List ACL Rules | [getaclrulepage](https://developer.ui.com/network/v11.0.97/getaclrulepage) |
| DNS 策略 | `GET /v1/sites/{siteId}/dns/policies` | List DNS Policies | [getdnspolicypage](https://developer.ui.com/network/v11.0.97/getdnspolicypage) |
| 流量匹配列表 | `GET /v1/sites/{siteId}/traffic-matching-lists` | List Traffic Matching Lists | [gettrafficmatchinglists](https://developer.ui.com/network/v11.0.97/gettrafficmatchinglists) |
| AP 与交换机 | `GET /v1/sites/{siteId}/switching/switch-stacks` | List Switch Stacks | [getswitchstackpage](https://developer.ui.com/network/v11.0.97/getswitchstackpage) |
| AP 与交换机 | `GET /v1/sites/{siteId}/switching/lags` | List LAGs | [getlagpage](https://developer.ui.com/network/v11.0.97/getlagpage) |
| VPN 与 WAN | `GET /v1/sites/{siteId}/vpn/servers` | List VPN Servers | [getvpnserverpage](https://developer.ui.com/network/v11.0.97/getvpnserverpage) |
| VPN 与 WAN | `GET /v1/sites/{siteId}/vpn/site-to-site-tunnels` | List Site-To-Site VPN Tunnels | [getsitetositevpntunnelpage](https://developer.ui.com/network/v11.0.97/getsitetositevpntunnelpage) |
| VPN 与 WAN | `GET /v1/sites/{siteId}/wans` | List WAN Interfaces | [getwansoverviewpage](https://developer.ui.com/network/v11.0.97/getwansoverviewpage) |
| Hotspot 凭证 | `GET /v1/sites/{siteId}/hotspot/vouchers` | List Vouchers | [getvouchers](https://developer.ui.com/network/v11.0.97/getvouchers) |

这套端点的设计规律很统一，掌握后可以猜路径：列表是 `GET` 集合（带分页与 filter），详情是 `GET {id}`，创建是 `POST` 集合，更新是 `PUT {id}`（只有防火墙策略额外提供 `PATCH {id}`，规范未说明两者字段级差异），删除是 `DELETE {id}`；顺序调整走独立的 `/ordering` 资源（ACL 规则与防火墙策略各有 `GET` 取序、`PUT` 改序）；设备与端口的即时操作走 `/actions` 子路径，请求体的必填字段只有 `{"action": "..."}`（规范里 `required` 仅 `action`，但官方文字同时要求带上该动作适用的输入参数，可选参数按动作而定）。hotspot 凭证特殊：除按 ID 删除外，还有集合级 `DELETE /v1/sites/{siteId}/hotspot/vouchers`，用 `filter` 批量吊销，适合「一次性作废某批访客码」的场景；该端点的官方描述原文是 "Remove Hotspot vouchers based on the specified filter criteria."，即删除动作由 filter 条件而不是 ID 列表决定。

写操作的必填字段规范里都有明确约束：创建网络至少需要 `name`、`vlanId`、`management`、`enabled`；创建 ACL 规则需要 `name`、`action`（枚举 `ALLOW`/`BLOCK`）、`type`、`enabled`；采纳设备需要 `macAddress` 与 `ignoreDeviceLimit`；创建设备/客户端/端口动作都需要 `action`。WiFi 广播的必填项最多（`IntegrationStandardWifiBroadcastCreateUpdateDto` 的 `required` 为 13 项：`advertiseDeviceName`、`arpProxyEnabled`、`broadcastingFrequenciesGHz`、`bssTransitionEnabled`、`channel2gLockedTo6`、`clientIsolationEnabled`、`dtimPeriod2gLockedTo3`、`enabled`、`hideName`、`multicastToUnicastConversionEnabled`、`name`、`securityConfiguration`、`uapsdEnabled`；另有判别字段 `type`），其中 `securityConfiguration` 自身仍要求 `type`，因此新建 SSID 前最好先 `GET` 一个同类型的现有广播做模板，再改字段提交。

落地前建议先打一次 `GET /v1/info` 做连通性与版本自检：它是不带 `siteId` 的少数端点之一（同类的还有 `/v1/sites`、`/v1/pending-devices`、`/v1/countries` 等），返回体只有一个 `applicationVersion` 字段（规范示例 `9.1.0`），能通说明基址、前缀、认证三件事同时正确，比直接调用业务端点更容易定位问题。

Site Manager API v1.0.0 只有 **10 条路径、14 个操作**，其中 5 个是同一条 `{id}/*path` 模板上的 GET/POST/PUT/PATCH/DELETE 透传（cloud-connector），另外 9 个是业务读接口：`GET /v1/hosts`（[listhosts](https://developer.ui.com/site-manager/v1.0.0/listhosts)）、`GET /v1/hosts/{id}`、`GET /v1/sites`（[listsites](https://developer.ui.com/site-manager/v1.0.0/listsites)）、`GET /v1/devices`（[listdevices](https://developer.ui.com/site-manager/v1.0.0/listdevices)）、`GET /v1/isp-metrics/{type}`、`POST /v1/isp-metrics/{type}/query`、`GET /v1/sd-wan-configs`、`GET /v1/sd-wan-configs/{id}`、`GET /v1/sd-wan-configs/{id}/status`。可以看到 Site Manager 是「只读盘点 + 指标查询」，写操作全部留给 Network API。

## 3.4 请求与响应要点

**分页。** Network API 的列表端点统一使用 `offset` 与 `limit`，规范中 23 个带分页参数的 GET 端点里有 22 个是默认 25、上限 200；唯一的例外是 hotspot 凭证列表，默认 100、上限 1000。响应是带统计信息的包裹体：`count`（本页实际返回条数，规范示例 10）、`data`（数组）、`limit`（本页上限，示例 25）、`offset`（示例 0）、`totalCount`（符合条件的总数，示例 1000），翻页要靠客户端自己累加 `offset`。Site Manager 则完全不同：用 `pageSize` 取页、用响应里的 `nextToken` 取下一页，文档写明单页最多 500 条；规范中 `pageSize` 被声明为字符串类型且未写上界，实际限值以文档的 500 为准。文档未给出续页终止条件的明文说明，工程上以响应中不再出现 `nextToken` 作为结束；它也不提供总条数。

**filter 语法。** Network API 的 `filter` 是结构化、URL 安全的表达式，分三类：属性表达式 `<property>.<function>(<arguments>)`、复合表达式 `<logical-operator>(<expressions>)`（`and` / `or` / `not`）、以及否定表达式 `not(<expression>)`。官方给出的真实例子包括 `id.eq(123)`、`createdAt.in(2025-01-01, 2025-01-05)`、`and(name.isNull(), createdAt.gt(2025-01-01))`、`or(name.isNull(), expired.isNull(), expiresAt.isNull())` 与 `not(name.like('guest*'))`。字面量类型有严格写法：字符串必须单引号包裹、内部单引号用两个单引号转义（`'Hello, ''World''!'`）；整数与小数必须以数字开头；时间戳用 ISO 8601（`2025-01-29` 或 `2025-01-29T12:39:11Z`）；布尔写 `true`/`false`；UUID 必须是 8-4-4-4-12；集合写成 `[1, 2, 3, 4, 5]`。可用函数包括 `isNull`、`isNotNull`、`eq`、`ne`、`gt`、`ge`、`lt`、`le`、`like`、`in`、`notIn`、`isEmpty`、`contains`、`containsAny`、`containsAll`、`containsExactly`，其中 `like` 支持 `*` 通配（`type.like('type.')`、`name.like('guest*')`）。每个端点允许的过滤属性不同，设备端点允许对 `name` 用 `like`、对 `firmwareVersion` 用 `lt`，客户端端点则有 `access.type`、`access.authorized` 这类带点号的属性名，因此下面三条过滤表达式都是合法写法（以 `curl --data-urlencode` 片段形式给出）：

```bash
# 1) 设备名以 AP 开头
--data-urlencode "filter=name.like('AP*')"
# 2) 在线且名为 AP 开头的设备
--data-urlencode "filter=and(state.eq('ONLINE'), name.like('AP*'))"
# 3) 固件版本低于 7.0.0 的设备
--data-urlencode "filter=firmwareVersion.lt('7.0.0')"
```

由于表达式里含单引号、括号和逗号，直接用 `curl -G --data-urlencode` 让 curl 完成百分号编码，比手工拼 URL 可靠得多。过滤与分页可以叠加：先用 `filter` 缩小集合，再翻页读取，用 `totalCount` 判断是否漏读——注意 `totalCount` 是「符合过滤条件」的总数，不是全量总数。

**请求体与内容类型。** 所有写操作的请求体都是 `application/json`，规范把它们标记为必填（`requestBody.required: true`）。更新语义按 HTTP 常规理解：`PUT` 为整体更新、`PATCH` 为局部更新，但只有防火墙策略同时提供两者，其余资源只有 `PUT`，因此修改单个字段也要提交完整对象。动作类端点（设备动作、客户端动作、端口动作）的请求体结构极简，只有一个 `action` 字符串；官方对设备动作页的说明是「请求体必须包含动作名称以及适用的输入参数」，具体可用动作名由设备类型决定，规范未枚举全部取值。

**错误响应。** Network API 的错误体是固定 7 字段结构：`code`（如 `api.authentication.missing-credentials`）、`message`（如 `Missing credentials`）、`requestId`（UUID，仅 500 类内部错误时用于在服务端日志中定位）、`requestPath`（如 `/integration/v1/sites/123`）、`statusCode`（int32，如 400）、`statusName`（如 `UNAUTHORIZED`）、`timestamp`（date-time，如 `2024-11-27T08:13:46.966Z`）。值得注意的是，v11.0.97 规范对每个操作只声明成功响应（73 个操作中 65 个为 200、8 个创建类为 201），没有任何错误响应定义，具体错误码也没有逐端点列出，排障要以错误体里的 `statusName` 与 `requestPath` 为准——`requestPath` 会告诉你服务端实际收到的路径，拼接前缀出错时一眼可见。

Site Manager 的错误体只有 4 个字段：`code`、`httpStatusCode`、`message`、`traceId`。官方文档列出的常见码为 400 `BAD_REQUEST`、401 `UNAUTHORIZED`、403 `FORBIDDEN`、404 `NOT_FOUND`、429 `RATE_LIMIT`、500 `SERVER_ERROR`、502 `BAD_GATEWAY`，示例消息分别是 `invalid request parameters`、`unauthorized`、`insufficient permissions`、`thing not found: {id}`、`rate limit exceeded, retry after 5.372786998s`、`failed to get resource by ID`、`bad gateway`。处理建议也被写进文档：先看 HTTP 状态码，再解析 `code`，记录 `traceId`，实现重试，并从响应里取出重试时间。

**429 与 Retry-After。** Site Manager 文档明确：超出速率限制返回 429，客户端应解析响应头里的重试时间，并至少等待那么久再重试；其规范中 429 响应示例带有 `Retry-After: 5`。**Network API 的官方文档没有提供 429 与 `Retry-After` 的说明**，规范里也没有 429 响应定义——这一点在写重试策略时必须区别对待，不能把 Site Manager 的限流语义套到本地 API 上。

## 3.5 完整调用示例

下面这段 shell 可直接复制执行，先用 `GET /v1/sites` 取 `siteId`，再列设备。它同时给出本地与云侧两种 base URL，实际用哪个由注释切换。

```bash
#!/usr/bin/env bash
set -euo pipefail

KEY="${UI_API_KEY:?请先导出 UI_API_KEY}"
HOST="192.168.0.1"                      # UCG-Max 的 LAN 地址
BASE="https://${HOST}/proxy/network/integration"   # 方式 A：本地直连

# 方式 B：经 Cloud Connector（无需内网可达），把上面一行换成
# CONSOLE_ID="942A6FF664C0000000000964970E0000000009E710560000000068A9873F:9999999999"
# BASE="https://api.ui.com/v1/connector/consoles/${CONSOLE_ID}/proxy/network/integration"

HDR=(-H "X-API-Key: ${KEY}" -H "Accept: application/json")

# 1) 取站点：data[] 里每项是 { id, internalReference, name }
SITE_ID=$(curl -sS -k "${HDR[@]}" "${BASE}/v1/sites" \
  | jq -r '.data[0].id')
echo "siteId = ${SITE_ID}"

# 2) 列设备（默认 25 条/页，最多 200 条/页），并只取在线设备
curl -sS -k -G "${HDR[@]}" "${BASE}/v1/sites/${SITE_ID}/devices" \
  --data-urlencode "limit=50" \
  --data-urlencode "filter=state.eq('ONLINE')" \
  | jq '{total:.totalCount, shown:.count, devices:[.data[]|{name,model,state,ipAddress,firmwareVersion}]}'
```

响应外壳与字段含义：外层 `count`/`limit`/`offset`/`totalCount` 即上面说的分页统计；`data[]` 每一项是设备概览对象，包含 `id`（UUID）、`macAddress`（如 `94:2a:6f:26:c6:ca`）、`ipAddress`（如 `192.168.1.55`）、`name`（如 `IW HD`）、`model`（如 `UHDIW`）、`state`、`supported`（布尔）、`firmwareVersion`（如 `6.6.55`）、`firmwareUpdatable`（布尔）、`features`（枚举 `switching`/`accessPoint`/`gateway` 的数组）与 `interfaces`（枚举 `ports`/`radios` 的数组）。`state` 的取值是封闭枚举：`ONLINE`、`OFFLINE`、`PENDING_ADOPTION`、`UPDATING`、`GETTING_READY`、`ADOPTING`、`DELETING`、`CONNECTION_INTERRUPTED`、`ISOLATED`、`U5G_INCORRECT_TOPOLOGY`。若 UCG-Max 自身的设备对象出现在列表里，`features` 会含 `gateway`，这是判断「网关是否被该站点纳管」的直接依据。

示例里对本地直连加了 `-k`：UCG-Max 默认使用自签证书，curl 会因证书链不受信而失败。这是部署经验而非官方文档条文；若已导入受信证书，应去掉 `-k`。云端方式不需要 `-k`。

执行这套脚本时最容易踩的四个坑，基本都能从响应结构直接定位：一是 401 且 `code` 为 `api.authentication.missing-credentials`，说明头名或 key 有问题，而不是路径问题；二是 404 且路径里带着 24 位十六进制的站点 ID，说明把 Site Manager 的 `siteId` 拿来调 Network API 了；三是错误体提示表达式非法（一般落在 4xx），多半是 `filter` 里的字符串忘了单引号，或把 `state.eq('ONLINE')` 直接裸拼进 URL 后被 shell 吞掉了引号；四是本地直连时 curl 报证书错误，属于自签证书的预期行为，而不是 API 不可用。

如果只想先确认账号级视角是否正常，可以用 Site Manager 的一行命令做对照——它不碰本地控制台，也不依赖内网可达：

```bash
curl -sS -H "X-API-Key: ${KEY}" -H "Accept: application/json" \
  'https://api.ui.com/v1/hosts?pageSize=10' | jq '.data[]|{id,type,ipAddress,owner}'
```

返回体是 `data`/`httpStatusCode`/`traceId`/`nextToken` 四件套，`data[]` 每项是一个控制台对象，字段包括 `id`、`hardwareId`、`type`、`ipAddress`、`owner`、`isBlocked`、`registrationTime`、`latestBackupTime`、`reportedState` 等；其中 `type` 的示例值是 `ucore`。注意 `hostId` 与 `hostName` 是 `GET /v1/devices` 返回里的字段，不在 hosts 响应中，两者不要混用。它能验证 key 与云端链路是否正常，但验证不了 Network API 的 `/proxy/network/integration` 前缀有没有拼对——这两类问题必须分开排查。

## 3.6 已知限制

**速率限制。** 官方只在 Site Manager 文档中给出数值：Early Access（`/ea/...`）为 100 请求/分钟，v1 stable 为 10,000 请求/分钟，超限返回 429 与 `Retry-After`。**Network API 的官方文档未提供任何限流数值**，规范中也没有 429 定义；把它当作「没有限流」并不安全，工程上应按有上限处理（串行化写入、指数退避），否则批量建 SSID 或批量吊销凭证时可能被静默截断或偶发失败。

**分页上限。** Network 列表端点 `limit` 上限 200（hotspot 凭证 1000），默认 25，翻页靠 `offset` 累加，没有游标——这意味着在翻页期间集合若有增删，会出现漏项或重复，盘点类任务要么接受一次快照误差，要么用 `totalCount` 做交叉校验。Site Manager 单页最多 500 条、必须用 `nextToken` 续页，且不返回总数，因此无法预判要翻多少页，只能循环到响应不再返回 token 为止。

**密钥权限范围。** 官方只说明 key 绑定生成它的组织或 UI 账户，**未提供端点级权限细分清单**。但读写能力要分两套看：**Site Manager 的 API Key 目前是只读的**（Getting Started 原文：The API key is currently read-only. When write endpoints become available, you'll be able to enable write access as needed, and the existing key won't be updated automatically），而 Network API 的 key 在站点内可读可写。最小权限只能靠「一个用途一把 key、定期轮换」在流程上实现。这同时意味着审计只能追到 key 级别，追不到「这条防火墙策略是谁的 key 改的」。

**版本演进。** Site Manager 有独立的 Version Control 页：提供 `https://api.ui.com/v1/...` 与 `https://api.ui.com/ea/...` 两套版本；当某个 EA 端点晋升为正式版（`/ea/` → `/v1/`）时，既有 EA 集成「继续按原样工作，无需立即修改」；EA 期间 `response.data` 里的字段可能是可选的。官方同时声明以下变更属于向后兼容、可能不预先通知：新增资源或端点、新增可选请求参数、新增响应属性、调整响应属性顺序、改变不透明字符串（对象 ID、错误消息等）的长度或格式。Network API 没有单独的版本演进页，它用带版本号的 URL 固化版本，`llms.txt` 里列出 v11.0.97 以及 v10.6.109、v10.6.106、v10.6.102 等历史版本入口。实践含义是：不要对响应做严格字段校验或依赖字段顺序，也不要解析对象 ID 的内部结构。

**可达性。** Network API 要么能直连控制台的 LAN 地址，要么经 Site Manager 的 Cloud Connector 代理；`llms.txt` 的原话是 Network 与 Protect 的 API 各自跑在 UniFi 主机本地，通过 Cloud Connector 或现场直连访问。跨 NAT、无公网映射的部署只能走云端代理，此时多一层网络跳转，且 `consoleId` 的获取方式官方未给出步骤。

**规范与文档的四处不一致（已核实）。** 写集成时把下面几点当已知偏差，不必反复怀疑自己看错：一是认证头，`llms.txt` 与 Site Manager 的 curl 示例写 `X-API-KEY`，Site Manager 规范写 `X-API-Key`；二是 Network 规范完全没有 `securitySchemes`，无法据规范自动生成认证代码；三是 Site Manager 的 `pageSize` 在规范中是字符串类型且无上界，真实上限 500 只见于文档正文；四是 Network 规范只声明成功响应（200 与 201），既没有逐端点错误码，也没有 429 定义。

**官方配套工具。** 官方为 Network API 提供 Ansible Collection：`ubiquiti.unifi_api`，按 Quick Start 页的说明通过 `ansible-galaxy collection install ubiquiti-unifi_api-latest.tar.gz` 安装，Python 依赖为 `httpx` 与 `urllib3`，模块入口是 `ubiquiti.unifi_api.network`（另有 `ubiquiti.unifi_api.common`），官方声明所有操作具备幂等性。除此之外只提供 OpenAPI 规范与 Postman Collection（每个服务各一份）。**结论：官方没有提供 SDK**——在 developer.ui.com 的全站索引 `llms.txt` 中不存在任何 SDK 类页面，只列出 OpenAPI 规范、Postman Collection 与 Ansible 模块三类配套。因此「用某语言的官方 SDK」这条路不存在，只能基于 OpenAPI 生成客户端或直接发 HTTP 请求。

## 3.7 版本与访问日期

本章对应两个规范版本：**UniFi Network API v11.0.97**（OpenAPI 3.1.0，44 条路径 / 73 个操作，本地与 Cloud Connector 两个 server 定义）与 **UniFi Site Manager API v1.0.0**（OpenAPI 3.0.3，10 条路径 / 14 个操作，server 为 `https://api.ui.com`）。规范与文档页面访问日期：**2026-10-31**。

## 参考来源

- <https://developer.ui.com/llms.txt>（全站索引；认证头 `X-API-KEY`、Site Manager 基址、Network/Protect 本地与 Cloud Connector 说明）
- <https://developer.ui.com/network/v11.0.97/openapi.json>（Network API v11.0.97 规范：servers、44 条路径、分页与字段定义）
- [Network API Getting Started](https://developer.ui.com/network/v11.0.97/gettingstarted)、[Filtering](https://developer.ui.com/network/v11.0.97/filtering)、[Error Handling](https://developer.ui.com/network/v11.0.97/error-handling)
- <https://developer.ui.com/network/v11.0.97/quick_start.ansible>（Ansible Collection 安装与幂等性说明）
- <https://developer.ui.com/site-manager/v1.0.0/openapi.json>（Site Manager API v1.0.0 规范：securityScheme `X-API-Key`、分页参数、429 与 `Retry-After` 示例）
- [Site Manager API Getting Started](https://developer.ui.com/site-manager/v1.0.0/gettingstarted)、[Response Format](https://developer.ui.com/site-manager/v1.0.0/responseformat)、[Version Control](https://developer.ui.com/site-manager/v1.0.0/versioncontrol)
