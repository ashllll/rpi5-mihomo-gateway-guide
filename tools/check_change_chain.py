#!/usr/bin/env python3
"""Behavioural regression for the gateway change-chain state machines.

The scenarios copy the real scripts from automation/sbin into throw-away roots,
rewrite absolute paths, and drive them with stubs.  Every scenario also runs a
mutation of the script under test: the mutation must make the scenario fail,
otherwise the scenario is not proving the property it claims to protect.

Only the Python standard library is used; no host path outside the temporary
directory is touched.  Run directly (exit 0 = all scenarios and mutations
passed) or let scripts/preflight.sh pick it up with the other tools/check_*.py.
"""
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SBIN = ROOT / 'automation' / 'sbin'
PYTHON = sys.executable
SH = '/bin/sh'

FAILURES = []


class CaseFailure(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise CaseFailure(message)


def write(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    path.chmod(mode)


def run(argv, env, timeout=60, cwd=None):
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)


def replace_all(text, pairs):
    for absolute, replacement in pairs:
        text = text.replace(absolute, str(replacement))
    return text


def make_stub(directory, name, body):
    path = Path(directory) / name
    write(path, body.replace('PYTHON_PLACEHOLDER', PYTHON), 0o755)
    return path


FLOCK_STUB = '''#!PYTHON_PLACEHOLDER
import fcntl, sys
args = sys.argv[1:]
fd = next((int(a) for a in reversed(args) if a.isdigit()), None)
if fd is None:
    sys.exit(2)
fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if '-n' in args else 0))
'''


class Sandbox:
    def __init__(self, label):
        self._tmp = tempfile.TemporaryDirectory(prefix='change-chain-' + label + '.')
        self.work = Path(self._tmp.name)
        self.root = self.work / 'root'
        self.bin = self.work / 'bin'
        self.bin.mkdir(parents=True)
        for directory in ('etc/mihomo', 'etc/mosdns/backups', 'etc/mosdns/rules',
                          'usr/local/bin', 'usr/local/sbin', 'usr/sbin',
                          'var/tmp', 'run/lock', 'var/backups', 'log', 'state'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        make_stub(self.bin, 'flock', FLOCK_STUB.replace('PYTHON_PLACEHOLDER', PYTHON))
        make_stub(self.bin, 'logger', '''#!PYTHON_PLACEHOLDER
import os, sys
with open(os.environ['CASE_LOG'], 'a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
''')
        make_stub(self.bin, 'install', '''#!PYTHON_PLACEHOLDER
import os, sys
args = sys.argv[1:]
clean = []
directory = False
index = 0
while index < len(args):
    item = args[index]
    if item == '-d':
        directory = True
    elif item in ('-o', '-g'):
        index += 1
    elif item == '-m':
        modus = args[index + 1]
        index += 1
    elif item.startswith('-'):
        pass
    else:
        clean.append(item)
    index += 1
os.execv('/usr/bin/install', ['/usr/bin/install'] + (['-d'] if directory else []) + clean)
''')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()

    def env(self, **extra):
        values = dict(os.environ)
        values.update(CASE_LOG=str(self.work / 'case.log'),
                      PATH=str(self.bin) + ':/usr/bin:/bin:/usr/sbin:/sbin')
        values.update({key: str(value) for key, value in extra.items()})
        return values


# ---------------------------------------------------------------------------
# F-09: mihomo-dns-mode writes the active upstream file with fsync + rename.
# ---------------------------------------------------------------------------

DNS_OLD = 'server=192.0.2.99\n'
DNS_NEW = 'server=192.0.2.53\n'


def dns_mode_rewrite(text, sandbox):
    text = text.replace('/etc/mihomo-automation.env', '@ENV@')
    text = text.replace('/etc/mihomo', str(sandbox.root / 'etc/mihomo'))
    text = text.replace('/etc/dnsmasq-upstreams.conf',
                        str(sandbox.root / 'etc/dnsmasq-upstreams.conf'))
    text = text.replace('/usr/sbin/dnsmasq', str(sandbox.root / 'usr/sbin/dnsmasq'))
    text = text.replace('/run/', str(sandbox.root) + '/run/')
    return text.replace('@ENV@', str(sandbox.root / 'etc/mihomo-automation.env'))


def dns_mode_copy(sandbox):
    path = sandbox.work / 'mihomo-dns-mode'
    write(path, dns_mode_rewrite((SBIN / 'mihomo-dns-mode').read_text(encoding='utf-8'), sandbox), 0o755)
    return path


def dns_mode_fixture(sandbox):
    write(sandbox.root / 'etc/dnsmasq-upstreams.conf', DNS_OLD)
    for mode in ('mosdns', 'mihomo', 'direct'):
        write(sandbox.root / 'etc/mihomo' / ('dnsmasq-upstreams.' + mode), DNS_NEW)
    write(sandbox.root / 'etc/mihomo-automation.env', '')
    write(sandbox.root / 'usr/sbin/dnsmasq', '#!/bin/sh\nexit 0\n', 0o755)
    make_stub(sandbox.bin, 'dig', '''#!PYTHON_PLACEHOLDER
import sys
args = sys.argv[1:]
if 'openai.com' in args:
    print('198.18.0.1')
else:
    print('203.0.113.10')
''')
    make_stub(sandbox.bin, 'chown', '#!/bin/sh\nexit 0\n')


def dns_mode_assert(sandbox, expect_old):
    active = (sandbox.root / 'etc/dnsmasq-upstreams.conf').read_text(encoding='utf-8')
    if expect_old:
        check(active == DNS_OLD, 'active file changed on a failed write: %r' % active)
    else:
        check(active == DNS_NEW, 'active file was not replaced: %r' % active)
    leftovers = sorted(path.name for path in (sandbox.root / 'etc').glob('dnsmasq-upstreams.conf.tmp.*'))
    check(not leftovers, 'temporary files left behind: %s' % leftovers)


def dns_mode_scenario(fail_python=False, fail_rename=False):
    with Sandbox('dns-mode') as sandbox:
        script = dns_mode_copy(sandbox)
        dns_mode_fixture(sandbox)
        if fail_python:
            make_stub(sandbox.bin, 'python3', '#!/bin/sh\nexit 1\n')
        if fail_rename:
            make_stub(sandbox.bin, 'mv', '#!/bin/sh\nexit 1\n')
        result = run([SH, str(script), 'mihomo'], sandbox.env(), timeout=30)
        output = (result.stdout + result.stderr).strip()
        if fail_python or fail_rename:
            check(result.returncode != 0, 'script reported success although the write failed: ' + output)
            dns_mode_assert(sandbox, expect_old=True)
        else:
            check(result.returncode == 0, 'healthy mode switch failed: ' + output)
            dns_mode_assert(sandbox, expect_old=False)


def dns_mode_mutation_killed():
    text = (SBIN / 'mihomo-dns-mode').read_text(encoding='utf-8')
    mutated = text.replace('temporary="${active}.tmp.$$"', 'temporary="$active"')
    check(mutated != text, 'dns-mode mutation point not found')
    with Sandbox('dns-mode-mutation') as sandbox:
        script = sandbox.work / 'mihomo-dns-mode'
        write(script, dns_mode_rewrite(mutated, sandbox), 0o755)
        dns_mode_fixture(sandbox)
        make_stub(sandbox.bin, 'python3', '#!/bin/sh\nexit 1\n')
        try:
            dns_mode_assert_direct_write(sandbox, script)
        except (CaseFailure, FileNotFoundError):
            return
        raise CaseFailure('mutation survived: a direct write to the active file was not detected')


def dns_mode_assert_direct_write(sandbox, script):
    result = run([SH, str(script), 'mihomo'], sandbox.env(), timeout=30)
    check(result.returncode != 0, 'mutated script unexpectedly succeeded')
    dns_mode_assert(sandbox, expect_old=True)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def case(name, function):
    try:
        function()
    except Exception as error:  # noqa: BLE001 - report every scenario failure
        FAILURES.append(name)
        print('FAIL %-34s %s' % (name, error))
    else:
        print('pass %-34s' % name)


def main():
    case('F-09 dns-mode atomic success', dns_mode_scenario)
    case('F-09 dns-mode failed write', lambda: dns_mode_scenario(fail_python=True))
    case('F-09 dns-mode failed rename', lambda: dns_mode_scenario(fail_rename=True))
    case('F-09 dns-mode mutation killed', dns_mode_mutation_killed)
    if FAILURES:
        print('change-chain: %d scenario(s) failed: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('change-chain: all scenarios and mutations passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
