#!/usr/bin/python3
"""Offline regression checks for service-local recovery and HTTP health."""
import copy
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SBIN = ROOT / 'automation/sbin'


def main():
    overlay = runpy.run_path(str(SBIN / 'mihomo-policy-overlay'))
    config = {'proxies': [{'name': 'test-node'}], 'rules': ['MATCH,Auto'],
              'proxy-groups': [{'name': 'Streaming', 'proxies': ['test-node'], 'interval': 300}]}
    manifest = {'stability': {'groups': {'Streaming': {'interval': 45}},
                              'telegram_cidrs': ['149.154.160.0/20']}}
    before = copy.deepcopy(config)
    overlay['apply_stability'](config, manifest)
    assert config['proxies'] == before['proxies']
    assert config['proxy-groups'][0]['proxies'] == ['test-node']
    assert config['proxy-groups'][0]['interval'] == 45
    assert config['rules'] == ['IP-CIDR,149.154.160.0/20,Telegram,no-resolve', 'MATCH,Auto']
    snapshot = copy.deepcopy(config)
    overlay['apply_stability'](config, manifest)
    assert config == snapshot

    score = runpy.run_path(str(SBIN / 'mihomo-adaptive-score'))
    for alive in (False, True):
        replies = iter([{'delay': 100}, {'extra': {'https://test/': {'alive': alive}}}])
        score['probe'].__globals__['api_get'] = lambda *args: next(replies)
        assert score['probe']('test-node', 'https://test/', 'TEST-ONLY', '200')['ok'] is alive

    # Execute the real shell branches in isolated state with fake commands.
    # No gateway, subscription, credentials or Linux-only utilities required.
    source = (SBIN / 'mihomo-healthcheck').read_text()
    with tempfile.TemporaryDirectory(prefix='stability-check-') as raw:
        temp = Path(raw)
        commands = temp / 'bin'
        commands.mkdir()
        actions = temp / 'actions'
        fixtures = {
            'curl': '''import os, sys
args = ' '.join(sys.argv[1:])
if '/group/' in args:
    with open(os.environ['TEST_ACTIONS'], 'a') as output: output.write(args + '\\n')
    sys.exit(0)
if 'x.com/' in args or 'core.telegram.org/' in args: print('000', end=''); sys.exit(28)
if 'chatgpt.com/' in args: print(os.environ['TEST_WEB_CODE'], end='')
elif 'generate_204' in args: print('204', end='')
elif 'api.openai.com/' in args or 'registry-1.docker.io/' in args: print('401', end='')
else: print('200', end='')
''',
            'systemctl': '''import os, sys
with open(os.environ['TEST_ACTIONS'], 'a') as output: output.write(' '.join(sys.argv[1:]) + '\\n')
''',
            'jq': "import sys; from urllib.parse import quote; print(quote(sys.stdin.read(), safe=''))\n",
            'flock': 'pass\n',
            'logger': 'pass\n',
            'date': "from datetime import datetime; print(datetime.now().astimezone().isoformat(timespec='seconds'))\n",
        }
        for name, body in fixtures.items():
            path = commands / name
            path.write_text('#!' + sys.executable + '\n' + body)
            path.chmod(0o755)
        secret = temp / 'test-secret'
        secret.write_text('TEST-ONLY-NOT-A-CREDENTIAL')
        script = source.replace('/run/lock/mihomo-maintenance.lock', str(temp / 'lock'))
        script = script.replace('state_dir=/run/mihomo-healthcheck', 'state_dir=' + raw)
        script = script.replace('/etc/mihomo-automation.env', str(temp / 'unused.env'))
        script = script.replace('/etc/mihomo/secrets/dashboard.secret', str(secret))
        script = script.replace('/usr/local/sbin/mihomo-reconcile-policy', 'true')
        for code, state in [('403', 'restricted'), ('000', 'unreachable')]:
            env = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'],
                       TEST_ACTIONS=str(actions), TEST_WEB_CODE=code)
            subprocess.run(['sh', '-c', script], env=env, check=True, timeout=20)
            status = (temp / 'status').read_text()
            assert 'social=false' in status and 'telegram=false' in status
            assert 'chatgpt_web=false' in status and 'chatgpt_state=' + state in status
            assert 'mode=degraded' in status
        logged = actions.read_text()
        assert 'reload' not in logged and 'restart' not in logged
        assert 'expected=200' in logged
        assert 'Social' in logged and 'Telegram' in logged
    print('stability checks passed: preserved policy, per-URL health, isolated recovery, web states')


if __name__ == '__main__':
    main()
