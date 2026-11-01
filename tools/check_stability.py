#!/usr/bin/env python3
"""Offline checks: process recovery never fans out into business probes.

The healthcheck may restart a dead process and, at most once per sample window,
fetch one 204 through the proxy port to prove the chain egresses. What it must
never do is turn one failure into a burst: no restart loops, no repeated active
probes after a restart, and no egress attempt at all while the core is down.

The gate must also prove the data plane exists, not just that a table exists:
an empty `inet mihomo` table passes `nft list table`, so a table-only probe
silently reports a healthy redirect on a box where every LAN client is bypassed.
That was the live implementation's blind spot; the empty-table regression below
keeps it closed.

A running core whose evidence stays red is also restarted, but only after three
consecutive samples and at most once an hour: the same blind spot in production
means a black-holed gateway, and self-healing it must not become a restart loop
when the fault is upstream rather than local. `degraded` (one cached egress miss)
never restarts, because that would turn a probe hiccup into dropped connections.
"""
import copy
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SBIN = ROOT / 'automation/sbin'
sys.dont_write_bytecode = True

HEADER = '#!' + sys.executable + '\n'
SYSTEMCTL = '''import os,sys
from pathlib import Path
with open(os.environ['TEST_ACTIONS'],'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
limited = os.environ.get('TEST_START_LIMIT') == '1' and not Path(os.environ['TEST_RESET']).exists()
if sys.argv[1] == 'restart':
    if limited:
        sys.stderr.write('Failed to restart mihomo.service: Start request repeated too quickly.\\n')
        sys.exit(1)
    Path(os.environ['TEST_RESTARTED']).touch()
if sys.argv[1] == 'reset-failed': Path(os.environ['TEST_RESET']).touch()
if sys.argv[1] == 'is-failed': sys.exit(0 if limited else 1)
if sys.argv[1] == 'is-active':
    mode, restarted = os.environ['TEST_MODE'], Path(os.environ['TEST_RESTARTED']).exists()
    quiet = '--quiet' in sys.argv
    if mode == 'active' or (mode == 'recover' and restarted):
        if quiet: sys.exit(0)
        print('active'); sys.exit(0)
    if quiet: sys.exit(3)
    print('inactive'); sys.exit(3)
if sys.argv[1] == 'show':
    print('2026-10-25 01:35:57'); sys.exit(0)
sys.exit(0)
'''
# Serves as both `ip` and `nft`. TEST_NFT=empty lists the tables with no rules:
# the table exists, the redirect rule does not.
NET = '''import os,sys
args = [a for a in sys.argv[1:] if not a.startswith('-')]
kind = args[0] if args else ''
verb = args[1] if len(args) > 1 else ''
table_ok = os.environ['TEST_TABLE_OK'] == '1'
if kind == 'link':
    sys.exit(0 if (table_ok and verb == 'show') else 2)
if kind == 'rule':
    if table_ok: print('9000:\\tfrom all lookup 2022')
    sys.exit(0)
if kind != 'list':
    sys.exit(0)
if not table_ok:
    sys.exit(1)
rules = os.environ.get('TEST_NFT', 'full') == 'full'
rule = ('\\t\\tmeta nfproto ipv4 tcp dport 80 counter packets %s bytes 0 redirect to :46225'
        % os.environ['TEST_PACKETS'])
if len(args) > 3 and args[2] == 'ruleset':
    names = ['mihomo', 'rp_filter']
elif len(args) > 3:
    names = [args[3]]
else:
    sys.exit(0)
for name in names:
    print('table inet %s {' % name)
    print('\\tchain prerouting {')
    if rules: print(rule)
    print('\\t}')
    print('}')
'''
CURL = '''import os,sys
with open(os.environ['TEST_ACTIONS'],'a') as f: f.write('CURL\\n')
print('204', end='')
'''
DIG = '''import os,sys
with open(os.environ['TEST_ACTIONS'],'a') as f: f.write('DIG\\n')
if os.environ.get('TEST_DNS_1053', 'ok') != 'ok': sys.exit(9)
name = next((a for a in sys.argv[1:] if not a.startswith(('+','-','@')) and a not in ('A','IN')), 'probe.invalid')
print(';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1')
print('%s. 60 IN A %s' % (name, '198.18.0.1' if 'baidu' not in name else '203.0.113.10'))
'''


