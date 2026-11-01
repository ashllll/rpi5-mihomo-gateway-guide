# Security and privacy

This repository publishes one gateway's deployment layer with site data removed.
Deployment data is never part of it: subscription links, node credentials, curated-pool
labels, dashboard tokens, WireGuard private keys and this deployment's real addresses
stay in `/etc/mihomo-automation.env`, `/etc/mihomo/secrets`,
`/etc/mihomo/adaptive/capabilities.yaml` and `/etc/mihomo-alert.env` (mode 0600).

## Listening surface on the recorded gateway

Measured on 2026-11-01 with `ss -tulnp` and `nft list ruleset`; only checked entries are listed.

| Address | Port | Service | Reachable from |
|---|---|---|---|
| `127.0.0.1` | 53/tcp+udp | dnsmasq (loopback resolver) | `lo` |
| `192.168.20.1` | 53/tcp+udp | dnsmasq (VLAN 20 pilot, `dnsmasq-vlan-pilot.service`) | `proxy20` |
| `0.0.0.0` | 67/udp | dnsmasq DHCP for the pilot scope | `proxy20`; other interfaces filtered by `inet rp_filter` input |
| `0.0.0.0` | 7890/tcp+udp | Mihomo mixed proxy (`bind-address: "*"`, `lan-allowed-ips` = loopback + upstream/VLAN20 placeholders) | `lo`; other interfaces filtered by `inet rp_filter` input |
| `0.0.0.0` | 9090/tcp | Mihomo external controller (bearer secret) | `lo`; other interfaces filtered by `inet rp_filter` input |
| `0.0.0.0` | 40937/tcp | Mihomo auto-redirect listener (dynamic port; the repo config pins `46225`, the recorded gateway listened on `40937`) | `proxy20`, `wg-ucg-pilot` |
| `127.0.0.1` | 1053, 5335, 9091 | Mihomo DNS, MosDNS DNS, MosDNS/prometheus API | loopback |
| `0.0.0.0` | 8799/tcp | rp5-agent read-only status (`/health` public metadata; `/api/v1/status` requires a Bearer token from `/etc/mihomo/secrets/rp5-agent.token`, 0600) | `wan0`/`wlan0`, source `192.168.0.0/24` |
| `0.0.0.0` | 22/tcp | SSH | `wan0`/`wlan0`, source `192.168.0.0/24`; `proxy20` via the pilot include |
| `wan0` | 51891/udp | WireGuard (`wg-ucg-pilot`) | upstream `192.168.0.1` only |
| — | 8123/tcp | Home Assistant UI | **rejected** from `wan0`/`wlan0` by `homeassistant-access-guard.service`; no upstream path reaches it. The compose template pins the image by digest and drops `/run/dbus`; `privileged`/host networking remain until the stage-3 integration regression |
| `0.0.0.0` | 18555/tcp | Home Assistant go2rtc | `lo`; other interfaces filtered by `inet rp_filter` input |
| `0.0.0.0` | 5353/udp | avahi + Home Assistant mDNS | `lo`; other interfaces filtered by `inet rp_filter` input |
| `0.0.0.0` | 1900/udp | Home Assistant SSDP | `lo`; other interfaces filtered by `inet rp_filter` input |

IPv6 forwarding and DNS are disabled; `inet rp_filter` rejects IPv6 input on the
service interface and drops MPTCP TCP options that would bypass redirection.

## Firewall model

`inet rp_filter` keeps INPUT and FORWARD at `policy drop` and applies an explicit
whitelist: `lo` and established/related are accepted, WAN-side access is limited to
SSH and 8799 from `192.168.0.0/24` plus DHCP/ICMP/WireGuard, `proxy20` only gets
DHCP, DNS, `ct status dnat` and `tcp/22`, and `wg-ucg-pilot` only reaches Mihomo or
`wan0`. `ip rp_nat` itself has no masquerade rule; the VLAN20 and WireGuard NAT live
in their include files. The kernel `net.ipv4.conf.*.rp_filter` sysctl is `2` (loose);
the nft table named `rp_filter` is a separate object with the same name.

The gateway has no separate downstream LAN interface: one cable into the UCG switch
carries VLAN1 (upstream and management) and VLAN20 (`proxy20`, the UCG-broadcast
`<SSID-1>` Wi-Fi clients). Transparent proxying relies on `ip rule 9000` and the
core's `redirect`, not on accepting a whole interface.

The core's `bind-address: "*"` makes the mixed proxy (`7890`) and the external
controller (`9090`) listen on every interface. Reach is narrowed by `lan-allowed-ips`
(mixed port) and the `inet rp_filter` input chain; the gateway's own calls go to
`127.0.0.1`. Neither mechanism replaces network isolation for untrusted clients.

The status agent pins its controller target to `127.0.0.1:9090` in the unit and refuses any non-loopback value at startup.

The published layer writes two kinds of files. Managed files are replaced on install:
scripts to `/usr/local/sbin` (0755), units and drop-ins to `/etc/systemd/system` (0644),
journald limits to `/etc/systemd/journald.conf.d`, and the NetworkManager dispatcher
`/etc/NetworkManager/dispatcher.d/90-wifi-management-routing` (only where
`/etc/NetworkManager` exists). A managed file that no longer matches a reviewed release
stops the install with `installed drift: <path>` rather than being replaced.

Everything the operator is expected to edit is seeded once with `install_default` and
left alone afterwards: `/etc/nftables.conf`, `/etc/nftables.d/*.nft`, the `/etc/mihomo/*`
data defaults (`config.local.yaml`, policy, mode baselines, `upstreams.*`),
`/etc/mosdns/config.yaml` and its rules, `/etc/dnsmasq.d/resolver.conf`,
`/etc/dnsmasq-vlan-pilot.conf`, `/etc/dnsmasq-upstreams.conf`, and the `*.example`
environment files. It never writes sshd configuration or sysctl.

## Before publishing

```sh
python3 tools/validate_public.py
python3 tools/check_publication_privacy.py
```

These scan the working tree, not Git history. Also run a secret scanner over history
with redacted output. The validators are a guard, not proof that arbitrary archives or
binaries are secret-free.

Do not commit:

- proxy subscription URLs or tokens
- proxy node credentials, node lists or curated-pool labels
- Wi-Fi, dashboard or controller secrets
- private keys or certificates
- full `/etc/mihomo` archives or unredacted router backups
- real MAC addresses, UUIDs or public IPv6 addresses

Private IPv4 addresses in the documentation describe this deployment's network
structure and are not authentication credentials; they must still fall inside a
network the repository documents.

These rules are executable: `tools/validate_public.py` blocks the publication on
device identifiers, credentials, subscription endpoints, node labels and
credential-bearing URLs in the tree, and on post-baseline history. The private
history before that baseline is removed by the phase-3 rewrite.


If you find an accidentally published credential, do not open a public issue containing
it. Revoke it first, then contact the repository owner through a private channel.
