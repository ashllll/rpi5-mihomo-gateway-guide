#!/usr/bin/env python3
"""Prove an edited package or drifted live baseline cannot be installed."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'automation'


class ReleaseCLI(unittest.TestCase):
    def test_begin_failure_does_not_claim_a_commit_or_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'root'
            injection = base / 'injection'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            injection.mkdir()
            (injection / 'sitecustomize.py').write_text(
                'import os, errno\n'
                'original = os.replace\n'
                'def replace(source, destination, *args, **kwargs):\n'
                '    if os.fspath(destination).endswith("/.install-pending"):\n'
                '        raise OSError(errno.ENOSPC, "synthetic full disk")\n'
                '    return original(source, destination, *args, **kwargs)\n'
                'os.replace = replace\n', encoding='utf-8')
            failed = subprocess.run(['sh', str(package / 'install.sh')],
                                    env=dict(os.environ, GATEWAY_ROOT=str(target),
                                             PYTHONPATH=str(injection)),
                                    capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('no pending file transaction', failed.stderr)
            self.assertNotIn('already committed', failed.stderr)
            self.assertNotIn('previous file state restored', failed.stderr)
            self.assertFalse((target / 'usr').exists())
            self.assertFalse((target / 'var/lib/mihomo-automation/.install-pending').exists())

    def test_default_file_appearing_after_preflight_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'root'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            target.mkdir()
            tool = package / 'check-release.py'
            begun = subprocess.run([sys.executable, str(tool), '--root', str(target), '--begin'],
                                   capture_output=True, text=True)
            self.assertEqual(begun.returncode, 0, begun.stderr)

            upstreams = target / 'etc/dnsmasq-upstreams.conf'
            upstreams.parent.mkdir(parents=True)
            upstreams.write_text('# operator-created during install preflight\n')
            seeded = subprocess.run(
                [sys.executable, str(tool), '--root', str(target), '--default', '--install-file',
                 str(package / 'etc/dnsmasq/upstreams.conf'), '/etc/dnsmasq-upstreams.conf', '0644'],
                capture_output=True, text=True,
            )
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            self.assertEqual(upstreams.read_text(), '# operator-created during install preflight\n')

            subprocess.run([sys.executable, str(tool), '--root', str(target), '--rollback'],
                           check=True, capture_output=True)

    def test_directory_mode_changes_have_only_recoverable_states(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; injection = base / 'injection'
            private_root = base / 'private-root'; new_root = base / 'new-root'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            injection.mkdir()
            sitecustomize = injection / 'sitecustomize.py'
            tool = package / 'check-release.py'

            private_root.mkdir()
            private = private_root / 'etc/mihomo/secrets'
            private.mkdir(parents=True)
            old_gid = next((gid for gid in os.getgroups() if gid != os.getegid()), os.getegid())
            os.chown(private, os.geteuid(), old_gid)
            private.chmod(0o755)
            begun = subprocess.run([sys.executable, str(tool), '--root', str(private_root), '--begin'],
                                   capture_output=True, text=True)
            self.assertEqual(begun.returncode, 0, begun.stderr)
            sitecustomize.write_text(
                'import os, signal\n'
                'original = os.chmod\n'
                'def chmod(path, *args, **kwargs):\n'
                '    if os.fspath(path) == os.environ["R4_KILL_PATH"]:\n'
                '        os.kill(os.getpid(), signal.SIGKILL)\n'
                '    return original(path, *args, **kwargs)\n'
                'os.chmod = chmod\n', encoding='utf-8')
            killed = subprocess.run(
                [sys.executable, str(tool), '--root', str(private_root), '--ensure-dir',
                 '/etc/mihomo/secrets', '0700'],
                env=dict(os.environ, PYTHONPATH=str(injection), R4_KILL_PATH=str(private.resolve())),
                capture_output=True, text=True,
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL)
            recovered = subprocess.run([sys.executable, str(tool), '--root', str(private_root), '--recover'],
                                       capture_output=True, text=True)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(private.stat().st_mode & 0o777, 0o755)
            self.assertEqual(private.stat().st_gid, old_gid)
            self.assertFalse((private_root / 'var/lib/mihomo-automation/.install-pending').exists())

            new_root.mkdir()
            new_root.chmod(0o2755)
            begun = subprocess.run([sys.executable, str(tool), '--root', str(new_root), '--begin'],
                                   capture_output=True, text=True)
            self.assertEqual(begun.returncode, 0, begun.stderr)
            sitecustomize.write_text(
                'import os, signal\n'
                'original = os.mkdir\n'
                'def mkdir(path, *args, **kwargs):\n'
                '    result = original(path, *args, **kwargs)\n'
                '    if os.fspath(path) == os.environ["R4_KILL_PATH"]:\n'
                '        os.chmod(path, 0o2755)\n'
                '        os.kill(os.getpid(), signal.SIGKILL)\n'
                '    return result\n'
                'os.mkdir = mkdir\n', encoding='utf-8')
            interrupted = subprocess.run(
                [sys.executable, str(tool), '--root', str(new_root), '--ensure-dir',
                 '/etc/mihomo/secrets', '0700'],
                env=dict(os.environ, PYTHONPATH=str(injection), R4_KILL_PATH=str((new_root / 'etc').resolve())),
                preexec_fn=lambda: os.umask(0o077), capture_output=True, text=True,
            )
            self.assertEqual(interrupted.returncode, -signal.SIGKILL)
            self.assertEqual((new_root / 'etc').stat().st_mode & 0o7777, 0o2755)
            recovered = subprocess.run([sys.executable, str(tool), '--root', str(new_root), '--recover'],
                                       capture_output=True, text=True)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertFalse((new_root / 'etc').exists())
            self.assertFalse((new_root / 'var/lib/mihomo-automation/.install-pending').exists())

    def test_recovery_rechecks_each_write_and_final_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package_a = base / 'package-a'; package_b = base / 'package-b'
            target = base / 'root'; injection = base / 'injection'; marker = base / 'entered'
            release = base / 'release'
            shutil.copytree(SOURCE, package_a)
            subprocess.run([sys.executable, str(package_a / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            installed = subprocess.run(['sh', str(package_a / 'install.sh')],
                                       env=dict(os.environ, GATEWAY_ROOT=str(target)),
                                       capture_output=True, text=True)
            self.assertEqual(installed.returncode, 0, installed.stderr)

            shutil.copytree(package_a, package_b)
            (package_b / 'sbin/dnsmasq-safe-start').write_text('#!/bin/sh\n# release B\n')
            (package_b / 'sbin/homeassistant-access-guard').write_text('#!/bin/sh\n# release B\n')
            subprocess.run([sys.executable, str(package_b / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            checker = package_b / 'check-release.py'
            begun = subprocess.run([sys.executable, str(checker), '--root', str(target), '--begin'],
                                   capture_output=True, text=True)
            self.assertEqual(begun.returncode, 0, begun.stderr)
            staged = subprocess.run(
                [sys.executable, str(checker), '--root', str(target), '--install-file',
                 str(package_b / 'sbin/dnsmasq-safe-start'),
                 '/usr/local/sbin/dnsmasq-safe-start', '0755'],
                capture_output=True, text=True,
            )
            self.assertEqual(staged.returncode, 0, staged.stderr)

            injection.mkdir()
            (injection / 'sitecustomize.py').write_text(
                'import os, pathlib, sys, time\n'
                'if "--prepare" in sys.argv:\n'
                '    original_replace = os.replace\n'
                '    def replace(source, destination, *args, **kwargs):\n'
                '        destination = os.fspath(destination)\n'
                '        if destination == os.environ["R4_STOP_PATH"]:\n'
                '            if os.environ["R4_REPLACE_MODE"] == "pause":\n'
                '                pathlib.Path(os.environ["R4_MARKER"]).write_text("ready")\n'
                '                release = pathlib.Path(os.environ["R4_RELEASE"])\n'
                '                while not release.exists(): time.sleep(0.01)\n'
                '                return original_replace(source, destination, *args, **kwargs)\n'
                '            result = original_replace(source, destination, *args, **kwargs)\n'
                '            path = pathlib.Path(os.environ["R4_LATE_EDIT_PATH"])\n'
                '            path.write_text("late edit during recovery\\n")\n'
                '            path.chmod(0o755)\n'
                '            return result\n'
                '        return original_replace(source, destination, *args, **kwargs)\n'
                '    os.replace = replace\n',
                encoding='utf-8',
            )
            first = target / 'usr/local/sbin/dnsmasq-safe-start'
            second = target / 'usr/local/sbin/homeassistant-access-guard'
            race_env = dict(os.environ, GATEWAY_ROOT=str(target), PYTHONPATH=str(injection),
                            R4_STOP_PATH=str(first.resolve()), R4_MARKER=str(marker),
                            R4_RELEASE=str(release), R4_LATE_EDIT_PATH=str(first.resolve()),
                            R4_REPLACE_MODE='pause')
            owner = subprocess.Popen(['sh', str(package_b / 'install.sh')], env=race_env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and owner.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not marker.exists():
                    release.touch()
                    output, error = owner.communicate(timeout=10)
                    self.fail(f'recovery did not reach its first atomic replacement: {output}{error}')
                second.write_text('operator edit during recovery\n')
                release.touch()
                output, error = owner.communicate(timeout=30)
            finally:
                release.touch()
                if owner.poll() is None:
                    owner.kill()
                    owner.communicate(timeout=10)
            self.assertNotEqual(owner.returncode, 0, output + error)
            self.assertIn('install recovery conflict', error)
            self.assertEqual(first.read_bytes(), (package_a / 'sbin/dnsmasq-safe-start').read_bytes())
            self.assertEqual(second.read_text(), 'operator edit during recovery\n')
            pending = target / 'var/lib/mihomo-automation/.install-pending/journal.json'
            self.assertTrue(pending.is_file())

            shutil.copyfile(package_a / 'sbin/homeassistant-access-guard', second)
            second.chmod(0o755)
            manifest = target / 'var/lib/mihomo-automation/installed-manifest.json'
            candidate = json.loads((package_b / 'release-manifest.json').read_text())
            manifest.write_text(json.dumps(candidate, indent=2) + '\n')
            manifest.chmod(0o600)
            marker.unlink(missing_ok=True)
            second_race_env = dict(race_env, R4_STOP_PATH=str(manifest.resolve()),
                                   R4_REPLACE_MODE='edit_after')
            last = subprocess.run(['sh', str(package_b / 'install.sh')], env=second_race_env,
                                  capture_output=True, text=True)
            self.assertNotEqual(last.returncode, 0)
            self.assertIn('install recovery readback mismatch', last.stderr)
            self.assertEqual(first.read_text(), 'late edit during recovery\n')
            self.assertTrue(pending.is_file())

            shutil.copyfile(package_a / 'sbin/dnsmasq-safe-start', first)
            first.chmod(0o755)
            completed = subprocess.run(['sh', str(package_b / 'install.sh')],
                                       env=dict(os.environ, GATEWAY_ROOT=str(target)),
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse((target / 'var/lib/mihomo-automation/.install-pending').exists())

    def test_incomplete_recovery_journal_never_adopts_operator_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'root'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            target.mkdir()
            begun = subprocess.run(
                [sys.executable, str(package / 'check-release.py'), '--root', str(target), '--begin'],
                capture_output=True, text=True,
            )
            self.assertEqual(begun.returncode, 0, begun.stderr)
            journal_path = target / 'var/lib/mihomo-automation/.install-pending/journal.json'
            journal = json.loads(journal_path.read_text())
            entry = next(item for item in journal['entries']
                         if item['path'] == 'usr/local/sbin/dnsmasq-safe-start')
            entry['new'].pop('sha256')
            journal_path.write_text(json.dumps(journal, indent=2) + '\n')
            damaged_journal = journal_path.read_bytes()

            operator_file = target / entry['path']
            operator_file.parent.mkdir(parents=True)
            operator_file.write_bytes(b'operator file must survive\n')
            operator_file.chmod(entry['new']['mode'])
            operator_bytes = operator_file.read_bytes()
            recovered = subprocess.run(
                ['sh', str(package / 'install.sh')],
                env=dict(os.environ, GATEWAY_ROOT=str(target)),
                capture_output=True, text=True,
            )
            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn('incomplete install recovery state', recovered.stderr)
            self.assertEqual(operator_file.read_bytes(), operator_bytes)
            self.assertEqual(journal_path.read_bytes(), damaged_journal)

    def test_operator_edit_after_snapshot_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package_a = base / 'package-a'; package_b = base / 'package-b'
            target = base / 'root'; shim_dir = base / 'shim'
            entered = base / 'entered'; release = base / 'release'; owner_file = base / 'owner-pid'
            shutil.copytree(SOURCE, package_a)
            subprocess.run([sys.executable, str(package_a / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            installed = subprocess.run(['sh', str(package_a / 'install.sh')],
                                       env=dict(os.environ, GATEWAY_ROOT=str(target)),
                                       capture_output=True, text=True)
            self.assertEqual(installed.returncode, 0, installed.stderr)

            shutil.copytree(package_a, package_b)
            (package_b / 'sbin/dnsmasq-safe-start').write_text('#!/bin/sh\n# release B\n')
            (package_b / 'sbin/homeassistant-access-guard').write_text('#!/bin/sh\n# release B\n')
            subprocess.run([sys.executable, str(package_b / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            shim_dir.mkdir()
            shim = shim_dir / 'install'
            shim.write_text(
                '#!/usr/bin/env python3\n'
                'import os, pathlib, sys, time\n'
                'args = sys.argv[1:]\n'
                'if "-d" not in args:\n'
                '    counter = pathlib.Path(os.environ["R4_INSTALL_COUNT"])\n'
                '    count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
                '    counter.write_text(str(count))\n'
                '    if count == 1:\n'
                '        pathlib.Path(os.environ["R4_ENTERED"]).write_text("blocked")\n'
                '        release = pathlib.Path(os.environ["R4_RELEASE"])\n'
                '        while not release.exists(): time.sleep(0.01)\n'
                'os.execv(os.environ["R4_REAL_INSTALL"], [os.environ["R4_REAL_INSTALL"], *args])\n',
                encoding='utf-8',
            )
            shim.chmod(0o755)
            env = dict(os.environ, GATEWAY_ROOT=str(target),
                       PATH=f'{shim_dir}{os.pathsep}{os.environ.get("PATH", "")}',
                       R4_INSTALL_COUNT=str(base / 'install-count'), R4_ENTERED=str(entered),
                       R4_RELEASE=str(release), R4_REAL_INSTALL=shutil.which('install'),
                       R4_OWNER_FILE=str(owner_file), R4_INSTALL_SH=str(package_b / 'install.sh'))
            owner = subprocess.Popen(
                ['sh', '-c', 'echo $$ > "$R4_OWNER_FILE"; exec sh "$R4_INSTALL_SH"'],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not entered.exists() and owner.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(entered.exists(), 'installer did not reach the blocking install command')
                edited = target / 'usr/local/sbin/homeassistant-access-guard'
                edited.write_text('operator edit during installation\n')
                release.touch()
                output, error = owner.communicate(timeout=30)
            finally:
                release.touch()
                if owner.poll() is None:
                    owner.kill()
                    owner.communicate(timeout=10)

            self.assertNotEqual(owner.returncode, 0, output + error)
            self.assertIn('install recovery conflict', error)
            self.assertEqual(edited.read_text(), 'operator edit during installation\n')
            self.assertTrue((target / 'var/lib/mihomo-automation/.install-pending/journal.json').is_file())
            self.assertIn('# release B',
                          (target / 'usr/local/sbin/dnsmasq-safe-start').read_text())

    def test_sigkill_first_install_cleans_temp_before_new_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'empty-root'
            shim_dir = base / 'shim'; owner_file = base / 'owner-pid'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            shim_dir.mkdir()
            shim = shim_dir / 'install'
            shim.write_text(
                '#!/usr/bin/env python3\n'
                'import os, pathlib, signal, subprocess, sys\n'
                'args = sys.argv[1:]\n'
                'if "-d" not in args:\n'
                '    counter = pathlib.Path(os.environ["R4_INSTALL_COUNT"])\n'
                '    count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
                '    counter.write_text(str(count))\n'
                '    if count == 1:\n'
                '        result = subprocess.run([os.environ["R4_REAL_INSTALL"], *args])\n'
                '        os.kill(int(pathlib.Path(os.environ["R4_OWNER_PID"]).read_text()), signal.SIGKILL)\n'
                '        os.kill(os.getppid(), signal.SIGKILL)\n'
                '        raise SystemExit(result.returncode)\n'
                'os.execv(os.environ["R4_REAL_INSTALL"], [os.environ["R4_REAL_INSTALL"], *args])\n',
                encoding='utf-8',
            )
            shim.chmod(0o755)
            env = dict(os.environ, GATEWAY_ROOT=str(target),
                       PATH=f'{shim_dir}{os.pathsep}{os.environ.get("PATH", "")}',
                       R4_INSTALL_COUNT=str(base / 'install-count'), R4_OWNER_FILE=str(owner_file),
                       R4_OWNER_PID=str(owner_file), R4_INSTALL_SH=str(package / 'install.sh'),
                       R4_REAL_INSTALL=shutil.which('install'))
            owner = subprocess.Popen(
                ['sh', '-c', 'echo $$ > "$R4_OWNER_FILE"; exec sh "$R4_INSTALL_SH"'],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            output, error = owner.communicate(timeout=30)
            self.assertEqual(owner.returncode, -signal.SIGKILL, output + error)
            pending = target / 'var/lib/mihomo-automation/.install-pending/journal.json'
            journal = json.loads(pending.read_text())
            interrupted = next(item for item in journal['entries']
                               if item['path'] == 'usr/local/sbin/dnsmasq-safe-start')
            self.assertTrue((target / interrupted['temp']).is_file())
            self.assertTrue((target / 'usr/local/sbin').is_dir())

            recovered = subprocess.run(['sh', str(package / 'install.sh')], env=env,
                                       capture_output=True, text=True)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertFalse((target / 'var/lib/mihomo-automation/.install-pending').exists())
            self.assertTrue((target / 'usr/local/sbin/dnsmasq-safe-start').is_file())

    def test_failed_first_install_removes_all_new_nested_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'empty-root'
            shim_dir = base / 'shim'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            shim_dir.mkdir()
            shim = shim_dir / 'install'
            shim.write_text(
                '#!/usr/bin/env python3\n'
                'import os, pathlib, sys\n'
                'args = sys.argv[1:]\n'
                'if "-d" not in args:\n'
                '    counter = pathlib.Path(os.environ["R4_INSTALL_COUNT"])\n'
                '    count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
                '    counter.write_text(str(count))\n'
                '    if count == 1:\n'
                '        raise SystemExit(73)\n'
                'os.execv(os.environ["R4_REAL_INSTALL"], [os.environ["R4_REAL_INSTALL"], *args])\n',
                encoding='utf-8',
            )
            shim.chmod(0o755)
            failed = subprocess.run(
                ['sh', str(package / 'install.sh')],
                env=dict(os.environ, GATEWAY_ROOT=str(target),
                         PATH=f'{shim_dir}{os.pathsep}{os.environ.get("PATH", "")}',
                         R4_INSTALL_COUNT=str(base / 'install-count'),
                         R4_REAL_INSTALL=shutil.which('install')),
                capture_output=True, text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((target / 'etc').exists())
            self.assertFalse((target / 'var/lib/mihomo-measurements').exists())
            self.assertFalse((target / 'usr').exists())
            self.assertFalse((target / 'var/lib/mihomo-automation/.install-pending').exists())

    def test_only_one_sandbox_installer_holds_the_maintenance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'root'
            entered = base / 'entered'; release = base / 'release'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            command = (
                'import os, pathlib, time; '
                'pathlib.Path(os.environ["R4_ENTERED"]).write_text("locked"); '
                'release = pathlib.Path(os.environ["R4_RELEASE"]); '
                'while_wait = True\n'
                'while while_wait and not release.exists(): time.sleep(0.01)\n'
            )
            owner = subprocess.Popen(
                [sys.executable, str(package / 'check-release.py'), '--root', str(target),
                 '--lock-run', sys.executable, '-c', command],
                env=dict(os.environ, R4_ENTERED=str(entered), R4_RELEASE=str(release)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not entered.exists() and owner.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not entered.exists():
                    release.touch()
                    output, error = owner.communicate(timeout=10)
                    self.fail(f'lock owner did not enter its command: {output}{error}')
                second = subprocess.run(['sh', str(package / 'install.sh')],
                                        env=dict(os.environ, GATEWAY_ROOT=str(target)),
                                        capture_output=True, text=True)
                self.assertNotEqual(second.returncode, 0)
                self.assertIn('gateway maintenance is busy', second.stderr)
                self.assertFalse((target / 'var/lib/mihomo-automation/.install-pending').exists())
            finally:
                release.touch()
                owner.communicate(timeout=10)

    def test_first_install_failure_removes_new_files_and_keeps_private_template(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package = base / 'package'; target = base / 'root'
            shim_dir = base / 'shim'
            shutil.copytree(SOURCE, package)
            subprocess.run([sys.executable, str(package / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            private = target / 'etc/mihomo/adaptive/capabilities.yaml'
            private.parent.mkdir(parents=True)
            private.write_bytes(b'# operator data\n')
            private.chmod(0o600)
            original_directory_mode = private.parent.stat().st_mode & 0o7777

            shim_dir.mkdir()
            shim = shim_dir / 'install'
            shim.write_text(
                '#!/usr/bin/env python3\n'
                'import os, pathlib, sys\n'
                'args = sys.argv[1:]\n'
                'if "-d" not in args:\n'
                '    counter = pathlib.Path(os.environ["R4_INSTALL_COUNT"])\n'
                '    count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
                '    counter.write_text(str(count))\n'
                '    if count == 1:\n'
                '        raise SystemExit(73)\n'
                'os.execv(os.environ["R4_REAL_INSTALL"], [os.environ["R4_REAL_INSTALL"], *args])\n',
                encoding='utf-8',
            )
            shim.chmod(0o755)
            env = dict(os.environ, GATEWAY_ROOT=str(target),
                       PATH=f'{shim_dir}{os.pathsep}{os.environ.get("PATH", "")}',
                       R4_INSTALL_COUNT=str(base / 'install-count'),
                       R4_REAL_INSTALL=shutil.which('install'))
            failed = subprocess.run(['sh', str(package / 'install.sh')], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(private.read_bytes(), b'# operator data\n')
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
            self.assertEqual(private.parent.stat().st_mode & 0o7777, original_directory_mode)
            self.assertFalse((target / 'usr/local/sbin').exists())
            self.assertFalse((target / 'etc/systemd/system').exists())
            self.assertFalse((target / 'var/lib/mihomo-automation/installed-manifest.json').exists())
            self.assertFalse((target / 'var/lib/mihomo-automation/.install-pending').exists())

    def test_sigkill_is_recovered_before_the_next_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package_a = base / 'package-a'; package_b = base / 'package-b'
            target = base / 'root'; shim_dir = base / 'shim'; owner_file = base / 'owner-pid'
            shutil.copytree(SOURCE, package_a)
            subprocess.run([sys.executable, str(package_a / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            env = dict(os.environ, GATEWAY_ROOT=str(target))
            installed = subprocess.run(['sh', str(package_a / 'install.sh')], env=env,
                                       capture_output=True, text=True)
            self.assertEqual(installed.returncode, 0, installed.stderr)

            shutil.copytree(package_a, package_b)
            (package_b / 'sbin/dnsmasq-safe-start').write_text('#!/bin/sh\n# release B\n')
            (package_b / 'sbin/homeassistant-access-guard').write_text('#!/bin/sh\n# release B\n')
            (package_b / 'sbin/zz-recovery-probe').write_text('#!/bin/sh\nexit 0\n')
            (package_b / 'sbin/zz-recovery-probe').chmod(0o755)
            subprocess.run([sys.executable, str(package_b / 'check-release.py'), '--write'],
                           check=True, capture_output=True)

            shim_dir.mkdir()
            shim = shim_dir / 'install'
            shim.write_text(
                '#!/usr/bin/env python3\n'
                'import os, pathlib, signal, subprocess, sys\n'
                'args = sys.argv[1:]\n'
                'if "-d" not in args:\n'
                '    counter = pathlib.Path(os.environ["R4_INSTALL_COUNT"])\n'
                '    count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
                '    counter.write_text(str(count))\n'
                '    if count == 2:\n'
                '        result = subprocess.run([os.environ["R4_REAL_INSTALL"], *args])\n'
                '        os.kill(int(pathlib.Path(os.environ["R4_OWNER_PID"]).read_text()), signal.SIGKILL)\n'
                '        os.kill(os.getppid(), signal.SIGKILL)\n'
                '        raise SystemExit(result.returncode)\n'
                'os.execv(os.environ["R4_REAL_INSTALL"], [os.environ["R4_REAL_INSTALL"], *args])\n',
                encoding='utf-8',
            )
            shim.chmod(0o755)
            owner = subprocess.Popen(
                ['sh', '-c', 'echo $$ > "$R4_OWNER_FILE"; exec sh "$R4_INSTALL_SH"'],
                env=dict(env, PATH=f'{shim_dir}{os.pathsep}{os.environ.get("PATH", "")}',
                         R4_INSTALL_COUNT=str(base / 'install-count'), R4_OWNER_FILE=str(owner_file),
                         R4_OWNER_PID=str(owner_file), R4_INSTALL_SH=str(package_b / 'install.sh'),
                         R4_REAL_INSTALL=shutil.which('install')),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            output, error = owner.communicate(timeout=30)
            self.assertEqual(owner.returncode, -signal.SIGKILL, output + error)
            pending = target / 'var/lib/mihomo-automation/.install-pending/journal.json'
            self.assertTrue(pending.is_file())
            journal = json.loads(pending.read_text())
            interrupted = next(item for item in journal['entries']
                               if item['path'] == 'usr/local/sbin/homeassistant-access-guard')
            self.assertTrue((target / interrupted['temp']).is_file())

            drifted = target / 'usr/local/sbin/mihomo-healthcheck'
            drifted.chmod(0o700)
            pending_bytes = pending.read_bytes()
            def snapshot_tree():
                return {
                    str(path.relative_to(target)): (
                        'dir' if path.is_dir() and not path.is_symlink() else
                        'symlink:' + os.readlink(path) if path.is_symlink() else
                        path.read_bytes(),
                        path.lstat().st_mode & 0o777,
                    )
                    for path in target.rglob('*')
                }
            before_dry_run = snapshot_tree()
            dry_run = subprocess.run(['sh', str(package_b / 'install.sh'), '--dry-run'], env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(dry_run.returncode, 0)
            self.assertEqual(snapshot_tree(), before_dry_run)
            self.assertEqual(pending.read_bytes(), pending_bytes)

            conflict = subprocess.run(['sh', str(package_b / 'install.sh')], env=env,
                                      capture_output=True, text=True)
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn('install recovery conflict', conflict.stderr)
            self.assertEqual(pending.read_bytes(), pending_bytes)
            self.assertEqual(drifted.read_bytes(), (package_a / 'sbin/mihomo-healthcheck').read_bytes())
            self.assertIn('# release B',
                          (target / 'usr/local/sbin/dnsmasq-safe-start').read_text())

            drifted.chmod(0o755)

            recovered = subprocess.run(['sh', str(package_b / 'install.sh')], env=env,
                                       capture_output=True, text=True)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertFalse((target / 'var/lib/mihomo-automation/.install-pending').exists())
            release_check = subprocess.run(
                [sys.executable, str(package_b / 'check-release.py'), '--root', str(target)],
                capture_output=True, text=True,
            )
            self.assertEqual(release_check.returncode, 0, release_check.stderr)
            expected = json.loads((package_b / 'release-manifest.json').read_text())
            saved = json.loads((target / 'var/lib/mihomo-automation/installed-manifest.json').read_text())
            self.assertEqual(saved['release_id'], expected['release_id'])

    def test_failed_upgrade_restores_the_previous_release(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); package_a = base / 'package-a'; package_b = base / 'package-b'
            target = base / 'root'; shim_dir = base / 'shim'
            shutil.copytree(SOURCE, package_a)
            subprocess.run([sys.executable, str(package_a / 'check-release.py'), '--write'],
                           check=True, capture_output=True)
            env = dict(os.environ, GATEWAY_ROOT=str(target))
            first = subprocess.run(['sh', str(package_a / 'install.sh')], env=env,
                                   capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            manifest = target / 'var/lib/mihomo-automation/installed-manifest.json'
            old_manifest = manifest.read_bytes()
            old_tree = {
                str(path.relative_to(target)): (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in target.rglob('*') if path.is_file()
            }

            shutil.copytree(package_a, package_b)
            (package_b / 'sbin/dnsmasq-safe-start').write_text('#!/bin/sh\n# release B\n')
            (package_b / 'sbin/homeassistant-access-guard').write_text('#!/bin/sh\n# release B\n')
            (package_b / 'sbin/zz-recovery-probe').write_text('#!/bin/sh\nexit 0\n')
            (package_b / 'sbin/zz-recovery-probe').chmod(0o755)
            subprocess.run([sys.executable, str(package_b / 'check-release.py'), '--write'],
                           check=True, capture_output=True)

            shim_dir.mkdir()
            real_install = shutil.which('install')
            shim = shim_dir / 'install'
            shim.write_text(
                '#!/usr/bin/env python3\n'
                'import os, pathlib, sys\n'
                'args = sys.argv[1:]\n'
                'if "-d" not in args:\n'
                '    counter = pathlib.Path(os.environ["R4_INSTALL_COUNT"])\n'
                '    count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
                '    counter.write_text(str(count))\n'
                '    if count == 2:\n'
                '        raise SystemExit(73)\n'
                'os.execv(os.environ["R4_REAL_INSTALL"], [os.environ["R4_REAL_INSTALL"], *args])\n',
                encoding='utf-8',
            )
            shim.chmod(0o755)
            failed = subprocess.run(
                ['sh', str(package_b / 'install.sh')],
                env=dict(env, PATH=f'{shim_dir}{os.pathsep}{os.environ.get("PATH", "")}',
                         R4_INSTALL_COUNT=str(base / 'install-count'), R4_REAL_INSTALL=real_install),
                capture_output=True, text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            after_tree = {
                str(path.relative_to(target)): (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in target.rglob('*') if path.is_file()
            }
            self.assertEqual(after_tree, old_tree)
            self.assertEqual(manifest.read_bytes(), old_manifest)
            self.assertFalse((target / 'usr/local/sbin/zz-recovery-probe').exists())

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
                    destination.chmod(entry['mode'])
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
