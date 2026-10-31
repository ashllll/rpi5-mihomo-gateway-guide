| Method | Path | Summary | Tag |
|---|---|---|---|
| GET | `/v1/countries` | List Countries | Supporting Resources |
| GET | `/v1/dpi/applications` | List DPI Applications | Supporting Resources |
| GET | `/v1/dpi/categories` | List DPI Application Categories | Supporting Resources |
| GET | `/v1/info` | Get Application Info | Application Info |
| GET | `/v1/pending-devices` | List Devices Pending Adoption | UniFi Devices |
| GET | `/v1/sites` | List Local Sites | Sites |
| GET | `/v1/sites/{siteId}/acl-rules` | List ACL Rules | Access Control (ACL Rules) |
| POST | `/v1/sites/{siteId}/acl-rules` | Create ACL Rule | Access Control (ACL Rules) |
| GET | `/v1/sites/{siteId}/acl-rules/ordering` | Get User-Defined ACL Rule Ordering | Access Control (ACL Rules) |
| PUT | `/v1/sites/{siteId}/acl-rules/ordering` | Reorder User-Defined ACL Rules | Access Control (ACL Rules) |
| DELETE | `/v1/sites/{siteId}/acl-rules/{aclRuleId}` | Delete ACL Rule | Access Control (ACL Rules) |
| GET | `/v1/sites/{siteId}/acl-rules/{aclRuleId}` | Get ACL Rule | Access Control (ACL Rules) |
| PUT | `/v1/sites/{siteId}/acl-rules/{aclRuleId}` | Update ACL Rule | Access Control (ACL Rules) |
| GET | `/v1/sites/{siteId}/clients` | List Connected Clients | Clients |
| GET | `/v1/sites/{siteId}/clients/{clientId}` | Get Connected Client Details | Clients |
| POST | `/v1/sites/{siteId}/clients/{clientId}/actions` | Execute Client Action | Clients |
| GET | `/v1/sites/{siteId}/device-tags` | List Device Tags | Supporting Resources |
| GET | `/v1/sites/{siteId}/devices` | List Adopted Devices | UniFi Devices |
| POST | `/v1/sites/{siteId}/devices` | Adopt Devices | UniFi Devices |
| DELETE | `/v1/sites/{siteId}/devices/{deviceId}` | Remove (Unadopt) Device | UniFi Devices |
| GET | `/v1/sites/{siteId}/devices/{deviceId}` | Get Adopted Device Details | UniFi Devices |
| POST | `/v1/sites/{siteId}/devices/{deviceId}/actions` | Execute Adopted Device Action | UniFi Devices |
| POST | `/v1/sites/{siteId}/devices/{deviceId}/interfaces/ports/{portIdx}/actions` | Execute Port Action | UniFi Devices |
| GET | `/v1/sites/{siteId}/devices/{deviceId}/statistics/latest` | Get Latest Adopted Device Statistics | UniFi Devices |
| GET | `/v1/sites/{siteId}/dns/policies` | List DNS Policies | DNS Policies |
| POST | `/v1/sites/{siteId}/dns/policies` | Create DNS Policy | DNS Policies |
| DELETE | `/v1/sites/{siteId}/dns/policies/{dnsPolicyId}` | Delete DNS Policy | DNS Policies |
| GET | `/v1/sites/{siteId}/dns/policies/{dnsPolicyId}` | Get DNS Policy | DNS Policies |
| PUT | `/v1/sites/{siteId}/dns/policies/{dnsPolicyId}` | Update DNS Policy | DNS Policies |
| GET | `/v1/sites/{siteId}/firewall/policies` | List Firewall Policies | Firewall |
| POST | `/v1/sites/{siteId}/firewall/policies` | Create Firewall Policy | Firewall |
| GET | `/v1/sites/{siteId}/firewall/policies/ordering` | Get User-Defined Firewall Policy Ordering | Firewall |
| PUT | `/v1/sites/{siteId}/firewall/policies/ordering` | Reorder User-Defined Firewall Policies | Firewall |
| DELETE | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Delete Firewall Policy | Firewall |
| GET | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Get Firewall Policy | Firewall |
| PATCH | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Patch Firewall Policy | Firewall |
| PUT | `/v1/sites/{siteId}/firewall/policies/{firewallPolicyId}` | Update Firewall Policy | Firewall |
| GET | `/v1/sites/{siteId}/firewall/zones` | List Firewall Zones | Firewall |
| POST | `/v1/sites/{siteId}/firewall/zones` | Create Custom Firewall Zone | Firewall |
| DELETE | `/v1/sites/{siteId}/firewall/zones/{firewallZoneId}` | Delete Custom Firewall Zone | Firewall |
| GET | `/v1/sites/{siteId}/firewall/zones/{firewallZoneId}` | Get Firewall Zone | Firewall |
| PUT | `/v1/sites/{siteId}/firewall/zones/{firewallZoneId}` | Update Firewall Zone | Firewall |
| DELETE | `/v1/sites/{siteId}/hotspot/vouchers` | Delete Vouchers | Hotspot |
| GET | `/v1/sites/{siteId}/hotspot/vouchers` | List Vouchers | Hotspot |
| POST | `/v1/sites/{siteId}/hotspot/vouchers` | Generate Vouchers | Hotspot |
| DELETE | `/v1/sites/{siteId}/hotspot/vouchers/{voucherId}` | Delete Voucher | Hotspot |
| GET | `/v1/sites/{siteId}/hotspot/vouchers/{voucherId}` | Get Voucher Details | Hotspot |
| GET | `/v1/sites/{siteId}/networks` | List Networks | Networks |
| POST | `/v1/sites/{siteId}/networks` | Create Network | Networks |
| DELETE | `/v1/sites/{siteId}/networks/{networkId}` | Delete Network | Networks |
| GET | `/v1/sites/{siteId}/networks/{networkId}` | Get Network Details | Networks |
| PUT | `/v1/sites/{siteId}/networks/{networkId}` | Update Network | Networks |
| GET | `/v1/sites/{siteId}/networks/{networkId}/references` | Get Network References | Networks |
| GET | `/v1/sites/{siteId}/radius/profiles` | List Radius Profiles | Supporting Resources |
| GET | `/v1/sites/{siteId}/switching/lags` | List LAGs | Switching |
| GET | `/v1/sites/{siteId}/switching/lags/{lagId}` | Get LAG Details | Switching |
| GET | `/v1/sites/{siteId}/switching/mc-lag-domains` | List MC-LAG Domains | Switching |
| GET | `/v1/sites/{siteId}/switching/mc-lag-domains/{mcLagDomainId}` | Get MC-LAG Domain | Switching |
| GET | `/v1/sites/{siteId}/switching/switch-stacks` | List Switch Stacks | Switching |
| GET | `/v1/sites/{siteId}/switching/switch-stacks/{switchStackId}` | Get Switch Stack | Switching |
| GET | `/v1/sites/{siteId}/traffic-matching-lists` | List Traffic Matching Lists | Traffic Matching Lists |
| POST | `/v1/sites/{siteId}/traffic-matching-lists` | Create Traffic Matching List | Traffic Matching Lists |
| DELETE | `/v1/sites/{siteId}/traffic-matching-lists/{trafficMatchingListId}` | Delete Traffic Matching List | Traffic Matching Lists |
| GET | `/v1/sites/{siteId}/traffic-matching-lists/{trafficMatchingListId}` | Get Traffic Matching List | Traffic Matching Lists |
| PUT | `/v1/sites/{siteId}/traffic-matching-lists/{trafficMatchingListId}` | Update Traffic Matching List | Traffic Matching Lists |
| GET | `/v1/sites/{siteId}/vpn/servers` | List VPN Servers | Supporting Resources |
| GET | `/v1/sites/{siteId}/vpn/site-to-site-tunnels` | List Site-To-Site VPN Tunnels | Supporting Resources |
| GET | `/v1/sites/{siteId}/wans` | List WAN Interfaces | Supporting Resources |
| GET | `/v1/sites/{siteId}/wifi/broadcasts` | List Wifi Broadcasts | WiFi Broadcasts |
| POST | `/v1/sites/{siteId}/wifi/broadcasts` | Create Wifi Broadcast | WiFi Broadcasts |
| DELETE | `/v1/sites/{siteId}/wifi/broadcasts/{wifiBroadcastId}` | Delete Wifi Broadcast | WiFi Broadcasts |
| GET | `/v1/sites/{siteId}/wifi/broadcasts/{wifiBroadcastId}` | Get Wifi Broadcast Details | WiFi Broadcasts |
| PUT | `/v1/sites/{siteId}/wifi/broadcasts/{wifiBroadcastId}` | Update Wifi Broadcast | WiFi Broadcasts |