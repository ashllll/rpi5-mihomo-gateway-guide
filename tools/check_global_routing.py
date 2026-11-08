#!/usr/bin/env python3
"""Global account constraints and owned-route regeneration use synthetic nodes."""
import copy
from pathlib import Path
import runpy
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
M = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-policy-overlay'))
PROFILE = yaml.safe_load((ROOT / 'automation/policy/global-routing.example.yaml').read_text())


class GlobalRouting(unittest.TestCase):
    def test_global_ai_account_and_download_priority_are_idempotent(self):
        policy = copy.deepcopy(PROFILE)
        policy['measurement'] = {'accounts': {'glados': {}, 'stt': {}}}
        config = {'proxies': [{'name': n} for n in ['ai-node', 'bulk-node']],
                  'proxy-groups': [{'name': 'GLaDOS AI Pool', 'type': 'fallback',
                                    'proxies': ['ai-node', 'bulk-node'], 'url': 'https://example.com'}],
                  'rules': ['MATCH,DIRECT']}
        measurement = {'account_for': lambda node, _: 'glados' if node['name'] == 'ai-node' else 'stt'}
        M['apply_client_routes'](config, policy, measurement)
        group = config['proxy-groups'][-1]
        self.assertEqual(group['proxies'], ['ai-node'])
        self.assertEqual(group['empty-fallback'], 'REJECT')
        rules = config['rules']
        self.assertIn('AND,((SRC-IP-CIDR,::/0),(DOMAIN-SUFFIX,openai.com)),GLaDOS AI Chat', rules)
        self.assertLess(rules.index('DOMAIN,api.github.com,GitHub API'),
                        rules.index('AND,((SRC-IP-CIDR,0.0.0.0/0),(DOMAIN-SUFFIX,openai.com)),GLaDOS AI Chat'))
        before = copy.deepcopy(config)
        M['apply_client_routes'](config, policy, measurement)
        self.assertEqual(config, before)
        config['proxy-groups'][-1]['x-client-route-rules'].append('MATCH,DIRECT')
        with self.assertRaises(ValueError):
            M['apply_client_routes'](config, policy, measurement)

    def test_downloads_exclude_glados_and_keep_both_providers(self):
        nodes = {'STT | Example', 'BWB | Example', 'GLaDOS | Example'}
        stt, bywave, glados = M['bulk_capable_nodes'](nodes, PROFILE)
        self.assertEqual(set(stt + bywave), nodes - {'GLaDOS | Example'})
        self.assertEqual(glados, [])
        self.assertEqual(PROFILE['client_routes']['ai-chat']['source_cidrs'], ['0.0.0.0/0', '::/0'])

    def test_route_kind_validation(self):
        config = {'download_routes': {'x': {'group': 'Download', 'domains': ['example.com'], 'rule_type': 'DOMAIN-SUFFIX'}}}
        self.assertEqual(M['domain_routes'](config, 'download_routes', 'DOMAIN', M['DOWNLOAD_ROUTE_GROUPS']), ['DOMAIN-SUFFIX,example.com,Download'])
        config['download_routes']['x']['rule_type'] = 'MATCH'
        with self.assertRaises(ValueError):
            M['domain_routes'](config, 'download_routes', 'DOMAIN', M['DOWNLOAD_ROUTE_GROUPS'])


if __name__ == '__main__':
    unittest.main()