def run_mode(script, root, commands, mode, packets='100', table_ok='1', nft='full',
             fresh=True, start_limit='0', dns_1053='ok') -> tuple[int, list[str]]:
    actions = root / 'actions'
    actions.write_text('')
    (root / 'restarted').unlink(missing_ok=True)
    (root / 'resetter').unlink(missing_ok=True)
    if fresh:
        for stale in ('packets', 'status', 'egress', 'egress_at', 'fail_streak', 'restarted_at',
                      'dns_fail_streak', 'last_restart_reason', 'restart_attempted_at',
                      'restart_succeeded_at', 'verified_streak', 'boot_resets', 'boot_id'):
            (root / stale).unlink(missing_ok=True)
    env = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'],
               TEST_ACTIONS=str(actions), TEST_RESTARTED=str(root / 'restarted'),
               TEST_RESET=str(root / 'resetter'), TEST_START_LIMIT=start_limit,
               TEST_DNS_1053=dns_1053,
               TEST_MODE=mode, TEST_TABLE_OK=table_ok, TEST_PACKETS=packets, TEST_NFT=nft)
    result = subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True, timeout=20)
    return result.returncode, actions.read_text().splitlines()


def main():
    overlay = runpy.run_path(str(SBIN / 'mihomo-policy-overlay'))
    policy = runpy.run_path(str(SBIN / 'mihomo-measurement-policy'))
    config = {'proxies': [{'name': 'test-node'}], 'rules': ['MATCH,Auto'],
              'proxy-groups': [{'name': 'Streaming', 'type': 'url-test', 'proxies': ['test-node']}]}
    manifest = {'stability': {'groups': {'Streaming': {'interval': 45}},
                              'telegram_cidrs': ['149.154.160.0/20']}}
    overlay['apply_stability'](config, manifest)
    policy['configure_measurement'](config, manifest)
    assert config['proxy-groups'][0] == {'name': 'Streaming', 'type': 'select', 'proxies': ['test-node']}
    before = copy.deepcopy(config)
    policy['configure_measurement'](config, manifest)
    assert config == before

    source = (SBIN / 'mihomo-healthcheck').read_text()
    with tempfile.TemporaryDirectory(prefix='stability-check-') as raw:
        temp = Path(raw)
        commands = temp / 'bin'
        commands.mkdir()
        for name, body in {'systemctl': SYSTEMCTL, 'ip': NET, 'nft': NET, 'curl': CURL, 'dig': DIG,
                           'flock': 'pass\n', 'logger': 'pass\n', 'journalctl': 'pass\n', 'alert': 'pass\n',
                           'date': "import sys,datetime;print(str(int(datetime.datetime.now().timestamp())) if '+%s' in sys.argv else datetime.datetime.now().astimezone().isoformat())\n"}.items():
            path = commands / name
            path.write_text(HEADER + body)
            path.chmod(0o755)
        # Rebase the root-only state and lock paths onto the temporary tree.
        script = re.sub(r'/run/lock/[A-Za-z0-9._-]+\.lock', str(temp / 'lock'), source)
        script = re.sub(r'(?m)^state_dir=.*$', 'state_dir=' + raw, script)
        script = re.sub(r'(?m)^alert=.*$', 'alert=' + str(commands / 'alert'), script)

        # Healthy: the sample is idle on the first tick and verified once the
        # packet counter advances.
        code, actions = run_mode(script, temp, commands, 'active', packets='100')
        assert code == 0, (code, actions)
        assert not [line for line in actions if 'restart' in line]
        assert actions.count('CURL') <= 1
        status = (temp / 'status').read_text()
        assert 'mode=process-running' in status and 'business=idle' in status and 'tun=1' in status
        code, actions = run_mode(script, temp, commands, 'active', packets='100')
        assert code == 0 and 'business=idle' in (temp / 'status').read_text()
        (temp / 'packets').write_text('100\n')
        code, _ = run_mode(script, temp, commands, 'active', packets='140', fresh=False)
        assert code == 0 and 'business=verified' in (temp / 'status').read_text()

        # Recovery: one restart, at most one active probe, and the pass is real.
        code, actions = run_mode(script, temp, commands, 'recover')
        assert code == 0, (code, actions)
        assert len([line for line in actions if line.startswith('restart')]) == 1
        assert actions.count('CURL') <= 1
        assert 'action=restart' in (temp / 'status').read_text()

        # Down: never report a healthy business, never probe egress, exit red.
        code, actions = run_mode(script, temp, commands, 'inactive')
        assert code == 1, (code, actions)
        assert 'business=failed' in (temp / 'status').read_text()
        assert 'CURL' not in actions

        # Broken hooks with a running process: red, and egress is not attempted
        # to "prove" a chain the kernel does not have.
        code, actions = run_mode(script, temp, commands, 'active', table_ok='0')
        assert code == 1 and 'CURL' not in actions
        assert 'business=failed' in (temp / 'status').read_text()
        assert 'reload' not in ' '.join(actions)

        # Blind spot regression: the table exists but carries no redirect, so
        # every LAN client bypasses the proxy. A table-only probe reports 1 here.
        code, actions = run_mode(script, temp, commands, 'active', nft='empty')
        assert code == 1, (code, actions)
        assert 'CURL' not in actions
        status = (temp / 'status').read_text()
        assert 'redirect=0' in status, status
        assert 'business=failed' in status, status
        assert 'reload' not in ' '.join(actions)

        # Self-heal: a running core whose evidence stays red for three samples is
        # restarted once, then the hourly cap keeps a broken upstream from turning
        # into a restart loop.
        code, actions = run_mode(script, temp, commands, 'active', nft='empty', fresh=True)
        assert code == 1 and not [line for line in actions if line.startswith('restart')]
        code, actions = run_mode(script, temp, commands, 'active', nft='empty', fresh=False)
        assert code == 1 and not [line for line in actions if line.startswith('restart')]
        code, actions = run_mode(script, temp, commands, 'active', nft='empty', fresh=False)
        assert code == 1, (code, actions)
        assert len([line for line in actions if line.startswith('restart')]) == 1, actions
        assert 'action=restart' in (temp / 'status').read_text()
        code, actions = run_mode(script, temp, commands, 'active', nft='empty', fresh=False)
        assert not [line for line in actions if line.startswith('restart')], 'hourly cap failed'

        # F-22: a successful recovery clears the attempt stamp. A new, unrelated
        # failure must be allowed to restart immediately instead of waiting out
        # the previous attempt's hour (the old code wrote the stamp before the
        # attempt and never cleared it).
        code, actions = run_mode(script, temp, commands, 'recover', fresh=True)
        assert code == 0, (code, actions)
        assert len([line for line in actions if line.startswith('restart')]) == 1, actions
        restarts = []
        for step in range(3):
            code, actions = run_mode(script, temp, commands, 'active', nft='empty', fresh=False)
            restarts.extend(line for line in actions if line.startswith('restart'))
        assert len(restarts) == 1, \
            'a new failure after a successful recovery was still blocked by the old cooldown'
        assert int((temp / 'restart_succeeded_at').read_text().strip()) > 0
        assert int((temp / 'restart_attempted_at').read_text().strip()) > 0
        assert 'action=restart' in (temp / 'status').read_text()
        assert 'restart_path=direct' in (temp / 'status').read_text()
        code, actions = run_mode(script, temp, commands, 'active', nft='empty', fresh=False)
        assert not [line for line in actions if line.startswith('restart')], \
            'an ineffective restart must keep the hourly cap'

        # F-22: systemd refuses a restart after StartLimitBurst. The healthcheck
        # must clear the failed state once and retry, not record a dead hour.
        code, actions = run_mode(script, temp, commands, 'inactive', fresh=True, start_limit='1')
        assert code == 1, (code, actions)
        assert [line for line in actions if line.startswith(('restart', 'reset-failed'))] == [
            'restart mihomo', 'reset-failed mihomo', 'restart mihomo'], actions
        assert 'restart_path=reset-failed' in (temp / 'status').read_text()

        # F-22: structured causes. The same decision path names why it acted.
        code, actions = run_mode(script, temp, commands, 'inactive', fresh=True)
        assert 'reason=process-inactive' in (temp / 'status').read_text()
        code, actions = run_mode(script, temp, commands, 'active', nft='empty', fresh=True)
        assert 'reason=structure' in (temp / 'status').read_text()

        # F-21/F-22: a silent client DNS listener drives the same three-strike
        # path, and the reason stays distinct from a kernel/structure failure.
        for step in range(3):
            code, actions = run_mode(script, temp, commands, 'active', dns_1053='fail',
                                     fresh=(step == 0))
        assert 'business=failed' in (temp / 'status').read_text()
        assert 'reason=dns-1053' in (temp / 'status').read_text()
        assert len([line for line in actions if line.startswith('restart')]) == 1, actions

        # One failed egress sample is cached for five minutes, so `degraded` can
        # describe a probe hiccup rather than a dead gateway. Restarting on it
        # would drop every live connection: it must only alert, never self-heal.
        (temp / 'packets').write_text('900\n')
        (temp / 'egress').write_text('fail\n')
        (temp / 'egress_at').write_text(str(int(time.time())))
        (temp / 'fail_streak').write_text('0\n')
        (temp / 'restart_attempted_at').unlink(missing_ok=True)
        for step in range(3):
            code, actions = run_mode(script, temp, commands, 'active',
                                     packets=str(940 + 10 * step), fresh=False)
            assert code == 0, (code, actions)
            assert 'business=degraded' in (temp / 'status').read_text()
            assert not [line for line in actions if line.startswith('restart')], actions

    print('stability checks passed: one restart, one probe window, no fanout,'
          ' empty redirect table stays red, self-heal capped at one restart an hour,'
          ' cached egress hiccup never restarts')


if __name__ == '__main__':
    main()
