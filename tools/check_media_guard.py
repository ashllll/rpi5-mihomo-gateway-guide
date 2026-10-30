#!/usr/bin/env python3
"""Exercise media admission through the overlay and transactional runner."""
import copy
from contextlib import contextmanager
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import time
import unittest

import yaml

sys.dont_write_bytecode = True
import check_overlay as fixture

ROOT = Path(__file__).resolve().parents[1]


def media_settings():
    return dict(enabled=True, interval_seconds=600, sample_bytes=1048576,
                timeout_seconds=8, min_bps=262144, recovery_bps=393216,
                bad_samples=2, good_samples=2, cooldown_seconds=900,
                max_age_seconds=21600, samples_per_group=2,
                profiles={g: dict(sample_url='https://media.example.invalid/sample.mp4',
                                  expected_content_type='video/mp4', extra_patterns=[])
                          for g in ('Social', 'Streaming')})


def prepare(directory):
    source, manifest = fixture.build_fixture(directory, fixture.MANIFEST_OVERLAY, fixture.ALL_NODES)
    policy = yaml.safe_load(manifest.read_text())
    policy['measurement'] = dict(enabled=True, native_budget=True, enforce_budget=False,
                                 native_interval_seconds=900,
                                 accounts={'fixture': {'names': fixture.ALL_NODES}})
    policy['media_admission'] = media_settings()
    manifest.write_text(yaml.safe_dump(policy))
    output = directory / 'config.yaml'
    result = fixture.run_overlay(source, manifest, output)
    if result.returncode:
        raise AssertionError(result.stderr)
    return output, manifest


def good_probe(*arguments, **keywords):
    return dict(name=arguments[1], success=True, status=206, bytes=1048576,
                elapsed=1, bps=1048576, error=None)


@contextmanager
def controller(config_path):
    configuration = yaml.safe_load(config_path.read_text())
    providers = {n: s for n, s in configuration['proxy-providers'].items() if 'x-media-admission' in s}
    live = {n: [] for n in providers}
    faults = {'ignore_next_refresh': False, 'fail_refresh_number': None,
              'refreshes': 0, 'put_groups': []}
    for name, spec in providers.items():
        path = config_path.parent / spec['path']
        if path.exists():
            live[name] = [n['name'] for n in yaml.safe_load(path.read_text())['proxies']]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path.startswith('/providers/proxies/'):
                name = self.path.rsplit('/', 1)[-1]
                value = {'proxies': [{'name': n} for n in live[name]]}
            else:
                group = self.path.rsplit('/', 1)[-1]
                name = next(n for n, s in providers.items() if s['x-media-admission']['group'] == group)
                members = live[name] or ['REJECT']
                value = dict(all=members, now=members[0], fixed='')
            data = json.dumps(value).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(data)
        def do_PUT(self):
            faults['refreshes'] += 1
            name = self.path.rsplit('/', 1)[-1]
            faults['put_groups'].append(providers[name]['x-media-admission']['group'])
            if faults['refreshes'] == faults['fail_refresh_number']:
                self.send_response(500); self.end_headers(); return
            if faults['ignore_next_refresh']:
                faults['ignore_next_refresh'] = False
            else:
                path = config_path.parent / providers[name]['path']
                members = [n['name'] for n in yaml.safe_load(path.read_text())['proxies']]
                if not members:
                    self.send_response(503); self.end_headers(); return
                live[name] = members
            self.send_response(204); self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    configuration['external-controller'] = '127.0.0.1:' + str(server.server_port)
    config_path.write_text(yaml.safe_dump(configuration))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield faults, live
    finally:
        server.shutdown(); thread.join(); server.server_close()


