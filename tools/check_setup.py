#!/usr/bin/env python3
"""Offline installation simulation: real generated files, simulated Linux commands.

No root privileges, packages, network or host service changes are required.
Use --mihomo PATH --rules DIR for the additional real-core provider/config test.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import copy
import gzip
import hashlib
import importlib.util
import io
import itertools
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gateway_setup', REPO / 'gateway/setup.py')
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)
DEFAULTS = json.loads((REPO / 'gateway/defaults.json').read_text())
PLAN = {'wan': 'enp1s0', 'lan': 'enx000000000001', 'backend': 'NetworkManager',
        'subnet': '10.77.0.0/24', 'address': '10.77.0.1',
        'ports': {'mixed': 17890, 'controller': 19090, 'dns': 11053}, 'package_manager': 'apt-get'}


def interfaces():
    return [
        {'ifname': PLAN['wan'], 'physical': True, 'wireless': False, 'flags': ['UP'],
         'addr_info': [{'family': 'inet', 'local': '192.168.50.5', 'prefixlen': 24}]},
        {'ifname': PLAN['lan'], 'physical': True, 'wireless': False, 'flags': ['UP'], 'addr_info': []},
        {'ifname': 'docker0', 'physical': False, 'flags': ['UP'], 'addr_info': []},
    ]


ROUTES = [{'dst': 'default', 'dev': PLAN['wan'], 'gateway': '192.168.50.1', 'metric': 100},
          {'dst': '192.168.50.0/24', 'dev': PLAN['wan'], 'table': 'main'}]


class FakeHost:
    """A stateful host model, including manager activation and systemd service state."""
    def __init__(self, directory, backend='NetworkManager', fail=None):
        self.root = directory.resolve()
        self.plan = {**copy.deepcopy(PLAN), 'backend': backend}
        self.links = [{**x, 'tunnel': False} for x in interfaces()]
        self.original_links = copy.deepcopy(self.links)
        self.services = {name: False for name in gw.UNITS}
        self.enabled = {name: 'not-found' for name in gw.UNITS}
        self.values = {k: '0' if k.endswith(('ip_forward', 'disable_ipv6')) else '1'
                       for k in gw.sysctl_values(self.plan)}
        self.original_values = dict(self.values)
        self.calls = []
        self.fail = fail
        self.failed = False
        self.ready = False
        self.stack = ExitStack()

    def path(self, value='.', *parts):
        path = Path(value, *parts)
        if str(path).startswith(('/etc/', '/opt/', '/var/lib/', '/sys/', '/run/', '/dev/')):
            return self.root / str(path).lstrip('/')
        return path

    def __enter__(self):
        for name, path in [('CONFIG', '/etc/mihomo-gateway'), ('APP', '/opt/mihomo-gateway'),
                           ('STATE', '/var/lib/mihomo-gateway')]:
            self.stack.enter_context(patch.object(gw, name, self.path(path)))
        self.stack.enter_context(patch.object(gw, 'Path', self.path))
        self.stack.enter_context(patch.object(gw, 'run', self.run))
        self.stack.enter_context(patch.object(gw, 'api', lambda *_: {'proxies': [{'type': 'Shadowsocks'}]}))
        self.stack.enter_context(patch.object(gw, 'dns_probe', lambda *_: None))
        real_which = shutil.which
        self.stack.enter_context(patch.object(gw.shutil, 'which', lambda name: '/usr/sbin/ip' if name == 'ip' else real_which(name)))
        for link in self.links:
            if link.get('physical'):
                (self.path('/sys/class/net') / link['ifname'] / 'device').mkdir(parents=True)
        gw.STATE.mkdir(parents=True)
        return self

    def __exit__(self, *exc):
        self.stack.__exit__(*exc)

    def run(self, *args, check=True, input=None, timeout=None):
        args = tuple(str(x) for x in args)
        self.calls.append(args)
        if self.fail and not self.failed and self.fail(args):
            self.failed = True
            if check:
                raise gw.SetupError('simulated command failure')
            return subprocess.CompletedProcess(args, 1, '', '')
        code, output = 0, ''
        if args[:2] == ('systemctl', 'is-active'):
            code = 0 if self.services.get(args[-1], False) else 3
        elif args[:2] == ('systemctl', 'is-enabled'):
            output = self.enabled.get(args[-1], 'not-found')
            code = 0 if output == 'enabled' else 1
        elif args[0] == 'systemctl':
            command, name = args[1], args[-1]
            if command in ('stop', 'disable'):
                self.services[name] = False
                if command == 'disable':
                    self.enabled[name] = 'disabled'
            elif command in ('start', 'enable'):
                if command == 'start' or '--now' in args:
                    self.services[name] = True
                if command == 'enable':
                    self.enabled[name] = 'enabled'
        elif args[:2] == ('sysctl', '-n'):
            output = self.values[args[2]]
        elif args[:2] == ('sysctl', '-w'):
            key, value = args[2].split('=', 1)
            self.values[key] = value
        elif args[:4] == ('ip', '-j', 'address', 'show'):
            output = json.dumps([x for x in self.links if len(args) == 4 or x['ifname'] == args[-1]])
        elif args[:4] == ('ip', '-j', '-4', 'route'):
            output = json.dumps(ROUTES)
        elif args[:3] == ('ip', 'address', 'del'):
            addr = args[3].split('/')[0]
            self.links[1]['addr_info'] = [a for a in self.links[1]['addr_info'] if a['local'] != addr]
        elif args[:3] == ('ip', 'address', 'replace'):
            addr, prefix = args[3].split('/')
            self.links[1]['addr_info'] = [{'family': 'inet6' if ':' in addr else 'inet', 'local': addr, 'prefixlen': int(prefix)}]
        elif args[:3] == ('ip', 'link', 'set'):
            self.links[1]['flags'] = ['UP'] if args[-1] == 'up' else []
        elif args[0] == 'nmcli':
            if 'GENERAL.CON-UUID' in args:
                output = '--'
            elif args[1:3] == ('connection', 'up'):
                self.activate()
            elif args[1:3] == ('device', 'disconnect'):
                self.links[1]['addr_info'] = []
        elif args[0] == 'networkctl':
            if args[1] == 'reconfigure':
                self.activate()
        elif args[0] == 'curl':
            pass
        else:
            raise AssertionError('Unmodeled host command: ' + repr(args))
        if check and code:
            raise gw.SetupError('simulated nonzero command')
        return subprocess.CompletedProcess(args, code, output, '')

    def activate(self):
        if (gw.CONFIG / 'config.yaml').exists():
            self.links[1]['addr_info'] = [{'family': 'inet', 'local': self.plan['address'], 'prefixlen': 24}]
        else:
            self.links[1] = copy.deepcopy(self.original_links[1])

    def install(self):
        config = gw.make_config(self.plan, DEFAULTS, 'https://subscription.example.invalid/test', 'test-secret')
        files, network_path = gw.make_files(self.plan, config, str(uuid.UUID(int=1)))
        stage = self.root / 'stage'
        stage.mkdir(exist_ok=True)
        for name in ('providers', 'rules'):
            (stage / name).mkdir(exist_ok=True)
            (stage / name / 'data').write_text('synthetic')
        (stage / 'mihomo').write_text('synthetic binary')
        with redirect_stdout(io.StringIO()):
            return gw.apply_install(self.plan, DEFAULTS, config, 'https://subscription.example.invalid/test',
                                    stage, files, network_path, str(uuid.UUID(int=1)))


class DetectionTests(unittest.TestCase):
    def test_two_nics_with_virtual_interfaces(self):
        self.assertEqual(gw.choose_interfaces(interfaces(), ROUTES), (PLAN['wan'], PLAN['lan']))

    def test_wifi_uplink(self):
        links = interfaces()
        links[0]['wireless'] = True
        self.assertEqual(gw.choose_interfaces(links, ROUTES), (PLAN['wan'], PLAN['lan']))

    def test_ambiguous_lan_and_explicit_override(self):
        links = interfaces() + [{'ifname': 'eth2', 'physical': True}]
        with self.assertRaisesRegex(gw.SetupError, 'unambiguous'):
            gw.choose_interfaces(links, ROUTES)
        self.assertEqual(gw.choose_interfaces(links, ROUTES, lan='eth2')[1], 'eth2')

    def test_two_default_routes_require_explicit_lan(self):
        routes = ROUTES + [{'dst': 'default', 'dev': PLAN['lan'], 'metric': 200}]
        with self.assertRaisesRegex(gw.SetupError, 'Second interface'):
            gw.choose_interfaces(interfaces(), routes)
        self.assertEqual(gw.choose_interfaces(interfaces(), routes, lan=PLAN['lan'])[1], PLAN['lan'])

    def test_equal_metric_uplinks(self):
        routes = ROUTES + [{'dst': 'default', 'dev': PLAN['lan'], 'metric': 100}]
        with self.assertRaisesRegex(gw.SetupError, 'Ambiguous uplinks'):
            gw.choose_interfaces(interfaces(), routes)

    def test_no_default_bridge_bond_single_nic(self):
        for links, routes in ((interfaces(), []), (interfaces()[:1], ROUTES),
                              ([{**x, 'master': 'br0'} for x in interfaces()], ROUTES)):
            with self.assertRaises(gw.SetupError):
                gw.choose_interfaces(links, routes)

    def test_no_overlap_including_vpn_routes(self):
        used = [ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12')]
        subnet = gw.choose_subnet(used, 'synthetic-host')
        self.assertTrue(subnet.subnet_of(ipaddress.ip_network('192.168.0.0/16')))
        self.assertEqual(subnet, gw.choose_subnet(used, 'synthetic-host'))
        with self.assertRaises(gw.SetupError):
            gw.choose_subnet(list(gw.PRIVATE), 'synthetic-host')

    def test_invalid_or_overlapping_explicit_subnet(self):
        for value in ('8.8.8.0/24', '10.77.0.0/25', '192.168.50.0/24'):
            with self.assertRaises(gw.SetupError):
                gw.choose_subnet([ipaddress.ip_network('192.168.50.0/24')], 'x', value)

    def test_other_address_and_other_table_conflicts(self):
        routes = ROUTES + [{'dst': '10.0.0.0/8', 'dev': 'vpn0', 'table': 123}]
        used = gw.used_networks(interfaces(), routes, PLAN['lan'])
        self.assertIn(ipaddress.ip_network('10.0.0.0/8'), used)

    def test_port_occupancy_and_duplicate_preferences(self):
        with socket.socket() as handle:
            handle.bind(('0.0.0.0', 0))
            port = handle.getsockname()[1]
            if port > 65530:
                self.skipTest('ephemeral port too close to limit')
            result = gw.allocate_ports({'mixed': port, 'controller': port, 'dns': port})
            self.assertNotIn(port, result.values())
            self.assertEqual(len(set(result.values())), 3)

    def test_firewall_preserves_ownership(self):
        self.assertTrue(gw.existing_firewall_conflict({'nftables': [{'chain': {'table': 'other', 'policy': 'drop'}}]}))
        self.assertTrue(gw.existing_firewall_conflict({'nftables': [{'rule': {'table': 'other', 'expr': [{'reject': None}]}}]}))
        self.assertFalse(gw.existing_firewall_conflict({'nftables': [{'chain': {'table': 'mihomo_gateway', 'policy': 'drop'}}]}))


class ConfigTests(unittest.TestCase):
    def test_distro_identity_precedes_path_order(self):
        with patch.object(gw.Path, 'exists', return_value=True), \
                patch.object(gw.Path, 'read_text', return_value='ID=rocky\nID_LIKE="rhel centos fedora"\n'), \
                patch.object(gw.shutil, 'which', return_value='/synthetic/tool'):
            self.assertEqual(gw.package_manager(), 'dnf')

    def test_dry_run_neither_prompts_downloads_nor_writes(self):
        args = argparse.Namespace(defaults=REPO / 'gateway/defaults.json', wan=None, lan=None, lan_cidr=None)
        with tempfile.TemporaryDirectory() as raw, ExitStack() as stack:
            state = Path(raw) / 'must-not-exist'
            for target, value in [('STATE', state), ('package_manager', lambda: 'apt-get'),
                                  ('inventory', lambda: (interfaces(), ROUTES)),
                                  ('network_backend', lambda _: 'NetworkManager')]:
                stack.enter_context(patch.object(gw, target, value))
            stack.enter_context(patch.object(gw.platform, 'machine', return_value='aarch64'))
            stack.enter_context(patch.object(gw.shutil, 'which', return_value='/synthetic/tool'))
            download = stack.enter_context(patch.object(gw, 'download_inputs'))
            prompt = stack.enter_context(patch.object(gw.getpass, 'getpass'))
            install = stack.enter_context(patch.object(gw, 'install_dependencies'))
            with redirect_stdout(io.StringIO()):
                gw.prepare(args, dry=True)
            download.assert_not_called()
            prompt.assert_not_called()
            install.assert_not_called()
            self.assertFalse(state.exists())

    def test_asset_checksum_failure_prevents_execution(self):
        with tempfile.TemporaryDirectory() as raw:
            def corrupt(_url, path, *unused):
                Path(path).write_bytes(b'corrupt-asset')
            with patch.object(gw, 'fetch', corrupt), patch.object(gw, 'run') as run, \
                    patch.object(gw.platform, 'machine', return_value='aarch64'):
                with self.assertRaisesRegex(gw.SetupError, 'SHA256'):
                    gw.download_inputs(Path(raw), DEFAULTS, 'https://example.invalid/test')
                run.assert_not_called()

    def test_verified_asset_decompression_and_download_manifest(self):
        payload = gzip.compress(b'synthetic executable')
        defaults = copy.deepcopy(DEFAULTS)
        defaults['assets']['aarch64']['sha256'] = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            def fetch(_url, path, *unused):
                Path(path).write_bytes(payload if Path(path).name == 'mihomo.gz' else b'synthetic-data')
            with patch.object(gw, 'fetch', fetch), patch.object(gw, 'run'), \
                    patch.object(gw.platform, 'machine', return_value='aarch64'):
                binary = gw.download_inputs(Path(raw), defaults, 'https://example.invalid/test')
                self.assertEqual(binary.read_bytes(), b'synthetic executable')
                self.assertTrue((Path(raw) / 'providers/airport.yaml').is_file())
                self.assertTrue((Path(raw) / 'rules/cn.mrs').is_file())

    def test_download_failure_redacts_url_and_argv(self):
        secret = 'https://example.invalid/private?token=synthetic-sensitive-value'
        with tempfile.TemporaryDirectory() as raw, patch.object(gw, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 22, secret, secret)
            with self.assertRaises(gw.SetupError) as error:
                gw.fetch(secret, Path(raw) / 'out')
            self.assertNotIn(secret, str(error.exception))
            self.assertNotIn(secret, run.call_args.args)
            self.assertIn(secret, run.call_args.kwargs['input'])

    def test_policy_is_provider_driven(self):
        config = gw.make_config(PLAN, DEFAULTS, 'https://subscription.example.invalid/test', 'test-secret')
        self.assertEqual(config['tun']['include-interface'], [PLAN['lan']])
        self.assertEqual(config['interface-name'], PLAN['wan'])
        for group in config['proxy-groups']:
            self.assertEqual(group['use'], ['airport'])
        self.assertNotIn('proxies', config)
        self.assertEqual(config['rules'][-1], 'MATCH,Proxy')
        self.assertLess(config['rules'].index('DOMAIN-SUFFIX,googlevideo.com,Streaming'), config['rules'].index('RULE-SET,cn,DIRECT'))

    def test_generated_network_has_no_default_gateway(self):
        for backend in ('NetworkManager', 'networkd'):
            plan = {**PLAN, 'backend': backend}
            config = gw.make_config(plan, DEFAULTS, 'https://subscription.example.invalid/test', 'test-secret')
            files, path = gw.make_files(plan, config, 'test-uuid')
            self.assertNotIn('gateway=', files[path].lower())
            self.assertIn(PLAN['address'], files[path])
            self.assertNotIn('flush ruleset', files['/etc/mihomo-gateway/nftables.conf'])
            self.assertNotIn('sshd', '\n'.join(files))

    def test_url_rejection_and_secret_not_in_error(self):
        for url in ('http://example.invalid/secret', 'file:///secret', 'https://a:b@example.invalid/',
                    'https://example.invalid/\nsecret', 'https:///secret'):
            with self.assertRaises(gw.SetupError) as error:
                gw.check_url(url)
            self.assertNotIn('secret', str(error.exception))

    def test_package_install_does_not_upgrade_os_or_start_dnsmasq(self):
        with patch.object(gw, 'run') as run, redirect_stdout(io.StringIO()):
            gw.install_dependencies('apt-get')
            self.assertIn('dnsmasq-base', run.call_args.args)
            self.assertNotIn('dnsmasq', run.call_args.args)
            gw.install_dependencies('pacman')
            self.assertNotIn('-Syu', run.call_args.args)

    def test_unsupported_host_has_no_side_effects(self):
        with patch.object(gw.platform, 'system', return_value='Darwin'), patch.object(gw, 'run') as run:
            with self.assertRaisesRegex(gw.SetupError, 'no host changes'):
                gw.main([])
            run.assert_not_called()


class TransactionTests(unittest.TestCase):
    def test_rerun_from_installed_entrypoint(self):
        with tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
            host.install()
            with patch.object(gw, '__file__', str(gw.APP / 'setup.py')):
                host.install()
            self.assertTrue(all(host.services[n] for n in gw.UNITS))

    def test_reinstall_failure_restores_previous_files_and_active_services(self):
        with tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
            host.install()
            before = {str(p.relative_to(gw.CONFIG)): p.read_bytes() for p in gw.CONFIG.rglob('*') if p.is_file()}
            values = dict(host.values)
            host.fail = lambda a: a[:3] == ('nmcli', 'connection', 'up')
            with self.assertRaises(gw.SetupError):
                host.install()
            after = {str(p.relative_to(gw.CONFIG)): p.read_bytes() for p in gw.CONFIG.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(values, host.values)
            self.assertTrue(all(host.services[n] for n in gw.UNITS))

    def test_partial_write_and_signal_roll_back(self):
        for exception in (OSError('simulated disk full'), KeyboardInterrupt()):
            with self.subTest(kind=type(exception).__name__), tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
                write = gw.write
                failed = False
                def fail_once(path, *args, **kwargs):
                    nonlocal failed
                    if not failed and str(path).endswith('/config.yaml'):
                        failed = True
                        raise exception
                    return write(path, *args, **kwargs)
                with patch.object(gw, 'write', fail_once), self.assertRaises(type(exception)):
                    host.install()
                self.assertFalse(gw.CONFIG.exists())
                self.assertFalse((gw.STATE / 'pending').exists())

    def test_failed_health_gate_rolls_back(self):
        with tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
            with patch.object(gw, 'health_check', side_effect=gw.SetupError('simulated DNS outage')), \
                    patch.object(gw.time, 'monotonic', side_effect=itertools.count(0, 100)), \
                    self.assertRaises(gw.SetupError):
                host.install()
            self.assertFalse(gw.CONFIG.exists())
            self.assertEqual(host.values, host.original_values)

    def test_failed_rollback_keeps_recovery_marker(self):
        with tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
            host.fail = lambda a: a[:3] == ('nmcli', 'connection', 'up')
            with patch.object(gw, 'restore_backup', side_effect=gw.SetupError('simulated recovery failure')), \
                    patch('sys.stderr', new_callable=io.StringIO) as stderr, self.assertRaises(gw.SetupError):
                host.install()
            self.assertTrue((gw.STATE / 'pending').exists())
            self.assertIn('Rollback incomplete', stderr.getvalue())

    def test_file_snapshot_roundtrip_and_permissions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            original, absent, backup = root / 'original', root / 'absent', root / 'backup'
            original.mkdir()
            gw.write(original / 'secret', 'synthetic-secret', 0o600)
            records = gw.snapshot([original, absent], backup)
            (original / 'secret').write_text('changed')
            gw.write(absent, 'created')
            gw.restore_files(records, backup)
            self.assertEqual((original / 'secret').read_text(), 'synthetic-secret')
            self.assertEqual((original / 'secret').stat().st_mode & 0o777, 0o600)
            self.assertFalse(absent.exists())

    def test_snapshot_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / 'link').symlink_to(root / 'missing')
            with self.assertRaises(gw.SetupError):
                gw.snapshot([root / 'link'], root / 'backup')

    def test_new_install_and_rollback_for_each_backend(self):
        for backend in ('NetworkManager', 'networkd'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw), backend) as host:
                backup = host.install()
                self.assertTrue(all(host.services[n] for n in gw.UNITS))
                self.assertTrue((gw.CONFIG / 'config.yaml').is_file())
                with redirect_stdout(io.StringIO()):
                    gw.restore_backup(backup)
                self.assertFalse(gw.CONFIG.exists())
                self.assertFalse(gw.APP.exists())
                self.assertFalse((gw.STATE / 'installed.json').exists())
                self.assertFalse((gw.STATE / 'pending').exists())
                self.assertEqual(host.links, host.original_links)
                self.assertEqual(host.values, host.original_values)
                self.assertTrue(all(not host.services[n] for n in gw.UNITS))

    def test_failure_injection_at_each_activation_stage(self):
        points = [
            lambda a: a[:2] == ('systemctl', 'daemon-reload'),
            lambda a: a[:2] == ('systemctl', 'enable') and a[-1].endswith('rollback.timer'),
            lambda a: a[:3] == ('nmcli', 'connection', 'reload'),
            lambda a: a[:3] == ('nmcli', 'connection', 'up'),
            lambda a: a[:2] == ('networkctl', 'reconfigure'),
            lambda a: a[:2] == ('sysctl', '-w'),
            *(lambda a, unit=unit: a[:2] == ('systemctl', 'enable') and a[-1] == unit for unit in gw.UNITS),
        ]
        for index, fail in enumerate(points):
            backend = 'networkd' if index == 4 else 'NetworkManager'
            with self.subTest(stage=index), tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw), backend, fail) as host:
                with self.assertRaises(gw.SetupError):
                    host.install()
                self.assertTrue(host.failed)
                self.assertFalse(gw.CONFIG.exists())
                self.assertFalse(gw.APP.exists())
                self.assertFalse((gw.STATE / 'installed.json').exists())
                self.assertEqual(host.links, host.original_links)
                self.assertEqual(host.values, host.original_values)

    def test_watchdog_does_not_rollback_committed_install(self):
        with tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
            backup = host.install()
            calls = len(host.calls)
            gw.restore_backup(backup, watchdog=True)
            self.assertEqual(len(host.calls), calls)
            self.assertTrue(gw.CONFIG.exists())

    def test_watchdog_cleans_commit_interrupted_before_timer_cleanup(self):
        with tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
            backup = host.install()
            gw.write(gw.STATE / 'pending', str(backup) + '\n')
            gw.restore_backup(backup, watchdog=True)
            self.assertFalse((gw.STATE / 'pending').exists())
            self.assertTrue(gw.CONFIG.exists())

    def test_watchdog_restores_killed_installer(self):
        with tempfile.TemporaryDirectory() as raw, FakeHost(Path(raw)) as host:
            backup = host.install()
            manifest = json.loads((backup / 'manifest.json').read_text())
            manifest['pending'] = True
            gw.write(backup / 'manifest.json', gw.json_text(manifest))
            with redirect_stdout(io.StringIO()):
                gw.restore_backup(backup, watchdog=True)
            self.assertFalse(gw.CONFIG.exists())
            self.assertEqual(host.links, host.original_links)


def real_core_checks(binary, rules):
    """No TUN or root. Validate native YAML and URI conversion against the pinned core."""
    import base64
    node = {'name': 'Synthetic node', 'type': 'ss', 'server': '192.0.2.1', 'port': 443,
            'cipher': 'aes-128-gcm', 'password': 'synthetic-test-password'}
    uri = 'ss://' + base64.urlsafe_b64encode(b'aes-128-gcm:synthetic-test-password').decode().rstrip('=') + '@192.0.2.1:443#Synthetic'
    formats = [json.dumps({'proxies': [node]}), uri, base64.b64encode(uri.encode()).decode(),
               json.dumps({'proxies': [node], 'mixed-port': 1, 'external-controller': '0.0.0.0:1',
                           'rules': ['MATCH,REJECT'], 'proxy-groups': []})]
    for data in formats:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / 'providers').mkdir()
            (directory / 'providers/airport.yaml').write_text(data)
            shutil.copytree(rules, directory / 'rules')
            config = gw.make_config(PLAN, DEFAULTS, 'https://subscription.example.invalid/test', 'test-secret')
            count = gw.validate_provider(binary, directory, config, timeout=10)
            assert count == 1, count
            gw.write(directory / 'config.yaml', gw.json_text(config))
            gw.run(binary, '-t', '-d', directory)
    for invalid in ('<html>login required</html>', json.dumps({'proxies': []}),
                    json.dumps({'proxies': [{'name': 'DIRECT', 'type': 'direct'}]}),
                    json.dumps({'proxies': [{**node, 'name': 'Traffic remaining: notice only'}]})):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / 'providers').mkdir()
            (directory / 'providers/airport.yaml').write_text(invalid)
            shutil.copytree(rules, directory / 'rules')
            config = gw.make_config(PLAN, DEFAULTS, 'https://subscription.example.invalid/test', 'test-secret')
            try:
                gw.validate_provider(binary, directory, config, timeout=2)
            except gw.SetupError:
                pass
            else:
                raise AssertionError('Real core accepted an unusable subscription')
    print('Real Mihomo checks passed: provider YAML, full YAML, URI, base64; rejected HTML, empty, DIRECT-only and notice-only subscriptions.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mihomo', type=Path)
    parser.add_argument('--rules', type=Path)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    if args.mihomo:
        if not args.rules:
            parser.error('--mihomo requires --rules with cn.mrs and cn-ip.mrs')
        real_core_checks(args.mihomo.resolve(), args.rules.resolve())
