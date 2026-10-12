#!/usr/bin/env python3
"""Validate generated firewall/DHCP files inside a fresh Linux network namespace.

CI invocation: sudo python3 tools/check_setup_linux.py
This tests real parsers, nft application/idempotence and foreign-table preservation.
It does not claim a real downstream client or physical gateway acceptance.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if platform.system() != 'Linux' or os.geteuid() != 0:
        parser.error('requires root on a Linux test host')
    if not args.inside:
        run('unshare', '--net', sys.executable, Path(__file__).resolve(), '--inside')
        print('Linux namespace checks passed: nftables syntax, apply twice, stop and foreign-table preservation; dnsmasq syntax.')
        return
    if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        parser.error('refusing tests in the host network namespace')
    spec = importlib.util.spec_from_file_location('gateway_setup', REPO / 'gateway/setup.py')
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)
    defaults = json.loads((REPO / 'gateway/defaults.json').read_text())
    plan = {'wan': 'gwwan', 'lan': 'gwlan', 'backend': 'networkd', 'subnet': '10.77.0.0/24',
            'address': '10.77.0.1', 'ports': defaults['ports']}
    config = gw.make_config(plan, defaults, 'https://subscription.example.invalid/test', 'synthetic-secret')
    files, _ = gw.make_files(plan, config, 'synthetic-uuid')
    for name, address in [('gwwan', '192.0.2.1/24'), ('gwlan', '10.77.0.1/24')]:
        run('ip', 'link', 'add', name, 'type', 'dummy')
        run('ip', 'address', 'add', address, 'dev', name)
        run('ip', 'link', 'set', name, 'up')
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        nft, dhcp, script = root / 'nft.conf', root / 'dhcp.conf', root / 'firewall.sh'
        nft.write_text(files['/etc/mihomo-gateway/nftables.conf'])
        dhcp.write_text(files['/etc/mihomo-gateway/dnsmasq.conf'])
        script.write_text(files['/opt/mihomo-gateway/firewall.sh'].replace('/etc/mihomo-gateway/nftables.conf', str(nft)))
        run('nft', '-c', '-f', nft)
        run('dnsmasq', '--test', '--conf-file=' + str(dhcp))
        run('nft', 'add', 'table', 'inet', 'unrelated_test_table')
        for _ in range(2):
            run('sh', script, 'start')
            run('nft', 'list', 'table', 'inet', 'mihomo_gateway')
            run('nft', 'list', 'table', 'inet', 'unrelated_test_table')
        run('sh', script, 'stop')
        run('nft', 'list', 'table', 'inet', 'unrelated_test_table')
        result = subprocess.run(['nft', 'list', 'table', 'inet', 'mihomo_gateway'], capture_output=True)
        if result.returncode == 0:
            raise RuntimeError('gateway table survived stop')


if __name__ == '__main__':
    main()
