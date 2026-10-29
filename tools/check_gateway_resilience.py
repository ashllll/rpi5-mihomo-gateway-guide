#!/usr/bin/env python3
"""Exercise the healthcheck CLI with isolated OS/notification boundaries.

These checks prove decisions and status compatibility, not Linux forwarding.
All state, locks and alert executables are rebased into a temporary directory.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('HEALTHCHECK_TEST_SOURCE',
                             ROOT / 'automation/sbin/mihomo-healthcheck'))

BOUNDARY = '''import json,os,sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
recovered = Path(os.environ['TEST_ACTIONS']).with_name('recovered')
with open(os.environ['TEST_ACTIONS'], 'a') as stream:
    stream.write(json.dumps([name] + args) + '\\n')
if name == 'systemctl':
    if args[0] == 'restart' and os.environ.get('TEST_RESTART_EXIT') == '1': sys.exit(1)
    if args[0] == 'restart' and os.environ.get('TEST_RECOVER_ON_RESTART') == '1': recovered.touch()
    if args[0] == 'show': print('2026-10-29 12:00:00')
    elif args[0] == 'is-active':
        state = 'active' if recovered.exists() else os.environ.get('TEST_PROCESS', 'active')
        if '--quiet' not in args: print(state)
        sys.exit(0 if state == 'active' else 3)
    sys.exit(0)
if name == 'ip':
    if args[:2] == ['link', 'show']:
        sys.exit(0 if recovered.exists() or os.environ.get('TEST_TUN', '1') == '1' else 1)
    if args[:2] == ['rule', 'show']:
        if os.environ.get('TEST_ROUTE', '1') == '1': print('9000: from all lookup 2022')
    sys.exit(0)
if name == 'nft':
    table = args[-1]
    if table == 'rp_filter': sys.exit(0 if os.environ.get('TEST_FILTER', '1') == '1' else 1)
    print('table inet mihomo {')
    if os.environ.get('TEST_REDIRECT', '1') == '1':
        print('  tcp counter packets %s bytes 0 redirect to :46225' % os.environ.get('TEST_PACKETS', '100'))
    print('}')
    sys.exit(0)
if name == 'curl':
    if '--socks5-hostname' not in args:
        print(json.dumps({'code': 200 if os.environ.get('TEST_NOTIFY', 'ok') == 'ok' else 500}))
        sys.exit(0)
    ok = os.environ.get('TEST_EGRESS', 'ok') == 'ok'
    print('204' if ok else '000', end='')
    sys.exit(0 if ok else 7)
if name == 'date':
    print(os.environ.get('TEST_NOW', '1800000000') if '+%s' in args else '2026-10-29T12:00:00+00:00')
if name == 'ss': print('LISTEN 0 128 127.0.0.1:5335 0.0.0.0:*')
if name == 'alert': sys.exit(int(os.environ.get('TEST_ALERT_EXIT', '0')))
sys.exit(0)
'''


class HealthcheckCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gateway-resilience-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.commands = self.directory / 'bin'
        self.commands.mkdir()
        for name in ('systemctl', 'ip', 'nft', 'curl', 'date', 'ss',
                     'flock', 'logger', 'journalctl', 'alert'):
            path = self.commands / name
            path.write_text('#!' + sys.executable + '\n' + BOUNDARY)
            path.chmod(0o755)
        self.actions = self.directory / 'actions'
        self.script = self.directory / 'healthcheck'
        source = SOURCE.read_text()
        for pattern, replacement in (
            (r'(?m)^state_dir=.*$', 'state_dir=' + shlex.quote(str(self.directory))),
            (r'(?m)^alert=.*$', 'alert=' + shlex.quote(str(self.commands / 'alert'))),
            (r'/run/lock/[A-Za-z0-9._-]+\.lock', str(self.directory / 'maintenance.lock')),
        ):
            source, count = re.subn(pattern, replacement, source)
            self.assertEqual(count, 1, 'fixture cannot safely rebase ' + pattern)
        self.script.write_text(source)

    def run_cli(self, *args, **settings):
        self.actions.write_text('')
        env = dict(PATH=str(self.commands) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   TEST_ACTIONS=str(self.actions), **settings)
        result = subprocess.run(['sh', str(self.script), *args], env=env,
                                capture_output=True, text=True, timeout=10)
        actions = [json.loads(line) for line in self.actions.read_text().splitlines()]
        status = self.directory / 'status'
        text = status.read_text() if status.exists() else ''
        return result, dict(token.split('=', 1) for token in text.split() if '=' in token), actions

    def test_startup_does_not_gate_core_on_proxy_egress(self):
        result, status, actions = self.run_cli('--startup-check', '1', TEST_EGRESS='fail')
        self.assertEqual(result.returncode, 0, (status, result.stderr))
        self.assertEqual(status.get('business'), 'idle')
        self.assertFalse([a for a in actions if a[0] in ('curl', 'ss')])
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_recovery_notifies_with_a_distinct_key_even_while_idle(self):
        _, _, first = self.run_cli(TEST_EGRESS='fail')
        fault = [a for a in first if a[0] == 'alert']
        self.assertEqual(len(fault), 1)
        result, status, actions = self.run_cli(TEST_NOW='1800000301')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('business'), 'idle')
        recovery = [a for a in actions if a[0] == 'alert']
        self.assertEqual(len(recovery), 1)
        self.assertNotEqual(fault[0][-1], recovery[0][-1])
        _, _, actions = self.run_cli(TEST_NOW='1800000302')
        self.assertFalse([a for a in actions if a[0] == 'alert'])

    def test_failed_notification_keeps_core_up_and_is_retried(self):
        for _ in range(2):
            result, status, actions = self.run_cli(TEST_EGRESS='fail', TEST_ALERT_EXIT='1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.get('business'), 'degraded')
            self.assertEqual(len([a for a in actions if a[0] == 'alert']), 1)
            self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def use_real_alert(self):
        alert = (ROOT / 'automation/sbin/mihomo-alert').read_text()
        env_file = self.directory / 'alert.env'
        env_file.write_text('BARK_URL=https://bark.invalid/fixture\n')
        for pattern, replacement in (
            (r'(?m)^env_file=.*$', 'env_file=' + shlex.quote(str(env_file))),
            (r'(?m)^state_file=.*$', 'state_file=' + shlex.quote(str(self.directory / 'alert.state'))),
        ):
            alert, count = re.subn(pattern, replacement, alert)
            self.assertEqual(count, 1)
        real = self.directory / 'real-alert'
        real.write_text(alert)
        wrapper = '''import json,os,sys
with open(os.environ['TEST_ACTIONS'], 'a') as stream:
    stream.write(json.dumps(['alert'] + sys.argv[1:]) + '\\n')
os.execv('/bin/sh', ['/bin/sh', %r] + sys.argv[1:])
''' % str(real)
        (self.commands / 'alert').write_text('#!' + sys.executable + '\n' + wrapper)

    def test_real_alert_failure_is_retryable_and_recovery_bypasses_fault_cooldown(self):
        self.use_real_alert()
        for notify in ('fail', 'fail', 'ok'):
            result, status, actions = self.run_cli(TEST_EGRESS='fail', TEST_NOTIFY=notify)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.get('business'), 'degraded')
            sends = [a for a in actions if a[0] == 'curl' and '--socks5-hostname' not in a]
            self.assertEqual(len(sends), 1, actions)
        _, _, actions = self.run_cli(TEST_EGRESS='fail')
        self.assertFalse([a for a in actions if a[0] == 'curl'])
        result, status, actions = self.run_cli(TEST_NOW='1800000301')
        self.assertEqual(result.returncode, 0, result.stderr)
        sends = [a for a in actions if a[0] == 'curl' and '--socks5-hostname' not in a]
        self.assertEqual(len(sends), 1, actions)
        self.assertEqual(status.get('business'), 'idle')

    def test_real_sender_cooldown_never_acknowledges_a_new_fault(self):
        self.use_real_alert()
        _, _, actions = self.run_cli(TEST_EGRESS='fail', EGRESS_EVERY='1')
        self.assertEqual(len([a for a in actions if a[0] == 'alert']), 1)
        _, _, actions = self.run_cli(TEST_EGRESS='fail', EGRESS_EVERY='1')
        self.assertFalse([a for a in actions if a[0] == 'alert'])
        result, status, actions = self.run_cli(TEST_NOW='1800000001', EGRESS_EVERY='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('business'), 'idle')
        self.assertEqual(len([a for a in actions if a[0] == 'alert']), 1)
        for now in ('1800000002', '1800000003'):
            result, status, actions = self.run_cli(TEST_NOW=now, TEST_EGRESS='fail', EGRESS_EVERY='1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.get('business'), 'degraded')
            self.assertEqual(len([a for a in actions if a[0] == 'alert']), 1)
            self.assertFalse([a for a in actions if a[0] == 'curl' and '--socks5-hostname' not in a])
        result, status, actions = self.run_cli(TEST_NOW='1800000601', TEST_EGRESS='fail', EGRESS_EVERY='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('business'), 'degraded')
        self.assertEqual(len([a for a in actions if a[0] == 'curl' and '--socks5-hostname' not in a]), 1)
        _, _, actions = self.run_cli(TEST_NOW='1800000602', TEST_EGRESS='fail', EGRESS_EVERY='1')
        self.assertFalse([a for a in actions if a[0] == 'alert'])

    def test_startup_invalidates_old_egress_without_probing_and_timer_checks_new_core(self):
        _, status, _ = self.run_cli()
        self.assertEqual(status.get('egress'), 'ok')
        result, status, actions = self.run_cli('--startup-check', '1', TEST_EGRESS='fail')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('egress'), 'unknown')
        self.assertFalse([a for a in actions if a[0] == 'curl'])
        result, status, actions = self.run_cli(TEST_EGRESS='fail')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('business'), 'degraded')
        self.assertEqual(len([a for a in actions if a[0] == 'curl']), 1)

    def test_self_recovery_invalidates_old_egress_before_next_timer_sample(self):
        _, status, _ = self.run_cli()
        self.assertEqual(status.get('egress'), 'ok')
        result, status, actions = self.run_cli(TEST_PROCESS='inactive', TEST_RECOVER_ON_RESTART='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('action'), 'restart')
        self.assertEqual(status.get('egress'), 'unknown')
        self.assertFalse([a for a in actions if a[0] == 'curl'])
        result, status, actions = self.run_cli(TEST_EGRESS='fail')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('business'), 'degraded')
        self.assertEqual(len([a for a in actions if a[0] == 'curl']), 1)

    def test_degraded_state_and_observation_remain_successful_but_explicit(self):
        self.run_cli(TEST_EGRESS='fail')
        result, status, actions = self.run_cli('--state')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('business=degraded', result.stdout)
        result, status, actions = self.run_cli('--observe-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('business'), 'degraded')
        self.assertFalse([a for a in actions if a[0] in ('alert', 'curl')])
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_explicit_egress_command_stays_strict(self):
        result, _, actions = self.run_cli('--egress', TEST_EGRESS='fail')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('egress=fail', result.stdout)
        self.assertEqual(len([a for a in actions if a[0] == 'curl']), 1)

    def test_each_missing_forwarding_structure_fails_startup_without_egress(self):
        for setting, field in (('TEST_TUN', 'tun'), ('TEST_REDIRECT', 'redirect'),
                               ('TEST_FILTER', 'filter'), ('TEST_ROUTE', 'route')):
            with self.subTest(field=field):
                result, status, actions = self.run_cli('--startup-check', '0', **{setting: '0'})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(status.get('business'), 'failed')
                self.assertEqual(status.get(field), '0')
                self.assertFalse([a for a in actions if a[0] == 'curl'])

    def test_inactive_recovery_is_bounded_and_failed_status_persists(self):
        restarts = []
        for step in range(4):
            result, status, actions = self.run_cli(TEST_PROCESS='inactive', TEST_NOW=str(1800000000 + 60 * step))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(status.get('business'), 'failed')
            restarts.extend(a for a in actions if a[:2] == ['systemctl', 'restart'])
            self.assertFalse([a for a in actions if a[0] == 'curl'])
        self.assertEqual(len(restarts), 1)

    def test_recovery_rechecks_forwarding_structure_before_status(self):
        result, status, actions = self.run_cli(TEST_PROCESS='inactive', TEST_TUN='0',
                                              TEST_RECOVER_ON_RESTART='1')
        self.assertEqual(result.returncode, 0, (status, result.stderr))
        self.assertEqual(status.get('action'), 'restart')
        self.assertEqual(status.get('tun'), '1')
        self.assertNotEqual(status.get('business'), 'failed')

    def test_rejected_restart_still_records_failed_status(self):
        result, status, actions = self.run_cli(TEST_PROCESS='inactive', TEST_RESTART_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(status.get('business'), 'failed')
        self.assertEqual(len([a for a in actions if a[:2] == ['systemctl', 'restart']]), 1)

    def test_transitional_core_states_do_not_trigger_competing_restarts(self):
        for state in ('activating', 'reloading', 'deactivating'):
            with self.subTest(state=state):
                _, status, actions = self.run_cli(TEST_PROCESS=state, TEST_TUN='0')
                self.assertEqual(status.get('business'), 'failed')
                self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])

    def test_failed_recovery_notification_is_discarded_when_core_fails_again(self):
        result, _, _ = self.run_cli(TEST_PROCESS='inactive')
        self.assertNotEqual(result.returncode, 0)
        result, _, actions = self.run_cli(TEST_NOW='1800000060', TEST_ALERT_EXIT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        recovery = [a for a in actions if a[0] == 'alert']
        self.assertEqual(len(recovery), 1)
        result, status, actions = self.run_cli(TEST_PROCESS='inactive', TEST_NOW='1800000120')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(status.get('business'), 'failed')
        self.assertFalse([a for a in actions if a[0] == 'alert' and a[-1] == recovery[0][-1]])
        result, _, actions = self.run_cli(TEST_NOW='1800000180')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([a for a in actions if a[0] == 'alert' and a[-1] == recovery[0][-1]]), 1)

    def test_first_idle_egress_failure_degrades_and_notifies_once(self):
        result, status, actions = self.run_cli(TEST_EGRESS='fail')
        self.assertEqual(result.returncode, 0, (status, result.stderr))
        self.assertEqual(status.get('business'), 'degraded')
        self.assertEqual(status.get('egress'), 'fail')
        self.assertTrue([a for a in actions if a[0] == 'alert'])
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])
        result, status, actions = self.run_cli(TEST_EGRESS='fail')
        self.assertEqual(result.returncode, 0, (status, result.stderr))
        self.assertEqual(status.get('business'), 'degraded')
        self.assertFalse([a for a in actions if a[0] in ('alert', 'curl')])
        self.assertFalse([a for a in actions if a[:2] == ['systemctl', 'restart']])


if __name__ == '__main__':
    unittest.main()
