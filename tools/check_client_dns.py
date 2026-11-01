#!/usr/bin/env python3
"""End-to-end test of the client-facing DNS probe in mihomo-healthcheck.

LAN clients have one DNS upstream, 127.0.0.1:1053, owned by mihomo.  A core can
be active, hold its TUN and nftables redirect, and still stop answering on 1053;
then every client loses DNS while every process-level signal stays green.

This check runs the real healthcheck against a real DNS server on loopback.  It
speaks the DNS wire protocol over UDP and TCP, so a stub that only prints a
plausible dig answer cannot make it pass.  The cases are:

* the listener answers both names over both transports -> dns_1053=ok;
* the listener answers UDP but drops TCP -> red after three samples;
* the listener is silent -> red after three samples and exactly one restart;
* the listener comes back -> the streak is cleared and no restart is requested.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

if shutil.which('dig') is None:
    print('MISSING DEPENDENCY: dig not found; install dnsutils (Debian) or bind9-dnsutils',
          file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'automation/sbin/mihomo-healthcheck'
DOMESTIC = 'www.baidu.com'
FAKE = 'example.com'
DOMESTIC_IP = '203.0.113.10'
FAKE_IP = '198.18.0.1'

HEADER = '#!' + sys.executable + '\n'
SYSTEMCTL = '''import os, sys
from pathlib import Path
with open(os.environ['TEST_ACTIONS'], 'a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args and args[0] == 'restart':
    Path(os.environ['TEST_RESTARTED']).touch()
if args and args[0] == 'is-active':
    if '--quiet' in args: sys.exit(0)
    print('active'); sys.exit(0)
if args and args[0] == 'show':
    print('2026-10-25 01:35:57'); sys.exit(0)
if args and args[0] == 'is-failed':
    sys.exit(1)
sys.exit(0)
'''
NET = '''import os, sys
args = [a for a in sys.argv[1:] if not a.startswith('-')]
kind = args[0] if args else ''
if kind == 'link': sys.exit(0)
if kind == 'rule':
    print('9000:\\tfrom all lookup 2022'); sys.exit(0)
if kind == 'list':
    print('table inet mihomo {')
    print('\\tchain prerouting {')
    print('\\t\\tmeta nfproto ipv4 tcp dport 80 counter packets 100 bytes 0 redirect to :46225')
    print('\\t}')
    print('}')
sys.exit(0)
'''


def _question_end(query: bytes) -> int:
    offset = 12
    while offset < len(query) and query[offset]:
        offset += 1 + query[offset]
    return offset + 5


def _answer(query: bytes, address: str) -> bytes:
    end = _question_end(query)
    question = query[12:end]
    header = query[:2] + b'\x81\x80' + struct.pack('!HHHH', 1, 1, 0, 0)
    body = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton(address)
    return header + question + body


class Listener:
    """A minimal authoritative answerer with switchable failure modes."""

    def __init__(self) -> None:
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(('127.0.0.1', 0))
        self.port = self.udp.getsockname()[1]
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind(('127.0.0.1', self.port))
        self.tcp.listen(8)
        self.mode = 'ok'
        self.queries: list[tuple[str, str, str]] = []
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=self._udp_loop, daemon=True),
                        threading.Thread(target=self._tcp_loop, daemon=True)]
        for thread in self.threads:
            thread.start()

    def close(self) -> None:
        self.stop.set()
        self.udp.close()
        self.tcp.close()
        for thread in self.threads:
            thread.join(timeout=2)

    def _name(self, query: bytes) -> str | None:
        labels = []
        offset = 12
        while offset < len(query):
            size = query[offset]
            if size == 0:
                return '.'.join(labels)
            if size > 63 or offset + 1 + size > len(query):
                return None
            labels.append(query[offset + 1:offset + 1 + size].decode('ascii', 'replace'))
            offset += 1 + size
        return None

    def _reply(self, query: bytes, transport: str) -> bytes | None:
        name = self._name(query)
        if name is None:
            return None
        self.queries.append((transport, name, self.mode))
        if self.mode in {'silent', 'udp-only'} and transport == 'tcp':
            return None
        if self.mode == 'silent':
            return None
        return _answer(query, DOMESTIC_IP if name == DOMESTIC else FAKE_IP)

    def _udp_loop(self) -> None:
        while not self.stop.is_set():
            try:
                query, peer = self.udp.recvfrom(4096)
            except OSError:
                return
            reply = self._reply(query, 'udp')
            if reply is not None:
                try:
                    self.udp.sendto(reply, peer)
                except OSError:
                    return

    def _tcp_loop(self) -> None:
        while not self.stop.is_set():
            try:
                connection, _ = self.tcp.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(2)
                try:
                    size = struct.unpack('!H', connection.recv(2))[0]
                    query = connection.recv(size)
                except (OSError, struct.error):
                    continue
                reply = self._reply(query, 'tcp')
                if reply is not None:
                    connection.sendall(struct.pack('!H', len(reply)) + reply)


STUBS = {'systemctl': SYSTEMCTL, 'ip': NET, 'nft': NET,
         'logger': 'pass\n', 'journalctl': 'pass\n', 'flock': 'pass\n',
         'curl': 'print("204", end="")\n',
         'date': "import datetime,sys; print(str(int(datetime.datetime.now().timestamp())) if '+%s' in sys.argv else datetime.datetime.now().astimezone().isoformat())\n"}


def run_healthcheck(script: Path, root: Path, commands: Path, port: int, fresh: bool = True) -> tuple[int, list[str], dict[str, str]]:
    actions = root / 'actions'
    actions.write_text('')
    if fresh:
        for stale in ('packets', 'status', 'egress', 'egress_at', 'fail_streak', 'restarted_at',
                      'dns_fail_streak', 'last_restart_reason'):
            (root / stale).unlink(missing_ok=True)
    (root / 'packets').write_text('100\n')
    env = dict(os.environ, PATH=str(commands) + ':' + os.environ.get('PATH', ''),
               TEST_ACTIONS=str(actions), TEST_RESTARTED=str(root / 'restarted'),
               DNS_1053_SERVER='127.0.0.1', DNS_1053_PORT=str(port),
               DNS_1053_TIMEOUT='1', DNS_1053_FAIL_THRESHOLD='3',
               DNS_1053_DOMESTIC_DOMAIN=DOMESTIC, DNS_1053_FAKEIP_DOMAIN=FAKE)
    result = subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True, timeout=60)
    fields = {}
    status = root / 'status'
    if status.exists():
        fields = dict(token.split('=', 1) for token in status.read_text().split() if '=' in token)
    return result.returncode, actions.read_text().splitlines(), fields


def main() -> int:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory(prefix='client-dns-') as raw:
        base = Path(raw)
        root = base / 'state'
        root.mkdir()
        commands = base / 'bin'
        commands.mkdir()
        for name, body in STUBS.items():
            path = commands / name
            path.write_text(HEADER + body)
            path.chmod(0o755)
        source = SOURCE.read_text()
        source = re.sub(r'/run/lock/[A-Za-z0-9._-]+\.lock', str(base / 'lock'), source)
        source = re.sub(r'(?m)^state_dir=.*$', 'state_dir=' + str(root), source)
        source = re.sub(r'(?m)^alert=.*$', 'alert=' + str(base / 'no-alert'), source)
        script = base / 'healthcheck'
        script.write_text(source)

        listener = Listener()
        try:
            code, actions, fields = run_healthcheck(script, root, commands, listener.port)
            check(code == 0, f'healthy listener: rc={code}')
            check(fields.get('dns_1053') == 'ok', f'healthy listener: {fields}')
            check(fields.get('business') in {'idle', 'verified'}, f'healthy listener: {fields}')
            check(not [line for line in actions if line.startswith('restart')], 'healthy listener restarted mihomo')
            transports = {transport for transport, _, _ in listener.queries}
            names = {name for _, name, _ in listener.queries}
            check(transports == {'udp', 'tcp'}, f'probe did not use both transports: {transports}')
            check(DOMESTIC in names and FAKE in names, f'probe did not ask both names: {names}')

            listener.mode = 'udp-only'
            code, actions, fields = run_healthcheck(script, root, commands, listener.port)
            check(fields.get('dns_1053') == 'fail', f'TCP silence not observed: {fields}')
            check(fields.get('business') == 'degraded', f'one TCP miss should degrade, not fail: {fields}')
            check(not [line for line in actions if line.startswith('restart')], 'restarted on one bad sample')
            for _ in range(2):
                code, actions, fields = run_healthcheck(script, root, commands, listener.port, fresh=False)
            check(fields.get('dns_1053') == 'fail', f'three TCP misses: {fields}')
            check(fields.get('business') == 'failed', f'three TCP misses must fail: {fields}')
            check(fields.get('reason') == 'dns-1053', f'reason not structured: {fields}')
            check(len([line for line in actions if line.startswith('restart')]) == 1,
                  f'expected one restart after three misses: {actions}')

            listener.mode = 'silent'
            for _ in range(3):
                code, actions, fields = run_healthcheck(script, root, commands, listener.port, fresh=False)
            check(fields.get('dns_1053') == 'fail', f'silent listener: {fields}')
            check(fields.get('business') == 'failed', f'silent listener must fail: {fields}')
            check(not [line for line in actions if line.startswith('restart')],
                  'hourly budget must block a second restart inside the same hour')

            listener.mode = 'ok'
            for _ in range(2):
                code, actions, fields = run_healthcheck(script, root, commands, listener.port, fresh=False)
            check(fields.get('dns_1053') == 'ok', f'recovered listener: {fields}')
            check(fields.get('dns_fail_streak') == '0', f'streak not cleared: {fields}')
            check(fields.get('business') != 'failed', f'recovered listener still failed: {fields}')
        finally:
            listener.close()

    if failures:
        print('client DNS probe failed:', file=sys.stderr)
        for failure in failures:
            print(' - ' + failure, file=sys.stderr)
        return 1
    print('client DNS probe passed: UDP+TCP, two names, three-strike restart, '
          'hourly budget, streak cleared on recovery')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
