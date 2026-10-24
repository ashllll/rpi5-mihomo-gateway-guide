#!/usr/bin/env python3
"""Offline checks for native URLTest admission and the persistent account ledger."""
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-measurement-policy'))
BROKER = runpy.run_path(str(ROOT / 'automation/sbin/mihomo-measurement-budget'))


def node(name='A', port=1):
    return {'name': name, 'type': 'http', 'server': '127.0.0.1', 'port': port}


def policy(limit=12, accounts=None):
    return {'enabled': True, 'native_budget': True,
            'accounts': accounts or {'shared': {'names': ['A'], 'native_probes_per_hour': 12,
                                                'quality_attempts_per_hour': limit}}}


def config(nodes=None):
    return {'proxies': nodes or [node()]}


def request(name='A', port=1, deadline=None):
    return {'protocol': BROKER['PROTOCOL'], 'name': name, 'addr': f'127.0.0.1:{port}', 'dialer': '',
            'deadline_ms': int((time.time() + 2 if deadline is None else deadline) * 1000)}


def socket_request(path, item=None):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(path)
            client.sendall(json.dumps(item or request()).encode() + b'\n')
            reply = client.makefile('rb').readline()
            # Listener backlog and worker saturation fail closed; a missing
            # response is denied and deliberately never retried by the client.
            return bool(json.loads(reply)['allowed']) if reply else False
    except OSError:
        return False


