#!/usr/bin/env python3
"""Verify subscription conversion through the production file interface."""
import subprocess
import base64
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

COMMAND = Path(__file__).resolve().parents[1] / 'automation/sbin/mihomo-subscription-normalize'


class NormalizeCLI(unittest.TestCase):
    def convert(self, text):
        with tempfile.TemporaryDirectory() as raw:
            source, output = Path(raw) / 'input', Path(raw) / 'output'
            source.write_text(text)
            result = subprocess.run([sys.executable, str(COMMAND), str(source), str(output)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return yaml.safe_load(output.read_text())['proxies']

    def test_ss_optional_slash_preserves_endpoint_and_credentials(self):
        node, = self.convert('ss://aes-128-gcm:fixture@198.51.100.1:443/#sample')
        self.assertEqual((node['server'], node['port'], node['cipher'], node['password']),
                         ('198.51.100.1', 443, 'aes-128-gcm', 'fixture'))

    def test_ss_percent_encoded_credentials_are_decoded_once(self):
        node, = self.convert('ss://aes-128-gcm:fixture%40pass%3A%25%2B@198.51.100.1:443#sample')
        self.assertEqual(node['password'], 'fixture@pass:%+')

    def test_ss_v2ray_plugin_options_keep_the_transport(self):
        node, = self.convert('ss://aes-128-gcm:fixture@[::1]:443/'
                             '?plugin=v2ray-plugin%3Btls%3Bhost%3Dexample.com%3Bpath%3D%2Fws#sample')
        self.assertEqual(node['server'], '::1')
        self.assertEqual(node['plugin'], 'v2ray-plugin')
        self.assertEqual(node['plugin-opts'],
                         {'mode': 'websocket', 'tls': True, 'host': 'example.com', 'path': '/ws'})

    def test_ss_obfs_local_options_are_mapped_to_mihomo(self):
        node, = self.convert('ss://aes-128-gcm:fixture@198.51.100.1:443/'
                             '?plugin=obfs-local%3Bobfs%3Dtls%3Bobfs-host%3Dexample.com#sample')
        self.assertEqual((node['plugin'], node['plugin-opts']),
                         ('obfs', {'mode': 'tls', 'host': 'example.com'}))

    def test_ss_urlsafe_base64_credentials(self):
        node, = self.convert('ss://YWVzLTEyOC1nY20656eY5a-G@198.51.100.1:443#sample')
        self.assertEqual(node['password'], '\u79d8\u5bc6')

    def test_ss_legacy_base64_and_encoded_userinfo_keep_literal_percent(self):
        credentials = 'aes-128-gcm:fixture%40pass'
        encoded = base64.b64encode(credentials.encode()).decode()
        legacy = base64.b64encode((credentials + '@198.51.100.1:443').encode()).decode()
        for uri in (f'ss://{encoded}@198.51.100.1:443#sample', f'ss://{legacy}#sample'):
            with self.subTest(format=uri.split('@')[-1]):
                node, = self.convert(uri)
                self.assertEqual(node['password'], 'fixture%40pass')

    def test_ss_unsupported_plugin_is_rejected_without_credentials(self):
        for plugin in ('unknown-plugin', 'v2ray-plugin%3Bmode%3Dquic',
                       'v2ray-plugin%3Bserver', 'obfs-local%3Bobfs%3Dunknown', '',
                       'v2ray-plugin&plugin=obfs-local'):
            with self.subTest(plugin=plugin), tempfile.TemporaryDirectory() as raw:
                source, output = Path(raw) / 'input', Path(raw) / 'output'
                source.write_text('ss://aes-128-gcm:synthetic-private-value@198.51.100.1:443/'
                                  '?plugin=' + plugin + '#sample')
                result = subprocess.run([sys.executable, str(COMMAND), str(source), str(output)],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                self.assertNotIn('synthetic-private-value', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
