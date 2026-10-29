#!/usr/bin/env python3
"""Exercise the client DNS guard CLI without real DNS, units or notifications."""
import json
import fcntl
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('CLIENT_DNS_GUARD_SOURCE', ROOT / 'automation/sbin/mihomo-dnsmasq-guard'))
BOUNDARY = r'''import fcntl,json,os,sys
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ['GUARD_ACTIONS'], 'a') as stream:
    stream.write(json.dumps([name] + args) + '\n')
if name == 'flock':
    operation = fcntl.LOCK_UN if '-u' in args else fcntl.LOCK_EX
    if '-n' in args: operation |= fcntl.LOCK_NB
    try: fcntl.flock(int(args[-1]), operation)
    except BlockingIOError: sys.exit(1)
if name == 'dig':
    server = next((arg[1:] for arg in args if arg.startswith('@')), '127.0.0.1')
    port = args[args.index('-p') + 1] if '-p' in args else '53'
    protocol = 'tcp' if '+tcp' in args else 'udp'
    domain = next(arg for arg in args if not arg.startswith(('+','-','@')) and not arg.isdigit() and arg not in ('A','IN'))
    replies = json.loads(os.environ.get('GUARD_REPLIES', '{}'))
    reply = replies.get(':'.join((server,port,protocol,domain)), replies.get(':'.join((server,port,protocol)), 'a'))
    if reply == 'timeout': print(';; communications error: timed out'); sys.exit(9)
    if '+short' in args:
        if reply == 'a': print('203.0.113.10')
        elif reply == 'cname': print('alias.example.invalid.')
    else:
        print(';; ->>HEADER<<- opcode: QUERY, status: %s, id: 1' % ('SERVFAIL' if reply in ('servfail', 'servfail-a') else 'NOERROR'))
        if reply in ('a', 'servfail-a'): print('%s. 60 IN A 203.0.113.10' % domain)
        elif reply == 'bad-a': print('%s. 60 IN A 203.0.113.999' % domain)
        elif reply == 'cname': print('%s. 60 IN CNAME alias.example.invalid.' % domain)
if name == 'date': print(os.environ.get('GUARD_NOW', '1800000000') if '+%s' in args else '2026-10-29T12:00:00+00:00')
sys.exit(0)
'''


class ClientDNSGuardCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='client-dns-guard-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for name in ('bin', 'lock', 'state'):
            (self.directory / name).mkdir()
        for name in ('dig', 'systemctl', 'flock', 'date', 'logger', 'alert'):
            path = self.directory / 'bin' / name
            path.write_text('#!' + sys.executable + '\n' + BOUNDARY)
            path.chmod(0o755)
        self.actions = self.directory / 'actions'
        site = self.directory / 'site.env'
        site.write_text('DNS_CLIENT_SERVER=192.0.2.53\nDNS_CLIENT_PORT=53\n'
                        'DNS_CLIENT_UNIT=fixture-client-dns.service\n'
                        'DNS_UPSTREAM_SERVER=127.0.0.1\nDNS_UPSTREAM_PORT=1053\n'
                        "DNS_PROBE_DOMAINS='one.example.invalid two.example.invalid'\n")
        source = SOURCE.read_text()
        for old, new in (
            ('/etc/mihomo-automation.env', str(site)),
            ('/run/lock/', str(self.directory / 'lock') + '/'),
            ('state_dir=/run/mihomo-dnsmasq-guard', 'state_dir=' + shlex.quote(str(self.directory / 'state'))),
            ('/usr/local/sbin/mihomo-alert', str(self.directory / 'bin/alert')),
        ):
            source = source.replace(old, new)
        self.script = self.directory / 'guard'
        self.script.write_text(source)

    def run_cli(self, *args, replies=None, now='1800000000'):
        self.actions.write_text('')
        env = dict(PATH=str(self.directory / 'bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   GUARD_ACTIONS=str(self.actions), GUARD_REPLIES=json.dumps(replies or {}), GUARD_NOW=now)
        result = subprocess.run(['sh', str(self.script), *args], env=env,
                                capture_output=True, text=True, timeout=15)
        actions = [json.loads(line) for line in self.actions.read_text().splitlines()]
        status = self.directory / 'state/status'
        fields = dict(token.split('=', 1) for token in status.read_text().split() if '=' in token) if status.exists() else {}
        return result, fields, actions

    def test_site_client_udp_tcp_and_actual_upstream_are_observed_separately(self):
        result, status, actions = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        queries = [a[1:] for a in actions if a[0] == 'dig']
        observed = set()
        for args in queries:
            server = next((a[1:] for a in args if a.startswith('@')), '127.0.0.1')
            port = args[args.index('-p') + 1] if '-p' in args else '53'
            observed.add((server, port, 'tcp' if '+tcp' in args else 'udp'))
        self.assertTrue({('192.0.2.53', '53', 'udp'), ('192.0.2.53', '53', 'tcp'),
                         ('127.0.0.1', '1053', 'udp'), ('127.0.0.1', '5335', 'udp')} <= observed, observed)
        self.assertEqual(status.get('unit'), 'fixture-client-dns.service')

    def test_persistent_single_domain_failure_only_degrades(self):
        replies = {'192.0.2.53:53:' + protocol + ':one.example.invalid': 'timeout'
                   for protocol in ('udp', 'tcp')}
        for step in range(4):
            result, status, actions = self.run_cli(replies=replies, now=str(1800000000 + step * 30))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.get('action'), 'degraded-watch')
            self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_one_failed_client_transport_does_not_restart_resolver(self):
        result, status, actions = self.run_cli(replies={'192.0.2.53:53:udp': 'timeout'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('action'), 'degraded-watch')
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_silent_client_recovers_only_the_configured_unit_with_live_actual_upstream(self):
        replies = {'192.0.2.53:53:udp': 'timeout', '192.0.2.53:53:tcp': 'timeout'}
        result, status, actions = self.run_cli(replies=replies)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('action'), 'restart')
        self.assertEqual([a for a in actions if a[:2] == ['systemctl', 'restart']],
                         [['systemctl', 'restart', 'fixture-client-dns.service']])

    def test_host_mosdns_success_does_not_hide_dead_actual_core_upstream(self):
        replies = {endpoint + ':' + protocol: 'timeout'
                   for endpoint in ('192.0.2.53:53', '127.0.0.1:1053') for protocol in ('udp', 'tcp')}
        result, status, actions = self.run_cli(replies=replies)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('action'), 'chain-down')
        self.assertEqual(status.get('host_mosdns'), '2')
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_error_text_cname_invalid_a_or_servfail_are_not_valid_client_answers(self):
        for reply in ('timeout', 'cname', 'bad-a', 'servfail-a'):
            with self.subTest(reply=reply):
                result, status, actions = self.run_cli(replies={'192.0.2.53:53:udp': reply,
                                                               '192.0.2.53:53:tcp': reply},
                                                       now=str(1800000000 + 1000 * ('timeout', 'cname', 'bad-a', 'servfail-a').index(reply)))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(status.get('client_udp'), '0')
                self.assertEqual(status.get('client_tcp'), '0')
                self.assertEqual(status.get('action'), 'restart')

    def test_maintenance_does_not_compete_with_recovery_but_still_records_observations(self):
        replies = {'192.0.2.53:53:udp': 'timeout', '192.0.2.53:53:tcp': 'timeout'}
        with (self.directory / 'lock/mihomo-maintenance.lock').open('a') as maintenance:
            fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, status, actions = self.run_cli(replies=replies)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('maintenance'), '1')
        self.assertEqual(status.get('action'), 'maintenance')
        self.assertTrue([a for a in actions if a[0] == 'dig'])
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_observe_only_does_not_mutate_recovery_state_or_notify(self):
        before = {p.name: p.read_bytes() for p in (self.directory / 'state').iterdir() if p.name != 'status'}
        result, status, actions = self.run_cli('--observe-only', replies={
            '192.0.2.53:53:udp': 'timeout', '192.0.2.53:53:tcp': 'timeout'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('action'), 'observe-only')
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart'] or a[0] == 'alert'])
        self.assertEqual({p.name: p.read_bytes() for p in (self.directory / 'state').iterdir() if p.name != 'status'}, before)

    def test_recovery_budget_stops_a_persistently_silent_client(self):
        replies = {'192.0.2.53:53:udp': 'timeout', '192.0.2.53:53:tcp': 'timeout'}
        restarts = []
        for step in range(5):
            result, status, actions = self.run_cli(replies=replies, now=str(1800000000 + step * 30))
            self.assertEqual(result.returncode, 0, result.stderr)
            restarts.extend(a for a in actions if a[:2] == ['systemctl', 'restart'])
        self.assertEqual(len(restarts), 3)
        self.assertEqual(status.get('action'), 'throttled')


if __name__ == '__main__':
    unittest.main()
