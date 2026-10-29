#!/usr/bin/env python3
"""CLI regression for DNS degradation, isolated from real resolvers/services."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK = Path(os.environ.get('DNS_HEALTHCHECK_TEST_SOURCE',
                                  ROOT / 'automation/sbin/mosdns-healthcheck'))
DNS_MODE = Path(os.environ.get('DNS_MODE_TEST_SOURCE', ROOT / 'automation/sbin/mihomo-dns-mode'))
BOUNDARY = '''import json,os,shutil,sys,time
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
recovered = Path(os.environ['TEST_ACTIONS']).with_name('recovered')
with open(os.environ['TEST_ACTIONS'], 'a') as stream:
    stream.write(json.dumps([name] + args) + '\\n')
if name == 'systemctl':
    if args[0] == 'restart' and os.environ.get('TEST_RECOVER') == '1': recovered.touch()
    if args[0] == 'is-active':
        state = 'active' if recovered.exists() else os.environ.get('TEST_UNIT_STATE',
                'active' if os.environ.get('TEST_ACTIVE', '1') == '1' else 'inactive')
        if '--quiet' not in args: print(state)
        sys.exit(0 if state == 'active' else 3)
if name == 'dig':
    time.sleep(float(os.environ.get('TEST_DIG_SECONDS', '0')))
    key, default = ('TEST_CN', '203.0.113.10') if 'baidu.com' in args else ('TEST_PROXY', '198.18.0.10')
    answer = default if recovered.exists() else os.environ.get(key, default)
    if answer: print(answer)
if name == 'curl': sys.exit(0 if recovered.exists() or os.environ.get('TEST_METRICS', '1') == '1' else 7)
if name == 'date': print(os.environ.get('TEST_NOW', '1800000000') if '+%s' in args else '2026-10-29T12:00:00+00:00')
if name == 'alert': sys.exit(int(os.environ.get('TEST_ALERT_EXIT', '0')))
if name == 'install':
    if '-d' in args: Path(args[-1]).mkdir(parents=True, exist_ok=True)
    else: shutil.copyfile(args[-2], args[-1])
sys.exit(0)
'''


class DNSResilienceCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dns-resilience-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.commands = self.directory / 'bin'
        self.commands.mkdir()
        for name in ('systemctl', 'dig', 'curl', 'date', 'flock', 'logger',
                     'alert', 'dns-mode', 'sleep', 'install', 'chown', 'dnsmasq'):
            path = self.commands / name
            path.write_text('#!' + sys.executable + '\n' + BOUNDARY)
            path.chmod(0o755)
        self.actions = self.directory / 'actions'
        source = HEALTHCHECK.read_text()
        source, count = re.subn(r'(?m)^state_dir=.*$',
                               'state_dir=' + shlex.quote(str(self.directory)), source)
        self.assertEqual(count, 1)
        source, count = re.subn(r'/run/lock/[A-Za-z0-9._-]+\.lock',
                               str(self.directory / 'healthcheck.lock'), source)
        self.assertEqual(count, 1)
        for original, replacement in (
            ('/usr/local/sbin/mihomo-alert', str(self.commands / 'alert')),
            ('/usr/local/sbin/mihomo-dns-mode', str(self.commands / 'dns-mode')),
        ):
            self.assertIn(original, source)
            source = source.replace(original, shlex.quote(replacement))
        self.script = self.directory / 'healthcheck'
        self.script.write_text(source)

    def run_cli(self, script=None, args=(), timeout=10, **settings):
        self.actions.write_text('')
        env = dict(PATH=str(self.commands) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   TEST_ACTIONS=str(self.actions), **settings)
        result = subprocess.run(['sh', str(script or self.script), *args], env=env,
                                capture_output=True, text=True, timeout=timeout)
        actions = [json.loads(line) for line in self.actions.read_text().splitlines()]
        status_file = self.directory / 'status'
        status = status_file.read_text() if status_file.exists() else ''
        fields = dict(token.split('=', 1) for token in status.split() if '=' in token)
        return result, fields, actions

    def test_domestic_dns_survives_proxy_and_metrics_failure_without_restart(self):
        result, status, actions = self.run_cli(TEST_PROXY='', TEST_METRICS='0')
        self.assertEqual(result.returncode, 0, (status, result.stderr))
        self.assertEqual(status.get('mode'), 'degraded')
        self.assertEqual(status.get('cn'), '1')
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])
        self.assertTrue([a for a in actions if a[0] == 'dns-mode'])

    def test_metrics_only_failure_does_not_restart(self):
        result, status, actions = self.run_cli(TEST_METRICS='0')
        self.assertEqual(result.returncode, 0, (status, result.stderr))
        self.assertEqual(status.get('mode'), 'degraded')
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_transitional_unit_states_do_not_trigger_competing_restarts(self):
        for unit_state in ('activating', 'reloading', 'deactivating'):
            with self.subTest(unit_state=unit_state):
                _, status, actions = self.run_cli(TEST_UNIT_STATE=unit_state, TEST_CN='',
                                                  TEST_PROXY='', TEST_METRICS='0')
                self.assertEqual(status.get('mode'), 'failed')
                self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_degraded_failed_and_recovery_notifications_are_distinct_and_retryable(self):
        result, status, actions = self.run_cli(TEST_PROXY='', TEST_ALERT_EXIT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        fault = [a for a in actions if a[0] == 'alert']
        self.assertEqual(len(fault), 1)
        _, _, actions = self.run_cli(TEST_PROXY='')
        self.assertEqual([a for a in actions if a[0] == 'alert'], fault)
        _, _, actions = self.run_cli(TEST_PROXY='')
        self.assertFalse([a for a in actions if a[0] == 'alert'])
        result, status, actions = self.run_cli(TEST_CN='', TEST_PROXY='', TEST_METRICS='0')
        self.assertNotEqual(result.returncode, 0)
        failed = [a for a in actions if a[0] == 'alert']
        self.assertEqual(len(failed), 1)
        self.assertNotEqual(failed[0][-1], fault[0][-1])
        result, status, actions = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        recovery = [a for a in actions if a[0] == 'alert']
        self.assertEqual(len(recovery), 1)
        self.assertNotIn(recovery[0][-1], (fault[0][-1], failed[0][-1]))
        _, _, actions = self.run_cli()
        self.assertFalse([a for a in actions if a[0] == 'alert'])

    def test_failed_recovery_notification_is_discarded_when_dns_fails_again(self):
        failure = dict(TEST_ACTIVE='0', TEST_CN='', TEST_PROXY='', TEST_METRICS='0')
        result, _, _ = self.run_cli(**failure)
        self.assertNotEqual(result.returncode, 0)
        result, _, actions = self.run_cli(TEST_ALERT_EXIT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        recovery = [a for a in actions if a[0] == 'alert']
        self.assertEqual(len(recovery), 1)
        result, status, actions = self.run_cli(**failure)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(status.get('mode'), 'failed')
        self.assertFalse([a for a in actions if a[0] == 'alert' and a[-1] == recovery[0][-1]])
        result, _, actions = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([a for a in actions if a[0] == 'alert' and a[-1] == recovery[0][-1]]), 1)

    def test_all_unresponsive_requires_three_samples_and_one_hour_recovery_cap(self):
        restarts = []
        for sample in range(4):
            result, status, actions = self.run_cli(TEST_CN='', TEST_PROXY='', TEST_METRICS='0',
                                                  TEST_NOW=str(1800000000 + sample * 60))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(status.get('mode'), 'failed')
            current = [a for a in actions if a[:2] == ['systemctl', 'restart']]
            self.assertEqual(len(current), 1 if sample == 2 else 0)
            restarts.extend(current)
        self.assertEqual(len(restarts), 1)

    def test_inactive_recovery_is_immediate_bounded_and_always_records_failure(self):
        restarts = []
        for sample in range(3):
            result, status, actions = self.run_cli(TEST_ACTIVE='0', TEST_CN='', TEST_PROXY='', TEST_METRICS='0',
                                                  TEST_NOW=str(1800000000 + sample * 60))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(status.get('mode'), 'failed')
            restarts.extend(a for a in actions if a[:2] == ['systemctl', 'restart'])
        self.assertEqual(len(restarts), 1)

    def test_recovered_status_uses_fresh_resolver_evidence(self):
        result, status, _ = self.run_cli(TEST_ACTIVE='0', TEST_CN='', TEST_PROXY='', TEST_METRICS='0',
                                          TEST_RECOVER='1')
        self.assertEqual(result.returncode, 0, (status, result.stderr))
        self.assertEqual(status.get('mode'), 'recovered')
        self.assertEqual(status.get('cn'), '1')
        self.assertEqual(status.get('active'), '1')

    def build_dns_mode(self):
        for name in ('etc/mihomo', 'run/dnsmasq', 'run/lock'):
            (self.directory / name).mkdir(parents=True)
        for mode, contents in (('mosdns', 'server=127.0.0.1#5335\n'),
                               ('mihomo', 'server=127.0.0.1#1053\n'), ('direct', 'server=192.0.2.53\n')):
            (self.directory / 'etc/mihomo' / ('dnsmasq-upstreams.' + mode)).write_text(contents)
        source = DNS_MODE.read_text()
        for original, replacement in (
            ('/etc/', str(self.directory / 'etc') + '/'),
            ('/run/', str(self.directory / 'run') + '/'),
            ('/usr/sbin/dnsmasq', str(self.commands / 'dnsmasq')),
        ):
            self.assertIn(original, source)
            source = source.replace(original, replacement)
        script = self.directory / 'dns-mode'
        script.write_text(source)
        return script

    def test_auto_mode_keeps_mosdns_when_domestic_answers_but_proxy_dns_fails(self):
        script = self.build_dns_mode()
        result, _, actions = self.run_cli(script, ('auto',), TEST_PROXY='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.directory / 'etc/dnsmasq-upstreams.conf').read_text(), 'server=127.0.0.1#5335\n')

    def test_wan_down_auto_hook_finishes_before_systemd_start_timeout(self):
        script = self.build_dns_mode()
        started = time.monotonic()
        result, _, actions = self.run_cli(script, ('auto',), timeout=18,
                                          TEST_CN='', TEST_PROXY='', TEST_DIG_SECONDS='2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual((self.directory / 'etc/dnsmasq-upstreams.conf').read_text(), 'server=192.0.2.53\n')
        self.assertEqual(len([a for a in actions if a[0] == 'dig']), 3)

    def test_direct_mode_with_wan_down_only_spends_one_dns_timeout(self):
        script = self.build_dns_mode()
        started = time.monotonic()
        result, _, actions = self.run_cli(script, ('direct',), timeout=8,
                                          TEST_CN='', TEST_PROXY='', TEST_DIG_SECONDS='2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(len([a for a in actions if a[0] == 'dig']), 1)
        self.assertEqual((self.directory / 'etc/dnsmasq-upstreams.conf').read_text(), 'server=192.0.2.53\n')

    def test_systemd_dns_hooks_are_best_effort_auto_mode(self):
        for relative in ('automation/systemd/mosdns.service',
                         'automation/systemd/mihomo.service.d/dns-failover.conf'):
            with self.subTest(unit=relative):
                source = (ROOT / relative).read_text()
                hooks = [line.split('=', 1)[1] for line in source.splitlines()
                         if line.startswith(('ExecStartPost=', 'ExecStopPost=')) and 'mihomo-dns-mode' in line]
                self.assertTrue(hooks)
                for hook in hooks:
                    self.assertIn('-', hook.split('/')[0])
                    self.assertTrue(hook.endswith('mihomo-dns-mode auto'), hook)
        source = (ROOT / 'automation/systemd/mosdns.service').read_text()
        dependencies = [line for line in source.splitlines() if line.startswith(('Wants=', 'After=', 'Requires='))]
        self.assertFalse([line for line in dependencies if 'mihomo.service' in line])


if __name__ == '__main__':
    unittest.main()
