#!/usr/bin/env python3
"""Run the subscription updater with real candidate generation and offline I/O."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import yaml
from check_overlay import ALL_NODES, MANIFEST_OVERLAY, build_fixture, native_group_nodes

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / 'automation/sbin/mihomo-update-subscription').read_text()
STUB = r'''
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
root = Path(os.environ['SUB_TEST_ROOT'])
mode = os.environ['SUB_TEST_MODE']
name, args = Path(sys.argv[0]).name, sys.argv[1:]
def fault():
    (root / 'fault').touch()
if name == 'curl':
    if '--output' in args and args[args.index('--output') + 1] != '/dev/null':
        output = Path(args[args.index('--output') + 1])
        source = args[-1].rsplit('/', 1)[-1]
        with (root / 'downloads-requested').open('a') as stream:
            stream.write(source + '\n')
        if mode == 'retained' and source == 'primary.yaml':
            sys.exit(22)
        if mode in {'cached', 'no-cache'} and source != 'primary.yaml':
            fault()
            output.write_text('proxies: []\n')
        else:
            shutil.copyfile(root / 'downloads' / source, output)
    else:
        url = args[-1]
        code = '204' if 'generate_204' in url else '401' if 'docker.io' in url or 'api.openai.com' in url else '200'
        if mode == 'health' and 'docker.io' in url:
            fault()
            if os.environ.get('SUB_TEST_BREAK_POLICY'):
                Path(os.environ['MIHOMO_CAPABILITIES']).write_text('{broken')
        print('500' if mode == 'health' and 'docker.io' in url else code, end='')
elif name == 'dig':
    domain = next((arg for arg in args if not arg.startswith(('+', '-', '@')) and arg not in ('A', 'IN')), 'probe.invalid')
    if mode in {'dns-1053', 'dns-1053-wrong-fake'}:
        fault()
        if mode == 'dns-1053':
            sys.exit(9)
    fake = 'baidu' not in domain
    address = '203.0.113.99' if fake and mode == 'dns-1053-wrong-fake' else ('198.18.0.1' if fake else '203.0.113.10')
    print(';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1')
    print('%s. 60 IN A %s' % (domain, address))
elif name == 'systemctl':
    with (root / 'calls').open('a') as stream:
        stream.write(' '.join(args) + '\n')
    if args[0] in {'reload', 'restart'}:
        if mode == 'reload' and not (root / 'reload-failed').exists():
            fault()
            (root / 'reload-failed').touch()
            sys.exit(1)
        (root / 'reloaded').touch()
        if mode in {'write', 'signal'} and os.environ.get('SUB_TEST_POST_APPLY') and not (root / 'post-apply-failed').exists():
            fault()
            (root / 'post-apply-failed').touch()
            if mode == 'signal':
                os.kill(int(os.environ['SUB_TEST_UPDATER_PID']), signal.SIGTERM)
            sys.exit(1)
    if args[0] == 'is-active' and mode == 'inactive' and (root / 'reloaded').exists() and not (root / 'inactive-failed').exists():
        fault()
        (root / 'inactive-failed').touch()
        sys.exit(1)
elif name == 'mihomo':
    if mode == 'validation' or (mode in {'installed-validation', 'rollback'} and '-f' not in args):
        fault()
        sys.exit(1)
elif name == 'install':
    cleaned = []
    while args:
        item = args.pop(0)
        if item in {'-o', '-g'}:
            args.pop(0)
        else:
            cleaned.append(item)
    if '-d' in cleaned:
        if mode == 'backup' and 'backups' in Path(cleaned[-1]).parts and Path(cleaned[-1]).name != 'backups':
            fault()
            sys.exit(1)
    else:
        source, target = map(Path, cleaned[-2:])
        if (target == root / 'etc/config.yaml' or target.name.startswith('config.yaml.tmp.')) and source.name == 'config.yaml' and source.parent.name.startswith('mihomo-subscription.'):
            if mode in {'write', 'signal'} and not os.environ.get('SUB_TEST_POST_APPLY'):
                fault()
                target.write_text('partial write\n')
                if mode == 'signal':
                    os.kill(int(os.environ['SUB_TEST_UPDATER_PID']), signal.SIGTERM)
                sys.exit(1)
        if mode == 'rollback' and ('backups' in source.parts or source.name == 'rollback.yaml'):
            sys.exit(1)
    sys.exit(subprocess.call([os.environ['SUB_TEST_INSTALL'], *cleaned]))
elif name == 'logger':
    with (root / 'messages').open('a') as stream:
        stream.write(' '.join(args) + '\n')
elif name == 'flock' and mode == 'locked':
    sys.exit(1)
elif name == 'xargs':
    sys.stdin.read()
elif name == 'find' and '-printf' not in args:
    sys.exit(subprocess.call(['/usr/bin/find', *args]))
'''


def check_case(mode):
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for name in ('etc', 'lock', 'bin', 'downloads', 'cache'):
            (root / name).mkdir()
        fixture, capabilities = build_fixture(root / 'etc', MANIFEST_OVERLAY, ALL_NODES)
        route_defaults = root / 'etc/route-defaults.yaml'
        shutil.copyfile(REPO / 'automation/policy/route-defaults.yaml', route_defaults)
        current = root / 'etc/config.yaml'
        fixture.rename(current)
        original = yaml.safe_load(current.read_text())
        primary = dict(original, proxies=[p for p in original['proxies']
                                         if not p['name'].startswith(('ByWave | ', 'BWB | ', 'InfraAPI | '))])
        (root / 'downloads/primary.yaml').write_text(yaml.safe_dump(primary))
        secrets = root / 'etc/secrets'
        (secrets / 'subscriptions.d').mkdir(parents=True)
        (secrets / 'subscription.url').write_text('https://fixture.invalid/primary.yaml\n')
        entries = []
        for source_id, names in (('ByWave', ['Bulk 01']), ('BWB', ['Backup 01', 'Backup 02']), ('InfraAPI', ['Backup 01'])):
            content = yaml.safe_dump({'proxies': [{'name': name, 'type': 'ss',
                'server': '198.51.100.1', 'port': 443} for name in names]})
            download = root / 'downloads' / (source_id + '.yaml')
            download.write_text(content)
            (secrets / 'subscriptions.d' / (source_id + '.url')).write_text('https://fixture.invalid/' + download.name)
            if mode != 'no-cache':
                old = yaml.safe_load(content)
                for proxy in old['proxies']:
                    proxy['server'] = 'old.example.invalid'
                (root / 'cache' / download.name).write_text(yaml.safe_dump(old))
            entries.append(f'{source_id}|{download}|1\n')
        for name in ('flock', 'curl', 'dig', 'mihomo', 'systemctl', 'install', 'logger', 'sleep', 'find', 'xargs', 'mihomo-reconcile-policy', 'mihomo-healthcheck', 'gateway-backup'):
            command = root / 'bin' / name
            command.write_text(f'#!{sys.executable}\n' + STUB)
            command.chmod(0o755)
        for name in ('mihomo-subscription-candidate', 'mihomo-policy-overlay', 'mihomo-measurement-policy',
                     'mihomo-subscription-normalize', 'mihomo-media-policy', 'mihomo-media-guard',
                     'mihomo-download-quality'):
            command = root / 'bin' / name
            code = (REPO / 'automation/sbin' / name).read_text().split('\n', 1)[1]
            prefix = ''
            if name == 'mihomo-subscription-candidate':
                prefix = ("import os,sys\n"
                          "if os.environ.get('SUB_TEST_PREPARE_FAIL') and sys.argv[1:2] == ['prepare']:\n"
                          "    Path(os.environ['SUB_TEST_ROOT']).joinpath('fault').touch()\n"
                          "    print('prepare failed by test', file=sys.stderr); sys.exit(1)\n")
            if prefix:
                code = code.replace('import yaml\n', 'import yaml\n' + prefix, 1)
            command.write_text(f'#!{sys.executable}\n' + code)
            command.chmod(0o755)
        environment = dict(os.environ, PATH=f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}",
                           MIHOMO_CAPABILITIES=str(capabilities), MIHOMO_ROUTE_DEFAULTS=str(route_defaults),
                           SUB_TEST_ROOT=str(root),
                           SUB_TEST_MODE=('health' if mode == 'revoke-invalid-policy' else mode.removeprefix('revoke-')),
                           SUB_TEST_PREPARE_FAIL=('1' if mode == 'prepare-fail' else ''),
                           SUB_TEST_INSTALL=shutil.which('install'), PYTHONDONTWRITEBYTECODE='1')
        if mode.startswith('revoke-'):
            # Policy rollback must be exercised after atomic apply. Plain
            # pre-rename write failures leave the original config untouched.
            if mode in {'revoke-write', 'revoke-signal'}:
                environment['SUB_TEST_POST_APPLY'] = '1'
            cap = yaml.safe_load(capabilities.read_text())
            cap['measurement'] = {'enabled': True, 'native_budget': True, 'native_interval_seconds': 900,
                'accounts': {'fixture': {'names': ALL_NODES, 'native_probes_per_hour': 10000,
                                        'quality_attempts_per_hour': 600}}}
            capabilities.write_text(yaml.safe_dump(cap))
            old = root / 'old-native.yaml'
            subprocess.run([str(root / 'bin/mihomo-policy-overlay'), str(current), str(old)],
                           env=environment, check=True, capture_output=True)
            shutil.copyfile(old, current)
            cap['roles']['bulk_download'].update(glados_patterns=[], glados_explicit_allow=[])
            capabilities.write_text(yaml.safe_dump(cap))
            if mode == 'revoke-invalid-policy':
                environment['SUB_TEST_BREAK_POLICY'] = '1'
        if mode in {'manual', 'manual-force', 'manual-no-cache'}:
            environment['MIHOMO_SUBSCRIPTION_MANUAL_SOURCES'] = 'BWB'
        if mode == 'manual-no-cache':
            (root / 'cache/BWB.yaml').unlink()
            (root / 'fault').touch()
        if mode in {'unchanged', 'force'}:
            manifest = root / 'extras'
            manifest.write_text(''.join(sorted(entries)))
            subprocess.run([str(root / 'bin/mihomo-subscription-candidate'), 'prepare', str(current),
                            str(root / 'downloads/primary.yaml'), str(manifest), str(root / 'stable'), str(root / 'status')],
                           env=environment, check=True, capture_output=True)
            shutil.copyfile(root / 'stable', current)
            shutil.copyfile(current, root / 'downloads/primary.yaml')
        before = current.read_bytes()
        expected_rollback = root / 'expected-rollback.yaml'
        subprocess.run([str(root / 'bin/mihomo-policy-overlay'), str(current), str(expected_rollback)],
                       env=environment, check=True, capture_output=True)
        cache_before = {p.name: p.read_bytes() for p in (root / 'cache').iterdir()}
        if mode == 'recover-only':
            # Simulate a crash after the config was replaced but before acceptance:
            # a pending transaction must be consumed without fetching anything.
            backup = root / 'backups/crash-1'
            backup.mkdir(parents=True)
            shutil.copyfile(current, backup / 'config.yaml')
            for name in ('cache-before', 'cache-after'):
                shutil.copytree(root / 'cache', backup / name)
            baseline = current.read_bytes()
            candidate = yaml.safe_load(baseline)
            candidate['log-level'] = 'warning'
            candidate_bytes = yaml.safe_dump(candidate).encode()
            current.write_bytes(candidate_bytes)
            digest = lambda data: hashlib.sha256(data).hexdigest()
            (root / 'backups/transaction.pending').write_text(
                f"prepared|{backup}|{digest(baseline)}|{digest(candidate_bytes)}|"
                f"{backup / 'cache-before'}|{backup / 'cache-after'}|\n")
        script = SOURCE
        for old, new in (('/etc/mihomo-automation.env', str(root / 'absent.env')), ('/etc/mihomo', str(root / 'etc')),
                         ('/var/lib/mihomo-extra-subscriptions', str(root / 'cache')),
                         ('/var/lib/mihomo-subscription-backups', str(root / 'backups')),
                         ('/run/lock', str(root / 'lock')), ('/var/tmp', str(root)), ('/usr/local/sbin', str(root / 'bin'))):
            script = script.replace(old, new)
        updater = root / 'update.sh'
        updater.write_text(script.replace('\n', '\nexport SUB_TEST_UPDATER_PID=$$\n', 1))
        arguments = {'force': ['--force'], 'manual-force': ['--force'],
                     'recover-only': ['--recover-only'], 'due-only': ['--due-only']}.get(mode, [])
        result = subprocess.run(['sh', str(updater), *arguments],
                                env=environment, capture_output=True, text=True, timeout=45)
        success = mode in {'success', 'cached', 'retained', 'unchanged', 'force', 'locked',
                           'manual', 'manual-force', 'recover-only', 'due-only'}
        assert (result.returncode == 0) == success, (mode, result.stderr)
        rolled_back = mode in {'installed-validation', 'reload', 'inactive', 'health',
                               'dns-1053', 'dns-1053-wrong-fake', 'recover-only'} or mode.startswith('revoke-')
        if rolled_back and mode != 'revoke-invalid-policy':
            assert yaml.safe_load(current.read_text()) == yaml.safe_load(expected_rollback.read_text()), (mode, 'rollback ignored current policy')
        elif (not success and mode != 'rollback' and mode != 'revoke-invalid-policy') or mode in {'unchanged', 'locked'}:
            assert current.read_bytes() == before, (mode, 'running configuration was not restored')
        if mode in {'rollback', 'revoke-invalid-policy'}:
            messages = (root / 'messages').read_text() if (root / 'messages').exists() else ''
            assert 'manual recovery required' in messages, (mode, messages)
        if not success:
            assert (root / 'fault').exists(), (mode, 'fault injection was not reached', result.stderr)
            assert {p.name: p.read_bytes() for p in (root / 'cache').iterdir()} == cache_before
        if mode in {'success', 'retained', 'force'}:
            assert {p.name: p.read_bytes() for p in (root / 'cache').iterdir()} == {
                p.name: p.read_bytes() for p in (root / 'downloads').iterdir() if p.name != 'primary.yaml'}
        if mode == 'cached':
            assert {p.name: p.read_bytes() for p in (root / 'cache').iterdir()} == cache_before
        if mode in {'manual', 'manual-force', 'manual-no-cache'}:
            requested = (root / 'downloads-requested').read_text().splitlines()
            assert 'BWB.yaml' not in requested, 'manual source was downloaded'
        if mode in {'manual', 'manual-force'}:
            assert {'primary.yaml', 'ByWave.yaml', 'InfraAPI.yaml'} <= set(requested)
            assert (root / 'cache/BWB.yaml').read_bytes() == cache_before['BWB.yaml']
            final = yaml.safe_load(current.read_text())
            expected = [dict(n, name='BWB | ' + n['name'])
                        for n in yaml.safe_load(cache_before['BWB.yaml'])['proxies']]
            assert expected and [n for n in final['proxies'] if n['name'].startswith('BWB | ')] == expected, 'manual nodes were removed or replaced'
        calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
        if mode in {'validation', 'backup', 'no-cache', 'unchanged', 'locked', 'write', 'signal'}:
            assert 'reload' not in calls, (mode, calls)
        if mode in {'success', 'cached', 'retained', 'force'}:
            assert 'restart mihomo' in calls and isinstance(yaml.safe_load(current.read_text()), dict)
            assert 'reload mihomo' not in calls
        if mode.startswith('revoke-'):
            final = yaml.safe_load(current.read_text())
            assert native_group_nodes(final, 'GLaDOS Download') == {'REJECT'}, (mode, 'revoked child revived')
            assert 'GLaDOS | D1 Eligible' not in native_group_nodes(final, 'Download'), (mode, 'revoked aggregate member revived')
            if mode == 'revoke-invalid-policy':
                assert calls.count('restart mihomo') == 1, calls

        # F-05: success schedules the full interval, a hard failure schedules a
        # minute-level retry and leaves a structured reason naming the step.
        next_update = root / 'backups/next-update'
        failure_state = root / 'backups/last-failure'
        now = int(time.time())
        if success and mode not in {'locked', 'recover-only'}:
            assert next_update.exists(), (mode, 'success left no next attempt')
            delay = int(next_update.read_text().strip()) - now
            expected = 21600 if mode == 'retained' else 172800
            assert expected - 180 <= delay <= expected + 180, (mode, delay)
            assert not failure_state.exists(), (mode, 'success left a failure record')
        if not success:
            assert failure_state.exists(), (mode, 'hard failure left no structured reason')
            fields = dict(token.split('=', 1) for token in failure_state.read_text().split() if '=' in token)
            assert fields.get('step') and fields.get('rc'), (mode, fields)
            delay = int(next_update.read_text().strip()) - now
            assert 540 <= delay <= 780, (mode, delay)
        if mode == 'prepare-fail':
            fields = dict(token.split('=', 1) for token in failure_state.read_text().split() if '=' in token)
            assert fields.get('step') == 'prepare' and fields.get('rc') == '1', fields
        if mode == 'recover-only':
            assert not (root / 'backups/transaction.pending').exists(), 'recovery did not clear the pending record'
            assert not (root / 'downloads-requested').exists(), 'recovery fetched a new subscription'
            assert 'restart mihomo' in calls, calls
            assert not failure_state.exists(), 'recovery recorded a failure'
        if mode == 'due-only':
            # The timer tick honours the schedule; a manual invocation does not.
            scheduled = next_update.read_text()
            requested_before = (root / 'downloads-requested').read_text().splitlines()
            tick = subprocess.run(['sh', str(updater), '--due-only'], env=environment,
                                  capture_output=True, text=True, timeout=45)
            assert tick.returncode == 0, (mode, tick.stderr)
            assert next_update.read_text() == scheduled, 'a not-due tick moved the schedule'
            assert (root / 'downloads-requested').read_text().splitlines() == requested_before, \
                'a not-due tick fetched the subscription'
            manual = subprocess.run(['sh', str(updater)], env=environment,
                                    capture_output=True, text=True, timeout=45)
            assert manual.returncode == 0, (mode, manual.stderr)
            assert (root / 'downloads-requested').read_text().splitlines() != requested_before, \
                'a manual run did not override the schedule'


if __name__ == '__main__':
    cases = ('success', 'cached', 'retained', 'no-cache', 'validation', 'backup', 'write', 'signal',
             'installed-validation', 'reload', 'inactive', 'health', 'rollback', 'unchanged', 'force', 'locked',
             'revoke-health', 'revoke-reload', 'revoke-write', 'revoke-signal', 'revoke-invalid-policy',
             'manual', 'manual-force', 'manual-no-cache', 'dns-1053', 'dns-1053-wrong-fake',
             'prepare-fail', 'recover-only', 'due-only')
    failures = []
    for case in cases:
        try:
            check_case(case)
        except (AssertionError, subprocess.SubprocessError) as error:
            failures.append(f'{case}: {error}')
    if failures:
        raise SystemExit('\n'.join(failures))
    print(f'Subscription update simulation passed ({len(cases)} scenarios)')
