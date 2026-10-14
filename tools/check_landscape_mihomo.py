#!/usr/bin/env python3
"""Local Mihomo API/DNS acceptance only; does not test Landscape or TProxy.

Requires an explicitly supplied, trusted Mihomo binary. Uses synthetic rules,
loopback listeners, a local DNS authority and temporary state. No subscriptions,
system DNS, routes, TUN, containers or live gateway configuration are changed.
"""
import argparse
import contextlib
import hashlib
import http.server
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def free_port():
    with socket.socket() as handle:
        handle.bind(('127.0.0.1', 0))
        return handle.getsockname()[1]


def name_end(data, offset):
    while data[offset]:
        if data[offset] & 0xc0 == 0xc0:
            return offset + 2
        offset += data[offset] + 1
    return offset + 1


def receive(handle, size):
    result = b''
    while len(result) < size:
        part = handle.recv(size - len(result))
        if not part:
            raise RuntimeError('Unexpected EOF')
        result += part
    return result


class Authority(socketserver.BaseRequestHandler):
    def handle(self):
        data, handle = self.request
        end = name_end(data, 12) + 4
        query_type = struct.unpack('!H', data[end - 4:end - 2])[0]
        answer = (b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 30, 4)
                  + socket.inet_aton('127.0.0.1')) if query_type == 1 else b''
        header = data[:2] + struct.pack('!HHHHH', 0x8180, 1, bool(answer), 0, 0)
        handle.sendto(header + data[12:end] + answer, self.client_address)


