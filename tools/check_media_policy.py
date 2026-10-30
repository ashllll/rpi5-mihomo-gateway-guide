#!/usr/bin/env python3
"""Behavior checks for the media-admission strategy seam."""
from __future__ import annotations

import copy
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-media-policy'))


def manifest():
    return {'measurement': {'enabled': True, 'native_budget': True,
                            'native_interval_seconds': 240}, 'media_admission': {
        'enabled': True, 'interval_seconds': 60, 'sample_bytes': 4096,
        'timeout_seconds': 12, 'min_bps': 1000, 'recovery_bps': 2000,
        'bad_samples': 2, 'good_samples': 2, 'cooldown_seconds': 120,
        'max_age_seconds': 600, 'samples_per_group': 2,
        'profiles': {
            'Social': {'sample_url': 'https://cdn.example.invalid/social.mp4',
                       'expected_content_type': 'video/mp4'},
            'Streaming': {'sample_url': 'https://cdn.example.invalid/stream.mp4',
                          'expected_content_type': 'video/mp4'},
        },
    }}


def config():
    return {'proxies': [
        {'name': 'Node A', 'type': 'http', 'server': '192.0.2.1', 'port': 443,
         'password': 'fixture-secret-a'},
        {'name': 'Node B', 'type': 'http', 'server': '192.0.2.2', 'port': 443},
        {'name': 'Node C', 'type': 'http', 'server': '192.0.2.3', 'port': 443},
    ], 'proxy-groups': [
        {'name': 'Social', 'type': 'fallback', 'proxies': ['Node B', 'Node A'],
         'url': 'https://api.example.invalid/check', 'expected-status': '200-499',
         'timeout': 5000, 'interval': 180, 'lazy': True},
        {'name': 'Streaming', 'type': 'fallback', 'proxies': ['Node C', 'Node B'],
         'url': 'https://www.example.invalid/check', 'expected-status': 204,
         'timeout': 5000, 'interval': 180},
    ]}


