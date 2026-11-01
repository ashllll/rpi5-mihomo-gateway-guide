#!/usr/bin/env python3
"""End-to-end checks for ops/mihomo-config-install.

The installer is the last writer between an operator and the only config file
that keeps the house online.  These cases run it against a real controller API
stub and a real filesystem:

* a good install leaves the new file, a 0600 fsynced backup and a verified API;
* an API rejection after install restores the old file and verifies it again;
* a power cut between fsync and rename leaves the old complete file, never a
  half-written live config (the pre-fix installer fails this);
* a rollback whose own API verification fails exits non-zero and says
  ROLLBACK FAILED instead of claiming success.

The bearer secret is a placeholder created by the test; the installer must send
it without printing it.
"""
from __future__ import annotations

import http.server
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'ops/mihomo-config-install'
SECRET = 'test-' + 'secret-value'
OLD_CONFIG = f'secret: {SECRET}\nmode: rule\nlog-level: info\nproxies: []\n'
NEW_CONFIG = f'secret: {SECRET}\nmode: rule\nlog-level: warning\nproxies: []\n'

STUBS = {
    'id': 'print(0)\n',
    'mihomo': 'import os\nraise SystemExit(1 if os.environ.get("MIHOMO_TEST_REJECT") else 0)\n',
    'install': '''import shutil,sys
args = sys.argv[1:]
while args and args[0].startswith('-'):
    flag = args.pop(0)
    if flag in {'-o', '-g', '-m'} and args:
        args.pop(0)
if len(args) >= 2:
    shutil.copyfile(args[-2], args[-1])
sys.exit(0)
''',
    'systemctl': '''import os,sys
from pathlib import Path
with open(os.environ['TEST_CALLS'], 'a') as stream: stream.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1] in {'reload', 'restart'} and os.environ.get('MIHOMO_TEST_RELOAD_FAIL'):
    sys.exit(1)
if sys.argv[1] == 'is-active' and os.environ.get('MIHOMO_TEST_INACTIVE'):
    sys.exit(3)
if sys.argv[1] == 'list-timers':
    sys.exit(0)
sys.exit(0)
''',
}


class Api:
    def __init__(self, live: Path) -> None:
        self.live = live
        self.always_500 = False
        self.allow_new = True
        self.secrets: list[str] = []
        self.hits = 0
        handler = self._handler()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self):
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                api.hits += 1
                api.secrets.append(self.headers.get('Authorization', ''))
                current = api.live.read_text()
                new = 'log-level: warning' in current
                healthy = not api.always_500 and (api.allow_new or not new)
                if self.path != '/version' or not healthy:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'no')
                    return
                body = b'{"version":"v-test"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def run(installer: Path, base: Path, api: Api, fault: str = '', extra_env: dict | None = None):
    etc = base / 'etc'
    live = etc / 'config.yaml'
    candidate = base / 'candidate.yaml'
    calls = base / 'calls'
    calls.write_text('')
    env = dict(os.environ, PATH=f"{base / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
               MIHOMO_DIR=str(etc), MIHOMO_BIN=str(base / 'bin/mihomo'),
               MIHOMO_API=f'http://127.0.0.1:{api.port}', MIHOMO_API_RETRIES='2',
               MIHOMO_API_SLEEP='0', MIHOMO_INSTALL_FAULT=fault, TEST_CALLS=str(calls),
               MIHOMO_API_SECRET='', PYTHONDONTWRITEBYTECODE='1')
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(['bash', str(installer), str(candidate)], env=env,
                            capture_output=True, text=True, timeout=60)
    return result, live, calls.read_text()


def main() -> int:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory(prefix='config-install-') as raw:
        for case in ('normal', 'api-500', 'kill-before-rename', 'rollback-fails'):
            base = Path(raw) / case
            (base / 'etc').mkdir(parents=True)
            (base / 'bin').mkdir()
            for name, body in STUBS.items():
                path = base / 'bin' / name
                path.write_text('#!' + sys.executable + '\n' + body)
                path.chmod(0o755)
            live = base / 'etc/config.yaml'
            live.write_text(OLD_CONFIG)
            (base / 'candidate.yaml').write_text(NEW_CONFIG)
            api = Api(live)
            try:
                if case == 'normal':
                    result, live, calls = run(INSTALLER, base, api)
                    check(result.returncode == 0, f'normal: rc={result.returncode} {result.stderr}')
                    check(live.read_text() == NEW_CONFIG, 'normal: live is not the candidate')
                    backups = list((base / 'etc').glob('config.yaml.bak-*'))
                    check(len(backups) == 1, f'normal: expected one backup, got {backups}')
                    if backups:
                        check(backups[0].read_text() == OLD_CONFIG, 'normal: backup is not the old config')
                        check(backups[0].stat().st_mode & 0o777 == 0o600, 'normal: backup mode is not 0600')
                    check(api.hits >= 1, 'normal: API was never asked')
                    check(all(f'Bearer {SECRET}' == value for value in api.secrets),
                          'normal: bearer secret missing or wrong')
                    check('restart mihomo' in calls or 'reload mihomo' in calls, calls)
                elif case == 'api-500':
                    api.allow_new = False
                    result, live, calls = run(INSTALLER, base, api)
                    check(result.returncode == 1, f'api-500: rc={result.returncode} {result.stderr}')
                    check(live.read_text() == OLD_CONFIG, 'api-500: live was not restored')
                    check('rolled back' in result.stdout or 'rolled back' in result.stderr,
                          f'api-500: rollback not reported: {result.stdout}')
                    check('ROLLBACK FAILED' not in result.stderr, 'api-500: falsely reported a failed rollback')
                elif case == 'kill-before-rename':
                    result, live, calls = run(INSTALLER, base, api, fault='kill-before-rename')
                    check(result.returncode != 0, 'kill: installer unexpectedly succeeded')
                    check(live.read_text() == OLD_CONFIG,
                          'kill: live config was partially written before the rename')
                elif case == 'rollback-fails':
                    api.always_500 = True
                    result, live, calls = run(INSTALLER, base, api)
                    check(result.returncode == 3, f'rollback-fails: rc={result.returncode} {result.stderr}')
                    check(live.read_text() == OLD_CONFIG, 'rollback-fails: old file was not restored')
                    check('ROLLBACK FAILED' in result.stderr, 'rollback-fails: no explicit failure message')
                    check('rolled back:' not in result.stdout, 'rollback-fails: claimed a successful rollback')
            finally:
                api.close()

    if failures:
        print('config install checks failed:', file=sys.stderr)
        for failure in failures:
            print(' - ' + failure, file=sys.stderr)
        return 1
    print('config install checks passed: atomic replace, real API gate, honest rollback')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
