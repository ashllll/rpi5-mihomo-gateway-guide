#!/usr/bin/env python3
"""Render a standalone, private candidate directory. Never install or reload."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets


def automation_environment(site):
    """Render only the parameters shared by the site and automation consumers."""
    shared = {
        "GATEWAY_LAN_IP": str(ipaddress.IPv4Address(site["lan_address"])),
        "MIHOMO_MIXED_PORT": site["mixed_port"],
        "MIHOMO_CONTROLLER_PORT": site["controller_port"],
        "WAN_INTERFACE": site["wan_interface"],
    }
    template = Path(__file__).resolve().parents[1] / "automation/env/automation.env.example"
    lines = template.read_text(encoding="utf-8").splitlines()
    found = {line.split("=", 1)[0] for line in lines if "=" in line}
    if not shared.keys() <= found:
        raise ValueError("Automation env template is missing shared site parameters")
    return "\n".join(
        f"{key}={shared[key]}" if (key := line.split("=", 1)[0]) in shared else line
        for line in lines
    ) + "\n"


def render(site, output):
    required = {
        "wan_interface", "lan_interface", "management_cidr", "lan_cidr",
        "lan_address", "dhcp_start", "dhcp_end", "dhcp_lease", "mixed_port",
        "controller_port", "mihomo_dns_port", "mosdns_port", "dns_upstreams",
    }
    if set(site) != required:
        raise ValueError("Site fields must match examples/site.example.json")
    for key in ("wan_interface", "lan_interface"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,15}", site[key]):
            raise ValueError("Invalid interface name")
    if site["wan_interface"] == site["lan_interface"]:
        raise ValueError("WAN and LAN must differ")
    lan = ipaddress.IPv4Network(site["lan_cidr"])
    management = ipaddress.IPv4Network(site["management_cidr"])
    private = [ipaddress.IPv4Network(c) for c in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if not all(any(n.subnet_of(p) for p in private) for n in (lan, management)):
        raise ValueError("Use RFC1918 private LAN and management subnets")
    if lan.overlaps(management) or lan.prefixlen > 30:
        raise ValueError("LAN must have usable hosts and not overlap management")
    address, start, end = (ipaddress.IPv4Address(site[k]) for k in
                           ("lan_address", "dhcp_start", "dhcp_end"))
    if any(a not in lan or a in (lan.network_address, lan.broadcast_address)
           for a in (address, start, end)):
        raise ValueError("Gateway and DHCP endpoints must be usable LAN addresses")
    if start > end or start <= address <= end:
        raise ValueError("DHCP range must be ordered and exclude the gateway")
    ports = [site[k] for k in ("mixed_port", "controller_port", "mihomo_dns_port", "mosdns_port")]
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != 4:
        raise ValueError("Service ports must be unique integers from 1024 to 65535")
    if not re.fullmatch(r"[1-9][0-9]*[mhd]", site["dhcp_lease"]):
        raise ValueError("Invalid DHCP lease")
    if not site["dns_upstreams"]:
        raise ValueError("At least one bootstrap DNS address is required")
    dns = [str(ipaddress.IPv4Address(a)) for a in site["dns_upstreams"]]
    environment = automation_environment(site)
    # Refuse an existing destination instead of overwriting private config.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    wan, interface = site["wan_interface"], site["lan_interface"]
    mixed, controller, dns_port, _ = ports
    nft = f'''#!/usr/sbin/nft -f
# Replaces only these tables; preserves Mihomo auto-redirect rules.
destroy table inet rp_filter
destroy table ip rp_nat
table inet rp_filter {{
  chain input {{
    type filter hook input priority filter; policy drop;
    iifname "lo" accept
    ct state invalid drop
    ct state established,related accept
    # Trusted local LAN, including dynamic auto-redirect listeners.
    iifname "{interface}" accept
    iifname "{wan}" ip saddr {management} tcp dport 22 accept
    iifname "{wan}" udp sport 67 udp dport 68 accept
    iifname "{wan}" udp sport 547 udp dport 546 accept
    ip protocol icmp accept
    meta l4proto ipv6-icmp accept
  }}
  chain forward {{
    type filter hook forward priority filter; policy drop;
    ct state invalid drop
    ct state established,related accept
    iifname "{interface}" oifname "Mihomo" ip saddr {lan} accept
    iifname "Mihomo" oifname "{interface}" ip daddr {lan} accept
    iifname "{interface}" oifname "{wan}" ip saddr {lan} accept
  }}
  chain output {{
    type filter hook output priority filter; policy accept;
  }}
}}
table ip rp_nat {{
  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
    ip saddr {lan} oifname "{wan}" masquerade
  }}
}}
'''
    dnsmasq = f'''interface={interface}
listen-address=127.0.0.1,{address}
bind-dynamic
dhcp-range={start},{end},{lan.netmask},{site['dhcp_lease']}
dhcp-option=3,{address}
dhcp-option=6,{address}
cache-size=0
no-resolv
server=127.0.0.1#{dns_port}
'''
    mihomo = {
        "mixed-port": mixed, "allow-lan": True, "bind-address": str(address),
        "lan-allowed-ips": [str(lan)], "mode": "rule", "log-level": "info",
        "ipv6": False, "external-controller": f"{address}:{controller}",
        "secret": secrets.token_hex(32),
        "tun": {"enable": True, "device": "Mihomo", "stack": "mixed",
                "auto-route": True, "auto-redirect": True, "strict-route": True,
                "include-interface": [interface], "dns-hijack": ["any:53", "tcp://any:53"]},
        "dns": {"enable": True, "listen": f"127.0.0.1:{dns_port}", "ipv6": False,
                "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
                "default-nameserver": dns, "nameserver": dns},
        "proxy-providers": {
            name: {"type": "file", "path": f"./providers/{name}.yaml",
                   "health-check": {"enable": True, "url": "https://www.gstatic.com/generate_204", "interval": 600}}
            for name in ("general-a", "general-b", "ai-only", "download-only")},
        "proxy-groups": [
            {"name": "Browsing", "type": "url-test", "use": ["general-a", "general-b"],
             "url": "https://www.gstatic.com/generate_204", "interval": 600, "tolerance": 100},
            {"name": "AI", "type": "fallback", "use": ["ai-only"],
             "url": "https://www.gstatic.com/generate_204", "interval": 600},
            {"name": "Download", "type": "load-balance", "use": ["general-a", "general-b", "download-only"],
             "strategy": "consistent-hashing", "url": "https://www.gstatic.com/generate_204", "interval": 600},
        ],
        "rules": [
            "DOMAIN-SUFFIX,openai.com,AI", "DOMAIN-SUFFIX,chatgpt.com,AI",
            "DOMAIN-SUFFIX,oaistatic.com,AI", "DOMAIN-SUFFIX,oaiusercontent.com,AI",
            "DOMAIN-SUFFIX,claude.ai,AI", "DOMAIN,gemini.google.com,AI",
            "DOMAIN-SUFFIX,huggingface.co,Download", "DOMAIN-SUFFIX,hf.co,Download",
            "DOMAIN-SUFFIX,hf.space,Download", "DOMAIN-SUFFIX,xethub.hf.co,Download",
            "DOMAIN,codeload.github.com,Download", "DOMAIN,release-assets.githubusercontent.com,Download",
            "DOMAIN,objects.githubusercontent.com,Download", "DOMAIN,registry-1.docker.io,Download",
            f"IP-CIDR,{lan},DIRECT,no-resolve", f"IP-CIDR,{management},DIRECT,no-resolve",
            "MATCH,Browsing"],
    }
    files = {
        "mihomo-automation.env": environment,
        "nftables.conf": nft,
        "dnsmasq-lan.conf": dnsmasq,
        "mihomo/config.yaml": json.dumps(mihomo, indent=2) + "\n",
        "gateway-sysctl.conf": f'''net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
net.ipv4.conf.{wan}.rp_filter=0
net.ipv4.conf.{interface}.rp_filter=0
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
''',
    }
    # Deliberately unusable nodes let syntax checks run without distributing access.
    for name in ("general-a", "general-b", "ai-only", "download-only"):
        files[f"mihomo/providers/{name}.yaml"] = json.dumps({"proxies": [
            {"name": f"REPLACE-{name}", "type": "http", "server": f"{name}.example.invalid", "port": 443, "tls": True}
        ]}, indent=2) + "\n"
    for relative, content in files.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("x", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            handle.write(content)
    print(f"Rendered {len(files)} private candidate files; replace provider placeholders before deployment.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(json.loads(args.site.read_text(encoding="utf-8")), args.output)
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(1, f"Cannot render: {error}\n")
