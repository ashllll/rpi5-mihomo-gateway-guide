#!/usr/bin/env python3
"""Detect, validate and install a two-interface Linux Mihomo gateway.

Only the standard library is required. Native Mihomo providers parse subscriptions.
Run via setup.sh to bootstrap Python. --dry-run never installs or writes files.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import getpass
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

HERE = Path(__file__).resolve().parent
CONFIG = Path('/etc/mihomo-gateway')
APP = Path('/opt/mihomo-gateway')
STATE = Path('/var/lib/mihomo-gateway')
UNITS = ('mihomo-gateway.service', 'mihomo-gateway-dhcp.service', 'mihomo-gateway-firewall.service')
PRIVATE = tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


class SetupError(Exception):
    pass


def run(*args, check=True, input=None, timeout=60):
    """Never include subprocess output/arguments in exceptions: they may be private."""
    result = subprocess.run([str(a) for a in args], input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                            env={**os.environ, 'LC_ALL': 'C'})
    if check and result.returncode:
        raise SetupError(f'{Path(str(args[0])).name} failed (exit {result.returncode})')
    return result


def active(name):
    return run('systemctl', 'is-active', '--quiet', name, check=False).returncode == 0


def write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SetupError('Refusing a symlink destination')
    descriptor, raw = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def json_text(value):
    return json.dumps(value, ensure_ascii=True, indent=2) + '\n'


def check_url(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or any(ord(c) < 33 or ord(c) == 127 for c in url)):
        raise SetupError('Subscription must be a complete HTTPS URL without userinfo or whitespace')
    return url


def fetch(url, target, limit=16 * 1024 * 1024, user_agent='mihomo'):
    """URL enters curl through stdin, never ps/command arguments or diagnostics."""
    if urlsplit(url).scheme != 'https' or any(ord(c) < 32 for c in url):
        raise SetupError('Downloads require HTTPS')
    escaped = url.replace('\\', '\\\\').replace('"', '\\"')
    result = run('curl', '-q', '--fail', '--location', '--silent', '--proto', '=https',
                 '--proto-redir', '=https', '--connect-timeout', '15', '--max-time', '120',
                 '--retry', '2', '--max-filesize', str(limit), '--user-agent', user_agent,
                 '--output', target, '--config', '-', input=f'url = "{escaped}"\n', check=False, timeout=390)
    if result.returncode or not Path(target).is_file() or not 0 < Path(target).stat().st_size <= limit:
        raise SetupError('HTTPS download failed or exceeded size limit (URL withheld)')
    os.chmod(target, 0o600)


def package_manager():
    release = Path('/etc/os-release')
    fields = dict(line.split('=', 1) for line in release.read_text().splitlines()
                  if '=' in line and not line.startswith('#')) if release.exists() else {}
    family = (fields.get('ID', '') + ' ' + fields.get('ID_LIKE', '')).replace('"', '').split()
    managers = {'debian': 'apt-get', 'ubuntu': 'apt-get', 'raspbian': 'apt-get',
                'fedora': 'dnf', 'rhel': 'dnf', 'centos': 'dnf', 'arch': 'pacman',
                'suse': 'zypper', 'opensuse': 'zypper', 'opensuse-leap': 'zypper',
                'opensuse-tumbleweed': 'zypper'}
    preferred = next((managers[name] for name in family if name in managers), None)
    if preferred:
        if not shutil.which(preferred):
            raise SetupError(f'The detected distribution requires {preferred}, but it is unavailable')
        return preferred
    available = [name for name in ('apt-get', 'dnf', 'pacman', 'zypper') if shutil.which(name)]
    if len(available) == 1:
        return available[0]
    raise SetupError('Cannot identify a unique supported package manager from /etc/os-release')


def install_dependencies(manager):
    packages = {
        'apt-get': ['python3', 'iproute2', 'nftables', 'dnsmasq-base', 'curl', 'ca-certificates', 'util-linux'],
        'dnf': ['python3', 'iproute', 'nftables', 'dnsmasq', 'curl', 'ca-certificates', 'util-linux'],
        'pacman': ['python', 'iproute2', 'nftables', 'dnsmasq', 'curl', 'ca-certificates', 'util-linux'],
        'zypper': ['python3', 'iproute2', 'nftables', 'dnsmasq', 'curl', 'ca-certificates', 'util-linux'],
    }[manager]
    print(f'Installing dependencies with {manager}...', flush=True)
    # dnsmasq-base has no distribution service/postinst auto-start on Debian.
    if manager == 'apt-get':
        run(manager, 'update', timeout=900)
        run('env', 'DEBIAN_FRONTEND=noninteractive', manager, 'install', '-y', *packages, timeout=900)
    elif manager == 'dnf':
        run(manager, 'install', '-y', *packages, timeout=900)
    elif manager == 'pacman':
        # Use the current package database; do not silently upgrade the OS.
        run(manager, '-S', '--noconfirm', '--needed', *packages, timeout=900)
    else:
        run(manager, '--non-interactive', 'install', *packages, timeout=900)


def inventory():
    if not shutil.which('ip'):
        raise SetupError('iproute2 is required for detection; install it first (dry-run installs nothing)')
    links = json.loads(run('ip', '-j', 'address', 'show').stdout)
    for link in links:
        path = Path('/sys/class/net') / link['ifname']
        link['physical'] = (path / 'device').exists()
        link['wireless'] = (path / 'wireless').exists()
        link['tunnel'] = (path / 'tun_flags').exists()
    return links, json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'all').stdout)


def choose_interfaces(links, routes, wan=None, lan=None):
    physical = {x['ifname']: x for x in links if x.get('physical') and not x.get('master')}
    defaults = [r for r in routes if r.get('dst') == 'default'
                and r.get('table', 'main') in ('main', 254) and r.get('dev') in physical
                and 'linkdown' not in r.get('flags', [])]
    if not wan:
        if not defaults:
            raise SetupError('No physical IPv4 uplink default route; connect the uplink first')
        metric = min(r.get('metric', 0) for r in defaults)
        candidates = {r['dev'] for r in defaults if r.get('metric', 0) == metric}
        if len(candidates) != 1:
            raise SetupError('Ambiguous uplinks; specify --wan INTERFACE')
        wan = candidates.pop()
    if wan not in physical or not any(r['dev'] == wan for r in defaults):
        raise SetupError('--wan must be a physical interface with a working default route')
    if not lan:
        candidates = [n for n, x in physical.items() if n != wan and not x.get('wireless')]
        if len(candidates) != 1:
            raise SetupError('Need one unambiguous downstream Ethernet interface; use --lan INTERFACE')
        lan = candidates[0]
        if any(r['dev'] == lan for r in defaults):
            raise SetupError('Second interface also has an uplink route; specify --lan deliberately')
    if lan == wan or lan not in physical or physical[lan].get('wireless'):
        raise SetupError('LAN must be a separate physical Ethernet interface, outside a bridge/bond')
    for name in (wan, lan):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', name):
            raise SetupError('Unsupported interface name')
    return wan, lan


def used_networks(links, routes, lan, owned=False):
    result = []
    for r in routes:
        dst = r.get('dst', 'default')
        if dst == 'default' or (owned and r.get('dev') == lan):
            continue
        try:
            net = ipaddress.ip_network(dst, strict=False)
        except ValueError:
            continue
        if net.version == 4:
            result.append(net)
    for link in links:
        if owned and link['ifname'] == lan:
            continue
        for a in link.get('addr_info', []):
            if a.get('family') == 'inet':
                result.append(ipaddress.ip_network(f"{a['local']}/{a['prefixlen']}", strict=False))
    return result


def choose_subnet(used, seed, requested=None):
    def suitable(net):
        return (net.version == 4 and net.prefixlen == 24 and any(net.subnet_of(n) for n in PRIVATE)
                and not any(net.overlaps(n) for n in used))
    if requested:
        net = ipaddress.ip_network(requested)
        if not suitable(net):
            raise SetupError('LAN must be an unused RFC1918 /24, without any route/address overlap')
        return net
    offset = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], 'big')
    for pool in PRIVATE:
        count = 1 << (24 - pool.prefixlen)
        for i in range(count):
            net = ipaddress.ip_network((int(pool.network_address) + ((offset + i) % count) * 256, 24))
            if suitable(net):
                return net
    raise SetupError('No unused private /24; resolve route conflicts before setup')


def network_backend(lan):
    nm = active('NetworkManager')
    nd = active('systemd-networkd')
    if nm and nd:
        raise SetupError('Both NetworkManager and networkd are active; resolve interface ownership first')
    if active('dhcpcd') or active('networking') or active('connman'):
        raise SetupError('Legacy/competing network manager detected; migrate to NetworkManager or networkd')
    if nm:
        if run('nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', lan).stdout.strip() != 'yes':
            raise SetupError('LAN is not managed by NetworkManager')
        return 'NetworkManager'
    if nd:
        return 'networkd'
    raise SetupError('Requires an active NetworkManager or systemd-networkd')


def allocate_ports(preferred):
    allocated = {}
    sockets = []
    try:
        for key, first in preferred.items():
            for port in range(first, 65536):
                attempts = []
                try:
                    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                        handle = socket.socket(socket.AF_INET, kind)
                        attempts.append(handle)
                        handle.bind(('0.0.0.0', port))
                    sockets.extend(attempts)
                    allocated[key] = port
                    break
                except OSError:
                    for handle in attempts:
                        handle.close()
            else:
                raise SetupError('No free service ports')
    finally:
        for handle in sockets:
            handle.close()
    return allocated


def make_config(plan, defaults, url, secret):
    address, ports, lan = plan['address'], plan['ports'], plan['lan']
    health = {'enable': True, 'url': defaults['health_url'], 'interval': defaults['health_interval']}
    providers = {'airport': {
        'type': 'http', 'url': check_url(url), 'path': './providers/airport.yaml',
        'interval': defaults['subscription_interval'], 'proxy': 'DIRECT', 'size-limit': 10485760,
        'header': {'User-Agent': ['mihomo/' + defaults['mihomo_version'].lstrip('v')]},
        'health-check': health,
        'exclude-filter': '(?i)剩余流量|套餐到期|更新订阅|traffic.*remaining|expire',
        'exclude-type': 'direct|reject|reject-drop|pass|compatible',
    }}
    groups = [{'name': 'Auto', 'type': 'url-test', 'use': ['airport'],
               'url': defaults['health_url'], 'interval': defaults['health_interval'], 'tolerance': 80},
              {'name': 'Proxy', 'type': 'select', 'proxies': ['Auto', 'DIRECT'], 'use': ['airport']}]
    for service in defaults['services']:
        groups.append({'name': service, 'type': 'select', 'proxies': ['Auto', 'Proxy', 'DIRECT'], 'use': ['airport']})
    rules = [f'IP-CIDR,{n},DIRECT,no-resolve' for n in ('127.0.0.0/8', '169.254.0.0/16',
             '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/4')]
    for service, domains in defaults['services'].items():
        rules.extend(f'DOMAIN-SUFFIX,{domain},{service}' for domain in domains)
    rules += ['RULE-SET,cn,DIRECT', 'RULE-SET,cn-ip,DIRECT,no-resolve', 'MATCH,Proxy']
    return {
        'mode': 'rule', 'log-level': 'warning', 'ipv6': False,
        'mixed-port': ports['mixed'], 'bind-address': address, 'allow-lan': True,
        'lan-allowed-ips': [plan['subnet']], 'external-controller': f"{address}:{ports['controller']}",
        'secret': secret, 'interface-name': plan['wan'],
        'profile': {'store-selected': True},
        'tun': {'enable': True, 'device': 'MihomoGW', 'stack': 'mixed', 'auto-route': True,
                'auto-redirect': True, 'strict-route': True, 'include-interface': [lan],
                'dns-hijack': ['any:53', 'tcp://any:53']},
        'dns': {'enable': True, 'listen': f"127.0.0.1:{ports['dns']}", 'ipv6': False,
                'enhanced-mode': 'fake-ip', 'fake-ip-range': '198.18.0.1/16',
                'fake-ip-filter': ['*.lan', '*.local', 'localhost'],
                'default-nameserver': defaults['bootstrap_dns'],
                'proxy-server-nameserver': defaults['direct_dns'],
                'nameserver': defaults['proxy_dns'],
                'nameserver-policy': {'rule-set:cn': defaults['direct_dns']}},
        'proxy-providers': providers, 'proxy-groups': groups,
        'rule-providers': {name: {'type': 'http', 'behavior': spec[1], 'format': 'mrs',
            'path': f'./rules/{name}.mrs', 'url': defaults['rule_base'] + '/' + spec[0],
            'interval': defaults['subscription_interval'], 'proxy': 'DIRECT'}
            for name, spec in defaults['rule_sets'].items()},
        'rules': rules,
    }


def make_files(plan, config, connection_uuid):
    lan, wan, addr = plan['lan'], plan['wan'], plan['address']
    net = ipaddress.ip_network(plan['subnet'])
    # An accepting filter does not override a competing firewall's DROP; preflight rejects those.
    firewall = f'''table inet mihomo_gateway {{
  chain input {{ type filter hook input priority -5; policy accept;
    iifname "{lan}" accept
  }}
  chain forward {{ type filter hook forward priority -5; policy accept;
    iifname "{lan}" oifname "{wan}" ip saddr {net} accept
  }}
  chain postrouting {{ type nat hook postrouting priority srcnat; policy accept;
    ip saddr {net} oifname "{wan}" masquerade
  }}
}}
'''
    dhcp = f'''interface={lan}
listen-address={addr}
bind-dynamic
dhcp-authoritative
dhcp-range={net[50]},{net[250]},{net.netmask},12h
dhcp-option=3,{addr}
dhcp-option=6,{addr}
no-resolv
server=127.0.0.1#{plan['ports']['dns']}
cache-size=0
dhcp-leasefile=/var/lib/mihomo-gateway/dnsmasq.leases
'''
    if plan['backend'] == 'NetworkManager':
        network_path = '/etc/NetworkManager/system-connections/mihomo-gateway-lan.nmconnection'
        network = f'''[connection]
id=mihomo-gateway-lan
uuid={connection_uuid}
type=ethernet
interface-name={lan}
autoconnect=true
autoconnect-priority=999
[ipv4]
method=manual
address1={addr}/{net.prefixlen}
never-default=true
ignore-auto-dns=true
[ipv6]
method=disabled
'''
    else:
        network_path = '/etc/systemd/network/00-mihomo-gateway-lan.network'
        network = f'''[Match]
Name={lan}
[Network]
Address={addr}/{net.prefixlen}
DHCP=no
IPv6AcceptRA=no
LinkLocalAddressing=no
ConfigureWithoutCarrier=yes
'''
    firewall_script = '''#!/bin/sh
set -eu
case "${1:-}" in
 start)
  if nft list table inet mihomo_gateway >/dev/null 2>&1; then
    { echo 'delete table inet mihomo_gateway'; cat /etc/mihomo-gateway/nftables.conf; } | nft -f -
  else
    nft -f /etc/mihomo-gateway/nftables.conf
  fi ;;
 stop) nft delete table inet mihomo_gateway 2>/dev/null || : ;;
 *) exit 2 ;;
esac
'''
    unit_prefix = '[Unit]\nAfter=network-online.target\nWants=network-online.target\n'
    files = {
        '/etc/mihomo-gateway/config.yaml': json_text(config),
        '/etc/mihomo-gateway/nftables.conf': firewall,
        '/etc/mihomo-gateway/dnsmasq.conf': dhcp,
        network_path: network,
        '/opt/mihomo-gateway/firewall.sh': firewall_script,
        '/etc/systemd/system/mihomo-gateway-firewall.service': unit_prefix + '''
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/mihomo-gateway/firewall.sh start
ExecStop=/opt/mihomo-gateway/firewall.sh stop
[Install]
WantedBy=multi-user.target
''',
        '/etc/systemd/system/mihomo-gateway-dhcp.service': unit_prefix + f'''
After=mihomo-gateway-firewall.service
[Service]
Type=simple
ExecStart={shutil.which('dnsmasq') or '/usr/sbin/dnsmasq'} --keep-in-foreground --user=root --conf-file=/etc/mihomo-gateway/dnsmasq.conf
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
''',
        '/etc/systemd/system/mihomo-gateway.service': unit_prefix + '''
After=mihomo-gateway-firewall.service mihomo-gateway-dhcp.service
[Service]
Type=simple
UMask=0077
ExecStartPre=/opt/mihomo-gateway/mihomo -t -d /etc/mihomo-gateway
ExecStart=/opt/mihomo-gateway/mihomo -d /etc/mihomo-gateway
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
''',
    }
    return files, network_path


def validate_provider(binary, directory, config, timeout=40):
    """Start the real core without TUN/DNS/routes and verify the parsed node count."""
    probe = copy.deepcopy(config)
    probe['tun'] = {'enable': False}
    probe['dns'] = {'enable': False}
    probe['mixed-port'] = 0
    probe['allow-lan'] = False
    probe['bind-address'] = '127.0.0.1'
    probe.pop('interface-name', None)
    with socket.socket() as handle:
        handle.bind(('127.0.0.1', 0))
        port = handle.getsockname()[1]
    probe['external-controller'] = f'127.0.0.1:{port}'
    # Use the downloaded cache: startup validation must not refetch a subscription.
    for provider in probe['proxy-providers'].values():
        provider['type'] = 'file'
        provider.pop('url', None)
        provider['health-check']['enable'] = False
    for provider in probe['rule-providers'].values():
        provider['type'] = 'file'
        provider.pop('url', None)
    for group in probe['proxy-groups']:
        if group['type'] == 'url-test':
            group['type'] = 'select'
    path = Path(directory) / 'probe.json'
    write(path, json_text(probe))
    process = subprocess.Popen([str(binary), '-d', str(directory), '-f', str(path)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise SetupError('Mihomo rejected subscription or rule data (private output withheld)')
            try:
                data = api(f'127.0.0.1:{port}', config['secret'], '/providers/proxies/airport')
                proxies = data.get('proxies', [])
                usable = [p for p in proxies if p.get('type', '').lower()
                          not in ('direct', 'reject', 'rejectdrop', 'compatible', 'pass')]
                if usable:
                    return len(usable)
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        raise SetupError('Subscription contains no usable nodes or provider initialization timed out')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        path.unlink(missing_ok=True)


def api(controller, secret, path):
    request = urllib.request.Request('http://' + controller + path,
                                     headers={'Authorization': 'Bearer ' + secret})
    # Do not send loopback/controller secrets through inherited HTTP proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        return json.load(response)


def dns_probe(address, port):
    query_id = os.urandom(2)
    question = b'\x07example\x03com\x00' + struct.pack('!HH', 1, 1)
    packet = query_id + struct.pack('!HHHHH', 0x100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as handle:
        handle.settimeout(3)
        handle.sendto(packet, (address, port))
        response, _ = handle.recvfrom(4096)
    if len(response) < 12 or response[:2] != query_id:
        raise SetupError('DNS response is invalid')
    flags, _, answers = struct.unpack('!HHH', response[2:8])
    if not flags & 0x8000 or flags & 0xF or not answers:
        raise SetupError('DNS did not return a successful answer')


def existing_firewall_conflict(ruleset):
    """Another base chain can drop traffic after ours accepts it; never flush it."""
    for item in ruleset.get('nftables', []):
        for kind in ('chain', 'rule'):
            data = item.get(kind, {})
            if data.get('table') == 'mihomo_gateway':
                continue
            if data.get('policy') == 'drop':
                return True
            if kind == 'rule' and any(k in json.dumps(data.get('expr', []))
                                      for k in ('"drop"', '"reject"')):
                return True
    return False


def preflight_network(plan, owned):
    for service in ('mihomo', 'clash', 'sing-box', 'firewalld', 'ufw', 'dnsmasq'):
        if active(service):
            raise SetupError(f'Existing {service} service conflicts; migrate it before automatic setup')
    rules = json.loads(run('nft', '-j', 'list', 'ruleset').stdout)
    if not owned and any(item.get('table', {}).get('name') == 'mihomo_gateway' for item in rules.get('nftables', [])):
        raise SetupError('Unmanaged nftables table already uses the gateway name')
    if existing_firewall_conflict(rules):
        raise SetupError('Existing firewall DROP/REJECT rules require manual integration; nothing flushed')
    if shutil.which('iptables-save'):
        old = run('iptables-save', check=False).stdout
        if re.search(r'^:(?:INPUT|FORWARD|OUTPUT) DROP|^-A .* -j (?:DROP|REJECT)', old, re.M):
            raise SetupError('Legacy iptables filtering requires manual integration')
    ssh = os.environ.get('SSH_CONNECTION', '').split()
    if ssh:
        route = json.loads(run('ip', '-j', 'route', 'get', ssh[0]).stdout)
        if not route or route[0].get('dev') == plan['lan']:
            raise SetupError('SSH uses the selected LAN; reconnect through the uplink or use a local console')
    if not Path('/dev/net/tun').exists():
        raise SetupError('/dev/net/tun is unavailable; enable the host TUN device first')
    if not owned:
        for path in (CONFIG, APP, *(Path('/etc/systemd/system') / name for name in UNITS)):
            if path.exists() or path.is_symlink():
                raise SetupError('Unmanaged files already occupy gateway paths; migrate them before setup')
    # Other DNS/DHCP daemons must not already own the downstream sockets.
    ports = run('ss', '-H', '-lunt').stdout.splitlines()
    for row in ports:
        fields = row.split()
        if len(fields) < 5:
            continue
        endpoint = fields[4]
        host, _, port = endpoint.rpartition(':')
        if port in ('53', '67') and host in ('*', '0.0.0.0', '[::]', plan['address']):
            if not owned or not active('mihomo-gateway-dhcp'):
                raise SetupError('Another DNS/DHCP listener occupies the downstream address')


def download_inputs(directory, defaults, url):
    directory = Path(directory)
    version = defaults['mihomo_version']
    spec = defaults['assets'].get(platform.machine())
    if not spec:
        raise SetupError('Supported architectures: ARM64, ARMv7 and x86-64')
    asset = f"mihomo-linux-{spec['arch']}-{version}.gz"
    archive = directory / 'mihomo.gz'
    fetch(f"{defaults['release_base']}/{version}/{asset}", archive, 64 * 1024 * 1024)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != spec['sha256']:
        raise SetupError('Official Mihomo asset SHA256 mismatch')
    binary = directory / 'mihomo'
    with gzip.open(archive, 'rb') as source, binary.open('xb') as output:
        shutil.copyfileobj(source, output)
    binary.chmod(0o700)
    run(binary, '-v')
    (directory / 'providers').mkdir(mode=0o700)
    fetch(url, directory / 'providers/airport.yaml', 10485760,
          'mihomo/' + version.lstrip('v'))
    (directory / 'rules').mkdir(mode=0o700)
    for name, spec in defaults['rule_sets'].items():
        fetch(defaults['rule_base'] + '/' + spec[0], directory / 'rules' / (name + '.mrs'))
    return binary


def sysctl_values(plan):
    return {
        'net/ipv4/ip_forward': '1',
        'net/ipv4/conf/all/rp_filter': '0',
        'net/ipv4/conf/' + plan['lan'] + '/rp_filter': '0',
        'net/ipv4/conf/' + plan['wan'] + '/rp_filter': '0',
        'net/ipv6/conf/' + plan['lan'] + '/disable_ipv6': '1',
    }


def snapshot(paths, backup):
    """Copy only owned destinations; absent paths are recorded for first-install rollback."""
    backup.mkdir(parents=True, mode=0o700)
    records = []
    for index, path in enumerate(paths):
        path = Path(path)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise SetupError('Refusing symlinks in managed destinations')
        record = {'path': str(path), 'saved': str(index), 'kind': 'absent'}
        if path.is_dir():
            if any(p.is_symlink() for p in path.rglob('*')):
                raise SetupError('Refusing symlinks inside managed directories')
            shutil.copytree(path, backup / str(index))
            record['kind'] = 'directory'
        elif path.exists():
            if not path.is_file():
                raise SetupError('Unsupported managed destination type')
            shutil.copy2(path, backup / str(index))
            record['kind'] = 'file'
        records.append(record)
    return records


def restore_files(records, backup):
    for record in reversed(records):
        target = Path(record['path'])
        if target.is_symlink():
            raise SetupError('Refusing changed symlink during rollback')
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        if record['kind'] == 'directory':
            shutil.copytree(backup / record['saved'], target)
        elif record['kind'] == 'file':
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / record['saved'], target)


def backup_install(plan, paths):
    backup = STATE / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    records = snapshot(paths, backup)
    states = {}
    for name in UNITS:
        states[name] = {'active': active(name),
                        'enabled': run('systemctl', 'is-enabled', name, check=False).stdout.strip()}
    old_connection = ''
    if plan['backend'] == 'NetworkManager':
        old_connection = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', plan['lan']).stdout.strip()
        if old_connection == '--':
            old_connection = ''
    old_lan = json.loads(run('ip', '-j', 'address', 'show', 'dev', plan['lan']).stdout)[0]
    manifest = {'files': records, 'services': states, 'plan': plan, 'old_lan': old_lan,
                'old_connection': old_connection, 'pending': True,
                'sysctl': {key: run('sysctl', '-n', key).stdout.strip() for key in sysctl_values(plan)}}
    write(backup / 'manifest.json', json_text(manifest))
    shutil.copy2(Path(__file__), backup / 'restore.py')
    return backup


def stop_watchdog():
    run('systemctl', 'disable', '--now', 'mihomo-gateway-rollback.timer', check=False)
    for suffix in ('timer', 'service'):
        Path('/etc/systemd/system/mihomo-gateway-rollback.' + suffix).unlink(missing_ok=True)
    (STATE / 'pending').unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')


def arm_watchdog(backup, seconds):
    # Persistent timer also handles a reboot between activation and commit.
    write(STATE / 'pending', str(backup) + '\n')
    write(Path('/etc/systemd/system/mihomo-gateway-rollback.service'), f'''[Unit]
Description=Recover an interrupted Mihomo gateway installation
After=network.target
[Service]
Type=oneshot
ExecStart={sys.executable} {backup}/restore.py --watchdog {backup}
''', 0o644)
    write(Path('/etc/systemd/system/mihomo-gateway-rollback.timer'), f'''[Unit]
Description=Rollback uncommitted Mihomo gateway configuration
[Timer]
OnActiveSec={seconds}s
AccuracySec=1s
[Install]
WantedBy=timers.target
''', 0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'mihomo-gateway-rollback.timer')


def restore_backup(backup, watchdog=False):
    backup = Path(backup).resolve()
    if backup.parent != (STATE / 'backups').resolve():
        raise SetupError('Rollback must name a backup under /var/lib/mihomo-gateway/backups')
    manifest = json.loads((backup / 'manifest.json').read_text())
    if watchdog and not manifest['pending']:
        pending = STATE / 'pending'
        if pending.exists() and Path(pending.read_text().strip()).resolve() == backup:
            stop_watchdog()
        return
    print('Restoring gateway backup...', flush=True)
    for name in UNITS:
        if active(name):
            run('systemctl', 'stop', name)
        run('systemctl', 'disable', name, check=False)
    restore_files(manifest['files'], backup)
    run('systemctl', 'daemon-reload')
    plan = manifest['plan']
    old_lan = manifest['old_lan']
    old_addresses = {f"{a['local']}/{a['prefixlen']}" for a in old_lan.get('addr_info', [])}
    new_address = plan['address'] + '/24'
    if new_address not in old_addresses:
        run('ip', 'address', 'del', new_address, 'dev', plan['lan'], check=False)
    if plan['backend'] == 'NetworkManager':
        run('nmcli', 'connection', 'reload')
        if manifest['old_connection']:
            run('nmcli', 'connection', 'up', 'uuid', manifest['old_connection'])
        else:
            run('nmcli', 'device', 'disconnect', plan['lan'], check=False)
    else:
        run('networkctl', 'reload')
        # A previously unconfigured port has no .network file after rollback.
        run('networkctl', 'reconfigure', plan['lan'], check=False)
    for key, value in manifest['sysctl'].items():
        run('sysctl', '-w', f'{key}={value}')
    if not manifest['old_connection']:
        for address in old_lan.get('addr_info', []):
            run('ip', 'address', 'replace', f"{address['local']}/{address['prefixlen']}", 'dev', plan['lan'])
        run('ip', 'link', 'set', 'dev', plan['lan'], 'up' if 'UP' in old_lan['flags'] else 'down')
    for name in reversed(UNITS):
        state = manifest['services'][name]
        if state['enabled'] == 'enabled':
            run('systemctl', 'enable', name)
        elif state['enabled'] == 'enabled-runtime':
            run('systemctl', 'enable', '--runtime', name)
        if state['active']:
            run('systemctl', 'start', name)
    manifest['pending'] = False
    write(backup / 'manifest.json', json_text(manifest))
    stop_watchdog()
    print('Previous managed files, LAN connection and service/sysctl state restored.', flush=True)


def health_check(plan, config):
    for name in UNITS:
        if not active(name):
            raise SetupError('A gateway service failed to start')
    address = plan['address']
    links, routes = inventory()
    lan = next((x for x in links if x['ifname'] == plan['lan']), {})
    if not any(a.get('local') == address for a in lan.get('addr_info', [])):
        raise SetupError('LAN address did not activate')
    if not any(r.get('dst') == 'default' and r.get('dev') == plan['wan']
               and r.get('table', 'main') in ('main', 254) for r in routes):
        raise SetupError('Uplink default route disappeared')
    controller = config['external-controller']
    providers = api(controller, config['secret'], '/providers/proxies/airport')
    if not providers.get('proxies'):
        raise SetupError('Running provider is empty')
    dns_probe(address, 53)
    # Local proxy reachability is only a host check, not a downstream-client acceptance.
    result = run('curl', '-q', '--fail', '--silent', '--connect-timeout', '5', '--max-time', '20',
                 '--proxy', f"http://{address}:{plan['ports']['mixed']}",
                 '--noproxy', '', 'https://www.gstatic.com/generate_204', check=False, timeout=25)
    if result.returncode:
        raise SetupError('No working proxied HTTPS path; restoring the previous installation')


def apply_install(plan, defaults, config, url, directory, files, network_path, connection_uuid):
    sysctl_path = Path('/etc/sysctl.d/90-mihomo-gateway.conf')
    values = sysctl_values(plan)
    files[str(sysctl_path)] = ''.join(f'{key}={value}\n' for key, value in values.items())
    targets = [CONFIG, APP, STATE / 'installed.json', Path(network_path), sysctl_path,
               *(Path('/etc/systemd/system') / name for name in UNITS)]
    backup = backup_install(plan, targets)
    print(f'Backup: {backup}', flush=True)
    previous_alarm = signal.signal(signal.SIGALRM, interrupted)
    try:
        arm_watchdog(backup, defaults['rollback_seconds'])
        signal.alarm(defaults['rollback_seconds'])
        for name in UNITS:
            if active(name):
                run('systemctl', 'stop', name)
        for destination, content in files.items():
            mode = 0o644 if destination.endswith(('.service', '.conf', '.network')) else 0o600
            if destination.endswith('.sh'):
                mode = 0o755
            write(Path(destination), content, mode)
        for name in ('providers', 'rules'):
            destination = CONFIG / name
            destination.mkdir(exist_ok=True, mode=0o700)
            for source in (Path(directory) / name).iterdir():
                shutil.copy2(source, destination / source.name)
        APP.mkdir(exist_ok=True, mode=0o700)
        shutil.copy2(Path(directory) / 'mihomo', APP / 'mihomo')
        (APP / 'mihomo').chmod(0o755)
        if Path(__file__).resolve() != (APP / 'setup.py').resolve():
            shutil.copy2(Path(__file__), APP / 'setup.py')
        write(APP / 'defaults.json', json_text(defaults))
        write(CONFIG / 'subscription.url', url + '\n')
        write(CONFIG / 'controller.secret', config['secret'] + '\n')
        run('systemctl', 'daemon-reload')
        if plan['backend'] == 'NetworkManager':
            run('nmcli', 'connection', 'reload')
            run('nmcli', 'connection', 'up', 'uuid', connection_uuid)
        else:
            run('networkctl', 'reload')
            run('networkctl', 'reconfigure', plan['lan'])
        for key, value in values.items():
            run('sysctl', '-w', f'{key}={value}')
        for name in reversed(UNITS):
            run('systemctl', 'enable', '--now', name)
        deadline = time.monotonic() + 35
        while True:
            try:
                health_check(plan, config)
                break
            except (SetupError, OSError, ValueError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    raise SetupError('Gateway health checks did not pass before the deadline') from None
                time.sleep(2)
        receipt = {'plan': plan, 'connection_uuid': connection_uuid, 'backup': str(backup),
                   'version': defaults['mihomo_version']}
        write(STATE / 'installed.json', json_text(receipt))
        manifest = json.loads((backup / 'manifest.json').read_text())
        manifest['pending'] = False
        write(backup / 'manifest.json', json_text(manifest))
        stop_watchdog()
    except BaseException:
        signal.alarm(0)
        try:
            restore_backup(backup)
        except BaseException:
            print(f'Rollback incomplete. Recover with: sudo python3 {backup}/restore.py --rollback {backup}', file=sys.stderr)
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_alarm)
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='detect and print plan; no downloads, prompts or writes')
    mode.add_argument('--rollback', metavar='BACKUP', help='restore a backup path, or latest')
    mode.add_argument('--watchdog', metavar='BACKUP', help=argparse.SUPPRESS)
    parser.add_argument('--subscription-file', type=Path, help='read HTTPS subscription from a private file')
    parser.add_argument('--wan', help='override ambiguous uplink detection')
    parser.add_argument('--lan', help='override ambiguous downstream detection')
    parser.add_argument('--lan-cidr', help='override automatic private /24 selection')
    parser.add_argument('--defaults', type=Path, default=HERE / 'defaults.json', help='policy/dependency defaults JSON')
    args = parser.parse_args(argv)
    if platform.system() != 'Linux' or not Path('/run/systemd/system').is_dir():
        raise SetupError('Requires Linux with systemd; no host changes made')
    if sys.version_info < (3, 9):
        raise SetupError('Python 3.9 or newer is required')
    if os.geteuid() != 0:
        raise SetupError('Run sudo sh setup.sh on the intended gateway')
    if args.dry_run:
        return prepare(args, dry=True)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.rollback or args.watchdog:
            backup = args.rollback or args.watchdog
            if backup == 'latest':
                receipt = STATE / 'installed.json'
                if not receipt.exists():
                    raise SetupError('No completed installation to roll back')
                backup = json.loads(receipt.read_text())['backup']
            return restore_backup(backup, watchdog=bool(args.watchdog))
        if (STATE / 'pending').exists():
            restore_backup((STATE / 'pending').read_text().strip(), watchdog=True)
        return prepare(args, dry=False)


def prepare(args, dry):
    defaults = json.loads(args.defaults.read_text())
    if platform.machine() not in defaults['assets']:
        raise SetupError('Supported architectures: ARM64, ARMv7 and x86-64')
    manager = package_manager()
    required = ('ip', 'ss', 'nft', 'dnsmasq', 'curl', 'sysctl')
    missing = [name for name in required if not shutil.which(name)]
    if not any(Path(p).is_file() for p in ('/etc/ssl/certs/ca-certificates.crt',
                                          '/etc/pki/tls/certs/ca-bundle.crt',
                                          '/etc/ssl/cert.pem', '/etc/ssl/ca-bundle.pem')):
        missing.append('ca-certificates')
    if missing and not dry:
        install_dependencies(manager)
    elif missing:
        print('Would install dependencies: ' + ', '.join(missing))
    links, routes = inventory()
    previous_path = STATE / 'installed.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    wan, lan = choose_interfaces(links, routes, args.wan, args.lan)
    if any(link.get('tunnel') and not (previous and link['ifname'] == 'MihomoGW') for link in links):
        raise SetupError('Existing VPN/TUN interface conflicts; stop it before automatic gateway setup')
    if previous and previous['plan']['lan'] != lan:
        raise SetupError('Existing gateway owns another LAN; roll it back before changing topology')
    backend = network_backend(lan)
    requested = args.lan_cidr or (previous['plan']['subnet'] if previous else None)
    seed = Path('/etc/machine-id').read_text().strip() if Path('/etc/machine-id').exists() else socket.gethostname()
    subnet = choose_subnet(used_networks(links, routes, lan, owned=bool(previous)), seed, requested)
    ports = previous['plan']['ports'] if previous else allocate_ports(defaults['ports'])
    plan = {'wan': wan, 'lan': lan, 'subnet': str(subnet), 'address': str(subnet[1]),
            'ports': ports, 'backend': backend, 'package_manager': manager}
    print(json_text(plan), flush=True)
    if dry:
        print('Detection only. Full TUN/firewall/socket and subscription checks run before activation.')
        return plan
    preflight_network(plan, owned=bool(previous))
    if args.subscription_file:
        if args.subscription_file.stat().st_mode & 0o077:
            raise SetupError('Subscription file must be private (chmod 600)')
        url = args.subscription_file.read_text().strip()
    elif (CONFIG / 'subscription.url').exists():
        url = (CONFIG / 'subscription.url').read_text().strip()
    else:
        if not sys.stdin.isatty():
            raise SetupError('Interactive terminal required, or pass --subscription-file PATH')
        url = getpass.getpass('Airport subscription URL (hidden): ').strip()
    check_url(url)
    connection_uuid = previous['connection_uuid'] if previous else str(uuid.uuid4())
    secret = (CONFIG / 'controller.secret').read_text().strip() if previous else os.urandom(32).hex()
    if not secret:
        raise SetupError('Existing controller secret is empty; restore it before reinstalling')
    config = make_config(plan, defaults, url, secret)
    with tempfile.TemporaryDirectory(prefix='mihomo-gateway-', dir=STATE) as raw:
        directory = Path(raw)
        print('Downloading verified Mihomo core, subscription and routing data...', flush=True)
        binary = download_inputs(directory, defaults, url)
        count = validate_provider(binary, directory, config)
        print(f'Subscription validated by Mihomo: {count} usable nodes.', flush=True)
        files, network_path = make_files(plan, config, connection_uuid)
        write(directory / 'config.yaml', json_text(config))
        run(binary, '-t', '-d', directory)
        write(directory / 'dnsmasq.conf', files[str(CONFIG / 'dnsmasq.conf')])
        run('dnsmasq', '--test', '--conf-file=' + str(directory / 'dnsmasq.conf'))
        nft = files[str(CONFIG / 'nftables.conf')]
        if run('nft', 'list', 'table', 'inet', 'mihomo_gateway', check=False).returncode == 0:
            nft = 'delete table inet mihomo_gateway\n' + nft
        run('nft', '-c', '-f', '-', input=nft)
        apply_install(plan, defaults, config, url, directory, files, network_path, connection_uuid)
    print(f"Gateway installed. LAN {lan}: {plan['address']}/24; connect clients to this interface.")
    print('Host DNS/proxy checks passed. Verify DHCP, DNS and TCP/UDP from a real downstream client.')
    print('Rollback: sudo python3 /opt/mihomo-gateway/setup.py --rollback latest')


def interrupted(_number, _frame):
    raise SetupError('Installation interrupted')


if __name__ == '__main__':
    os.umask(0o077)
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        main()
    except (SetupError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        # Parser/library exceptions may embed subscription data, so only our own messages are shown.
        message = str(error) if isinstance(error, SetupError) else type(error).__name__ + ' during setup; private details withheld'
        print('error: ' + message, file=sys.stderr)
        sys.exit(1)
