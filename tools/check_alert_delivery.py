#!/usr/bin/env python3
"""Exercise the real notifier CLI without reaching any notification service."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'automation/sbin/mihomo-alert'


class Delivery(unittest.TestCase):
    def test_failed_delivery_retries_and_recovery_has_its_own_key(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / 'state'
            config = root / 'env'
            config.write_text('BARK_URL=http://fixture.invalid/fake-key\n')
            source = SOURCE.read_text().replace('/etc/mihomo-alert.env', str(config))
            source = source.replace('/run/mihomo-alert.state', str(state))
            script = root / 'alert'
            script.write_text(source)
            for name, body in {
                'logger': '#!/bin/sh\nexit 0\n',
                'flock': '#!/bin/sh\nexit 0\n',
                'curl': '#!/bin/sh\nprintf "call\\n" >> "$CALLS"\nprintf "%s" "$RESPONSE"\n',
            }.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       CALLS=str(root / 'calls'), RESPONSE='{"code":500}')
            def invoke(key):
                return subprocess.run(['sh', str(script), '--health', 'fixture', key],
                                      env=env, capture_output=True, text=True)
            self.assertNotEqual(invoke('proxy-down').returncode, 0)
            self.assertFalse(state.exists(), 'failed delivery must not start cooldown')
            env['RESPONSE'] = '{"code":200}'
            self.assertEqual(invoke('proxy-down').returncode, 0)
            self.assertTrue(state.exists())
            self.assertEqual(invoke('proxy-down').returncode, 75)
            self.assertEqual(len((root / 'calls').read_text().splitlines()), 2)
            self.assertEqual(invoke('proxy-up').returncode, 0)
            self.assertEqual(len((root / 'calls').read_text().splitlines()), 3)
            self.assertEqual(invoke('proxy-down').returncode, 75)
            state.write_text('proxy-down\t1\n')
            self.assertEqual(invoke('proxy-down').returncode, 0)
            self.assertEqual(len((root / 'calls').read_text().splitlines()), 4)


if __name__ == '__main__':
    unittest.main()
