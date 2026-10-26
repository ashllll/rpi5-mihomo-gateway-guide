# Security and privacy

This repository publishes one gateway's deployment layer with site data removed.
Deployment data is never part of it: subscription links, node credentials, curated-pool
labels, dashboard tokens, WireGuard private keys and this deployment's real addresses
stay in `/etc/mihomo-automation.env`, `/etc/mihomo/secrets`,
`/etc/mihomo/adaptive/capabilities.yaml` and `/etc/mihomo-alert.env` (mode 0600).

## Listening surface on the recorded gateway

| Address | Port | Service | Reachable from |
|---|---|---|---|
| `127.0.0.1` | 53/tcp+udp | dnsmasq (loopback resolver) | `lo` |
| `192.168.20.1` | 53/tcp+udp | dnsmasq (VLAN 20 pilot, `dnsmasq-vlan-pilot.service`) | `proxy20` |
| `0.0.0.0` | 7890/tcp+udp | Mihomo mixed proxy (`bind-address: "*"`, `lan-allowed-ips` = loopback + upstream/VLAN20 placeholders) | `lo`; other interfaces filtered by `inet rp_filter` input |
| `0.0.0.0` | 9090/tcp | Mihomo external controller (bearer secret) | `lo`; other interfaces filtered by `inet rp_filter` input |
| `0.0.0.0` | 46225/tcp | Mihomo auto-redirect listener | `proxy20`, `wg-ucg-pilot` |
| `127.0.0.1` | 1053, 5335, 9091 | Mihomo DNS, MosDNS DNS, MosDNS/prometheus API | loopback |
| `0.0.0.0` | 8799/tcp | rp5-agent read-only metrics (Bearer secret from env) | `wan0`/`wlan0`, source `192.168.0.0/24` |
| `0.0.0.0` | 22/tcp | SSH | `wan0`/`wlan0`, source `192.168.0.0/24`; `proxy20` via the pilot include |
| `wan0` | 51891/udp | WireGuard (`wg-ucg-pilot`) | upstream `192.168.0.1` only |
| — | 8123/tcp | Home Assistant UI | **rejected** from `wan0`/`wlan0` by `homeassistant-access-guard.service`; no upstream path reaches it |

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
`RP5-PC-TEST` Wi-Fi clients). Transparent proxying relies on `ip rule 9000` and the
core's `redirect`, not on accepting a whole interface, and management calls are pinned
to loopback. Mihomo listens on `bind-address: "*"` and restricts the mixed port with
`lan-allowed-ips`; neither replaces network isolation for untrusted clients.

The published layer writes only files it owns (scripts, units, dnsmasq/MosDNS/locality
skeletons) and never edits sshd, NetworkManager, sysctl or `/etc/nftables.conf`.

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
structure and are not authentication credentials.

If you find an accidentally published credential, do not open a public issue containing
it. Revoke it first, then contact the repository owner through a private channel.
