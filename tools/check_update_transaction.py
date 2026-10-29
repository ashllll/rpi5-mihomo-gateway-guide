#!/usr/bin/env python3
"""Run the updater CLI with real candidate helpers and isolated I/O/OS locks."""
import os
import base64
import hashlib
import json
import fcntl
from pathlib import Path
import shutil
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import quote

import yaml
from check_overlay import ALL_NODES, MANIFEST_OVERLAY, build_fixture
from check_subscription_update import STUB as UPDATE_BOUNDARY

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('UPDATER_UNDER_TEST', ROOT / 'automation/sbin/mihomo-update-subscription'))
NORMALIZER = Path(os.environ.get('NORMALIZER_UNDER_TEST', ROOT / 'automation/sbin/mihomo-subscription-normalize'))
LOCK_BOUNDARY = r'''
import fcntl
import time
import json
if name == 'flock':
    operation = fcntl.LOCK_UN if '-u' in args else fcntl.LOCK_EX
    if '-n' in args: operation |= fcntl.LOCK_NB
    try: fcntl.flock(int(args[-1]), operation)
    except BlockingIOError: sys.exit(1)
def maintenance_available():
    with (root / 'lock/mihomo-maintenance.lock').open('a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        return True
if name == 'curl' and '--output' in args and args[args.index('--output') + 1] != '/dev/null':
    with (root / 'lock-observations').open('a') as stream:
        stream.write('download:' + ('available' if maintenance_available() else 'held') + '\n')
    drift = os.environ.get('SUB_TEST_DRIFT')
    if drift and not (root / 'drifted').exists():
        target = {'config': root / 'etc/config.yaml',
                  'policy': Path(os.environ['MIHOMO_CAPABILITIES']),
                  'helper': root / 'bin/mihomo-policy-overlay',
                  'core': root / 'bin/mihomo',
                  'reconcile': Path(os.environ['MIHOMO_RECONCILE_TARGETS'])}[drift]
        with target.open('a') as stream: stream.write('\n# concurrent operator change\n')
        (root / 'drifted').touch()
if name == 'curl' and '--output' in args and args[args.index('--output') + 1] == '/dev/null' and os.environ.get('TX_SLOW_PROBE') and not (root / 'slow-probe').exists():
    (root / 'slow-probe').touch()
    duration = float(os.environ['TX_SLOW_PROBE'])
    limit = float(args[args.index('--max-time') + 1])
    time.sleep(min(duration, limit))
    if duration > limit: sys.exit(28)
if name == 'curl':
    with (root / 'curl-events').open('a') as stream: stream.write('curl\n')
    if '--output' in args and args[args.index('--output') + 1] == '/dev/null' and os.environ.get('TX_PARTIAL_PROBE') and 'www.gstatic.com' in args[-1]:
        (root / 'partial-probe').touch()
        print('204', end='')
        sys.exit(18)
    if '--output' in args and args[args.index('--output') + 1] == '/dev/null' and os.environ.get('TX_KILL_AT_PROBE'):
        (root / 'probe-kill').touch()
        if os.environ.get('TX_OWNER_ORPHAN'):
            (root / 'owner-child-started').write_text(str(time.monotonic()))
            code="import time; from pathlib import Path; time.sleep(5); Path(%r).write_text('late owner write')" % str(root / 'late-owner-write')
            subprocess.Popen([sys.executable,'-c',code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.kill(int(os.environ['TX_UPDATER_PID']), signal.SIGKILL)
        if os.environ.get('TX_OWNER_ORPHAN'): time.sleep(10)
        sys.exit(1)
if name == 'mihomo-healthcheck':
    with (root / 'structure-observations').open('a') as stream: stream.write(' '.join(args) + '\n')
    if os.environ.get('TX_TRACE_RECOVERY'):
        with (root / 'calls').open('a') as stream: stream.write('observe ' + ' '.join(args) + '\n')
if name in ('install', 'mv') and args[-1] == str(root / 'etc/config.yaml'):
    with (root / 'lock-observations').open('a') as stream:
        stream.write('commit:' + ('available' if maintenance_available() else 'held') + '\n')
    if Path(args[-2]).read_bytes() == Path(args[-1]).read_bytes():
        (root / 'same-bytes-apply').touch()
if name in ('install', 'mv') and Path(args[-1]).parent == root / 'cache' and mode in ('cache-write', 'cache-kill'):
    counter = root / 'cache-writes'
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    if count == 2:
        Path(args[-1]).write_text('partial cache promotion\n')
        (root / 'cache-write-failed').touch()
        if mode == 'cache-kill': os.kill(int(os.environ['TX_UPDATER_PID']), signal.SIGKILL)
        sys.exit(1)
if name == 'mv':
    expected_sync = os.environ.get('TX_EXPECT_SYNC_HASHES')
    if expected_sync and Path(args[-2]).is_file() and Path(args[-2]).read_bytes().startswith(b'accepted|'):
        (root / 'accepted-observed').touch()
        log = root / 'fsync-events'
        synced = set(log.read_text().splitlines()) if log.exists() else set()
        if not set(json.loads(expected_sync)) <= synced: (root / 'accepted-before-cache-sync').touch()
    sys.exit(subprocess.call([os.environ['SUB_TEST_MV'], *args]))
if name == 'rm':
    clearing_recovery = bool(os.environ.get('TX_TRACE_RECOVERY')) and any(
        Path(arg).is_file() and Path(arg).read_bytes().startswith((b'prepared|', b'restoring|'))
        for arg in args if not arg.startswith('-'))
    clearing_accepted = bool(os.environ.get('TX_FSYNC_LOG')) and any(
        Path(arg).is_file() and Path(arg).read_bytes().startswith(b'accepted|')
        for arg in args if not arg.startswith('-'))
    if clearing_accepted:
        paths = [root / 'cache', *(root / 'cache').rglob('*')]
        required = {str(path.stat().st_dev) + ':' + str(path.stat().st_ino) for path in paths}
        log = root / 'fsync-inodes'
        synced = set(log.read_text().splitlines()) if log.exists() else set()
        if not required <= synced: (root / 'pending-cleared-before-cache-sync').touch()
    result = subprocess.call([os.environ['SUB_TEST_RM'], *args])
    if clearing_recovery and result == 0:
        with (root / 'calls').open('a') as stream: stream.write('pending-unlink\n')
    if clearing_accepted and result == 0:
        with (root / 'fsync-order').open('a') as stream: stream.write('pending-unlink\n')
    sys.exit(result)
if name == 'mihomo-subscription-normalize':
    shutil.copyfile(args[0], args[1])
'''


class UpdateTransactionCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='update-transaction-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for name in ('etc', 'bin', 'lock', 'downloads', 'cache'):
            (self.directory / name).mkdir()
        fixture, self.capabilities = build_fixture(self.directory / 'etc', MANIFEST_OVERLAY, ALL_NODES)
        self.config = self.directory / 'etc/config.yaml'
        fixture.rename(self.config)
        current = yaml.safe_load(self.config.read_text())
        primary = dict(current, proxies=[p for p in current['proxies']
                                        if not p['name'].startswith(('ByWave | ', 'BWB | ', 'InfraAPI | '))])
        primary['proxies'][0]['server'] = '203.0.113.20'
        (self.directory / 'downloads/primary.yaml').write_text(yaml.safe_dump(primary))
        secrets = self.directory / 'etc/secrets'
        (secrets / 'subscriptions.d').mkdir(parents=True)
        (secrets / 'subscription.url').write_text('https://fixture.invalid/primary.yaml\n')
        for source_id, names in (('ByWave', ['Bulk 01']), ('BWB', ['Backup 01', 'Backup 02']), ('InfraAPI', ['Backup 01'])):
            data = {'proxies': [{'name': name, 'type': 'ss', 'server': '198.51.100.1', 'port': 443} for name in names]}
            (self.directory / 'downloads' / (source_id + '.yaml')).write_text(yaml.safe_dump(data))
            (secrets / 'subscriptions.d' / (source_id + '.url')).write_text('https://fixture.invalid/' + source_id + '.yaml\n')
            for proxy in data['proxies']:
                proxy['server'] = 'old.example.invalid'
            (self.directory / 'cache' / (source_id + '.yaml')).write_text(yaml.safe_dump(data))
        boundary = UPDATE_BOUNDARY.replace('def fault():', LOCK_BOUNDARY + '\ndef fault():')
        for name in ('flock', 'curl', 'mihomo', 'systemctl', 'install', 'mv', 'rm', 'logger', 'sleep', 'find', 'xargs',
                     'mihomo-reconcile-policy', 'mihomo-healthcheck', 'mihomo-subscription-normalize'):
            path = self.directory / 'bin' / name
            path.write_text('#!' + sys.executable + '\n' + boundary)
            path.chmod(0o755)
        for name in ('mihomo-subscription-candidate', 'mihomo-policy-overlay', 'mihomo-measurement-policy',
                     'mihomo-subscription-normalize'):
            original = ROOT / 'automation/sbin' / name
            if not original.exists():
                continue
            path = self.directory / 'bin' / name
            path.write_text('#!' + sys.executable + '\n' + original.read_text().split('\n', 1)[1])
            path.chmod(0o755)
        self.defaults = self.directory / 'etc/route-defaults.yaml'
        shutil.copyfile(ROOT / 'automation/policy/route-defaults.yaml', self.defaults)
        self.targets = self.directory / 'etc/reconcile-targets.conf'
        self.targets.write_text('Default Proxy = Auto\n')
        source = SOURCE.read_text()
        for old, new in (
            ('/etc/mihomo-automation.env', str(self.directory / 'absent.env')),
            ('/etc/mihomo', str(self.directory / 'etc')),
            ('/usr/local/sbin', str(self.directory / 'bin')),
            ('/run/lock', str(self.directory / 'lock')),
            ('/var/tmp', str(self.directory)),
            ('/var/lib/mihomo-extra-subscriptions', str(self.directory / 'cache')),
            ('/var/lib/mihomo-subscription-backups', str(self.directory / 'backups')),
            ('/var/lib/', str(self.directory / 'var-lib') + '/'),
        ):
            source = source.replace(old, new)
        self.script = self.directory / 'updater'
        self.script.write_text(source.replace('\n', '\nexport TX_UPDATER_PID=$$\n', 1))

    def run_cli(self, *args, mode='success', **settings):
        env = dict(os.environ, PATH=str(self.directory / 'bin') + os.pathsep + os.environ['PATH'],
                   MIHOMO_CAPABILITIES=str(self.capabilities), MIHOMO_ROUTE_DEFAULTS=str(self.defaults),
                   MIHOMO_RECONCILE_TARGETS=str(self.targets),
                   SUB_TEST_ROOT=str(self.directory), SUB_TEST_MODE=mode,
                   SUB_TEST_INSTALL=shutil.which('install'), SUB_TEST_MV=shutil.which('mv'), SUB_TEST_RM=shutil.which('rm'),
                   PYTHONDONTWRITEBYTECODE='1', **settings)
        return subprocess.run(['sh', str(self.script), *args], env=env,
                              capture_output=True, text=True, timeout=30)

    def stall_extra_validator(self):
        command = self.directory / 'bin/mihomo-subscription-candidate'
        original = command.with_name('candidate-unwrapped')
        command.rename(original)
        wrapper = '''import os,subprocess,sys,time
from pathlib import Path
root=Path(os.environ['SUB_TEST_ROOT'])
if sys.argv[1] == 'validate-extra' and not (root/'validator-started').exists():
    (root/'validator-started').write_text(str(time.monotonic()))
    if os.environ.get('TX_VALIDATOR_DESCENDANT'):
        code="import time; from pathlib import Path; time.sleep(8); Path(%r).write_text('late write')" % str(root/'late-validator-write')
        subprocess.Popen([sys.executable,'-c',code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(10)
os.execv(sys.executable, [sys.executable, __ORIGINAL__] + sys.argv[1:])
'''.replace('__ORIGINAL__', repr(str(original)))
        command.write_text('#!' + sys.executable + '\n' + wrapper)
        command.chmod(0o755)

    def test_download_does_not_exclude_recovery_but_commit_holds_maintenance_lock(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        observations = (self.directory / 'lock-observations').read_text().splitlines()
        self.assertIn('download:available', observations)
        self.assertNotIn('download:held', observations)
        self.assertIn('commit:held', observations)
        self.assertNotIn('commit:available', observations)

    def assert_drift_rejected(self, kind):
        before = self.config.read_bytes()
        cache = {p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}
        result = self.run_cli(SUB_TEST_DRIFT=kind)
        expected = before + b'\n# concurrent operator change\n' if kind == 'config' else before
        self.assertEqual(self.config.read_bytes(), expected)
        calls = self.directory / 'calls'
        mutations = [line for line in calls.read_text().splitlines() if line.startswith(('restart ', 'reload '))]
        self.assertFalse(mutations, mutations)
        self.assertEqual({p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}, cache)

    def test_concurrent_config_edit_during_download_is_not_overwritten(self):
        self.assert_drift_rejected('config')

    def test_concurrent_policy_edit_during_download_aborts_promotion(self):
        self.assert_drift_rejected('policy')

    def test_concurrent_policy_helper_replacement_aborts_promotion(self):
        self.assert_drift_rejected('helper')

    def test_concurrent_core_replacement_aborts_promotion(self):
        self.assert_drift_rejected('core')

    def test_concurrent_selector_target_change_aborts_promotion(self):
        self.assert_drift_rejected('reconcile')

    def test_success_restarts_core_to_replace_provider_lifecycles(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.directory / 'calls').read_text().splitlines()
        self.assertEqual([line for line in calls if line.startswith(('restart ', 'reload '))], ['restart mihomo'])

    def test_primary_base64_and_extra_uri_inputs_are_normalized_before_prepare_and_cache(self):
        for path in (self.directory / 'downloads').iterdir():
            nodes = yaml.safe_load(path.read_text())['proxies']
            uris = '\n'.join('ss://aes-128-gcm:fixture@%s:%s#%s' %
                             (node['server'], node['port'], quote(node['name'], safe='')) for node in nodes)
            path.write_text(base64.b64encode(uris.encode()).decode() if path.name == 'primary.yaml' else uris)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, 'valid base64/URI subscription was not accepted')
        candidate = yaml.safe_load(self.config.read_text())
        self.assertEqual(candidate['proxies'][0]['server'], '203.0.113.20')
        for path in (self.directory / 'cache').iterdir():
            normalized = yaml.safe_load(path.read_text())
            self.assertIsInstance(normalized, dict)
            self.assertTrue(normalized.get('proxies'))
            self.assertTrue(all(node.get('cipher') == 'aes-128-gcm' for node in normalized['proxies']))

    def test_normalizer_accepts_clash_yaml_even_when_base64_guess_is_invalid_utf8(self):
        expected = {'proxies': [{'cipher': 'aes-128-gcm', 'name': 'fixture', 'password': 'fixture',
                                  'port': 443, 'server': '203.0.113.10', 'type': 'ss'}]}
        raw = yaml.safe_dump(expected)
        compact = ''.join(raw.split())
        compact += '=' * (-len(compact) % 4)
        with self.assertRaises(UnicodeDecodeError):
            base64.b64decode(compact).decode('utf-8')
        source, destination = self.directory / 'plain-clash', self.directory / 'normalized-clash'
        source.write_text(raw)
        result = subprocess.run([sys.executable, str(NORMALIZER), str(source), str(destination)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, 'valid Clash YAML was rejected during base64 detection')
        self.assertEqual(yaml.safe_load(destination.read_text()), expected)

    def test_partial_http_transfer_cannot_satisfy_critical_acceptance(self):
        before = yaml.safe_load(self.config.read_text())['proxies']
        cache = {path.name: path.read_bytes() for path in (self.directory / 'cache').iterdir()}
        result = self.run_cli(TX_PARTIAL_PROBE='1')
        self.assertTrue((self.directory / 'partial-probe').exists(), 'partial HTTP injection was not reached')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(yaml.safe_load(self.config.read_text())['proxies'], before)
        self.assertEqual({path.name: path.read_bytes() for path in (self.directory / 'cache').iterdir()}, cache)

    def test_failed_primary_normalization_retains_baseline_primary_nodes(self):
        previous = yaml.safe_load(self.config.read_text())['proxies'][0]
        (self.directory / 'downloads/primary.yaml').write_text('unsupported://fixture.invalid/\n')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        nodes = {node['name']: node for node in yaml.safe_load(self.config.read_text())['proxies']}
        self.assertEqual(nodes[previous['name']]['server'], previous['server'])

    def test_failed_extra_normalization_retains_its_last_known_good_cache(self):
        cached = (self.directory / 'cache/BWB.yaml').read_bytes()
        (self.directory / 'downloads/BWB.yaml').write_text('unsupported://fixture.invalid/\n')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        nodes = {node['name']: node for node in yaml.safe_load(self.config.read_text())['proxies']}
        self.assertEqual(nodes['BWB | Backup 01']['server'], 'old.example.invalid')
        self.assertEqual((self.directory / 'cache/BWB.yaml').read_bytes(), cached)

    def test_extra_validator_is_bounded_by_the_update_deadline(self):
        self.stall_extra_validator()
        before = self.config.read_bytes()
        started = time.monotonic()
        result = self.run_cli(MIHOMO_SUBSCRIPTION_TIMEOUT_SECONDS='6')
        self.assertTrue((self.directory / 'validator-started').exists(), result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(self.config.read_bytes(), before)

    def test_timeout_terminates_validator_descendants_before_they_can_write(self):
        self.stall_extra_validator()
        result = self.run_cli(MIHOMO_SUBSCRIPTION_TIMEOUT_SECONDS='6', TX_VALIDATOR_DESCENDANT='1')
        self.assertNotEqual(result.returncode, 0)
        child_started = float((self.directory / 'validator-started').read_text())
        time.sleep(max(0, child_started + 9 - time.monotonic()))
        self.assertFalse((self.directory / 'late-validator-write').exists(),
                         'a timed out validator descendant continued writing')

    def test_dead_owner_terminates_blocking_command_substitution_and_releases_locks(self):
        result = self.run_cli(TX_KILL_AT_PROBE='1', TX_OWNER_ORPHAN='1',
                              MIHOMO_SUBSCRIPTION_TIMEOUT_SECONDS='60')
        self.assertNotEqual(result.returncode, 0)
        child_started = float((self.directory / 'owner-child-started').read_text())
        observed = (self.directory / 'curl-events').read_bytes()
        time.sleep(max(0, child_started + 6 - time.monotonic()))
        self.assertFalse((self.directory / 'late-owner-write').exists(),
                         'a command-substitution descendant kept writing after its updater owner died')
        self.assertEqual((self.directory / 'curl-events').read_bytes(), observed)
        for name in ('mihomo-maintenance.lock', 'mihomo-update-subscription.lock'):
            with self.subTest(lock=name), (self.directory / 'lock' / name).open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.fail('an orphan runner retained ' + name)

    def trace_fsync(self):
        hook = '''import hashlib,os,stat
original_fsync=os.fsync
def traced_fsync(fd):
    original_fsync(fd)
    metadata=os.fstat(fd)
    if stat.S_ISREG(metadata.st_mode):
        data=os.pread(fd, metadata.st_size, 0)
        with open(os.environ['TX_FSYNC_LOG'], 'a') as stream:
            stream.write(hashlib.sha256(data).hexdigest()+'\\n')
    root=os.path.dirname(os.environ['TX_FSYNC_LOG'])
    with open(os.path.join(root,'fsync-inodes'), 'a') as stream:
        stream.write(str(metadata.st_dev)+':'+str(metadata.st_ino)+'\\n')
    backup=os.path.join(root,'backups')
    if stat.S_ISDIR(metadata.st_mode) and os.path.isdir(backup):
        expected=os.stat(backup)
        if (metadata.st_dev,metadata.st_ino)==(expected.st_dev,expected.st_ino):
            with open(os.path.join(root,'fsync-order'), 'a') as stream: stream.write('backup-directory-sync\\n')
os.fsync=traced_fsync
'''
        (self.directory / 'sitecustomize.py').write_text(hook)
        return dict(PYTHONPATH=str(self.directory), TX_FSYNC_LOG=str(self.directory / 'fsync-events'))

    def test_cache_snapshots_are_fsynced_before_the_accepted_marker_is_published(self):
        hashes = {hashlib.sha256(path.read_bytes()).hexdigest()
                  for folder in ('cache', 'downloads') for path in (self.directory / folder).iterdir()
                  if path.name != 'primary.yaml'}
        result = self.run_cli(**self.trace_fsync(), TX_EXPECT_SYNC_HASHES=json.dumps(sorted(hashes)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.directory / 'accepted-observed').exists())
        self.assertFalse((self.directory / 'accepted-before-cache-sync').exists(),
                         'accepted was published before its cache snapshots were durable')
        self.assertFalse((self.directory / 'pending-cleared-before-cache-sync').exists(),
                         'pending was removed before live cache files and directories were durable')
        order = (self.directory / 'fsync-order').read_text().splitlines()
        unlink = order.index('pending-unlink')
        self.assertIn('backup-directory-sync', order[unlink + 1:],
                      'pending removal was not followed by durable backup-directory metadata')

    def test_failed_cache_promotion_preserves_accepted_core_and_restores_known_good_cache(self):
        before = yaml.safe_load(self.config.read_text())
        cache = {p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}
        result = self.run_cli(mode='cache-write', **self.trace_fsync())
        self.assertTrue((self.directory / 'cache-write-failed').exists(), result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual({p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}, cache)
        self.assertNotEqual(yaml.safe_load(self.config.read_text())['proxies'], before['proxies'])
        calls = (self.directory / 'calls').read_text().splitlines()
        self.assertEqual(len([line for line in calls if line.startswith(('restart ', 'reload '))]), 1)
        messages = (self.directory / 'messages').read_text().lower()
        self.assertIn('accept', messages)
        self.assertIn('cache', messages)
        order = self.directory / 'fsync-order'
        self.assertNotIn('pending-unlink', order.read_text() if order.exists() else '')

    def test_repeated_failed_candidate_is_cooled_down_until_explicit_force(self):
        first = self.run_cli(mode='health')
        self.assertNotEqual(first.returncode, 0)
        calls = self.directory / 'calls'
        before = calls.read_text().splitlines()
        again = self.run_cli(mode='health')
        self.assertEqual(again.returncode, 0, again.stderr)
        new = calls.read_text().splitlines()[len(before):]
        self.assertFalse([line for line in new if line.startswith(('restart ', 'reload '))], new)
        before = calls.read_text().splitlines()
        forced = self.run_cli('--force', mode='health')
        self.assertNotEqual(forced.returncode, 0)
        new = calls.read_text().splitlines()[len(before):]
        self.assertTrue([line for line in new if line.startswith(('restart ', 'reload '))], new)

    def test_total_network_deadline_rolls_back_unaccepted_candidate_and_keeps_cache(self):
        before = yaml.safe_load(self.config.read_text())
        cache = {p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}
        started = time.monotonic()
        result = self.run_cli(MIHOMO_SUBSCRIPTION_TIMEOUT_SECONDS='6', TX_SLOW_PROBE='10')
        self.assertTrue((self.directory / 'slow-probe').exists(), result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(yaml.safe_load(self.config.read_text())['proxies'], before['proxies'])
        self.assertEqual({p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}, cache)

    def test_sigkill_during_accepted_cache_promotion_repairs_cache_without_core_restart(self):
        before = yaml.safe_load(self.config.read_text())
        result = self.run_cli(mode='cache-kill')
        self.assertTrue((self.directory / 'cache-write-failed').exists(), result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(yaml.safe_load(self.config.read_text())['proxies'], before['proxies'])
        accepted = self.config.read_bytes()
        calls = self.directory / 'calls'
        previous_calls = calls.read_text().splitlines()
        recovered = self.run_cli()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(self.config.read_bytes(), accepted)
        new_calls = calls.read_text().splitlines()[len(previous_calls):]
        self.assertFalse([line for line in new_calls if line.startswith(('restart ', 'reload '))], new_calls)
        for path in (self.directory / 'downloads').iterdir():
            if path.name != 'primary.yaml':
                self.assertEqual(yaml.safe_load((self.directory / 'cache' / path.name).read_text()),
                                 yaml.safe_load(path.read_text()))

    def test_normalizer_rejection_never_echoes_uri_credentials_or_parse_values(self):
        command = NORMALIZER
        for kind in ('unknown-scheme', 'scheme-value', 'invalid-port'):
            with self.subTest(kind=kind):
                token = 'secret-' + secrets.token_hex(6)
                uri = {'unknown-scheme': 'x://' + token + '@fixture.invalid/',
                       'scheme-value': token + '://fixture.invalid/',
                       'invalid-port': 'ss://aes-128-gcm:fixture@203.0.113.10:' + token}[kind]
                source, destination = self.directory / 'invalid-subscription', self.directory / 'normalized'
                source.write_text(uri + '\n')
                result = subprocess.run([sys.executable, str(command), str(source), str(destination)],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(token not in result.stdout + result.stderr,
                                'normalizer exposed a synthetic credential or rejected value')

    def test_sigkill_before_acceptance_recovers_old_nodes_locally_before_any_download(self):
        before = yaml.safe_load(self.config.read_text())
        cache = {p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}
        result = self.run_cli(TX_KILL_AT_PROBE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.directory / 'probe-kill').exists())
        self.assertNotEqual(yaml.safe_load(self.config.read_text())['proxies'], before['proxies'])
        curl_events = (self.directory / 'curl-events').read_bytes()
        recovered = self.run_cli()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(yaml.safe_load(self.config.read_text())['proxies'], before['proxies'])
        self.assertEqual((self.directory / 'curl-events').read_bytes(), curl_events)
        self.assertIn('--observe-only', (self.directory / 'structure-observations').read_text())
        self.assertEqual({p.name: p.read_bytes() for p in (self.directory / 'cache').iterdir()}, cache)

    def test_pending_recovery_does_not_overwrite_a_concurrent_config_change(self):
        result = self.run_cli(TX_KILL_AT_PROBE='1')
        self.assertNotEqual(result.returncode, 0)
        with self.config.open('a') as stream:
            stream.write('\n# independent configuration installed after crash\n')
        independent = self.config.read_bytes()
        curl_events = (self.directory / 'curl-events').read_bytes()
        calls = (self.directory / 'calls').read_text().splitlines()
        recovered = self.run_cli()
        self.assertNotEqual(recovered.returncode, 0)
        self.assertEqual(self.config.read_bytes(), independent)
        self.assertEqual((self.directory / 'curl-events').read_bytes(), curl_events)
        new_calls = (self.directory / 'calls').read_text().splitlines()[len(calls):]
        self.assertFalse([line for line in new_calls if line.startswith(('restart ', 'reload '))], new_calls)

    def test_same_bytes_force_restart_failure_still_restores_runtime(self):
        accepted = self.run_cli()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        marker = self.directory / 'same-bytes-apply'
        marker.unlink(missing_ok=True)
        baseline = self.config.read_bytes()
        previous_calls = (self.directory / 'calls').read_text().splitlines()
        observations = self.directory / 'structure-observations'
        previous_observations = observations.read_text().splitlines() if observations.exists() else []
        result = self.run_cli('--force', mode='reload', TX_TRACE_RECOVERY='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker.exists(), 'forced candidate did not exercise equal configuration bytes')
        self.assertEqual(self.config.read_bytes(), baseline)
        calls = (self.directory / 'calls').read_text().splitlines()[len(previous_calls):]
        self.assertEqual([line for line in calls if line.startswith(('restart ', 'reload '))],
                         ['restart mihomo', 'restart mihomo'])
        self.assertIn('is-active --quiet mihomo', calls[calls.index('restart mihomo') + 1:])
        self.assertIn('--observe-only', observations.read_text().splitlines()[len(previous_observations):])
        self.assertLess(calls.index('observe --observe-only'), calls.index('pending-unlink'))

    def test_same_bytes_force_sigkill_recovers_prepared_runtime_without_network(self):
        accepted = self.run_cli()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        marker = self.directory / 'same-bytes-apply'
        marker.unlink(missing_ok=True)
        baseline = self.config.read_bytes()
        killed = self.run_cli('--force', TX_KILL_AT_PROBE='1')
        self.assertNotEqual(killed.returncode, 0)
        self.assertTrue(marker.exists(), 'forced candidate did not exercise equal configuration bytes')
        self.assertTrue((self.directory / 'probe-kill').exists())
        previous_calls = (self.directory / 'calls').read_text().splitlines()
        observations = self.directory / 'structure-observations'
        previous_observations = observations.read_text().splitlines() if observations.exists() else []
        network = (self.directory / 'curl-events').read_bytes()
        recovered = self.run_cli(TX_TRACE_RECOVERY='1')
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(self.config.read_bytes(), baseline)
        calls = (self.directory / 'calls').read_text().splitlines()[len(previous_calls):]
        self.assertEqual([line for line in calls if line.startswith(('restart ', 'reload '))], ['restart mihomo'])
        self.assertIn('is-active --quiet mihomo', calls)
        self.assertIn('--observe-only', observations.read_text().splitlines()[len(previous_observations):])
        self.assertEqual((self.directory / 'curl-events').read_bytes(), network)
        self.assertLess(calls.index('observe --observe-only'), calls.index('pending-unlink'))


if __name__ == '__main__':
    unittest.main()
