#!/usr/bin/env python3
"""Behavioural checks for the whole-machine rollback point (F-08 / CC-04 / A-107).

The scenario copies the real producer, consumer and hook scripts into a
throw-away gateway root, drives them with command stubs, and asserts the
contract the rest of the tree relies on:

* gateway-backup writes a self-describing archive named with the generation
  time and the installed release_id, captures the six categories, records
  secret metadata only, records unit enablement, and prunes to --keep;
* every destructive path calls the producer before it writes its own pending
  record / swap intent;
* gateway-restore refuses a busy maintenance lock, a pending transaction, a
  cross-release point and a tainted point; dry-run lists differences and
  changes nothing; an empty restore prints the Chinese no-op notice; a full
  restore is byte-identical, removes files that are not in the point, fixes
  secret modes, regenerates the agent token, restores unit state, and runs the
  native validators (mihomo -t, a throwaway mosdns instance, dnsmasq --test,
  nft -c, unit set) in that order;
* mosdns-rollout-backup produces the archive mosdns-rollout-rollback consumes;
  the consumer validates the archive before it deletes anything and no longer
  removes a rules tree that the point carries.

Every property is mutation-tested inside this check: a broken producer,
consumer or ordering helper must make the corresponding assertion fail.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tempdirs import temp_root  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SBIN = ROOT / 'automation/sbin'
BACKUP = SBIN / 'gateway-backup'
RESTORE = SBIN / 'gateway-restore'
MOSDNS_BACKUP = SBIN / 'mosdns-rollout-backup'
MOSDNS_ROLLBACK = SBIN / 'mosdns-rollout-rollback'
INSTALL = ROOT / 'automation/install.sh'
CONFIG_INSTALL = ROOT / 'ops/mihomo-config-install'
CHECK_AUTOMATION = ROOT / 'tools/check_automation.py'
RELEASE_MANIFEST = ROOT / 'automation/release-manifest.json'

NO_OP = "\u65e0\u9700\u6062\u590d"
CROSS_RELEASE = "\u62d2\u7edd\u8de8\u7248\u672c\u6062\u590d"

RELEASE = json.loads(RELEASE_MANIFEST.read_text(encoding='utf-8'))['release_id']
RELEASE12 = RELEASE[:12]

# Synthetic values are assembled at run time so the repository never contains
# a token-looking literal.
SUB_URL = 'https://subscription.invalid/' + 'fixture.yaml'
DASH_SECRET = 'dash' + '-fixture-' + 'value'
AGENT_TOKEN = 'agent' + '-fixture-' + 'token-' + '0123456789abcdef'
CONFIG_SECRET = 'api' + '-fixture-' + 'secret'

STUB_HEAD = '#!' + sys.executable + '\n'

SYSTEMCTL_STUB = STUB_HEAD + r'''
import json, os, sys
state_path = os.environ['TEST_SYSTEMD_STATE']
state = json.load(open(state_path)) if os.path.exists(state_path) else {}
args = sys.argv[1:]
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write('systemctl ' + ' '.join(args) + '\n')
def save():
    with open(state_path, 'w') as handle:
        json.dump(state, handle, sort_keys=True)
if not args:
    sys.exit(2)
if args[0] == 'is-enabled':
    value = state.get(args[-1], 'not-found')
    print(value)
    sys.exit(0 if value in ('enabled', 'static', 'generated', 'indirect') else 1)
if args[0] == 'enable':
    for unit in args[1:]:
        if not unit.startswith('-'):
            state[unit] = 'enabled'
    save(); sys.exit(0)
if args[0] == 'disable':
    for unit in args[1:]:
        if not unit.startswith('-'):
            state[unit] = 'disabled'
    save(); sys.exit(0)
if args[0] == 'restart':
    sys.exit(1 if os.environ.get('TEST_SYSTEMCTL_FAIL') == '1' else 0)
sys.exit(0)
'''

MIHOMO_STUB = STUB_HEAD + r'''
import os, sys
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write('mihomo ' + ' '.join(sys.argv[1:]) + '\n')
sys.exit(int(os.environ.get('TEST_MIHOMO_FAIL', '0')))
'''

MOSDNS_STUB = STUB_HEAD + r'''
import os, re, socket, sys, time
args = sys.argv[1:]
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write('mosdns ' + ' '.join(args) + '\n')
if os.environ.get('TEST_MOSDNS_FAIL') == '1':
    sys.exit(2)
config = args[args.index('-c') + 1]
text = open(config).read()
match = re.search(r'http:\s*127\.0\.0\.1:(\d+)', text)
if not match:
    time.sleep(30)
    sys.exit(0)
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('127.0.0.1', int(match.group(1))))
server.listen(1)
while True:
    time.sleep(1)
'''

DNSMASQ_STUB = STUB_HEAD + r'''
import os, sys
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write('dnsmasq ' + ' '.join(sys.argv[1:]) + '\n')
sys.exit(int(os.environ.get('TEST_DNSMASQ_FAIL', '0')))
'''

NFT_STUB = STUB_HEAD + r'''
import os, sys
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write('nft ' + ' '.join(sys.argv[1:]) + '\n')
sys.exit(int(os.environ.get('TEST_NFT_FAIL', '0')))
'''

FLOCK_STUB = STUB_HEAD + r'''
import fcntl, sys
args = sys.argv[1:]
fd = next((int(item) for item in reversed(args) if item.isdigit()), None)
if fd is None:
    sys.exit(2)
fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if '-n' in args else 0))
'''

DNS_MODE_STUB = STUB_HEAD + r'''
import os, sys
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write('dns-mode ' + ' '.join(sys.argv[1:]) + '\n')
sys.exit(int(os.environ.get('TEST_DNS_MODE_FAIL', '0')))
'''

PRODUCER_STUB = '#!/bin/sh\nexit 0\n'


class CaseFailure(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise CaseFailure(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode('utf-8')
    path.write_bytes(data)
    path.chmod(mode)
    return path


def rewrite(text, pairs):
    for old, new in pairs:
        text = text.replace(old, str(new))
    return text


class Sandbox:
    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix='gateway-rollout.',
                                                     dir=temp_root())
        self.dir = Path(self._temporary.name)
        self.root = self.dir / 'root'
        self.bin = self.dir / 'bin'
        self.state = self.dir / 'systemd-state.json'
        self.calls = self.dir / 'calls.log'
        self.calls.write_text('')
        for relative in ('etc/mihomo/adaptive', 'etc/mihomo/providers',
                         'etc/mosdns/rules/releases/r1', 'etc/dnsmasq.d',
                         'etc/nftables.d', 'etc/systemd/system/nftables.service.d',
                         'etc/systemd/system/mihomo.service.d',
                         'etc/systemd/system/mosdns.service.d',
                         'usr/local/sbin', 'var/lib/mihomo-automation',
                         'var/lib/mihomo-swap', 'var/backups', 'var/tmp',
                         'run/lock', 'usr/sbin', 'opt/stubs'):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        for name, body in (('systemctl', SYSTEMCTL_STUB), ('mihomo', MIHOMO_STUB),
                           ('mosdns', MOSDNS_STUB), ('dnsmasq', DNSMASQ_STUB),
                           ('nft', NFT_STUB), ('flock', FLOCK_STUB),
                           ('dns-mode', DNS_MODE_STUB)):
            write(self.bin / name, body, 0o755)
        for name in ('gateway-backup', 'mosdns-rollout-backup'):
            write(self.bin / name, PRODUCER_STUB, 0o755)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._temporary.cleanup()

    def env(self, **extra):
        values = dict(os.environ)
        values.update(GATEWAY_ROOT=str(self.root),
                      TEST_SYSTEMD_STATE=str(self.state),
                      TEST_CALLS=str(self.calls),
                      PATH=str(self.bin) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                      PYTHONDONTWRITEBYTECODE='1')
        values.update({key: str(value) for key, value in extra.items()})
        return values

    def install(self, source, replacements=()):
        destination = self.dir / Path(source).name
        text = rewrite(Path(source).read_text(encoding='utf-8'), replacements)
        write(destination, text, 0o755)
        return destination

    def mutate(self, source, old, new):
        # A mutation must run with the same tool-path rewrites as the real
        # install: otherwise the mutated tool dies on a missing absolute path
        # and the assertions pass for a reason unrelated to the guard.
        binary = self.producer() if source == BACKUP else self.restore_tool()
        text = Path(binary).read_text(encoding='utf-8')
        write(binary, text.replace(old, str(new)), 0o755)
        return binary

    def producer(self):
        return self.install(BACKUP, (('/usr/bin/systemctl', self.bin / 'systemctl'),))

    def restore_tool(self):
        return self.install(RESTORE, (
            ('/usr/local/bin/mihomo', self.bin / 'mihomo'),
            ('/usr/local/bin/mosdns', self.bin / 'mosdns'),
            ('/usr/sbin/dnsmasq', self.bin / 'dnsmasq'),
            ('/usr/sbin/nft', self.bin / 'nft'),
            ('/usr/bin/systemctl', self.bin / 'systemctl'),
        ))

    def mosdns_backup(self):
        return self.install(MOSDNS_BACKUP)

    def mosdns_rollback(self):
        return self.install(MOSDNS_ROLLBACK, (
            ('/usr/local/sbin/mihomo-dns-mode', self.bin / 'dns-mode'),
            ('/var/backups/mosdns-rollout-', str(self.root / 'var/backups') + '/mosdns-rollout-'),
        ))

    def run(self, argv, timeout=60, **extra):
        return subprocess.run([str(item) for item in argv], env=self.env(**extra),
                              capture_output=True, text=True, timeout=timeout)

    def populate(self, release=RELEASE):
        files = {
            'etc/mihomo/config.yaml': 'port: 7890\n' + 'sec' + 'ret: ' + CONFIG_SECRET + '\nproxies: []\n',
            'etc/mihomo/config.local.yaml': 'log-level: info\n',
            'etc/mihomo/adaptive/capabilities.yaml': 'overlay: {}\n',
            'etc/mihomo/adaptive/route-defaults.yaml': 'routes: []\n',
            'etc/mihomo/providers/demo.yaml': 'proxies: []\n',
            'usr/local/sbin/rp5-agent.py': '#!/usr/bin/env python3\nprint("fixture")\n',
            'etc/mosdns/config.yaml': 'log:\n  level: info\napi:\n  http: 127.0.0.1:9091\n'
                                      'plugins:\n  - tag: server\n    type: udp_server\n'
                                      '    args:\n      entry: main\n      listen: 127.0.0.1:5335\n',
            'etc/mosdns/rules/custom-direct.txt': 'direct.example.invalid\n',
            'etc/mosdns/rules/custom-proxy.txt': 'proxy.example.invalid\n',
            'etc/mosdns/rules/releases/r1/direct-list.txt': 'rules.example.invalid\n',
            'etc/dnsmasq-upstreams.conf': 'server=127.0.0.1#5335\n',
            'etc/mihomo/dnsmasq-upstreams.direct': 'server=192.0.2.53\n',
            'etc/mihomo/dnsmasq-upstreams.mihomo': 'server=127.0.0.1#1053\n',
            'etc/mihomo/dnsmasq-upstreams.mosdns': 'server=127.0.0.1#5335\n',
            'etc/dnsmasq.d/resolver.conf': 'server=127.0.0.1#5335\n',
            'etc/dnsmasq-vlan-pilot.conf': 'server=127.0.0.1#5335\n',
            'etc/nftables.conf': 'table inet rp_filter {\n}\n',
            'etc/nftables.d/ucg-pc-link.nft': 'table ip rp_nat {\n}\n',
            'etc/systemd/system/nftables.service.d/override.conf': '[Service]\nExecStop=\n',
            'etc/systemd/system/mihomo.service': '[Service]\nExecStart=/usr/local/bin/mihomo\n',
            'etc/systemd/system/mihomo-healthcheck.timer': '[Timer]\nOnBootSec=30s\n',
            'etc/systemd/system/mosdns.service': '[Service]\nExecStart=/usr/local/bin/mosdns\n',
            'etc/systemd/system/dnsmasq.service.d/resolver.conf': '[Service]\n',
            'etc/systemd/system/rp5-agent.service': '[Service]\nExecStart=/usr/local/sbin/rp5-agent.py\n',
            'etc/systemd/system/homeassistant-access-guard.service': '[Service]\n',
            'etc/systemd/system/nftables.service.d/override.conf': '[Service]\nExecStop=\n',
            'etc/mihomo/secrets/subscription.url': SUB_URL + '\n',
            'etc/mihomo/secrets/dashboard.secret': DASH_SECRET + '\n',
            'etc/mihomo/secrets/rp5-agent.token': AGENT_TOKEN + '\n',
        }
        for relative, content in files.items():
            if relative == 'etc/systemd/system/nftables.service.d/override.conf':
                continue
            write(self.root / relative, content)
        write(self.root / 'etc/systemd/system/nftables.service.d/override.conf',
              '[Service]\nExecStop=\n')
        for secret in ('subscription.url', 'dashboard.secret', 'rp5-agent.token'):
            (self.root / 'etc/mihomo/secrets' / secret).chmod(0o600)
        os.symlink('releases/r1', self.root / 'etc/mosdns/rules/current')
        write(self.root / 'var/lib/mihomo-automation/installed-manifest.json',
              json.dumps({'format': 1, 'release_id': release}))
        unit_states = {
            'mihomo.service': 'enabled',
            'mihomo-healthcheck.timer': 'enabled',
            'mosdns.service': 'enabled',
            'dnsmasq.service': 'enabled',
            'nftables.service': 'enabled',
            'rp5-agent.service': 'enabled',
            'homeassistant-access-guard.service': 'disabled',
        }
        json.dump(unit_states, open(self.state, 'w'), sort_keys=True)

    def generate(self, reason='test'):
        result = self.run([self.producer(), '--reason', reason])
        check(result.returncode == 0, 'gateway-backup failed: %s%s'
              % (result.stdout, result.stderr))
        match = re.search(r'archive=(\S+)', result.stdout)
        check(match is not None, 'gateway-backup did not report the archive path')
        return Path(match.group(1))


def archive_members(path):
    with tarfile.open(path, 'r:gz') as archive:
        manifest = json.loads(archive.extractfile('gateway-rollout.json').read().decode('utf-8'))
        contents = {}
        links = {}
        for member in archive.getmembers():
            if member.name == 'gateway-rollout.json':
                continue
            if member.issym():
                links[member.name] = member.linkname
            elif member.isfile():
                contents[member.name] = archive.extractfile(member).read()
        return manifest, contents, links


def tree_snapshot(root):
    snapshot = {}
    for directory, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            # run/ is runtime state (/run is tmpfs on the gateway): the
            # maintenance lock lives there, and taking it is not a change to
            # the managed tree a caller asked to compare.
            if relative == 'run' or relative.startswith('run/'):
                continue
            if path.is_symlink():
                snapshot[relative] = ('symlink', os.readlink(path))
            else:
                snapshot[relative] = ('file', path.stat().st_mode & 0o777,
                                      sha256(path.read_bytes()))
    return snapshot


def assert_restored(sandbox, manifest, contents, links, baseline):
    for entry in manifest['files']:
        path = sandbox.root / entry['path']
        if entry['type'] == 'symlink':
            check(path.is_symlink() and os.readlink(path) == entry['target'],
                  'symlink not restored: %s' % entry['path'])
        else:
            check(path.is_file(), 'file not restored: %s' % entry['path'])
            check(sha256(path.read_bytes()) == entry['sha256'],
                  'content not restored: %s' % entry['path'])
            check(path.stat().st_mode & 0o777 == int(entry['mode'], 8),
                  'mode not restored: %s' % entry['path'])
    for relative, expected in baseline.items():
        if relative == 'etc/mihomo/secrets/rp5-agent.token':
            continue
        current = sandbox.root / relative
        if expected[0] == 'file':
            check(current.is_file() and sha256(current.read_bytes()) == expected[2],
                  'unexpected change after restore: %s' % relative)


def scenario_static_contract():
    for path in (BACKUP, RESTORE, MOSDNS_BACKUP, MOSDNS_ROLLBACK):
        check(path.is_file(), 'missing script: %s' % path.name)
        check(os.access(path, os.X_OK), 'script is not executable: %s' % path.name)
    install_text = INSTALL.read_text(encoding='utf-8')
    check('for script in "$here"/sbin/*' in install_text,
          'install.sh no longer installs every sbin script')
    order_cases = (
        ('install.sh', INSTALL, 'gateway-backup', '--begin'),
        ('subscription', SBIN / 'mihomo-update-subscription',
         '--reason subscription-apply', 'write_pending prepared'),
        ('mosdns binary', SBIN / 'mosdns-update-binary',
         '--reason mosdns-binary', 'write_pending prepared'),
        ('mosdns rules', SBIN / 'mosdns-update-rules',
         '--reason mosdns-rules', 'write_pending prepared'),
        ('core-swap', SBIN / 'mihomo-core-swap',
         'rollback_point("core-swap")', 'write_state(intent)'),
        ('dns-mode', SBIN / 'mihomo-dns-mode',
         '--reason dns-mode', 'candidate=$(mktemp'),
        ('config-install', CONFIG_INSTALL,
         '--reason config-install', 'atomic_write "$CAND" "$LIVE"'),
    )
    for label, path, call, pending in order_cases:
        text = path.read_text(encoding='utf-8')
        check(call in text, '%s: rollback point call is missing' % label)
        check(pending in text, '%s: pending marker is missing' % label)
        check(text.index(call) < text.index(pending),
              '%s: rollback point is not taken before the pending write' % label)
    rollback_text = MOSDNS_ROLLBACK.read_text(encoding='utf-8')
    check('tar -tzf "$backup"' in rollback_text,
          'mosdns-rollout-rollback no longer validates the archive listing')
    check('rm -rf -- etc/mosdns/rules\nfi' in rollback_text,
          'mosdns-rollout-rollback no longer guards the rules removal')
    manifest = json.loads(RELEASE_MANIFEST.read_text(encoding='utf-8'))
    targets = {entry.get('target') for entry in manifest['files']}
    for name in ('gateway-backup', 'gateway-restore', 'mosdns-rollout-backup'):
        check('/usr/local/sbin/' + name in targets,
              'release manifest does not install sbin/%s' % name)
    automation = CHECK_AUTOMATION.read_text(encoding='utf-8')
    for name in ('gateway-backup', 'gateway-restore', 'mosdns-rollout-backup'):
        check('"%s"' % name in automation,
              'check_automation does not record sbin/%s' % name)


def scenario_generate():
    with Sandbox() as sandbox:
        sandbox.populate()
        archive = sandbox.generate('subscription-apply')
        name = archive.name
        check(re.fullmatch(r'gateway-rollout-\d{8}T\d{6}Z-%s\.tar\.gz' % RELEASE12, name),
              'archive name does not carry time and release: %s' % name)
        check(archive.stat().st_mode & 0o777 == 0o600, 'archive is not mode 0600')
        manifest, contents, links = archive_members(archive)
        check(manifest['release_id'] == RELEASE, 'manifest release_id mismatch')
        check(manifest['reason'] == 'subscription-apply', 'manifest reason mismatch')
        check(manifest['pending_at_generation'] == [], 'clean point marked as pending')
        representatives = {
            'mihomo-config': 'etc/mihomo/config.yaml',
            'mosdns-rules': 'etc/mosdns/rules/custom-proxy.txt',
            'dnsmasq-upstreams': 'etc/dnsmasq-upstreams.conf',
            'nftables': 'etc/nftables.d/ucg-pc-link.nft',
            'units': 'etc/systemd/system/mosdns.service',
        }
        for category, relative in representatives.items():
            check(relative in contents or relative in links,
                  'category %s is missing %s' % (category, relative))
            check(manifest['categories'][category] > 0,
                  'category %s is empty' % category)
        check(manifest['categories']['secrets'] >= 3, 'secret metadata is missing')
        check(any(entry['path'] == 'etc/mosdns/rules/current' and entry['type'] == 'symlink'
                  for entry in manifest['files']), 'rules/current symlink is not recorded')
        check(any(entry['path'] == 'etc/systemd/system/nftables.service.d/override.conf'
                  for entry in manifest['files']),
              'the nftables drop-in is not part of the point')
        check(any(entry['path'] == 'usr/local/sbin/rp5-agent.py' for entry in manifest['files']),
              'the status agent is not part of the point')
        for entry in manifest['files']:
            if entry['type'] == 'symlink':
                continue
            check(entry['path'] in contents, 'manifest file missing from archive')
            check(sha256(contents[entry['path']]) == entry['sha256'],
                  'manifest hash mismatch for %s' % entry['path'])
            check(len(contents[entry['path']]) == entry['size'],
                  'manifest size mismatch for %s' % entry['path'])
        check(not any(name.startswith('etc/mihomo/secrets/') for name in contents),
              'a secret file was written into the archive')
        token = [entry for entry in manifest['secrets']
                 if entry['path'] == 'etc/mihomo/secrets/rp5-agent.token']
        check(len(token) == 1 and token[0].get('min_length_ok') is True,
              'the agent token metadata is not recorded correctly')
        for entry in manifest['secrets']:
            check('sha256' not in entry and 'value' not in entry and 'content' not in entry,
                  'secret metadata contains content or a hash')
        for name, data in contents.items():
            for secret in (SUB_URL, DASH_SECRET, AGENT_TOKEN):
                check(secret.encode('utf-8') not in data,
                      'secret value leaked into %s' % name)
        states = {entry['unit']: entry['state'] for entry in manifest['units']}
        check(states.get('mosdns.service') == 'enabled',
              'unit state was not captured: mosdns.service')
        check(states.get('homeassistant-access-guard.service') == 'disabled',
              'unit state was not captured: homeassistant-access-guard.service')
        check(states.get('nftables.service') == 'enabled', 'nftables.service state missing')


def scenario_retention():
    with Sandbox() as sandbox:
        sandbox.populate()
        directory = sandbox.root / 'var/backups'
        for index in range(7):
            write(directory / ('gateway-rollout-2020010%dT000000Z-aaaaaaaaaaaa.tar.gz' % index),
                  b'old')
        archive = sandbox.generate('retention')
        names = sorted(item.name for item in directory.iterdir())
        check(len(names) == 5, 'retention kept %d files, expected 5' % len(names))
        check(archive.name in names, 'the new point was pruned')


def scenario_restore_lifecycle():
    with Sandbox() as sandbox:
        sandbox.populate()
        archive = sandbox.generate('core-swap')
        manifest, contents, links = archive_members(archive)
        baseline = tree_snapshot(sandbox.root)
        # Damage every category plus unit state and secret modes.
        write(sandbox.root / 'etc/mihomo/config.yaml', 'port: 1\n')
        (sandbox.root / 'etc/mosdns/rules/custom-proxy.txt').unlink()
        write(sandbox.root / 'etc/mosdns/rules/custom-extra.txt', 'extra\n')
        write(sandbox.root / 'etc/nftables.d/extra.nft', 'table inet extra {\n}\n')
        write(sandbox.root / 'etc/dnsmasq-upstreams.conf', 'server=192.0.2.99\n')
        (sandbox.root / 'etc/mihomo/secrets/dashboard.secret').chmod(0o644)
        (sandbox.root / 'etc/mihomo/secrets/rp5-agent.token').unlink()
        link = sandbox.root / 'etc/mosdns/rules/current'
        link.unlink()
        os.symlink('releases/missing', link)
        states = json.load(open(sandbox.state))
        states['mosdns.service'] = 'disabled'
        states['homeassistant-access-guard.service'] = 'enabled'
        json.dump(states, open(sandbox.state, 'w'), sort_keys=True)
        damaged = tree_snapshot(sandbox.root)
        restore = sandbox.restore_tool()
        dry = sandbox.run([restore, archive, '--dry-run'])
        check(dry.returncode == 0, 'dry-run failed: %s%s' % (dry.stdout, dry.stderr))
        for relative in ('etc/mihomo/config.yaml', 'etc/mosdns/rules/custom-proxy.txt',
                         'etc/mosdns/rules/custom-extra.txt', 'etc/nftables.d/extra.nft',
                         'etc/dnsmasq-upstreams.conf', 'etc/mosdns/rules/current'):
            check(relative in dry.stdout, 'dry-run does not list %s' % relative)
        check('mosdns.service' in dry.stdout and 'homeassistant-access-guard.service' in dry.stdout,
              'dry-run does not list unit state changes')
        check(tree_snapshot(sandbox.root) == damaged, 'dry-run changed the tree')
        sandbox.calls.write_text('')
        applied = sandbox.run([restore, archive])
        check(applied.returncode == 0, 'restore failed: %s%s' % (applied.stdout, applied.stderr))
        check(NO_OP not in applied.stdout, 'a real restore claimed there was nothing to do')
        assert_restored(sandbox, manifest, contents, links, baseline)
        check(not (sandbox.root / 'etc/mosdns/rules/custom-extra.txt').exists(),
              'a file outside the point was not pruned')
        check(not (sandbox.root / 'etc/nftables.d/extra.nft').exists(),
              'an extra nftables include was not pruned')
        token = sandbox.root / 'etc/mihomo/secrets/rp5-agent.token'
        check(token.is_file(), 'the agent token was not regenerated')
        check(token.stat().st_mode & 0o777 == 0o600, 'the regenerated token is not 0600')
        check(len(token.read_text().strip()) >= 16, 'the regenerated token is too short')
        check(token.read_text().strip() != AGENT_TOKEN, 'the old token was reused')
        check((sandbox.root / 'etc/mihomo/secrets/dashboard.secret').stat().st_mode & 0o777 == 0o600,
              'secret mode was not repaired')
        states = json.load(open(sandbox.state))
        check(states.get('mosdns.service') == 'enabled', 'mosdns.service was not re-enabled')
        check(states.get('homeassistant-access-guard.service') == 'disabled',
              'homeassistant-access-guard.service was not disabled')
        calls = sandbox.calls.read_text().splitlines()
        def first(prefix):
            for index, line in enumerate(calls):
                if line.startswith(prefix):
                    return index
            return -1
        positions = [first('systemctl daemon-reload'), first('mihomo -t'),
                     first('mosdns start'), first('dnsmasq --test'), first('nft -c')]
        check(all(item >= 0 for item in positions), 'a validator was not called: %s' % calls)
        check(positions == sorted(positions), 'validators ran out of order: %s' % calls)
        last_is_enabled = max(index for index, line in enumerate(calls)
                              if line.startswith('systemctl is-enabled'))
        check(last_is_enabled > max(positions), 'unit set was not verified last')
        check(first('systemctl restart rp5-agent.service') >= 0,
              'rp5-agent.service was not restarted after the token rotation')
        second = sandbox.run([restore, archive])
        check(second.returncode == 0, 'second restore failed: %s%s' % (second.stdout, second.stderr))
        check(NO_OP in second.stdout, 'the empty restore did not report the no-op notice')


def scenario_refusals():
    with Sandbox() as sandbox:
        sandbox.populate()
        archive = sandbox.generate('dns-mode')
        restore = sandbox.restore_tool()
        # Cross-release is refused by default and does not touch the tree.
        manifest_path = sandbox.root / 'var/lib/mihomo-automation/installed-manifest.json'
        write(manifest_path, json.dumps({'format': 1, 'release_id': 'f' * 64}))
        before = tree_snapshot(sandbox.root)
        cross = sandbox.run([restore, archive])
        check(cross.returncode == 3, 'cross-release restore exited %d' % cross.returncode)
        check(CROSS_RELEASE in (cross.stdout + cross.stderr),
              'cross-release refusal did not say why')
        check(tree_snapshot(sandbox.root) == before, 'cross-release refusal changed the tree')
        allowed = sandbox.run([restore, archive, '--allow-cross-release'])
        check(allowed.returncode == 0, 'explicit cross-release restore failed: %s%s'
              % (allowed.stdout, allowed.stderr))
        write(manifest_path, json.dumps({'format': 1, 'release_id': RELEASE}))
        # A pending transaction is refused before anything is read.
        write(sandbox.root / 'etc/mosdns/rules/pending', 'phase=prepared\n')
        before = tree_snapshot(sandbox.root)
        pending = sandbox.run([restore, archive])
        check(pending.returncode == 1, 'pending restore exited %d' % pending.returncode)
        check('pending' in (pending.stdout + pending.stderr), 'pending refusal is not explained')
        check(tree_snapshot(sandbox.root) == before, 'pending refusal changed the tree')
        (sandbox.root / 'etc/mosdns/rules/pending').unlink()
        # The maintenance lock is honoured.
        lock_path = sandbox.root / 'run/lock/mihomo-maintenance.lock'
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            busy = sandbox.run([restore, archive])
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        check(busy.returncode == 1, 'busy-lock restore exited %d' % busy.returncode)
        check('maintenance is busy' in (busy.stdout + busy.stderr),
              'busy-lock refusal is not explained')
        # A missing installed manifest cannot prove the current release.
        manifest_path.unlink()
        missing = sandbox.run([restore, archive])
        check(missing.returncode == 1, 'missing-release restore exited %d' % missing.returncode)


def scenario_tainted_point():
    with Sandbox() as sandbox:
        sandbox.populate()
        write(sandbox.root / 'etc/mosdns/backups/transaction.pending', 'phase=prepared\n')
        archive = sandbox.generate('mosdns-binary')
        manifest, _contents, _links = archive_members(archive)
        check(manifest['pending_at_generation'] != [],
              'a point taken during a pending transaction was not marked')
        (sandbox.root / 'etc/mosdns/backups/transaction.pending').unlink()
        restore = sandbox.restore_tool()
        before = tree_snapshot(sandbox.root)
        result = sandbox.run([restore, archive])
        check(result.returncode == 1, 'tainted point restore exited %d' % result.returncode)
        check('pending' in (result.stdout + result.stderr).lower(),
              'tainted point refusal is not explained')
        check(tree_snapshot(sandbox.root) == before, 'tainted point restore changed the tree')


def scenario_mosdns_pair():
    with Sandbox() as sandbox:
        sandbox.populate()
        write(sandbox.root / 'etc/mosdns/config.yaml',
              'log:\n  level: info\n'
              'plugins:\n'
              '  - tag: direct_domains\n    type: domain_set\n    args:\n'
              '      files:\n        - /etc/mosdns/rules/current/direct-list.txt\n')
        for relative in ('usr/local/sbin/dnsmasq-safe-start',
                         'usr/local/sbin/mosdns-healthcheck',
                         'usr/local/sbin/mosdns-update-binary',
                         'usr/local/sbin/mosdns-update-rules',
                         'usr/local/sbin/mosdns-emergency-direct',
                         'usr/local/sbin/mosdns-resume-auto'):
            write(sandbox.root / relative, '#!/bin/sh\nexit 0\n', 0o755)
        write(sandbox.root / 'etc/systemd/system/mosdns-healthcheck.timer', '[Timer]\n')
        write(sandbox.root / 'etc/systemd/system/dnsmasq.service.d/boot-safe.conf', '[Service]\n')
        backup = sandbox.mosdns_backup()
        result = sandbox.run([backup])
        check(result.returncode == 0, 'mosdns-rollout-backup failed: %s%s'
              % (result.stdout, result.stderr))
        match = re.search(r'archive=(\S+)', result.stdout)
        check(match is not None, 'mosdns-rollout-backup did not report the archive')
        archive = Path(match.group(1))
        check(re.fullmatch(r'mosdns-rollout-\d{8}T\d{6}Z-%s\.tar\.gz' % RELEASE12, archive.name),
              'mosdns archive name does not carry time and release')
        with tarfile.open(archive, 'r:gz') as handle:
            names = handle.getnames()
        for required in ('etc/mosdns/config.yaml', 'etc/mosdns/rules/custom-direct.txt',
                         'etc/mosdns/rules/releases/r1/direct-list.txt',
                         'etc/mosdns/rules/current',
                         'etc/systemd/system/mosdns-healthcheck.timer',
                         'usr/local/sbin/dnsmasq-safe-start',
                         'etc/mihomo/dnsmasq-upstreams.mosdns'):
            check(required in names, 'mosdns point is missing %s' % required)
        rollback = sandbox.mosdns_rollback()
        write(sandbox.root / 'etc/mosdns/config.yaml', 'log:\n  level: debug\n')
        sandbox.calls.write_text('')
        applied = sandbox.run([rollback, archive])
        check(applied.returncode == 0, 'mosdns rollback failed: %s%s'
              % (applied.stdout, applied.stderr))
        check('level: info' in (sandbox.root / 'etc/mosdns/config.yaml').read_text(),
              'mosdns config was not restored')
        check((sandbox.root / 'etc/mosdns/rules/custom-direct.txt').is_file(),
              'the rollback deleted a rules file the point carries')
        calls = sandbox.calls.read_text()
        check(calls.index('dns-mode direct') < calls.index('systemctl enable --now mosdns.service'),
              'the direct switch no longer precedes the unit enable')
        # A corrupt archive is refused before any file is removed.
        witness = sandbox.root / 'usr/local/sbin/dnsmasq-safe-start'
        check(witness.is_file(), 'witness file missing before the corrupt-archive case')
        corrupt = sandbox.root / 'var/backups/mosdns-rollout-20200101T000000Z-aaaaaaaaaaaa.tar.gz'
        import gzip
        corrupt.write_bytes(gzip.compress(b'not a tar archive\n'))
        rejected = sandbox.run([rollback, corrupt])
        check(rejected.returncode != 0, 'corrupt archive was accepted')
        check(witness.is_file(), 'corrupt archive deleted files before validation')


def mutation_cases():
    return [
        ('secret redaction', BACKUP, 'SECRETS_ARE_METADATA_ONLY = True',
         'SECRETS_ARE_METADATA_ONLY = False', mutation_secret_redaction),
        ('cross-release guard', RESTORE, 'if release != installed:',
         'if False:', mutation_cross_release),
        ('pending guard', RESTORE, 'if pending:', 'if False:', mutation_pending),
        ('token mode', RESTORE, 'SECRET_MODE = 0o600', 'SECRET_MODE = 0o644',
         mutation_token_mode),
        ('unit restore', RESTORE,
         'run_systemctl("enable" if desired == "enabled" else "disable", entry["unit"])',
         'pass', mutation_unit_restore),
    ]


def mutation_secret_redaction(binary):
    with Sandbox() as sandbox:
        sandbox.populate()
        result = sandbox.run([binary, '--reason', 'mutation'])
        check(result.returncode == 0, 'mutated producer failed: %s%s'
              % (result.stdout, result.stderr))
        match = re.search(r'archive=(\S+)', result.stdout)
        check(match is not None, 'mutated producer did not report an archive')
        _manifest, contents, _links = archive_members(Path(match.group(1)))
        for data in contents.values():
            for secret in (SUB_URL, DASH_SECRET, AGENT_TOKEN):
                if secret.encode('utf-8') in data:
                    raise CaseFailure('secret value found in the archive')


def mutation_cross_release(binary):
    with Sandbox() as sandbox:
        sandbox.populate()
        archive = sandbox.generate('mutation')
        write(sandbox.root / 'var/lib/mihomo-automation/installed-manifest.json',
              json.dumps({'format': 1, 'release_id': 'f' * 64}))
        result = sandbox.run([binary, archive])
        check(result.returncode == 3, 'mutated restore did not refuse the cross-release point')


def mutation_pending(binary):
    with Sandbox() as sandbox:
        sandbox.populate()
        archive = sandbox.generate('mutation')
        write(sandbox.root / 'etc/mosdns/rules/pending', 'phase=prepared\n')
        before = tree_snapshot(sandbox.root)
        result = sandbox.run([binary, archive])
        # rc==1 alone proves nothing: an unrelated failure exits 1 too.  The
        # guard is only real when the refusal left the managed tree untouched.
        check(result.returncode == 1, 'mutated restore did not refuse the pending transaction')
        check(tree_snapshot(sandbox.root) == before,
              'mutated restore touched the tree while a transaction was pending')


def mutation_token_mode(binary):
    with Sandbox() as sandbox:
        sandbox.populate()
        archive = sandbox.generate('mutation')
        (sandbox.root / 'etc/mihomo/secrets/rp5-agent.token').unlink()
        result = sandbox.run([binary, archive])
        check(result.returncode == 0, 'restore failed while checking the token mode')
        check((sandbox.root / 'etc/mihomo/secrets/rp5-agent.token').stat().st_mode & 0o777 == 0o600,
              'the token was written with the wrong mode')


def mutation_unit_restore(binary):
    with Sandbox() as sandbox:
        sandbox.populate()
        archive = sandbox.generate('mutation')
        states = json.load(open(sandbox.state))
        states['mosdns.service'] = 'disabled'
        json.dump(states, open(sandbox.state, 'w'), sort_keys=True)
        result = sandbox.run([binary, archive])
        check(result.returncode == 0, 'restore failed while checking unit restore')
        check(json.load(open(sandbox.state)).get('mosdns.service') == 'enabled',
              'the unit was not re-enabled')


def run_named(name, function):
    try:
        function()
    except CaseFailure as error:
        return '%s: %s' % (name, error)
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only_mutations = '--only-mutations' in argv
    failures = []
    if not only_mutations:
        for name, function in (
            ('static contract', scenario_static_contract),
            ('generate', scenario_generate),
            ('retention', scenario_retention),
            ('restore lifecycle', scenario_restore_lifecycle),
            ('refusals', scenario_refusals),
            ('tainted point', scenario_tainted_point),
            ('mosdns pair', scenario_mosdns_pair),
        ):
            failure = run_named(name, function)
            if failure:
                failures.append(failure)
                print('FAIL ' + failure)
            else:
                print('ok   ' + name)
    for name, source, old, new, scenario in mutation_cases():
        text = Path(source).read_text(encoding='utf-8')
        if old not in text:
            failures.append('mutation %s: marker not found' % name)
            print('FAIL mutation %s: marker not found' % name)
            continue
        with Sandbox() as sandbox:
            mutated = sandbox.mutate(source, old, new)
            try:
                scenario(mutated)
            except CaseFailure:
                print('ok   mutation %s killed' % name)
            else:
                failures.append('mutation %s survived' % name)
                print('FAIL mutation %s survived' % name)
    if failures:
        print('gateway rollout checks failed:', file=sys.stderr)
        for failure in failures:
            print(' - ' + failure, file=sys.stderr)
        return 1
    print('gateway rollout checks passed: producer, consumer, hooks, refusals and mutations')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
