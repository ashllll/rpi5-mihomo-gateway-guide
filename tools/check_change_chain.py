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
import json
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
# F-07: mihomo-core-swap persists the swap intent before exchanging.
# ---------------------------------------------------------------------------

CORE_OLD = b'old-core\n'
CORE_NEW = b'new-core\n'
SWAP_OLD = '''def exchange(first, second):
    if _libc.renameat2(-100, first.encode(), -100, second.encode(), RENAME_EXCHANGE) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))'''
SWAP_DARWIN = '''def exchange(first, second):
    if sys.platform == "darwin":
        rc = _libc.renamex_np(first.encode(), second.encode(), 2)
    else:
        rc = _libc.renameat2(-100, first.encode(), -100, second.encode(), 2)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))'''


def digest(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def core_swap_copy(sandbox, exchange=None):
    text = (SBIN / 'mihomo-core-swap').read_text(encoding='utf-8')
    text = text.replace('LIVE = "/usr/local/bin/mihomo"', 'LIVE = "%s/bin/mihomo"' % sandbox.work)
    text = text.replace('BASE = "/usr/local/lib/mihomo-swap"', 'BASE = "%s/lib/mihomo-swap"' % sandbox.work)
    if exchange is None and sys.platform == 'darwin':
        check(SWAP_OLD in text, 'core-swap exchange block not found')
        text = text.replace(SWAP_OLD, SWAP_DARWIN)
    elif exchange is not None:
        check(SWAP_OLD in text, 'core-swap exchange block not found')
        text = text.replace(SWAP_OLD, exchange)
    path = sandbox.work / 'core-swap'
    write(path, text, 0o755)
    return path


def core_swap_setup(sandbox):
    (sandbox.work / 'bin').mkdir(parents=True, exist_ok=True)
    (sandbox.work / 'lib/mihomo-swap').mkdir(parents=True, exist_ok=True)
    write(sandbox.work / 'bin/mihomo', CORE_OLD.decode())
    write(sandbox.work / 'new-core', CORE_NEW.decode())


def core_swap_run(script, *arguments, env=None):
    return subprocess.run([PYTHON, str(script), *arguments], capture_output=True,
                          text=True, timeout=60, env=env)


def core_swap_crash_recovery():
    with Sandbox('core-swap-crash') as sandbox:
        script = core_swap_copy(sandbox)
        core_swap_setup(sandbox)
        state = sandbox.work / 'lib/mihomo-swap/state.json'
        result = core_swap_run(script, 'install', str(sandbox.work / 'new-core'))
        check(result.returncode == 0, 'install failed: ' + result.stdout + result.stderr)
        before = state.read_text(encoding='utf-8')
        result = core_swap_run(script, 'activate')
        check(result.returncode == 0, 'activate failed: ' + result.stdout + result.stderr)
        check((sandbox.work / 'bin/mihomo').read_bytes() == CORE_NEW, 'activate did not swap in the new core')
        # Crash image: the old state file survived while the exchange did.
        state.write_text(before, encoding='utf-8')
        result = core_swap_run(script, 'rollback')
        check(result.returncode == 0, 'rollback failed: ' + result.stdout + result.stderr)
        check((sandbox.work / 'bin/mihomo').read_bytes() == CORE_OLD,
              'rollback did not restore the old core')


def core_swap_snapshot(live, slot):
    return {
        'live': slot, 'live_sha256': digest(live), 'rollback_slot': slot,
        'staged': None,
    }


def core_swap_inconsistent_refused():
    with Sandbox('core-swap-consistency') as sandbox:
        script = core_swap_copy(sandbox)
        core_swap_setup(sandbox)
        state = sandbox.work / 'lib/mihomo-swap/state.json'
        slot_path = sandbox.work / 'lib/mihomo-swap/a'
        # State says the new core is live, but the disk holds the old core.
        write(sandbox.work / 'bin/mihomo', CORE_OLD.decode())
        write(slot_path, CORE_NEW.decode())
        state.write_text(json.dumps(core_swap_snapshot(CORE_NEW, 'a')), encoding='utf-8')
        result = core_swap_run(script, 'rollback')
        check(result.returncode != 0, 'rollback accepted an inconsistent live core')
        check((sandbox.work / 'bin/mihomo').read_bytes() == CORE_OLD,
              'inconsistent rollback moved the live core')


CRASH_EXCHANGE = r'''def exchange(first, second):
    with open(os.environ["FSYNC_LOG"], "a") as stream:
        stream.write("EXCHANGE\n")
    os._exit(137)'''


def core_swap_intent_fsynced():
    with Sandbox('core-swap-intent') as sandbox:
        script = core_swap_copy(sandbox, exchange=CRASH_EXCHANGE)
        core_swap_setup(sandbox)
        site = sandbox.work / 'site'
        write(site / 'sitecustomize.py', r'''import os
log = os.environ.get("FSYNC_LOG")
if log:
    original = os.fsync
    def describe(descriptor):
        try:
            return os.readlink("/proc/self/fd/%d" % descriptor)
        except OSError:
            pass
        try:
            import fcntl as _fcntl
            return _fcntl.fcntl(descriptor, 50, b"\0" * 1024).split(b"\0", 1)[0].decode()
        except OSError:
            return "unknown"
    def recording(descriptor):
        target = describe(descriptor)
        with open(log, "a") as stream:
            stream.write("FSYNC %s\n" % target)
        return original(descriptor)
    os.fsync = recording
''')
        log = sandbox.work / 'fsync.log'
        state = sandbox.work / 'lib/mihomo-swap/state.json'
        env = sandbox.env(FSYNC_LOG=log, PYTHONPATH=site)
        result = subprocess.run([PYTHON, str(script), 'install', str(sandbox.work / 'new-core')],
                                capture_output=True, text=True, timeout=60, env=env)
        check(result.returncode == 0, 'install failed: ' + result.stdout + result.stderr)
        result = subprocess.run([PYTHON, str(script), 'activate'], capture_output=True,
                                text=True, timeout=60, env=env)
        check(result.returncode == 137, 'activate did not stop at the exchange: ' + result.stdout + result.stderr)
        saved = json.loads(state.read_text(encoding='utf-8'))
        check(saved.get('phase') == 'swapping', 'intent phase was not persisted: %r' % saved)
        check(saved.get('old_sha256') == digest(CORE_OLD), 'intent lost the old core hash: %r' % saved)
        check(saved.get('new_sha256') == digest(CORE_NEW), 'intent lost the new core hash: %r' % saved)
        check((sandbox.work / 'bin/mihomo').read_bytes() == CORE_OLD,
              'live core changed before the exchange')
        lines = [line.strip() for line in log.read_text(encoding='utf-8').splitlines() if line.strip()]
        check('EXCHANGE' in lines, 'exchange marker missing from the fsync log')
        before_exchange = lines[:lines.index('EXCHANGE')]
        check(any(line.endswith('state.json.stage') for line in before_exchange),
              'state file was not fsynced before the exchange: %s' % before_exchange)
        check(any(line.endswith('mihomo-swap') for line in before_exchange),
              'state directory was not fsynced before the exchange: %s' % before_exchange)
        # A durable intent with the old core still live must resolve as a no-op.
        normal = core_swap_copy(sandbox)
        result = core_swap_run(normal, 'rollback')
        check(result.returncode == 0, 'rollback of an unexchanged intent failed: ' + result.stdout + result.stderr)
        check((sandbox.work / 'bin/mihomo').read_bytes() == CORE_OLD,
              'unexchanged intent rollback moved the live core')
        final = json.loads(state.read_text(encoding='utf-8'))
        check('phase' not in final, 'swap intent was not cleared: %r' % final)


def core_swap_mutation_killed():
    text = (SBIN / 'mihomo-core-swap').read_text(encoding='utf-8')
    mutated = text.replace('if live_sha != state.get("live_sha256"):',
                           'if False and live_sha != state.get("live_sha256"):')
    check(mutated != text, 'core-swap mutation point not found')
    with Sandbox('core-swap-mutation') as sandbox:
        script = sandbox.work / 'core-swap'
        text = mutated.replace('LIVE = "/usr/local/bin/mihomo"', 'LIVE = "%s/bin/mihomo"' % sandbox.work)
        text = text.replace('BASE = "/usr/local/lib/mihomo-swap"', 'BASE = "%s/lib/mihomo-swap"' % sandbox.work)
        if sys.platform == 'darwin':
            text = text.replace(SWAP_OLD, SWAP_DARWIN)
        write(script, text, 0o755)
        core_swap_setup(sandbox)
        state = sandbox.work / 'lib/mihomo-swap/state.json'
        write(sandbox.work / 'bin/mihomo', CORE_OLD.decode())
        write(sandbox.work / 'lib/mihomo-swap/a', CORE_NEW.decode())
        state.write_text(json.dumps(core_swap_snapshot(CORE_NEW, 'a')), encoding='utf-8')
        result = core_swap_run(script, 'rollback')
        try:
            check(result.returncode != 0, 'mutated rollback accepted an inconsistent live core')
            check((sandbox.work / 'bin/mihomo').read_bytes() == CORE_OLD,
                  'mutated rollback moved the live core')
        except CaseFailure:
            return
        raise CaseFailure('mutation survived: the live-core hash guard is not exercised')


# ---------------------------------------------------------------------------
# F-06: mosdns-update-binary keeps a durable pending record around the swap.
# ---------------------------------------------------------------------------

BINARY_OLD = '''#!/bin/sh
if [ "${1:-}" = version ]; then echo 'v1.0.0'; exit 0; fi
trap 'exit 0' TERM INT
while :; do sleep 1; done
'''
BINARY_NEW = '''#!/bin/sh
if [ "${1:-}" = version ]; then echo 'v2.0.0'; exit 0; fi
trap 'exit 0' TERM INT
while :; do sleep 1; done
'''
BINARY_BROKEN = '''#!/bin/sh
if [ "${1:-}" = version ]; then echo 'v2.0.0'; exit 0; fi
exit 1
'''

BINARY_STUBS = {
    'systemctl': '''#!/usr/bin/env python3
import os, signal, sys, time
root = os.environ['MOSDNS_ROOT']
state = os.path.join(root, 'state')
os.makedirs(state, exist_ok=True)
def read(name, default='0'):
    try:
        return open(os.path.join(state, name)).read().strip()
    except OSError:
        return default
def write(name, value):
    open(os.path.join(state, name), 'w').write(str(value) + '\\n')
args = sys.argv[1:]
if args and args[0] == 'restart':
    count = int(read('restart_count')) + 1
    write('restart_count', count)
    if os.environ.get('KILL_ON_RESTART') == '1' and count == 1:
        os.kill(int(os.environ['TOP_PID']), signal.SIGKILL)
        time.sleep(0.2)
        sys.exit(137)
    sys.exit(0)
sys.exit(0)
''',
    'mosdns-healthcheck': '''#!/usr/bin/env python3
import os, sys
sys.exit(int(os.environ.get('HEALTH_RC', '0')))
''',
    'mihomo-dns-mode': '''#!/usr/bin/env python3
import os, sys
root = os.environ['MOSDNS_ROOT']
os.makedirs(os.path.join(root, 'state'), exist_ok=True)
open(os.path.join(root, 'state', 'dns-mode.log'), 'a').write(' '.join(sys.argv[1:]) + '\\n')
sys.exit(0)
''',
    'dig': '''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if 'baidu.com' in args:
    print('203.0.113.10')
elif 'openai.com' in args:
    print('198.18.0.1')
''',
    'curl': '''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
url = args[-1]
out = args[args.index('--output') + 1] if '--output' in args else None
fixtures = os.environ['MOSDNS_FIXTURES']
if 'api.github.com' in url:
    data = open(os.path.join(fixtures, 'release.json'), 'rb').read()
elif 'mosdns-linux-arm64.zip' in url:
    data = open(os.path.join(fixtures, 'mosdns.zip'), 'rb').read()
else:
    sys.exit(0)
if out:
    open(out, 'wb').write(data)
sys.exit(0)
''',
    'find': '''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if '-printf' in args and any('%T@' in item for item in args):
    root = next((item for item in args if not item.startswith('-') and '%T@' not in item), None)
    if root:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if os.path.isdir(path) and not os.path.islink(path):
                print('%.9f %s' % (os.stat(path).st_mtime, path))
    sys.exit(0)
os.execv('/usr/bin/find', ['/usr/bin/find'] + args)
''',
}


def binary_rewrite(text, sandbox):
    return replace_all(text, [
        ('/etc/', str(sandbox.root) + '/etc/'),
        ('/usr/local/bin', str(sandbox.root / 'usr/local/bin')),
        ('/usr/local/sbin', str(sandbox.root / 'usr/local/sbin')),
        ('/var/tmp', str(sandbox.root / 'var/tmp')),
        ('/run/lock', str(sandbox.root / 'run/lock')),
    ])


def binary_fixture(sandbox):
    import hashlib
    import json
    import zipfile
    root = sandbox.root
    write(root / 'etc/mosdns/config.yaml', '''log:
  level: info
plugins:
  - tag: forward
    type: forward
    args:
      upstreams:
        - addr: 127.0.0.1:5335
api:
  http: 127.0.0.1:9091
''')
    write(root / 'etc/mihomo-automation.env', 'MIHOMO_MIXED_PORT=7890\n')
    write(root / 'usr/local/bin/mosdns', BINARY_OLD, 0o755)
    for name, body in BINARY_STUBS.items():
        make_stub(sandbox.bin, name, body)
        if name in ('mosdns-healthcheck', 'mihomo-dns-mode'):
            shutil.copyfile(sandbox.bin / name, root / 'usr/local/sbin' / name)
            (root / 'usr/local/sbin' / name).chmod(0o755)
    fixtures = sandbox.work / 'fixtures'
    fixtures.mkdir()
    zip_path = fixtures / 'mosdns.zip'
    with zipfile.ZipFile(zip_path, 'w') as archive:
        archive.writestr('mosdns', BINARY_NEW)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    release = {'tag_name': 'v2.0.0', 'draft': False, 'prerelease': False,
               'assets': [{'name': 'mosdns-linux-arm64.zip',
                           'browser_download_url':
                               'https://github.com/IrineSistiana/mosdns/releases/download/v2.0.0/mosdns-linux-arm64.zip',
                           'digest': 'sha256:' + digest}]}
    write(fixtures / 'release.json', json.dumps(release), 0o644)


def binary_copy(sandbox):
    path = sandbox.work / 'update-binary'
    write(path, binary_rewrite((SBIN / 'mosdns-update-binary').read_text(encoding='utf-8'), sandbox), 0o755)
    launch = sandbox.work / 'launch.py'
    write(launch, '''#!/usr/bin/env python3
import os, sys
os.setsid()
os.environ['TOP_PID'] = str(os.getpid())
os.execvp(sys.argv[1], sys.argv[1:])
''', 0o755)
    return path, launch


def binary_run(sandbox, script, launch, **extra):
    env = sandbox.env(MOSDNS_ROOT=sandbox.root,
                      MOSDNS_FIXTURES=sandbox.work / 'fixtures', **extra)
    return subprocess.run([PYTHON, str(launch), SH, str(script)], env=env,
                          capture_output=True, text=True, timeout=60)


def binary_live_version(sandbox):
    result = subprocess.run([str(sandbox.root / 'usr/local/bin/mosdns'), 'version'],
                            capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def binary_scenario():
    with Sandbox('mosdns-binary') as sandbox:
        script, launch = binary_copy(sandbox)
        binary_fixture(sandbox)
        pending = sandbox.root / 'etc/mosdns/backups/transaction.pending'
        result = binary_run(sandbox, script, launch, KILL_ON_RESTART='1')
        check(result.returncode in (-9, 137), 'first run did not die at restart: rc=%s %s' %
              (result.returncode, result.stdout + result.stderr))
        check(pending.is_file(), 'no pending record survived the crash')
        fields = dict(line.split('=', 1) for line in pending.read_text(encoding='utf-8').splitlines())
        check(fields.get('kind') == 'binary', 'pending record is not a binary transaction: %r' % fields)
        check(fields.get('phase') == 'applied', 'pending phase is not applied: %r' % fields)
        check(fields.get('old_sha256') == digest(BINARY_OLD.encode()), 'pending lost the old sha256')
        check(fields.get('new_sha256') == digest(BINARY_NEW.encode()), 'pending lost the new sha256')
        check(binary_live_version(sandbox) == 'v2.0.0', 'the crash did not leave the new binary live')
        # The version string still claims v2, but the binary no longer starts.
        write(sandbox.root / 'usr/local/bin/mosdns', BINARY_BROKEN, 0o755)
        result = binary_run(sandbox, script, launch, HEALTH_RC='1')
        check(result.returncode == 0, 'recovery run failed: ' + result.stdout + result.stderr)
        check(binary_live_version(sandbox) == 'v1.0.0',
              'recovery did not restore the old binary while the new one was unverified')
        check(not pending.exists(), 'recovery left the pending record behind')
        # With a healthy candidate the same script must commit and clear.
        result = binary_run(sandbox, script, launch)
        check(result.returncode == 0, 'healthy update failed: ' + result.stdout + result.stderr)
        check(binary_live_version(sandbox) == 'v2.0.0', 'healthy update did not install the new binary')
        check(not pending.exists(), 'committed update left the pending record behind')


def binary_mutation_killed():
    text = (SBIN / 'mosdns-update-binary').read_text(encoding='utf-8')
    marker = 'if [ -s "$pending_file" ]; then'
    check(marker in text, 'binary recovery block not found')
    mutated = text.replace(marker, 'if false; then', 1)
    with Sandbox('mosdns-binary-mutation') as sandbox:
        script = sandbox.work / 'update-binary'
        write(script, binary_rewrite(mutated, sandbox), 0o755)
        launch = sandbox.work / 'launch.py'
        write(launch, '''#!/usr/bin/env python3
import os, sys
os.setsid()
os.environ['TOP_PID'] = str(os.getpid())
os.execvp(sys.argv[1], sys.argv[1:])
''', 0o755)
        binary_fixture(sandbox)
        result = binary_run(sandbox, script, launch, KILL_ON_RESTART='1')
        check(result.returncode in (-9, 137), 'mutated first run did not crash as expected')
        write(sandbox.root / 'usr/local/bin/mosdns', BINARY_BROKEN, 0o755)
        result = binary_run(sandbox, script, launch, HEALTH_RC='1')
        try:
            check(binary_live_version(sandbox) == 'v1.0.0',
                  'mutated run skipped recovery and left the broken binary live')
            check(result.returncode == 0, 'mutated recovery run failed unexpectedly')
        except CaseFailure:
            return
        raise CaseFailure('mutation survived: recovery-before-version is not exercised')


# ---------------------------------------------------------------------------
# F-06: mosdns-update-rules keeps a durable pending record around the switch.
# ---------------------------------------------------------------------------

RULES_TAG = '20261101'
RULES_VERSION = RULES_TAG + '-01234567'
RULES_FILES = ('direct-list.txt', 'proxy-list.txt', 'apple-cn.txt',
               'google-cn.txt', 'geoip-cn.txt')
RULES_LINES = {'direct-list.txt': 50000, 'proxy-list.txt': 10000,
               'apple-cn.txt': 100, 'google-cn.txt': 50, 'geoip-cn.txt': 3000}

RULES_CURL = '''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
url = args[-1]
out = args[args.index('--output') + 1] if '--output' in args else None
fixtures = os.environ['MOSDNS_FIXTURES']
if os.environ.get('FAIL_FETCH') == '1' and ('github.com' in url or 'githubusercontent.com' in url):
    sys.exit(22)
mapping = [
    ('v2ray-rules-dat/releases/latest', 'rules-release.json'),
    ('geoip/commits/release', 'geoip-commit.json'),
    ('geoip/contents/text/cn.txt', 'geoip-content.json'),
    ('/text/cn.txt', 'geoip-cn.txt'),
]
target = None
for needle, name in mapping:
    if needle in url:
        target = os.path.join(fixtures, name)
        break
if target is None and '/releases/download/' in url:
    target = os.path.join(fixtures, url.rsplit('/', 1)[-1])
if target is None:
    sys.exit(0)
data = open(target, 'rb').read()
if out:
    open(out, 'wb').write(data)
sys.exit(0)
'''

RULES_SED = '''#!/usr/bin/env python3
import os, platform, sys
args = sys.argv[1:]
if platform.system() == 'Darwin' and '-i' in args:
    index = args.index('-i')
    args = args[:index] + ['-i', ''] + args[index + 1:]
os.execv('/usr/bin/sed', ['/usr/bin/sed'] + args)
'''

RULES_WC = '''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args == ['-c']:
    print(len(sys.stdin.buffer.read()))
    sys.exit(0)
os.execv('/usr/bin/wc', ['/usr/bin/wc'] + args)
'''

RULES_MV = '''#!/usr/bin/env python3
import os, sys
args = []
for item in sys.argv[1:]:
    if item.startswith('-') and 'T' in item:
        item = item.replace('T', '')
        if not item:
            continue
    args.append(item)
os.execv('/bin/mv', ['/bin/mv'] + args)
'''


def rules_rewrite(text, sandbox):
    return replace_all(text, [
        ('/etc/', str(sandbox.root) + '/etc/'),
        ('/usr/local/bin', str(sandbox.root / 'usr/local/bin')),
        ('/usr/local/sbin', str(sandbox.root / 'usr/local/sbin')),
        ('/var/tmp', str(sandbox.root / 'var/tmp')),
        ('/run/lock', str(sandbox.root / 'run/lock')),
    ])


def rules_fixture(sandbox):
    import hashlib
    import json
    root = sandbox.root
    write(root / 'etc/mosdns/config.yaml', '''log:
  level: info
plugins:
  - tag: forward
    type: forward
    args:
      upstreams:
        - addr: 127.0.0.1:5335
api:
  http: 127.0.0.1:9091
''')
    write(root / 'etc/mihomo-automation.env', 'MIHOMO_MIXED_PORT=7890\n')
    write(root / 'usr/local/bin/mosdns', BINARY_NEW, 0o755)
    for name, body in {
        'systemctl': BINARY_STUBS['systemctl'],
        'mosdns-healthcheck': BINARY_STUBS['mosdns-healthcheck'],
        'mihomo-dns-mode': BINARY_STUBS['mihomo-dns-mode'],
        'dig': BINARY_STUBS['dig'],
        'curl': RULES_CURL,
        'find': BINARY_STUBS['find'],
        'sed': RULES_SED,
        'mv': RULES_MV,
        'wc': RULES_WC,
    }.items():
        make_stub(sandbox.bin, name, body)
        if name in ('mosdns-healthcheck', 'mihomo-dns-mode'):
            shutil.copyfile(sandbox.bin / name, root / 'usr/local/sbin' / name)
            (root / 'usr/local/sbin' / name).chmod(0o755)
    fixtures = sandbox.work / 'fixtures'
    fixtures.mkdir()
    assets = []
    for name, count in RULES_LINES.items():
        content = ('example.invalid\n' * count).encode()
        (fixtures / name).write_bytes(content)
        if name != 'geoip-cn.txt':
            assets.append({'name': name,
                           'browser_download_url':
                               'https://github.com/Loyalsoldier/v2ray-rules-dat/releases/download/%s/%s' % (RULES_TAG, name),
                           'digest': 'sha256:' + hashlib.sha256(content).hexdigest()})
    write(fixtures / 'rules-release.json',
          json.dumps({'tag_name': RULES_TAG, 'assets': assets}), 0o644)
    geoip = (fixtures / 'geoip-cn.txt').read_bytes()
    blob = hashlib.sha1(b'blob %d\0' % len(geoip) + geoip).hexdigest()
    write(fixtures / 'geoip-commit.json',
          json.dumps({'sha': '0123456789abcdef0123456789abcdef01234567'}), 0o644)
    write(fixtures / 'geoip-content.json', json.dumps({'sha': blob}), 0o644)


def rules_copy(sandbox):
    path = sandbox.work / 'update-rules'
    write(path, rules_rewrite((SBIN / 'mosdns-update-rules').read_text(encoding='utf-8'), sandbox), 0o755)
    launch = sandbox.work / 'launch.py'
    write(launch, '''#!/usr/bin/env python3
import os, sys
os.setsid()
os.environ['TOP_PID'] = str(os.getpid())
os.execvp(sys.argv[1], sys.argv[1:])
''', 0o755)
    return path, launch


def rules_run(sandbox, script, launch, **extra):
    env = sandbox.env(MOSDNS_ROOT=sandbox.root,
                      MOSDNS_FIXTURES=sandbox.work / 'fixtures', **extra)
    return subprocess.run([PYTHON, str(launch), SH, str(script)], env=env,
                          capture_output=True, text=True, timeout=90)


def rules_current(sandbox):
    link = sandbox.root / 'etc/mosdns/rules/current'
    if not link.is_symlink():
        return ''
    return os.readlink(link)


def rules_scenario():
    with Sandbox('mosdns-rules') as sandbox:
        script, launch = rules_copy(sandbox)
        rules_fixture(sandbox)
        pending = sandbox.root / 'etc/mosdns/rules/pending'
        result = rules_run(sandbox, script, launch, KILL_ON_RESTART='1')
        check(result.returncode in (-9, 137), 'first rules run did not die at restart: rc=%s %s' %
              (result.returncode, result.stdout + result.stderr))
        check(pending.is_file(), 'no pending rules record survived the crash')
        fields = dict(line.split('=', 1) for line in pending.read_text(encoding='utf-8').splitlines())
        check(fields.get('kind') == 'rules', 'pending record is not a rules transaction: %r' % fields)
        check(fields.get('phase') == 'applied', 'pending rules phase is not applied: %r' % fields)
        check(fields.get('new_target') == 'releases/' + RULES_VERSION,
              'pending record lost the new release target: %r' % fields)
        check(rules_current(sandbox) == 'releases/' + RULES_VERSION,
              'the crash did not leave the new rules release current')
        # Recovery must put the empty pre-update state back without health.
        result = rules_run(sandbox, script, launch, HEALTH_RC='1', FAIL_FETCH='1')
        check(result.returncode == 0, 'rules recovery run failed: ' + result.stdout + result.stderr)
        check(rules_current(sandbox) == '',
              'rules recovery did not restore the old (empty) current link')
        check(not pending.exists(), 'rules recovery left the pending record behind')
        # A healthy run must commit the release and clear the record.
        result = rules_run(sandbox, script, launch)
        check(result.returncode == 0, 'healthy rules update failed: ' + result.stdout + result.stderr)
        check(rules_current(sandbox) == 'releases/' + RULES_VERSION,
              'healthy rules update did not switch the current release')
        check(not pending.exists(), 'committed rules update left the pending record behind')


def rules_mutation_killed():
    text = (SBIN / 'mosdns-update-rules').read_text(encoding='utf-8')
    marker = 'if [ -s "$pending_file" ]; then'
    check(marker in text, 'rules recovery block not found')
    mutated = text.replace(marker, 'if false; then', 1)
    with Sandbox('mosdns-rules-mutation') as sandbox:
        script = sandbox.work / 'update-rules'
        write(script, rules_rewrite(mutated, sandbox), 0o755)
        launch = sandbox.work / 'launch.py'
        write(launch, '''#!/usr/bin/env python3
import os, sys
os.setsid()
os.environ['TOP_PID'] = str(os.getpid())
os.execvp(sys.argv[1], sys.argv[1:])
''', 0o755)
        rules_fixture(sandbox)
        result = rules_run(sandbox, script, launch, KILL_ON_RESTART='1')
        check(result.returncode in (-9, 137), 'mutated first rules run did not crash as expected')
        result = rules_run(sandbox, script, launch, HEALTH_RC='1', FAIL_FETCH='1')
        try:
            check(rules_current(sandbox) == '',
                  'mutated run skipped recovery and kept the unverified release current')
        except CaseFailure:
            return
        raise CaseFailure('mutation survived: recovery-before-download is not exercised')


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
    case('F-07 core-swap crash recovery', core_swap_crash_recovery)
    case('F-07 core-swap inconsistent refused', core_swap_inconsistent_refused)
    case('F-07 core-swap intent fsynced', core_swap_intent_fsynced)
    case('F-07 core-swap mutation killed', core_swap_mutation_killed)
    case('F-06 binary pending recovery', binary_scenario)
    case('F-06 binary mutation killed', binary_mutation_killed)
    case('F-06 rules pending recovery', rules_scenario)
    case('F-06 rules mutation killed', rules_mutation_killed)
    if FAILURES:
        print('change-chain: %d scenario(s) failed: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('change-chain: all scenarios and mutations passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
