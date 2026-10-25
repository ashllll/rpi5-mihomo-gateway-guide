# Security and privacy

This repository publishes one gateway's deployment layer with site data removed.
Deployment data is never part of it: subscription links, node credentials, curated-pool
labels, dashboard tokens, WireGuard private keys and this deployment's real addresses
stay in `/etc/mihomo-automation.env`, `/etc/mihomo/secrets`,
`/etc/mihomo/adaptive/capabilities.yaml` and `/etc/mihomo-alert.env` (mode 0600).

## Listening surface on the recorded gateway

| Address | Port | Service | Reachable from |
|---|---|---|---|
| `192.168.1.1` | 53/tcp+udp | dnsmasq (main LAN) | `lan0` |
| `192.168.20.1` | 53/tcp+udp | dnsmasq (VLAN 20 pilot) | `proxy20` |
| `192.168.1.1` | 7890/tcp+udp | Mihomo mixed proxy (`lan-allowed-ips: 192.168.1.0/24`) | `lan0` |
| `192.168.1.1` | 9090/tcp | Mihomo external controller (bearer secret) | `lan0` |
| `0.0.0.0` | 46225/tcp | Mihomo auto-redirect listener | `lan0`, `proxy20`, `wg-ucg-pilot` |
| `127.0.0.1` | 1053, 5335, 9091 | Mihomo DNS, MosDNS DNS, MosDNS/prometheus API | loopback |
| `0.0.0.0` | 8799/tcp | rp5-agent read-only metrics (Bearer secret from env) | `wan0`/`wlan0`, source `192.168.0.0/24` |
| `0.0.0.0` | 22/tcp | SSH | `wan0`/`wlan0`, source `192.168.0.0/24` |
| `wan0` | 51891/udp | WireGuard (`wg-ucg-pilot`) | upstream `192.168.0.1` only |
| — | 8123/tcp | Home Assistant UI | **rejected** from `wan0`/`wlan0` by `homeassistant-access-guard.service`; allowed only from downstream LANs |

IPv6 forwarding and DNS are disabled; `inet rp_filter` rejects IPv6 input on the
service interface and drops MPTCP TCP options that would bypass redirection.

## Firewall model

`inet rp_filter` keeps INPUT and FORWARD at `policy drop` and applies an explicit
whitelist: `lan0` and `lo` are accepted as a trusted LAN, WAN-side access is limited to
SSH and 8799 from `192.168.0.0/24` plus DHCP/ICMP, `proxy20` only gets DHCP, DNS and
`ct status dnat`, and `wg-ucg-pilot` only reaches Mihomo or `wan0`. `ip rp_nat`
masquerades `192.168.1.0/24` out `wan0`. The kernel `net.ipv4.conf.*.rp_filter` sysctl is
`2` (loose); the nft table named `rp_filter` is a separate object with the same name.

The LAN trust model is intentional: accepting all of `lan0` is what lets Mihomo's
dynamically allocated redirect port work. It is **not** guest isolation, and a static
port whitelist would silently break transparent proxying. Mihomo binds mixed and
controller to the LAN address and restricts mixed traffic with `lan-allowed-ips`;
neither replaces network isolation for untrusted clients.

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
