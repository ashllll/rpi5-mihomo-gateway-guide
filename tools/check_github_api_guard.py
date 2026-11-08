#!/usr/bin/python3
from pathlib import Path
import runpy
import unittest

M = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'automation/sbin/mihomo-github-api-guard'))


class QuotaTest(unittest.TestCase):
    def sample(self, remaining, status=200):
        return M['quota_result'](status, {'x-ratelimit-remaining': str(remaining),
                                        'x-ratelimit-reset': '2000'}, b'{}', 1000)

    def test_200_with_zero_quota_is_not_healthy(self):
        self.assertFalse(self.sample(0)['success'])
        self.assertFalse(self.sample(5)['success'])
        self.assertTrue(self.sample(6)['success'])
        self.assertEqual(self.sample(0)['retry_at'], 2002)

    def test_429_retry_after_and_invalid_headers(self):
        self.assertEqual(M['quota_result'](429, {'retry-after': '300'}, b'', 1000)['retry_at'], 1300)
        with self.assertRaises(ValueError):
            M['quota_result'](200, {}, b'{}', 1000)
        with self.assertRaises(ValueError):
            self.sample(-1)

    def test_switch_to_verified_candidate_and_honor_cooldown(self):
        state = {};calls = []
        def check(node, current):
            calls.append(node)
            return self.sample(0 if node == 'bad' else 50)
        chosen, _ = M['choose']('bad', ['bad', 'good'], state, check, 1000)
        self.assertEqual(chosen, 'good')
        self.assertEqual(calls, ['bad', 'good'])
        calls.clear()
        chosen, _ = M['choose']('bad', ['bad', 'good'], state, check, 1100)
        self.assertEqual(chosen, 'good')
        self.assertEqual(calls, ['good'])

    def test_keep_healthy_current_and_bound_failed_search(self):
        state = {};calls = []
        def good(node, current):
            calls.append(node);return self.sample(50)
        self.assertEqual(M['choose']('a', ['a', 'b'], state, good, 1000)[0], 'a')
        self.assertEqual(calls, ['a'])
        state = {};calls.clear()
        def bad(node, current):
            calls.append(node);raise OSError('test')
        self.assertIsNone(M['choose']('a', list('abcdefghij'), state, bad, 1000)[0])
        self.assertEqual(len(calls), 5)

    def test_connection_cleanup_never_targets_downloads_or_new_egress(self):
        connections = [{'id': str(i), 'metadata': {'host': host}, 'chains': [node],
                        'upload': 10, 'download': 20}
                       for i, (host, node) in enumerate([
                           ('api.github.com', 'bad'), ('api.github.com', 'good'),
                           ('release-assets.githubusercontent.com', 'bad'),
                           ('registry.npmjs.org', 'bad'), ('github.com', 'bad')])]
        self.assertEqual(M['stale_api_connections'](connections, {'bad'}), {'0': (10, 20)})


if __name__ == '__main__':
    unittest.main()
