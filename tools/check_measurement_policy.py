#!/usr/bin/env python3
"""Offline contracts for measurement ownership and account reservations."""
import copy
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'automation/sbin/mihomo-measurement-policy'


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.q = runpy.run_path(str(POLICY))

    def config(self):
        return {'proxies': [{'name': n, 'type': 'http', 'server': '127.0.0.1', 'port': 1}
                            for n in ['A | one', 'A | two']],
                'proxy-groups': [{'name': n, 'type': 'url-test',
                                  'proxies': ['A | one', 'A | two'], 'url': 'https://example.invalid/',
                                  'interval': 45, 'lazy': False} for n in ['Web', 'AI']],
                'rules': ['MATCH,Web']}

    def settings(self):
        return {'enabled': True, 'native_budget': True, 'native_interval_seconds': 600,
                'accounts': {'account-a': {'prefixes': ['A | '],
                                          'native_probes_per_hour': 12,
                                          'quality_attempts_per_hour': 6}}}

    def test_pause_covers_every_generated_group(self):
        c = self.config()
        self.q['configure_measurement'](c, {})
        self.assertTrue(all(g['type'] == 'select' and 'url' not in g for g in c['proxy-groups']))
        self.assertEqual(c['rules'], ['MATCH,Web'])

    def test_identical_checks_have_one_owner(self):
        c = self.config()
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        groups = c['proxy-groups']
        self.assertEqual(groups[0]['use'], groups[1]['use'])
        self.assertNotIn('proxies', groups[0])
        self.assertEqual(len(c['proxy-providers']), 1)

    def test_padded_urls_share_one_owner_before_plan_budget_check(self):
        for padding in (' ', '\t', '\u00a0', '\u0085'):
            with self.subTest(padding=repr(padding)):
                c = self.config()
                c['proxy-groups'][1]['url'] = padding + 'https://example.invalid/' + padding
                self.q['configure_measurement'](c, {'measurement': self.settings()})
                self.assertEqual(c['proxy-groups'][0]['use'], c['proxy-groups'][1]['use'])
                self.assertEqual(len(c['proxy-providers']), 1)
                self.assertEqual(next(iter(c['proxy-providers'].values()))['health-check']['url'],
                                 'https://example.invalid/')
                once = copy.deepcopy(c)
                self.q['configure_measurement'](c, {'measurement': self.settings()})
                self.assertEqual(c, once)

    def test_padded_urls_cannot_hide_status_conflicts(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                c, settings = self.config(), self.settings()
                settings['accounts']['account-a']['native_probes_per_hour'] = 100
                c['proxy-groups'][0]['expected-status'] = '204'
                c['proxy-groups'][1].update(url='  https://example.invalid/  ',
                                           **{'expected-status': '401'})
                if existing:
                    c['proxy-providers'] = {}
                    for group in c['proxy-groups']:
                        c['proxy-providers'][group['name']] = {'type': 'inline',
                            'payload': copy.deepcopy(c['proxies']), 'health-check': {
                                'enable': True, 'url': group['url'], 'interval': 600,
                                'expected-status': group['expected-status']}}
                        group.pop('proxies')
                        group['use'] = [group['name']]
                with self.assertRaisesRegex(ValueError, 'conflicting expected'):
                    self.q['configure_measurement'](c, {'measurement': settings})

    def test_existing_provider_and_group_compare_effective_url(self):
        c = self.config()
        c['proxy-providers'] = {'external': {'type': 'inline',
            'payload': copy.deepcopy(c['proxies']), 'health-check': {
                'enable': True, 'url': '  https://example.invalid/  ',
                'interval': 600, 'expected-status': '204'}}}
        for group in c['proxy-groups']:
            group.pop('proxies')
            group['use'] = ['external']
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        self.assertEqual(c['proxy-providers']['external']['health-check']['url'],
                         'https://example.invalid/')
        self.assertTrue(all(g['expected-status'] == '204' for g in c['proxy-groups']))

    def test_enabled_provider_requires_nonempty_effective_url(self):
        for enabled, provider_enabled in ((False, True), (True, True), (True, False)):
            for url in ('', ' \t\u00a0 ', None):
                with self.subTest(measurement_enabled=enabled, provider_enabled=provider_enabled,
                                  url=repr(url)):
                    c, settings = self.config(), self.settings()
                    settings['enabled'] = enabled
                    c['proxy-providers'] = {'external': {'type': 'inline',
                        'payload': copy.deepcopy(c['proxies']), 'health-check': {
                            'enable': provider_enabled, 'url': url, 'interval': 600}}}
                    for group in c['proxy-groups']:
                        group.pop('proxies')
                        group.pop('url')
                        group['use'] = ['external']
                    if enabled and provider_enabled:
                        with self.assertRaisesRegex(ValueError, 'explicit check URL'):
                            self.q['configure_measurement'](c, {'measurement': settings})
                    else:
                        self.q['configure_measurement'](c, {'measurement': settings})
                        self.assertFalse(c['proxy-providers']['external']['health-check']['enable'])

    def test_native_plan_rejects_account_over_budget(self):
        settings = self.settings()
        settings['accounts']['account-a']['native_probes_per_hour'] = 1
        with self.assertRaisesRegex(ValueError, 'native.*budget'):
            self.q['configure_measurement'](self.config(), {'measurement': settings})

    def test_native_requires_the_core_budget_contract(self):
        settings = self.settings()
        settings.pop('native_budget')
        with self.assertRaisesRegex(ValueError, 'core budget'):
            self.q['configure_measurement'](self.config(), {'measurement': settings})

    def test_alias_paths_cannot_split_the_existing_account_ledger(self):
        settings = self.settings()
        settings['accounts']['account-a']['quality_attempts_per_hour'] = 8
        with tempfile.TemporaryDirectory() as raw:
            ledger, alias = Path(raw)/'native.json', Path(raw)/'alias.json'
            alias.symlink_to(ledger)
            nodes = self.config()['proxies'][:1]
            self.assertTrue(self.q['reserve_native'](ledger, settings, nodes, 2, 10000))
            self.assertTrue(self.q['reserve_native'](alias, settings, nodes, 6, 10000))
            self.assertTrue(alias.is_symlink())
            self.assertFalse(self.q['reserve_native'](ledger, settings, nodes, 2, 10000))
            self.assertEqual(sum(e[1] for e in json.loads(ledger.read_text())['accounts']['account-a']), 8)

    def test_native_attempt_budget_uses_the_existing_key_and_rolling_ledger(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / 'budget.json'
            reserve = self.q['reserve_native']
            nodes = self.config()['proxies'][:1]
            self.assertTrue(reserve(ledger, self.settings(), nodes, 4, 10000))
            self.assertFalse(reserve(ledger, self.settings(), nodes, 4, 10001))
            self.assertFalse(reserve(ledger, self.settings(), nodes, 1, 9999))
            self.assertTrue(reserve(ledger, self.settings(), nodes, 4, 13601))
            self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)

    def test_unknown_account_cannot_probe(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assertFalse(self.q['reserve_native'](Path(raw)/'budget.json', self.settings(),
                                                     [{'name': 'Unknown'}], 1, 10000))

    def test_semantically_invalid_ledger_cannot_authorize_requests(self):
        invalid = [-100, 0, True, 1.5, float('nan'), float('inf'), '6']
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / 'budget.json'
            for amount in invalid:
                with self.subTest(amount=amount):
                    ledger.write_text(json.dumps({'accounts': {'account-a': [[10000, amount]]},
                                                  'last_reservation_at': 10000}))
                    before = ledger.read_bytes()
                    with self.assertRaises(ValueError):
                        self.q['reserve_native'](ledger, self.settings(), self.config()['proxies'][:1], 6, 10001)
                    self.assertEqual(ledger.read_bytes(), before)

    def test_runtime_timestamp_is_taken_after_waiting_for_shared_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / 'budget.json'
            with ledger.with_suffix('.lock').open('a+b') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                started = threading.Event()
                clock = [10000]
                def attempt():
                    started.set()
                    return self.q['reserve_native'](ledger, self.settings(), self.config()['proxies'][:1], 6)
                with patch('time.time', side_effect=lambda: clock[0]), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(attempt)
                    self.assertTrue(started.wait(1))
                    clock[0] = 14000
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    self.assertTrue(future.result(timeout=2))
            self.assertEqual(json.loads(ledger.read_text())['last_reservation_at'], 14000)

    def test_native_plan_preserves_contract_and_reapply(self):
        c = self.config()
        c['proxy-groups'][1]['proxies'].reverse()
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        once = copy.deepcopy(c)
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        self.assertEqual(c, once)
        self.assertNotEqual(c['proxy-groups'][0]['filter'], c['proxy-groups'][1]['filter'])
        c = self.config()
        c['proxy-groups'][1]['expected-status'] = '401'
        with self.assertRaisesRegex(ValueError, 'conflicting expected'):
            self.q['configure_measurement'](c, {'measurement': self.settings()})

    def test_budget_concurrent_and_corrupt_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw)/'budget.json'
            reserve = self.q['reserve_native']
            nodes = self.config()['proxies'][:1]
            with ThreadPoolExecutor(max_workers=8) as workers:
                allowed = list(workers.map(lambda _: reserve(ledger, self.settings(), nodes, 1, 10000), range(20)))
            self.assertEqual(sum(allowed), 6)
            ledger.write_text('{broken')
            with self.assertRaises(json.JSONDecodeError):
                reserve(ledger, self.settings(), nodes, 1, 14000)

    def test_round_robin_parents_become_ordered_native_fallback(self):
        c = self.config()
        c['proxy-groups'].append({'name': 'Download', 'type': 'load-balance',
            'proxies': ['Web', 'AI'], 'strategy': 'round-robin', 'url': 'https://example.invalid/'})
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        parent = c['proxy-groups'][-1]
        self.assertEqual(parent['type'], 'fallback')
        self.assertNotIn('strategy', parent)
        self.assertEqual(parent['use'], c['proxy-groups'][0]['use'])
        self.assertEqual(len(c['proxy-providers']), 1)

    def test_ambiguous_account_and_native_chain_are_rejected(self):
        c = self.config()
        c['proxies'][0]['dialer-proxy'] = 'A | two'
        with self.assertRaisesRegex(ValueError, 'dialer chains'):
            self.q['configure_measurement'](c, {'measurement': self.settings()})
        m = self.settings()
        m['accounts']['alias'] = copy.deepcopy(m['accounts']['account-a'])
        with self.assertRaisesRegex(ValueError, 'unambiguous account'):
            self.q['configure_measurement'](self.config(), {'measurement': m})

    def test_nested_manual_or_builtin_routes_are_never_dropped(self):
        c = self.config()
        c['proxy-groups'][0].update(type='select', proxies=['DIRECT', 'A | one'])
        c['proxy-groups'].append({'name': 'Download', 'type': 'load-balance',
            'proxies': ['Web'], 'url': 'https://example.invalid/'})
        with self.assertRaisesRegex(ValueError, 'manual or unresolved'):
            self.q['configure_measurement'](c, {'measurement': self.settings()})

    def test_complete_overlay_can_generate_future_opt_in(self):
        overlay = runpy.run_path(str(ROOT / 'tools/check_overlay.py'))
        import yaml
        with tempfile.TemporaryDirectory() as raw:
            source, manifest = overlay['build_fixture'](Path(raw), overlay['MANIFEST_OVERLAY'], overlay['ALL_NODES'])
            data = yaml.safe_load(manifest.read_text())
            data['measurement'] = self.settings()
            data['measurement']['accounts'] = {'synthetic': {'prefixes': [
                'Example | ', 'STT | ', 'ByWave | ', 'BWB | ', 'InfraAPI | ', 'GLaDOS | '],
                'native_probes_per_hour': 10000, 'quality_attempts_per_hour': 10000}}
            manifest.write_text(yaml.safe_dump(data))
            result = overlay['run_overlay'](source, manifest, Path(raw)/'generated.yaml')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_subscription_rotation_rebuilds_owned_payload(self):
        c = self.config()
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        c['proxies'][0]['port'] = 2
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        payload = next(iter(c['proxy-providers'].values()))['payload']
        self.assertEqual(payload[0]['port'], 2)
        self.assertEqual(len(c['proxy-providers']), 1)
        c['proxies'].pop(0)
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        self.assertEqual([n['name'] for n in next(iter(c['proxy-providers'].values()))['payload']], ['A | two'])

    def test_disabled_native_budget_rejects_reservation_without_creating_state(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / 'budget.json'
            settings = self.settings()
            settings['enabled'] = False
            self.assertFalse(self.q['reserve_native'](ledger, settings, self.config()['proxies'][:1], 2, 10000))
            self.assertFalse(ledger.exists())

    def test_pause_after_enabled_removes_stale_owned_payload(self):
        c = self.config()
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        c['proxies'][0]['port'] = 2
        self.q['configure_measurement'](c, {})
        self.assertEqual(c['proxy-providers'], {})
        self.assertTrue(all(g['type'] == 'select' and 'use' not in g for g in c['proxy-groups']))
        self.assertEqual(self.q['definitions'](c)['A | one']['port'], 2)

    def test_pause_after_revocation_never_restores_missing_members(self):
        c = self.config()
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        c['proxies'].pop(0)
        self.q['configure_measurement'](c, {})
        self.assertTrue(all(g['proxies'] == ['A | two'] for g in c['proxy-groups']))

    def test_group_rechecks_keep_the_provider_status_contract(self):
        for existing in (False, True):
            with self.subTest(existing_provider=existing):
                c = self.config()
                for group in c['proxy-groups']:
                    group['expected-status'] = 204
                if existing:
                    c['proxy-providers'] = {'external': {
                        'type': 'inline', 'payload': c['proxies'],
                        'health-check': {'enable': True, 'url': 'https://example.invalid/',
                                         'expected-status': 204, 'interval': 600}}}
                    for group in c['proxy-groups']:
                        group.pop('proxies')
                        group.pop('expected-status')
                        group['use'] = ['external']
                self.q['configure_measurement'](c, {'measurement': self.settings()})
                for group in c['proxy-groups']:
                    self.assertEqual(str(group.get('expected-status')), '204')
                    self.assertNotIn('url', group)  # No second health-check owner.

    def test_control_names_cannot_enter_shared_owner_filters(self):
        for enabled in (False, True):
            for suffix in ('\n', '\r', '\t', '\x00', '\x7f', '\u0085', '\u2028', '\u2029'):
                with self.subTest(enabled=enabled, suffix=repr(suffix)):
                    c = self.config()
                    c['proxies'][1]['name'] += suffix
                    for group in c['proxy-groups']:
                        group['proxies'][1] += suffix
                    settings = self.settings()
                    settings['enabled'] = enabled
                    with self.assertRaisesRegex(ValueError, 'node name'):
                        self.q['configure_measurement'](c, {'measurement': settings})

    def test_regular_unicode_and_joined_emoji_names_are_preserved(self):
        c = self.config()
        name = 'A | 中国 👩\u200d💻 (HK)'
        c['proxies'][0]['name'] = name
        for group in c['proxy-groups']:
            group['proxies'][0] = name
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        self.assertEqual(c['proxies'][0]['name'], name)
        self.assertIn(name, self.q['definitions'](c))

    def test_decimal_string_ports_share_canonical_identity_and_budget(self):
        broker = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-measurement-budget'))
        c = self.config()
        c['proxies'][0]['port'] = '010'
        self.q['configure_measurement'](c, {'measurement': self.settings()})
        self.assertEqual(c['proxies'][0]['port'], 10)
        self.assertEqual(self.q['definitions'](c)['A | one']['port'], 10)
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / 'existing.json'
            request = {'protocol': broker['PROTOCOL'], 'name': 'A | one', 'addr': '127.0.0.1:10',
                       'dialer': '', 'deadline_ms': int((time.time() + 5) * 1000)}
            for _ in range(3):
                self.assertTrue(broker['authorize'](request, c, self.settings(), ledger))
            before = ledger.read_bytes()
            self.assertFalse(broker['authorize'](request, c, self.settings(), ledger))
            self.assertEqual(before, ledger.read_bytes())
            self.assertEqual(sum(row[1] for row in json.loads(before)['accounts']['account-a']), 6)

    def test_decimal_ports_are_emitted_for_existing_providers_and_pause(self):
        for enabled in (False, True):
            c = self.config()
            c['proxies'][0]['port'] = '00080'
            c['proxy-providers'] = {'external': {'type': 'inline', 'payload': copy.deepcopy(c['proxies']),
                'health-check': {'enable': True, 'url': 'https://example.invalid/', 'interval': 600}}}
            for group in c['proxy-groups']:
                group.pop('proxies')
                group['use'] = ['external']
            settings = self.settings()
            settings['enabled'] = enabled
            self.q['configure_measurement'](c, {'measurement': settings})
            self.assertEqual(c['proxies'][0]['port'], 80)
            self.assertEqual(c['proxy-providers']['external']['payload'][0]['port'], 80)

    def test_invalid_native_ports_fail_during_generation(self):
        for value in (True, 0, 65536, '1.0', '+1', ' 1', '\u0661'):
            with self.subTest(port=value):
                c = self.config()
                c['proxies'][0]['port'] = value
                with self.assertRaisesRegex(ValueError, 'port'):
                    self.q['configure_measurement'](c, {'measurement': self.settings()})


class NativeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.overlay = runpy.run_path(str(ROOT / 'tools/check_overlay.py'))
        self.source, self.manifest = self.overlay['build_fixture'](
            self.directory, self.overlay['MANIFEST_OVERLAY'], self.overlay['ALL_NODES'])
        self.settings = yaml.safe_load(self.manifest.read_text())
        self.settings['measurement'] = {
            'enabled': True, 'native_budget': True, 'native_interval_seconds': 900,
            'accounts': {'synthetic': {'names': self.overlay['ALL_NODES'],
                         'native_probes_per_hour': 10000, 'quality_attempts_per_hour': 10000}}}

    def generate(self, source=None):
        self.manifest.write_text(yaml.safe_dump(self.settings))
        destination = self.directory / 'output.yaml'
        result = self.overlay['run_overlay'](source or self.source, self.manifest, destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        return yaml.safe_load(destination.read_text())

    def group(self, config, name):
        return next(g for g in config['proxy-groups'] if g['name'] == name)

    def leaves(self, config, name):
        group = self.group(config, name)
        if 'proxies' in group:
            return group['proxies']
        payload = config['proxy-providers'][group['use'][0]]['payload']
        return list(dict.fromkeys(n['name'] for pattern in group['filter'].split('`')
                                 for n in payload if re.fullmatch(pattern, n['name'])))

    def save_current(self, config):
        path = self.directory / 'current.yaml'
        path.write_text(yaml.safe_dump(config))
        return path

    def test_revoked_download_children_deny_retained_references(self):
        first = self.generate()
        for name in ('GLaDOS Download', 'GLaDOS GitHub Download'):
            self.assertEqual(self.leaves(first, name), ['GLaDOS | D1 Eligible'])
            first['proxy-groups'].append({'name': 'Entry ' + name, 'type': 'select', 'proxies': [name]})
        first['rules'].insert(0, 'DOMAIN,retained.example.invalid,Entry GLaDOS Download')
        current = self.save_current(first)
        bulk = self.settings['roles']['bulk_download']
        bulk.update(glados_patterns=[], glados_explicit_allow=[])
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.settings['measurement']['enabled'] = enabled
                after = self.generate(current)
                for name in ('GLaDOS Download', 'GLaDOS GitHub Download'):
                    self.assertEqual(self.leaves(after, name), ['REJECT'])
                    self.assertEqual(self.group(after, name)['type'], 'select')
                    self.assertEqual(self.leaves(after, 'Entry ' + name), [name])
                self.assertNotIn('GLaDOS | D1 Eligible', self.leaves(after, 'Download'))

    def test_url_padding_keeps_complete_overlay_owner_and_pools_unchanged(self):
        for name in ('Download', 'Package Download'):
            self.settings['stability']['groups'].setdefault(name, {}).update(
                url='https://www.gstatic.com/generate_204', **{'expected-status': 204})
        before = self.generate()
        self.settings['stability']['groups']['Package Download']['url'] = (
            '  https://www.gstatic.com/generate_204  ')
        after = self.generate()
        self.assertEqual(after, before)
        self.assertEqual(self.group(after, 'Download')['use'],
                         self.group(after, 'Package Download')['use'])

    def test_ai_fallback_order_survives_reapply_and_retained_subscription(self):
        source = yaml.safe_load(self.source.read_text())
        self.group(source, 'Research + AI')['proxies'] = ['Example | TW 01', 'Example | HK 01']
        self.source.write_text(yaml.safe_dump(source))
        first = self.generate()
        expected = ['Example | JP 01', 'Example | TW 01', 'Example | HK 01']
        self.assertEqual(self.leaves(first, 'Research + AI'), expected)
        current = self.save_current(first)
        self.assertEqual(self.leaves(self.generate(current), 'Research + AI'), expected)
        extras, status, candidate = [self.directory / n for n in ('extras', 'status', 'candidate.yaml')]
        extras.write_text('')
        result = subprocess.run([sys.executable, str(ROOT / 'automation/sbin/mihomo-subscription-candidate'),
            'prepare', str(current), str(current), str(extras), str(candidate), str(status)],
            env={**os.environ, 'MIHOMO_CAPABILITIES': str(self.manifest),
                 'MIHOMO_ROUTE_DEFAULTS': str(ROOT / 'automation/policy/route-defaults.yaml')},
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.leaves(yaml.safe_load(candidate.read_text()), 'Research + AI'), expected)
        # The shared provider also contains other nodes: only this group's filter
        # is authoritative, and current subscription definitions win on rotation.
        first['proxies'] = [n for n in first['proxies'] if n['name'] != 'Example | HK 01']
        for node in first['proxies']:
            if node['name'] == 'Example | TW 01':
                node['port'] = 8443
        after = self.generate(self.save_current(first))
        self.assertEqual(self.leaves(after, 'Research + AI'), expected[:2])
        provider = after['proxy-providers'][self.group(after, 'Research + AI')['use'][0]]
        self.assertEqual(next(n for n in provider['payload'] if n['name'] == 'Example | TW 01')['port'], 8443)

    def test_download_type_override_cannot_disable_auto_switching(self):
        for name in ('Download', 'GitHub Download', 'Model Download', 'Package Download',
                     'STT Download', 'ByWave Download', 'GLaDOS Download'):
            with self.subTest(group=name):
                self.settings['stability']['groups'][name] = {'type': 'select'}
                self.manifest.write_text(yaml.safe_dump(self.settings))
                destination = self.directory / 'rejected.yaml'
                result = self.overlay['run_overlay'](self.source, self.manifest, destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('strategy', result.stderr)
                self.assertFalse(destination.exists())
                del self.settings['stability']['groups'][name]

    def test_explicit_ai_fixed_selection_and_global_pause_remain_available(self):
        self.settings['stability']['groups']['Research + AI']['type'] = 'select'
        active = self.generate()
        self.assertEqual(self.group(active, 'Research + AI')['type'], 'select')
        self.assertEqual(self.group(active, 'Download')['type'], 'url-test')
        self.settings['measurement']['enabled'] = False
        paused = self.generate(self.save_current(active))
        self.assertTrue(all(g['type'] == 'select' for g in paused['proxy-groups']))
        self.assertTrue(all(not p.get('health-check', {}).get('enable')
                            for p in paused['proxy-providers'].values()))


if __name__ == '__main__':
    unittest.main()