class MediaPolicyTests(unittest.TestCase):
    def test_configure_creates_exclusive_empty_file_provider_and_preserves_group_order(self):
        value = config()
        POLICY['configure'](value, manifest())
        social = next(group for group in value['proxy-groups'] if group['name'] == 'Social')
        provider_name = social['use'][0]
        provider = value['proxy-providers'][provider_name]
        self.assertEqual(provider_name, 'media-admission-' + provider['x-media-admission']['generation'][:16])
        self.assertEqual(provider['type'], 'file')
        self.assertEqual(provider['path'], './providers/' + provider_name + '.yaml')
        self.assertEqual(provider['payload'], [])
        self.assertEqual(provider['health-check'], {
            'enable': True, 'url': 'https://api.example.invalid/check',
            'expected-status': '200-499', 'timeout': 5000,
            'interval': 180, 'lazy': False,
        })
        self.assertEqual(provider['x-media-admission']['candidates'], ['Node B', 'Node A'])
        self.assertEqual(social['type'], 'fallback')
        self.assertEqual(social['use'], [provider_name])
        self.assertEqual(social['empty-fallback'], 'REJECT')
        self.assertNotIn('proxies', social)
        self.assertNotIn('url', social)
        self.assertNotIn('interval', social)
        self.assertNotIn('lazy', social)

    def test_group_identity_keeps_equal_policy_and_candidate_pools_exclusive(self):
        value = config()
        streaming = next(group for group in value['proxy-groups'] if group['name'] == 'Streaming')
        streaming['proxies'] = ['Node B', 'Node A']
        streaming.update(url='https://api.example.invalid/check', **{'expected-status': '200-499'})
        manifest_value = manifest()
        manifest_value['media_admission']['profiles']['Streaming'] = copy.deepcopy(
            manifest_value['media_admission']['profiles']['Social'])
        POLICY['configure'](value, manifest_value)
        owners = [group['use'][0] for group in value['proxy-groups']]
        self.assertEqual(len(set(owners)), 2)
        self.assertEqual(len(value['proxy-providers']), 2)

    def test_media_groups_keep_fallback_strategy(self):
        value = config()
        value['proxy-groups'][0]['type'] = 'select'
        POLICY['configure'](value, manifest())
        self.assertEqual(value['proxy-groups'][0]['type'], 'fallback')

    def test_configure_requires_enabled_native_budget_contract(self):
        for key, value in (('enabled', False), ('native_budget', False)):
            capabilities = manifest()
            capabilities['measurement'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                POLICY['configure'](config(), capabilities)

    def test_extra_patterns_append_matching_current_nodes_after_flat_order(self):
        capabilities = manifest()
        capabilities['media_admission']['profiles']['Social']['extra_patterns'] = ['^Node C$']
        value = config()
        POLICY['configure'](value, capabilities)
        social = next(group for group in value['proxy-groups'] if group['name'] == 'Social')
        owner = value['proxy-providers'][social['use'][0]]
        self.assertEqual(owner['x-media-admission']['candidates'], ['Node B', 'Node A', 'Node C'])

    def test_malformed_existing_provider_identity_fails_closed(self):
        value = config()
        POLICY['configure'](value, manifest())
        name, spec = next(iter(value['proxy-providers'].items()))
        collision = config()
        collision['proxy-providers'] = {name: {
            'type': 'file', 'path': spec['path'], 'payload': [], 'x-media-admission': None}}
        with self.assertRaises(ValueError):
            POLICY['configure'](collision, manifest())

    def test_generation_binds_candidate_credentials_and_content_profile(self):
        baseline = config()
        POLICY['configure'](baseline, manifest())
        baseline_name = next(group for group in baseline['proxy-groups']
                             if group['name'] == 'Social')['use'][0]

        rotated = config()
        rotated['proxies'][1]['password'] = 'fixture-secret-b'
        POLICY['configure'](rotated, manifest())
        rotated_name = next(group for group in rotated['proxy-groups']
                            if group['name'] == 'Social')['use'][0]
        self.assertNotEqual(baseline_name, rotated_name)

        changed_profile = manifest()
        changed_profile['media_admission']['profiles']['Social']['sample_url'] = (
            'https://cdn.example.invalid/other.mp4')
        changed = config()
        POLICY['configure'](changed, changed_profile)
        changed_name = next(group for group in changed['proxy-groups']
                            if group['name'] == 'Social')['use'][0]
        self.assertNotEqual(baseline_name, changed_name)

        only_streaming_changed = manifest()
        only_streaming_changed['media_admission']['profiles']['Streaming']['sample_url'] = (
            'https://cdn.example.invalid/other.mp4')
        other = config()
        POLICY['configure'](other, only_streaming_changed)
        other_name = next(group for group in other['proxy-groups']
                          if group['name'] == 'Social')['use'][0]
        self.assertEqual(baseline_name, other_name)

    def test_candidate_resolution_uses_only_current_top_level_definitions(self):
        value = config()
        POLICY['configure'](value, manifest())
        social = next(group for group in value['proxy-groups'] if group['name'] == 'Social')
        spec = value['proxy-providers'][social['use'][0]]
        nodes = POLICY['candidate_nodes'](value, spec)
        self.assertEqual([node['name'] for node in nodes], ['Node B', 'Node A'])
        self.assertTrue(all(node['name'] != 'Retired node' for node in nodes))

    def test_disabled_settings_and_invalid_enabled_settings(self):
        disabled = manifest()
        disabled['media_admission']['enabled'] = False
        self.assertIsNone(POLICY['settings'](disabled))
        for update in (
            {'sample_bytes': True},
            {'sample_bytes': 512},
            {'interval_seconds': 1},
            {'min_bps': float('inf')},
            {'recovery_bps': 999},
            {'unknown_setting': 1},
            {'profiles': {'Social': {'sample_url': 'http://cdn.example.invalid/a.mp4',
                                     'expected_content_type': 'video/mp4'}}},
            {'profiles': {'Social': {'sample_url': 'https://user:pass@cdn.example.invalid/a.mp4',
                                     'expected_content_type': 'video/mp4'}}},
            {'profiles': {'Social': {'sample_url': 'https://cdn.example.invalid/a.mp4',
                                     'expected_content_type': 'video/mp4',
                                     'extra_patterns': ['[']}}},
            {'profiles': {'Telegram': {'sample_url': 'https://cdn.example.invalid/a.mp4',
                                       'expected_content_type': 'video/mp4'}}},
            {'profiles': {'Social': {'sample_url': 'https://cdn.example.invalid/a.mp4',
                                     'expected_content_type': 'video/mp4', 'unknown': True}}},
        ):
            invalid = manifest()
            invalid['media_admission'].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                POLICY['settings'](invalid)

    def test_resource_caps_reject_oversized_rounds_and_allow_exact_budget(self):
        for update in (
            {'sample_bytes': 2 * 1024 * 1024 + 1},
            {'samples_per_group': 5},
            {'timeout_seconds': 16},
            {'sample_bytes': 1024 * 1024 + 1, 'samples_per_group': 4,
             'timeout_seconds': 15},
        ):
            invalid = manifest()
            invalid['media_admission'].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                POLICY['settings'](invalid)

        exact = manifest()
        exact['media_admission'].update(sample_bytes=1024 * 1024,
                                         samples_per_group=4, timeout_seconds=15)
        self.assertIsNotNone(POLICY['settings'](exact))

    def test_disabled_cleanup_removes_only_identity_matched_owned_providers(self):
        value = config()
        POLICY['configure'](value, manifest())
        social = next(group for group in value['proxy-groups'] if group['name'] == 'Social')
        owner = social['use'][0]
        value['proxy-providers']['user-owned'] = {
            'type': 'file', 'path': './providers/user-owned.yaml', 'payload': []}
        disabled = manifest()
        disabled['media_admission']['enabled'] = False
        POLICY['configure'](value, disabled)
        self.assertNotIn(owner, value['proxy-providers'])
        self.assertIn('user-owned', value['proxy-providers'])
        self.assertNotIn(owner, social.get('use', []))

    def test_owned_provider_requires_matching_name_path_and_full_generation(self):
        value = config()
        POLICY['configure'](value, manifest())
        name, spec = next(iter(value['proxy-providers'].items()))
        self.assertTrue(POLICY['owned_provider'](name, spec))
        with_payload = copy.deepcopy(spec)
        with_payload['payload'] = [{'name': 'Fake'}]
        self.assertFalse(POLICY['owned_provider'](name, with_payload))
        wrong_type = copy.deepcopy(spec)
        wrong_type['type'] = 'inline'
        self.assertFalse(POLICY['owned_provider'](name, wrong_type))
        wrong_path = copy.deepcopy(spec)
        wrong_path['path'] = './providers/not-this-owner.yaml'
        self.assertFalse(POLICY['owned_provider'](name, wrong_path))
        wrong_name = name[:-1] + ('0' if name[-1] != '0' else '1')
        self.assertFalse(POLICY['owned_provider'](wrong_name, spec))

    def test_two_good_samples_admit_and_two_bad_samples_isolate_until_cooldown(self):
        policy = POLICY['settings'](manifest())
        state = {}
        high = {'name': 'Node A', 'success': True, 'status': 206,
                'bytes': 24000, 'elapsed': 10, 'bps': 1}
        state = POLICY['update_samples'](state, [high], policy, 0)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 0), [])
        state = POLICY['update_samples'](state, [high], policy, 10)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 10), ['Node A'])

        low = {'name': 'Node A', 'success': True, 'status': 200,
               'bytes': 5000, 'elapsed': 10, 'bps': 999999}
        state = POLICY['update_samples'](state, [low], policy, 20)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 20), ['Node A'])
        state = POLICY['update_samples'](state, [low], policy, 30)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 30), [])

        state = POLICY['update_samples'](state, [high], policy, 100)
        state = POLICY['update_samples'](state, [high], policy, 110)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 110), [])
        state = POLICY['update_samples'](state, [high], policy, 150)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 150), [])
        state = POLICY['update_samples'](state, [high], policy, 160)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 160), ['Node A'])

    def test_http_rejections_and_short_samples_are_inconclusive_but_errors_are_bad(self):
        policy = POLICY['settings'](manifest())
        high = {'name': 'Node A', 'success': True, 'status': 206,
                'bytes': 24000, 'elapsed': 10}
        state = POLICY['update_samples']({}, [high, high], policy, 0)
        for at, status, byte_count in ((100, 403, 24000), (200, 429, 24000),
                                       (300, 200, 1000), (400, 500, 24000)):
            row = {'name': 'Node A', 'status': status, 'bytes': byte_count,
                   'elapsed': 2, 'success': False}
            state = POLICY['update_samples'](state, [row], policy, at)
        unexpected_type = {'name': 'Node A', 'success': False, 'status': 200,
                           'bytes': 24000, 'elapsed': 2,
                           'error': 'UnexpectedContentType'}
        state = POLICY['update_samples'](state, [unexpected_type], policy, 450)
        for at, error in ((460, 'IncompleteRead'), (470, 'MihomoExited')):
            row = {'name': 'Node A', 'success': False, 'status': 206,
                   'bytes': 24000, 'elapsed': 2, 'error': error}
            state = POLICY['update_samples'](state, [row], policy, at)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 500), ['Node A'])
        # Inconclusive responses do not refresh the age of the last valid sample.
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 601), [])

        state = POLICY['update_samples']({}, [high, high], policy, 0)
        timeout = {'name': 'Node A', 'error': 'timeout', 'status': None}
        state = POLICY['update_samples'](state, [timeout], policy, 20)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 20), ['Node A'])
        url_error = {'name': 'Node A', 'error': 'URLError: failed', 'status': None}
        state = POLICY['update_samples'](state, [url_error], policy, 30)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 30), [])

        other = {**high, 'name': 'Node B'}
        state = POLICY['update_samples']({}, [other, other], policy, 0)
        connection = {'name': 'Node B', 'error': 'ConnectionError', 'status': None}
        state = POLICY['update_samples'](state, [connection, connection], policy, 20)
        self.assertEqual(POLICY['admitted'](state, ['Node B'], policy, 20), [])

    def test_removed_candidate_is_not_recovered_from_provider_payload(self):
        value = config()
        POLICY['configure'](value, manifest())
        social = next(group for group in value['proxy-groups'] if group['name'] == 'Social')
        spec = value['proxy-providers'][social['use'][0]]
        value['proxies'] = [node for node in value['proxies'] if node['name'] != 'Node A']
        with self.assertRaises(ValueError):
            POLICY['candidate_nodes'](value, spec)

    def test_unknown_good_streak_expires_and_bad_reported_bps_is_ignored(self):
        policy = POLICY['settings'](manifest())
        result = {'name': 'Node A', 'success': True, 'status': 200, 'bytes': 24000,
                  'elapsed': 10, 'bps': 999999999}
        state = POLICY['update_samples']({}, [result], policy, 0)
        low = {'name': 'Node B', 'status': 206, 'bytes': 5000,
               'elapsed': 10, 'bps': 999999999}
        state = POLICY['update_samples'](state, [low], policy, 5)
        self.assertEqual(POLICY['admitted'](state, ['Node A', 'Node B'], policy, 5), [])
        state = POLICY['update_samples'](state, [result], policy, 601)
        self.assertEqual(POLICY['admitted'](state, ['Node A'], policy, 601), [])
        self.assertEqual(state['candidates']['Node A']['good_count'], 1)

    def test_update_is_pure_and_admission_preserves_requested_order(self):
        policy = POLICY['settings'](manifest())
        sample = {'success': True, 'status': 200, 'bytes': 24000, 'elapsed': 10}
        original = {'candidates': {}}
        updated = POLICY['update_samples'](original,
            [{**sample, 'name': 'Node A'}, {**sample, 'name': 'Node A'},
             {**sample, 'name': 'Node B'}, {**sample, 'name': 'Node B'}], policy, 10)
        self.assertEqual(original, {'candidates': {}})
        self.assertEqual(POLICY['admitted'](updated, ['Node B', 'Node A'], policy, 10),
                         ['Node B', 'Node A'])


if __name__ == '__main__':
    unittest.main()