class NativeBudgetTests(unittest.TestCase):
    def test_account_ids_validate_before_generation_and_admission(self):
        for account in (12345, True, None, '', 1.5):
            settings = policy(accounts={account: {'names': ['A'], 'native_probes_per_hour': 8,
                                                 'quality_attempts_per_hour': 8}})
            with self.subTest(account=account), tempfile.TemporaryDirectory(prefix='mb-', dir='/tmp') as raw:
                directory = Path(raw)
                with self.assertRaisesRegex(ValueError, 'account'):
                    POLICY['configure_measurement'](
                        {'proxies': [node()], 'proxy-groups': []}, {'measurement': settings})
                cfg, manifest = directory/'config.yaml', directory/'policy.yaml'
                cfg.write_text(yaml.safe_dump(config()))
                manifest.write_text(yaml.safe_dump({'measurement': settings}))
                with self.assertRaisesRegex(ValueError, 'account'):
                    BROKER['read_inputs'](cfg, manifest)
                self.assertFalse((directory/'budget.json').exists())

    def test_existing_string_account_key_is_preserved_and_native_cost_is_two(self):
        settings = policy(accounts={'12345': {'names': ['A'], 'native_probes_per_hour': 12,
                                               'quality_attempts_per_hour': 6}})
        config_value = config()
        with tempfile.TemporaryDirectory(prefix='mb-', dir='/tmp') as raw, patch('time.time', return_value=10000):
            ledger = Path(raw)/'quality-budget.json'
            ledger.write_text(json.dumps({'accounts': {'12345': [[9999, 2]]}, 'last_reservation_at': 9999}))
            self.assertTrue(BROKER['authorize'](request(deadline=10002), config_value, settings, ledger))
            self.assertTrue(BROKER['authorize'](request(deadline=10002), config_value, settings, ledger))
            self.assertFalse(BROKER['authorize'](request(deadline=10002), config_value, settings, ledger))
            entries = json.loads(ledger.read_text())['accounts']['12345']
            self.assertEqual(sum(row[1] for row in entries), 6)
            self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)

    def test_socket_rechecks_policy_after_lock_wait_before_predebit(self):
        settings = policy(limit=6)
        node_value = node()
        with tempfile.TemporaryDirectory(prefix='mb-', dir='/tmp') as raw:
            directory = Path(raw)
            cfg, manifest, ledger = (directory/name for name in ('config.yaml', 'policy.yaml', 'budget.json'))
            cfg.write_text(yaml.safe_dump(config([node_value])))
            manifest.write_text(yaml.safe_dump({'measurement': settings}))
            path = str(directory/'budget.sock')
            waiting = threading.Event()
            actual_flock = fcntl.flock

            def observed_flock(fd, flags):
                if flags & fcntl.LOCK_EX:
                    waiting.set()
                return actual_flock(fd, flags)

            with BROKER['Server'](path, cfg, manifest, ledger) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    with ledger.with_suffix('.lock').open('a+b') as held:
                        actual_flock(held, fcntl.LOCK_EX)
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool, patch(
                                'fcntl.flock', side_effect=observed_flock):
                            future = pool.submit(socket_request, path)
                            self.assertTrue(waiting.wait(2))
                            changed = {**settings, 'accounts': {'replacement': {
                                'names': ['A'], 'native_probes_per_hour': 12,
                                'quality_attempts_per_hour': 6}}}
                            manifest.write_text(yaml.safe_dump({'measurement': changed}))
                            actual_flock(held, fcntl.LOCK_UN)
                            self.assertFalse(future.result(timeout=3))
                    self.assertFalse(ledger.exists())
                finally:
                    server.shutdown()
                    worker.join()

    def test_managed_core_inherits_only_the_checked_socket(self):
        settings = policy()
        with tempfile.TemporaryDirectory(prefix='mb-', dir='/tmp') as raw:
            directory = Path(raw)
            cfg, manifest, ledger = (directory/name for name in ('config.yaml', 'policy.yaml', 'budget.json'))
            cfg.write_text('{}')
            manifest.write_text(yaml.safe_dump({'measurement': settings}))
            path = str(directory/'checked.sock')
            core = directory/'core'
            core.write_text('#!' + sys.executable + '\nimport os,sys\n'
                'print("mihomo-budget-v1" if "-measurement-budget-protocol" in sys.argv '
                'else os.environ.get("MIHOMO_MEASUREMENT_SOCKET", ""))\n')
            core.chmod(0o700)
            with BROKER['Server'](path, cfg, manifest, ledger) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    result = subprocess.run([sys.executable, str(ROOT/'automation/sbin/mihomo-measurement-budget'),
                        '--run-core', str(core), '--socket', path, '--config', str(cfg),
                        '--manifest', str(manifest), '--budget-state', str(ledger)],
                        env={**os.environ, 'MIHOMO_MEASUREMENT_SOCKET': str(directory/'other.sock')},
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), path)
                finally:
                    server.shutdown()
                    worker.join()

    def test_invalid_leaf_identity_dialer_and_deadline_never_charge(self):
        settings, config_value = policy(), config()
        good = request(deadline=10010)
        with tempfile.TemporaryDirectory() as raw, patch('time.time', return_value=10000):
            ledger = Path(raw)/'quality-budget.json'
            for changes in ({'name': 'group'}, {'addr': '127.0.0.1:2'}, {'dialer': 'hidden'},
                            {'deadline_ms': 10000000}, {'deadline_ms': True}, {'protocol': 'old'}):
                with self.subTest(changes=changes):
                    self.assertFalse(BROKER['authorize']({**good, **changes}, config_value, settings, ledger))
            settings['enabled'] = False
            self.assertFalse(BROKER['authorize'](good, config_value, settings, ledger))
            self.assertFalse(ledger.exists())

    def test_lock_wait_cannot_charge_an_expired_native_request(self):
        settings, config_value = policy(), config()
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw)/'quality-budget.json'
            with ledger.with_suffix('.lock').open('a+b') as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                expired = request(deadline=time.time()+0.04)
                start = time.monotonic()
                self.assertFalse(BROKER['authorize'](expired, config_value, settings, ledger))
                self.assertLess(time.monotonic()-start, 0.5)
            self.assertFalse(ledger.exists())

    def test_socket_concurrency_and_corrupt_ledger_fail_closed(self):
        settings = policy(limit=12)
        config_value = config()
        with tempfile.TemporaryDirectory(prefix='mb-', dir='/tmp') as raw:
            directory = Path(raw)
            cfg, manifest, ledger = (directory/name for name in ('config.yaml', 'policy.yaml', 'quality-budget.json'))
            cfg.write_text(yaml.safe_dump(config_value))
            manifest.write_text(yaml.safe_dump({'measurement': settings}))
            now = time.time()
            ledger.write_text(json.dumps({'accounts': {'shared': [[now, 6]]},
                                          'last_reservation_at': now}))
            path = str(directory/'budget.sock')
            with BROKER['Server'](path, cfg, manifest, ledger) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                        results = list(pool.map(lambda _: socket_request(path), range(80)))
                    granted = sum(results)
                    self.assertLessEqual(granted, 3)
                    charges = json.loads(ledger.read_text())['accounts']['shared']
                    charged = sum(row[1] for row in charges)
                    self.assertEqual(charged, 6 + 2 * granted)
                    self.assertLessEqual(charged, 12)

                    # The saturated burst may be denied before reaching the
                    # broker; once it drains, the same broker must still answer.
                    reply = BROKER['request_status'](path)
                    self.assertEqual(reply['protocol'], BROKER['PROTOCOL'])
                    self.assertEqual(reply['budget_path'], str(ledger.resolve()))

                    # Drain any unused budget serially to prove every admitted
                    # request costs two and the account cap remains exact.
                    while granted < 3 and socket_request(path):
                        granted += 1
                    charges = json.loads(ledger.read_text())['accounts']['shared']
                    self.assertEqual(sum(row[1] for row in charges), 12)
                    for invalid in ('{broken', '{"accounts":{"shared":[[1,-100]]},"last_reservation_at":1}'):
                        ledger.write_text(invalid)
                        self.assertFalse(socket_request(path))
                        self.assertEqual(ledger.read_text(), invalid)
                finally:
                    server.shutdown()
                    worker.join()

    def test_startup_failure_refresh_and_rollback_requests_each_predebit(self):
        settings = policy(limit=10)
        config_value = config()
        with tempfile.TemporaryDirectory() as raw, patch('time.time', return_value=10000):
            ledger = Path(raw)/'quality-budget.json'
            for lifecycle_event in ('startup', 'failure-retry', 'refresh', 'rollback', 'periodic'):
                with self.subTest(event=lifecycle_event):
                    self.assertTrue(BROKER['authorize'](
                        request(deadline=10002), config_value, settings, ledger))
            entries = json.loads(ledger.read_text())['accounts']['shared']
            self.assertEqual(sum(row[1] for row in entries), 10)


if __name__ == '__main__':
    unittest.main()
