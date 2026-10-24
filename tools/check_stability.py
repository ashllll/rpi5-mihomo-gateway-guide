#!/usr/bin/env python3
"""Offline checks: process recovery never fans out into business probes.

The healthcheck may restart a dead process and, at most once per sample window,
fetch one 204 through the proxy port to prove the chain egresses. What it must
never do is turn one failure into a burst: no restart loops, no repeated active
probes after a restart, and no egress attempt at all while the core is down.
"""
import copy
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SBIN = ROOT / 'automation/sbin'
sys.dont_write_bytecode = True

HEADER = '#!' + sys.executable + '\n'
SYSTEMCTL = '''import os,sys
from pathlib import Path
with open(os.environ['TEST_ACTIONS'],'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
if sys.argv[1] == 'restart': Path(os.environ['TEST_RESTARTED']).touch()
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
NET = '''import os,sys
table_ok = os.environ['TEST_TABLE_OK'] == '1'
kind, verb = sys.argv[1], sys.argv[2]
if kind == 'link':
    sys.exit(0 if (table_ok and verb == 'show') else 2)
if kind == 'rule':
    print('9000:\\tfrom all lookup 2022'); sys.exit(0)
name = sys.argv[4] if len(sys.argv) > 4 else ''
if not (table_ok and name in ('mihomo', 'rp_filter')):
    sys.exit(1)
print('counter packets %s bytes 0' % os.environ['TEST_PACKETS'])
'''
CURL = '''import os,sys
with open(os.environ['TEST_ACTIONS'],'a') as f: f.write('CURL\\n')
print('204', end='')
'''


def run_mode(script, root, commands, mode, packets='100', table_ok='1', fresh=True) -> tuple[int, list[str]]:
    actions = root / 'actions'
    actions.write_text('')
    (root / 'restarted').unlink(missing_ok=True)
    if fresh:
        for stale in ('packets', 'status', 'egress', 'egress_at'):
            (root / stale).unlink(missing_ok=True)
    env = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'],
               TEST_ACTIONS=str(actions), TEST_RESTARTED=str(root / 'restarted'),
               TEST_MODE=mode, TEST_TABLE_OK=table_ok, TEST_PACKETS=packets)
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
        for name, body in {'systemctl': SYSTEMCTL, 'ip': NET, 'nft': NET, 'curl': CURL,
                           'flock': 'pass\n', 'logger': 'pass\n', 'journalctl': 'pass\n',
                           'date': "import sys,datetime;print(str(int(datetime.datetime.now().timestamp())) if '+%s' in sys.argv else datetime.datetime.now().astimezone().isoformat())\n"}.items():
            path = commands / name
            path.write_text(HEADER + body)
            path.chmod(0o755)
        script = source.replace('/run/lock/mihomo-maintenance.lock', str(temp / 'lock'))
        script = script.replace('state_dir=/run/mihomo-healthcheck', 'state_dir=' + raw)

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
    print('stability checks passed: one restart, one probe window, no fanout')


if __name__ == '__main__':
    main()
