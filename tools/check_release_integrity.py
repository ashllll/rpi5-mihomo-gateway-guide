#!/usr/bin/env python3
"""Prove an edited package or drifted live baseline cannot be installed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'automation'


class ReleaseCLI(unittest.TestCase):
    def test_installer_rejects_dispatcher_drift_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'root'
            shutil.copytree(SOURCE, package)
            (target / 'etc/NetworkManager').mkdir(parents=True)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'], check=True, capture_output=True)
            env = dict(os.environ, GATEWAY_ROOT=str(target))
            first = subprocess.run(['sh', str(package / 'install.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            dispatcher = target / 'etc/NetworkManager/dispatcher.d/90-wifi-management-routing'
            dispatcher.write_text('operator managed route\n')
            before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()}
            second = subprocess.run(['sh', str(package / 'install.sh')], env=env, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('installed drift', second.stderr)
            self.assertEqual(before, {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()})

    def test_identity_and_installed_drift_are_checked_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'root'
            shutil.copytree(SOURCE, package)
            target.mkdir()
            tool = package / 'check-release.py'
            def run(*args):
                return subprocess.run([sys.executable, str(tool), *args], capture_output=True, text=True)
            self.assertEqual(run('--write').returncode, 0)
            self.assertEqual(run('--root', str(target)).returncode, 0)
            manifest = json.loads((package / 'release-manifest.json').read_text())
            for entry in manifest['files']:
                if entry['target']:
                    destination = target / entry['target'].lstrip('/')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(package / entry['source'], destination)
            self.assertEqual(run('--root', str(target), '--record').returncode, 0)
            saved = (target / 'var/lib/mihomo-automation/installed-manifest.json').read_bytes()
            # Live edits must be rejected before a different file can be overwritten.
            guard = target / 'usr/local/sbin/mihomo-dnsmasq-guard'
            guard.write_text('operator edit\n')
            result = run('--root', str(target))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('installed drift', result.stderr)
            self.assertEqual((target / 'var/lib/mihomo-automation/installed-manifest.json').read_bytes(), saved)
            shutil.copyfile(package / 'sbin/mihomo-dnsmasq-guard', guard)
            (package / 'sbin/mihomo-dnsmasq-guard').write_text('unreviewed package edit\n')
            result = run('--root', str(target))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('package differs', result.stderr)
            # Generating a new reviewed manifest still cannot adopt unknown existing code.
            self.assertEqual(run('--write').returncode, 0)
            (target / 'var/lib/mihomo-automation/installed-manifest.json').unlink()
            result = run('--root', str(target))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('untracked installed file differs', result.stderr)


if __name__ == '__main__':
    unittest.main()
