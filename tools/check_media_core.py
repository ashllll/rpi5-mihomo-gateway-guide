#!/usr/bin/env python3
"""Optional black-box hot-refresh check for a real Mihomo binary.

Set MIHOMO_TEST_BINARY to an isolated test binary. The fixture contains only
local controller traffic and reject proxies, so it cannot send external traffic.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import yaml

LOCAL_OPENER = build_opener(ProxyHandler({}))


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def request(address, path, method='GET'):
    req = Request('http://' + address + path, method=method)
    with LOCAL_OPENER.open(req, timeout=2) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


def wait_controller(process, address):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Mihomo exited during startup')
        try:
            request(address, '/version')
            return
        except (OSError, HTTPError, URLError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError('local Mihomo controller did not become ready')


def replace_provider(path, name):
    node = ({'name': 'REJECT', 'type': 'reject'} if name == 'REJECT' else {
        'name': name, 'type': 'http', 'server': '127.0.0.1', 'port': 1,
    })
    path.write_text(yaml.safe_dump({'proxies': [node]}, allow_unicode=True))


def check_transition(address, provider, expected):
    request(address, '/providers/proxies/' + quote(provider, safe=''), 'PUT')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        proxies = request(address, '/providers/proxies/' + quote(provider, safe=''))
        group = request(address, '/proxies/' + quote('Media Admission Fixture', safe=''))
        actual = [entry['name'] for entry in proxies['proxies']]
        if actual == expected and group.get('all') == expected:
            if expected == ['REJECT'] and proxies['proxies'][0].get('type') != 'Reject':
                raise AssertionError('core did not load the REJECT sentinel as a reject proxy')
            now = group.get('now')
            if now not in expected:
                raise AssertionError(f'group auto selection is outside refreshed membership: {now!r}')
            if group.get('fixed'):
                raise AssertionError('fallback group unexpectedly has a fixed selection')
            return group
        time.sleep(0.1)
    raise AssertionError(f'provider did not hot-refresh to expected membership {expected!r}')


def main():
    binary = os.environ.get('MIHOMO_TEST_BINARY')
    if not binary:
        print('SKIP: set MIHOMO_TEST_BINARY to run the real-core hot-refresh check')
        return 0
    binary_path = Path(binary).resolve()
    if not binary_path.is_file() or not os.access(binary_path, os.X_OK):
        raise ValueError('MIHOMO_TEST_BINARY must name an executable file')

    with tempfile.TemporaryDirectory(prefix='mihomo-media-core-') as raw:
        directory = Path(raw)
        provider_dir = directory / 'providers'
        provider_dir.mkdir(mode=0o700)
        provider_name = 'media-admission-fixture'
        provider_path = provider_dir / (provider_name + '.yaml')
        replace_provider(provider_path, 'Fixture Node A')
        port = free_port()
        config_path = directory / 'config.yaml'
        config_path.write_text(yaml.safe_dump({
            'mode': 'rule',
            'log-level': 'silent',
            'ipv6': False,
            'external-controller': f'127.0.0.1:{port}',
            'proxies': [],
            'proxy-providers': {
                provider_name: {
                    'type': 'file',
                    'path': './providers/' + provider_name + '.yaml',
                    'payload': [],
                    'health-check': {'enable': False},
                },
            },
            'proxy-groups': [{
                'name': 'Media Admission Fixture',
                'type': 'fallback',
                'use': [provider_name],
                'empty-fallback': 'REJECT',
            }],
            'rules': ['MATCH,REJECT'],
        }, allow_unicode=True))
        process = subprocess.Popen(
            [str(binary_path), '-d', str(directory), '-f', str(config_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            address = f'127.0.0.1:{port}'
            wait_controller(process, address)
            pid = process.pid
            check_transition(address, provider_name, ['Fixture Node A'])
            replace_provider(provider_path, 'REJECT')
            check_transition(address, provider_name, ['REJECT'])
            replace_provider(provider_path, 'Fixture Node B')
            check_transition(address, provider_name, ['Fixture Node B'])
            if process.poll() is not None or process.pid != pid:
                raise AssertionError('Mihomo process restarted during provider refresh')
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print('PASS: real Mihomo file provider hot-refreshed node → REJECT → node without restart')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('FAIL: media core check: ' + type(error).__name__ + ': ' + str(error))
        raise SystemExit(1)