class HeldHTTP(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.flush()
        self.server.release.wait(5)
        self.wfile.write(b'OK')

    def log_message(self, *args):
        pass


def query_dns(port, name, tcp=False):
    identity = secrets.token_bytes(2)
    encoded = b''.join(bytes([len(x)]) + x.encode() for x in name.split('.')) + b'\0'
    packet = identity + struct.pack('!HHHHH', 0x100, 1, 0, 0, 0) + encoded + struct.pack('!HH', 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as handle:
        handle.settimeout(3)
        handle.connect(('127.0.0.1', port))
        if tcp:
            handle.sendall(struct.pack('!H', len(packet)) + packet)
            data = receive(handle, struct.unpack('!H', receive(handle, 2))[0])
        else:
            handle.send(packet)
            data = handle.recv(4096)
    assert data[:2] == identity and data[3] & 15 == 0, 'DNS response failed'
    offset = name_end(data, 12) + 4
    for _ in range(struct.unpack('!H', data[6:8])[0]):
        offset = name_end(data, offset)
        kind, _, _, size = struct.unpack('!HHIH', data[offset:offset + 10])
        offset += 10
        if kind == 1 and size == 4:
            return socket.inet_ntoa(data[offset:offset + size])
        offset += size
    raise AssertionError('No A answer')


def run(binary):
    checks = []
    version = subprocess.check_output([str(binary), '-v'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='mihomo-acceptance-') as raw, contextlib.ExitStack() as stack:
        directory = Path(raw)
        authority = socketserver.ThreadingUDPServer(('127.0.0.1', 0), Authority)
        web_server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HeldHTTP)
        web_server.release = threading.Event()
        for server in (authority, web_server):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            stack.callback(server.server_close)
            stack.callback(server.shutdown)
        controller, mixed, dns = free_port(), free_port(), free_port()
        assert len({controller, mixed, dns}) == 3, 'Port collision; rerun'
        token = secrets.token_hex(32)
        provider = directory / 'provider.json'
        provider_data = {'proxies': [{'name': 'Fixture Node A', 'type': 'http',
                                     'server': '127.0.0.1', 'port': web_server.server_port}]}
        provider.write_text(json.dumps(provider_data))
        (directory / 'domains.yaml').write_text("payload:\n  - '+.set.test'\n")
        config = {
            'mixed-port': mixed, 'bind-address': '127.0.0.1', 'allow-lan': False,
            'external-controller': f'127.0.0.1:{controller}', 'secret': token,
            'mode': 'rule', 'log-level': 'info', 'ipv6': False,
            'tun': {'enable': False}, 'find-process-mode': 'off',
            'profile': {'store-selected': True, 'store-fake-ip': True},
            'dns': {'enable': True, 'listen': f'127.0.0.1:{dns}', 'ipv6': False,
                    'enhanced-mode': 'fake-ip', 'fake-ip-range': '198.18.0.1/16',
                    'fake-ip-filter': [], 'use-hosts': False, 'use-system-hosts': False,
                    'nameserver': [f'udp://127.0.0.1:{authority.server_address[1]}']},
            'proxy-providers': {'fixture': {'type': 'file', 'path': './provider.json',
                                            'health-check': {'enable': False}}},
            'proxy-groups': [{'name': 'Fixture Select', 'type': 'select',
                              'proxies': ['DIRECT', 'REJECT'], 'use': ['fixture']}],
            'rule-providers': {'fixture-domains': {'type': 'file', 'behavior': 'domain',
                                                  'format': 'yaml', 'path': './domains.yaml'}},
            'rules': ['DOMAIN,route.fixture.test,Fixture Select',
                      'RULE-SET,fixture-domains,Fixture Select', 'MATCH,REJECT'],
        }
        config_path = directory / 'config.json'
        config_path.write_text(json.dumps(config))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def api(path, method='GET', body=None, secret=token):
            request = urllib.request.Request(f'http://127.0.0.1:{controller}' + path,
                data=None if body is None else json.dumps(body).encode(), method=method,
                headers={'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'})
            with opener.open(request, timeout=3) as response:
                data = response.read()
                return json.loads(data) if data else None

        process = None
        log = stack.enter_context((directory / 'core.log').open('wb'))

        def stop():
            nonlocal process
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process = None

        stack.callback(stop)

        def start():
            nonlocal process
            process = subprocess.Popen([str(binary), '-d', str(directory), '-f', str(config_path)],
                                       stdout=log, stderr=log)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Fixture core exited: ' + (directory / 'core.log').read_text()[-2500:])
                try:
                    api('/version')
                    return
                except (OSError, ValueError):
                    time.sleep(0.1)
            raise RuntimeError('Fixture API readiness timed out')

        start()
        assert api('/configs')['tun']['enable'] is False
        checks.append('isolated_core_started_without_tun')
        try:
            api('/configs', secret='invalid-fixture-token')
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError('Controller accepted invalid credential')
        checks.append('controller_rejects_invalid_secret')
        group_path = '/proxies/' + urllib.parse.quote('Fixture Select', safe='')
        rules = api('/rules')['rules']
        assert [r['type'] for r in rules] == ['Domain', 'RuleSet', 'Match']
        assert [r['proxy'] for r in rules] == ['Fixture Select', 'Fixture Select', 'REJECT']
        assert api('/providers/rules')['providers']['fixture-domains']['ruleCount'] == 1
        checks.append('synthetic_rules_and_rule_provider_loaded')
        api(group_path, 'PUT', {'name': 'REJECT'})
        assert api(group_path)['now'] == 'REJECT'
        checks.append('selector_write_readback')
        provider_data['proxies'][0]['name'] = 'Fixture Node B'
        provider.write_text(json.dumps(provider_data))
        api('/providers/proxies/fixture', 'PUT')
        assert [p['name'] for p in api('/providers/proxies/fixture')['proxies']] == ['Fixture Node B']
        checks.append('file_provider_update_readback')
        first = query_dns(dns, 'route.fixture.test')
        assert ipaddress.ip_address(first) in ipaddress.ip_network('198.18.0.0/16')
        assert query_dns(dns, 'route.fixture.test', tcp=True) == first
        second = query_dns(dns, 'entry.set.test')
        assert second != first
        checks.append('mihomo_dns_udp_tcp_same_fake_ip')
        # Graceful restart only. This is not a power-loss persistence test.
        time.sleep(1)
        stop()
        start()
        assert api(group_path)['now'] == 'REJECT'
        # Reverse query order so deterministic fresh allocation cannot fake persistence.
        assert query_dns(dns, 'entry.set.test') == second
        assert query_dns(dns, 'route.fixture.test') == first
        checks.append('graceful_restart_preserves_selector_and_fake_ip')
        api(group_path, 'PUT', {'name': 'DIRECT'})
        for domain, expected_rule in [('route.fixture.test', 'Domain'), ('entry.set.test', 'RuleSet')]:
            fake_ip = query_dns(dns, domain)
            web_server.release.clear()
            try:
                with socket.create_connection(('127.0.0.1', mixed), timeout=3) as handle:
                    handle.sendall(b'\x05\x01\x00')
                    assert receive(handle, 2) == b'\x05\x00'
                    handle.sendall(b'\x05\x01\x00\x01' + socket.inet_aton(fake_ip)
                                   + struct.pack('!H', web_server.server_port))
                    header = receive(handle, 4)
                    assert header[:2] == b'\x05\x00', 'SOCKS connection failed'
                    size = {1: 4, 4: 16}.get(header[3])
                    if size is None:
                        assert header[3] == 3
                        size = receive(handle, 1)[0]
                    receive(handle, size + 2)
                    handle.sendall(b'GET / HTTP/1.1\r\nHost: fixture.test\r\nConnection: close\r\n\r\n')
                    assert receive(handle, 8) == b'HTTP/1.0'
                    connections = api('/connections')['connections']
                    matches = [c for c in connections if c['metadata'].get('host') == domain]
                    assert len(matches) == 1 and matches[0]['rule'] == expected_rule
                    assert 'Fixture Select' in matches[0]['chains']
                    web_server.release.set()
                    while handle.recv(4096):
                        pass
            except Exception as error:
                raise RuntimeError(f'{domain}: {error}; synthetic core log:\n'
                                   + (directory / 'core.log').read_text()[-3500:]) from error
            finally:
                web_server.release.set()
            checks.append('fake_ip_restores_domain_and_' + expected_rule + '_via_socks_only')
        try:
            api('/configs?force=true', 'PUT', {'payload': 'rules:\n  - UNKNOWN,REJECT\n'})
        except urllib.error.HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError('Invalid reload unexpectedly accepted')
        assert len(api('/rules')['rules']) == len(config['rules'])
        assert query_dns(dns, 'route.fixture.test') == first
        checks.append('invalid_reload_rejected_existing_rules_dns_survive')
        api('/configs', 'PATCH', {'log-level': 'warning'})
        assert api('/configs')['log-level'] == 'warning'
        api('/configs', 'PATCH', {'log-level': config['log-level']})
        assert api('/configs')['log-level'] == config['log-level']
        checks.append('runtime_setting_change_and_restore')
    return {'version': version, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'passed': checks, 'scope': 'synthetic loopback API/DNS/SOCKS only',
            'not_tested': ['live rule migration', 'Landscape DNS/API', 'Linux TProxy/eBPF',
                           'IPv6', 'proxy node connectivity', 'abrupt power loss', 'gateway rollback']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mihomo', required=True, type=Path)
    args = parser.parse_args()
    def terminate(signum, _frame):
        # Let ExitStack stop our child before TemporaryDirectory removes its state.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    # Generated secrets and caches stay private until the temporary tree is removed.
    os.umask(0o077)
    print(json.dumps(run(args.mihomo.resolve(strict=True)), ensure_ascii=False, indent=2))