class MediaGuardTests(unittest.TestCase):
    def test_media_health_preserves_stability_ceiling_and_explicit_shorter_interval(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, manifest = prepare(directory)
            policy = yaml.safe_load(manifest.read_text())
            policy['stability']['max_interval'] = 250
            policy['stability'].setdefault('groups', {}).setdefault('Social', {})['interval'] = 120
            manifest.write_text(yaml.safe_dump(policy))
            output = directory / 'ceiling.yaml'
            result = fixture.run_overlay(config, manifest, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            candidate = yaml.safe_load(output.read_text())
            intervals = {p['x-media-admission']['group']: p['health-check']['interval']
                         for p in candidate['proxy-providers'].values() if 'x-media-admission' in p}
            self.assertEqual(intervals, {'Social': 120, 'Streaming': 250})
            guard = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-media-guard'))
            guard['active_providers'](candidate, policy)
            # Two leaves per group cost 2*3600/120 + 2*3600/250 = 88.8 checks/hour.
            metered = copy.deepcopy(candidate)
            metered['proxy-groups'] = [g for g in metered['proxy-groups'] if g['name'] in intervals]
            metered['proxy-providers'] = {n: p for n, p in metered['proxy-providers'].items()
                                          if 'x-media-admission' in p}
            policy['measurement']['enforce_budget'] = True
            policy['measurement']['accounts']['fixture']['native_probes_per_hour'] = 88
            with self.assertRaisesRegex(ValueError, 'exceeds account budget'):
                guard['MEASUREMENT']['configure_measurement'](copy.deepcopy(metered), policy)
            policy['measurement']['accounts']['fixture']['native_probes_per_hour'] = 89
            guard['MEASUREMENT']['configure_measurement'](metered, policy)
            repeated = directory / 'repeated.yaml'
            self.assertEqual(fixture.run_overlay(output, manifest, repeated).returncode, 0)
            self.assertEqual(output.read_bytes(), repeated.read_bytes())

    def runner(self, directory):
        guard = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-media-guard'))
        config, manifest = prepare(directory)
        args = guard['parse_args'](['--config', str(config), '--manifest', str(manifest),
                                   '--state', str(directory / 'state.json'),
                                   '--budget', str(directory / 'budget.json'),
                                   '--lock-dir', str(directory / 'locks'), '--prepare', '--force'])
        return guard, args

    def test_unchanged_interval_does_not_refresh_provider_or_probe(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            for _ in range(2):
                guard['run_cycle'](args, probe=good_probe)
            with controller(args.config) as (faults, _):
                args.prepare = False
                args.force = False
                def forbidden(*a, **kw): self.fail('interval guard sampled again')
                guard['run_cycle'](args, probe=forbidden)
                self.assertEqual(faults['refreshes'], 0)

    def test_subscription_change_inherits_only_unchanged_node_qualifications(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            for _ in range(2):
                guard['run_cycle'](args, probe=good_probe)
            before = args.state.read_bytes()
            config = yaml.safe_load(args.config.read_text())
            first = next(p for p in config['proxy-providers'].values() if 'x-media-admission' in p)
            changed = first['x-media-admission']['candidates'][0]
            next(n for n in config['proxies'] if n['name'] == changed)['server'] = 'new.example.invalid'
            args.config.write_text(yaml.safe_dump(config))
            refreshed = Path(raw) / 'refreshed.yaml'
            result = fixture.run_overlay(args.config, args.manifest, refreshed)
            self.assertEqual(result.returncode, 0, result.stderr)
            args.config = refreshed
            args.check_ready = True
            args.force = False
            result = guard['run_cycle'](args, probe=lambda *a, **kw: self.fail('readiness sampled'))
            self.assertEqual(result['status'], 'ready')
            self.assertTrue(all(changed not in names for names in result['groups'].values()))
            self.assertEqual(args.state.read_bytes(), before)
            # Changed sampling contract cannot inherit any qualification.
            policy = yaml.safe_load(args.manifest.read_text())
            policy['media_admission']['min_bps'] += 1
            args.manifest.write_text(yaml.safe_dump(policy))
            updated = Path(raw) / 'updated.yaml'
            self.assertEqual(fixture.run_overlay(refreshed, args.manifest, updated).returncode, 0)
            args.config = updated
            result = guard['run_cycle'](args, probe=lambda *a, **kw: self.fail('readiness sampled'))
            self.assertEqual(result['status'], 'not-ready')

    def test_slow_removal_is_visible_in_runtime_and_unrelated_config_is_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            for _ in range(2):
                guard['run_cycle'](args, probe=good_probe)
            with controller(args.config) as (_, live):
                before = args.config.read_bytes()
                args.prepare = False
                def slow(*a, **kw):
                    return {**good_probe(*a, **kw), 'elapsed': 6}
                guard['run_cycle'](args, probe=slow)
                self.assertTrue(all(len(names) == 2 for names in live.values()))
                guard['run_cycle'](args, probe=slow)
                self.assertTrue(all(names == ['REJECT'] for names in live.values()))
                self.assertEqual(args.config.read_bytes(), before)

    def test_refresh_readback_failure_restores_file_but_retains_sample_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            guard['run_cycle'](args, probe=good_probe)
            before = args.state.read_bytes()
            with controller(args.config) as (faults, live):
                args.prepare = False
                faults['ignore_next_refresh'] = True
                with self.assertRaises(ValueError):
                    guard['run_cycle'](args, probe=good_probe)
                after = json.loads(args.state.read_text())
                self.assertNotEqual(args.state.read_bytes(), before)
                self.assertTrue(all(not p['applied'] for p in after['profiles'].values()))
                self.assertTrue(all(names == ['REJECT'] for names in live.values()))
                self.assertTrue(all(yaml.safe_load(p.read_text())['proxies'] == [{'name':'REJECT','type':'reject'}]
                                    for p in (Path(raw) / 'providers').glob('*.yaml')))

    def test_second_group_publish_failure_keeps_slow_isolation_state(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            for _ in range(2):
                guard['run_cycle'](args, probe=good_probe)
            with controller(args.config) as (faults, live):
                args.prepare = False
                args.force = True

                def slow(*a, **kw):
                    return {**good_probe(*a, **kw), 'elapsed': 6}

                guard['run_cycle'](args, probe=slow)
                self.assertTrue(all(len(names) == 2 for names in live.values()))
                faults['refreshes'] = 0
                faults['put_groups'] = []
                faults['fail_refresh_number'] = 2
                with self.assertRaises(guard['SAMPLER']['ControllerError']):
                    guard['run_cycle'](args, probe=slow)
                self.assertGreaterEqual(faults['refreshes'], 3)
                published_group = faults['put_groups'][0]
                providers = yaml.safe_load(args.config.read_text())['proxy-providers']
                self.assertEqual(live[next(
                    name for name, spec in providers.items()
                    if 'x-media-admission' in spec
                    and spec['x-media-admission']['group'] == published_group)], ['REJECT'])
                state_after_publish_failure = json.loads(args.state.read_text())
                now = time.time()
                self.assertTrue(all(
                    all(not node['admitted'] and node['isolated_until'] > now
                        for node in profile['candidates'].values())
                    for profile in state_after_publish_failure['profiles'].values()))

                result = guard['run_cycle'](args, probe=lambda *a, **kw: {
                    'name': a[1], 'success': False, 'status': 403, 'bytes': 0,
                    'elapsed': 0.1, 'error': 'HTTPError',
                })
                self.assertTrue(all(not names for names in result['groups'].values()))
                self.assertTrue(all(names == ['REJECT'] for names in live.values()))

    def test_maintenance_busy_keeps_slow_isolation_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            for _ in range(2):
                guard['run_cycle'](args, probe=good_probe)
            with controller(args.config) as (_, live):
                args.prepare = False
                args.force = True

                def slow(*a, **kw):
                    return {**good_probe(*a, **kw), 'elapsed': 6}

                guard['run_cycle'](args, probe=slow)
                maintenance_path = args.lock_dir / 'mihomo-maintenance.lock'
                with maintenance_path.open('a+b') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        result = guard['run_cycle'](args, probe=slow)
                        self.assertEqual(result['status'], 'maintenance-busy')
                        state = json.loads(args.state.read_text())
                        now = time.time()
                        self.assertTrue(all(
                            all(not node['admitted'] and node['isolated_until'] > now
                                for node in profile['candidates'].values())
                            for profile in state['profiles'].values()))
                        self.assertTrue(all(len(names) == 2 for names in live.values()))
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)

                result = guard['run_cycle'](args, probe=lambda *a, **kw: {
                    'name': a[1], 'success': False, 'status': 403, 'bytes': 0,
                    'elapsed': 0.1, 'error': 'HTTPError',
                })
                self.assertTrue(all(not names for names in result['groups'].values()))
                self.assertTrue(all(names == ['REJECT'] for names in live.values()))

    def test_disabled_readiness_accepts_long_validity_without_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            guard, args = self.runner(directory)
            policy = yaml.safe_load(args.manifest.read_text())
            policy['media_admission']['enabled'] = False
            args.manifest.write_text(yaml.safe_dump(policy))
            ready_args = guard['parse_args']([
                '--config', str(args.config), '--manifest', str(args.manifest),
                '--state', str(args.state), '--budget', str(args.budget),
                '--lock-dir', str(args.lock_dir), '--check-ready', '--valid-for', '7200',
            ])
            result = guard['run_cycle'](ready_args)
            self.assertEqual(result, {'status': 'disabled'})
            self.assertFalse(args.state.exists())
            self.assertFalse((directory / 'providers').exists())

    def test_sample_cap_one_bounds_each_group_and_native_debit(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            guard, args = self.runner(directory)
            policy = yaml.safe_load(args.manifest.read_text())
            policy['media_admission']['samples_per_group'] = 1
            args.manifest.write_text(yaml.safe_dump(policy))
            config = directory / 'capped.yaml'
            result = fixture.run_overlay(args.config, args.manifest, config)
            self.assertEqual(result.returncode, 0, result.stderr)
            args.config = config
            # Preseed an acknowledged active member so the controller's `now` and
            # the independent cursor suggest different candidates on the next run.
            guard['run_cycle'](args, probe=good_probe)
            guard['run_cycle'](args, probe=good_probe)
            prior_debit = sum(row[1] for row in json.loads(args.budget.read_text())['accounts']['fixture'])
            with controller(args.config) as (_, _live):
                args.prepare = False
                args.force = True
                sampled = []
                sampled_bytes = []

                def recording_probe(*a, **kw):
                    sampled.append(a[1])
                    result = good_probe(*a, **kw)
                    sampled_bytes.append(result['bytes'])
                    return result

                for _ in range(2):
                    before = len(sampled)
                    result = guard['run_cycle'](args, probe=recording_probe)
                    round_samples = result['samples']
                    self.assertEqual(len(round_samples), 2)
                    self.assertEqual(sum(row['bytes'] for row in round_samples), 2 * 1048576)
                    self.assertEqual(len(sampled) - before, 2)
                    self.assertEqual({row['group'] for row in round_samples}, {'Social', 'Streaming'})
                self.assertEqual(len(sampled), 4)
            after_debit = sum(row[1] for row in json.loads(args.budget.read_text())['accounts']['fixture'])
            self.assertEqual(after_debit - prior_debit, 24)
            self.assertEqual(sum(sampled_bytes), 4 * policy['media_admission']['sample_bytes'])

    def test_budget_denial_does_not_call_probe(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            policy = yaml.safe_load(args.manifest.read_text())
            policy['measurement'].update(enforce_budget=True)
            args.manifest.write_text(yaml.safe_dump(policy))
            def forbidden(*a, **kw):
                self.fail('denied account caused network sampling')
            result = guard['run_cycle'](args, probe=forbidden)
            self.assertTrue(result['samples'])
            self.assertTrue(all(r['error'] == 'AdmissionDenied' for r in result['samples']))

    def test_policy_change_during_sampling_discards_files_but_retains_debit(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            def changed(*a, **kw):
                args.manifest.write_text(args.manifest.read_text() + '\n# changed\n')
                return good_probe(*a, **kw)
            result = guard['run_cycle'](args, probe=changed)
            self.assertEqual(result['status'], 'inputs-changed')
            self.assertFalse(args.state.exists())
            self.assertFalse((Path(raw) / 'providers').exists())
            ledger = json.loads(args.budget.read_text())
            self.assertEqual(sum(row[1] for row in ledger['accounts']['fixture']), 6)

    def test_expiration_before_startup_clears_only_media_files_without_probes(self):
        with tempfile.TemporaryDirectory() as raw:
            guard, args = self.runner(Path(raw))
            for _ in range(2):
                guard['run_cycle'](args, probe=good_probe)
            state = json.loads(args.state.read_text())
            for profile in state['profiles'].values():
                for node in profile['candidates'].values():
                    node['last_good'] = 1
            args.state.write_text(json.dumps(state))
            args.expire_only = True
            def forbidden(*a, **kw): self.fail('startup made a network probe')
            result = guard['run_cycle'](args, probe=forbidden)
            self.assertTrue(all(not names for names in result['groups'].values()))

    def test_preparation_requires_two_real_samples_and_keeps_accounting(self):
        guard = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-media-guard'))
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, manifest = prepare(directory)
            args = guard['parse_args'](['--config', str(config), '--manifest', str(manifest),
                                       '--state', str(directory / 'state.json'),
                                       '--budget', str(directory / 'budget.json'),
                                       '--lock-dir', str(directory / 'locks'), '--prepare', '--force'])
            def probe(*arguments, **keywords):
                return dict(name=arguments[1], success=True, status=206, bytes=1048576,
                            elapsed=1, bps=1048576, error=None)
            guard['run_cycle'](args, probe=probe)
            generated = yaml.safe_load(config.read_text())
            paths = [directory / p['path'] for p in generated['proxy-providers'].values()
                     if 'x-media-admission' in p]
            self.assertTrue(paths)
            self.assertTrue(all(yaml.safe_load(p.read_text())['proxies'] == [{'name':'REJECT','type':'reject'}] for p in paths))
            guard['run_cycle'](args, probe=probe)
            self.assertTrue(all(len(yaml.safe_load(p.read_text())['proxies']) == 2 for p in paths))
            ledger = json.loads((directory / 'budget.json').read_text())
            self.assertEqual(sum(row[1] for row in ledger['accounts']['fixture']), 48)

    def test_overlay_media_owners_are_empty_until_admission_and_survive_refresh(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, manifest = prepare(directory)
            first = yaml.safe_load(config.read_text())
            for group in ('Social', 'Streaming'):
                entry = next(g for g in first['proxy-groups'] if g['name'] == group)
                provider = first['proxy-providers'][entry['use'][0]]
                self.assertEqual(provider['type'], 'file')
                self.assertEqual(provider['payload'], [])
                self.assertEqual(entry['empty-fallback'], 'REJECT')
                self.assertEqual(provider['x-media-admission']['group'], group)
            second = directory / 'second.yaml'
            result = fixture.run_overlay(config, manifest, second)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_bytes(), second.read_bytes())


if __name__ == '__main__':
    unittest.main()
