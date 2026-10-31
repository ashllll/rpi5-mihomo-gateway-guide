#!/usr/bin/env python3
"""Isolated native admission load check; never connects to a proxy server.

Run on the target filesystem/CPU. This is an explicit performance acceptance
check, separate from platform-independent unit tests. All inputs are synthetic.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import runpy
import socket
import tempfile
import threading
import time

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--broker', type=Path, default=Path(__file__).resolve().parents[1]
                        / 'automation/sbin/mihomo-measurement-budget')
    parser.add_argument('--directory', type=Path, default=Path('/var/tmp'))
    parser.add_argument('--concurrency', type=int, default=70)
    parser.add_argument('--entries', type=int, default=2400)
    parser.add_argument('--rpc-timeout', type=float, default=1)
    parser.add_argument('--probe-timeout', type=float, default=5)
    args = parser.parse_args()
    if not (0 < args.concurrency <= 256 and args.entries >= 0
            and 0 < args.rpc_timeout < args.probe_timeout <= 30):
        parser.error('invalid workload or deadlines')
    broker = runpy.run_path(str(args.broker))
    passed = True
    for warm in (False, True):
        with tempfile.TemporaryDirectory(prefix='mb-load-', dir=args.directory) as raw:
            directory = Path(raw)
            cfg, manifest, ledger = (directory / name for name in
                                     ('config.yaml', 'policy.yaml', 'budget.json'))
            now = time.time() - 10
            accounts = {f'account-{i}': {'names': [f'node-{i}']} for i in range(4)}
            nodes = [{'name': f'node-{i}', 'type': 'http', 'server': '127.0.0.1',
                      'port': i + 1} for i in range(4)]
            cfg.write_text(yaml.safe_dump({'proxies': nodes}))
            manifest.write_text(yaml.safe_dump({'measurement': {
                'enabled': True, 'native_budget': True, 'enforce_budget': False,
                'accounts': accounts}}))
            prior = {key: [] for key in accounts}
            for index in range(args.entries):
                prior[f'account-{index % 4}'].append([now, 2, now + 3607])
            ledger.write_text(json.dumps({'accounts': prior, 'last_reservation_at': now}))
            path = str(directory / 'budget.sock')
            barrier = threading.Barrier(args.concurrency)

            def request(index):
                node = nodes[index % len(nodes)]
                barrier.wait(timeout=15)
                started = time.monotonic()
                allowed, error = False, None
                try:
                    with socket.socket(socket.AF_UNIX) as client:
                        deadline = started + args.rpc_timeout
                        client.settimeout(args.rpc_timeout)
                        client.connect(path)
                        body = {'protocol': broker['PROTOCOL'], 'name': node['name'],
                                'addr': f"127.0.0.1:{node['port']}", 'dialer': '',
                                'deadline_ms': int((time.time() + args.probe_timeout) * 1000)}
                        client.sendall(json.dumps(body).encode() + b'\n')
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('admission deadline')
                        client.settimeout(remaining)
                        response = json.loads(client.makefile('rb').readline())
                        allowed = response.get('allowed') is True and time.monotonic() < deadline
                except (OSError, ValueError) as exc:
                    error = type(exc).__name__
                return allowed, time.monotonic() - started, error

            with broker['Server'](path, cfg, manifest, ledger) as server:
                # Join in-flight handlers before inspecting/removing their ledger.
                server.daemon_threads = False
                if warm:
                    server.inputs.get()
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as pool:
                        results = list(pool.map(request, range(args.concurrency)))
                finally:
                    server.shutdown()
                    worker.join()
            state = broker['POLICY']['load_budget'](ledger)
            preserved = all(state['accounts'][key][:len(rows)] == rows
                            for key, rows in prior.items())
            charged = sum(row[1] for rows in state['accounts'].values() for row in rows)
            accepted = sum(result[0] for result in results)
            ok = (accepted == args.concurrency and preserved
                  and charged == 2 * (args.entries + args.concurrency))
            passed = passed and ok
            print(json.dumps({'warm': warm, 'requests': args.concurrency,
                              'seed_entries': args.entries, 'allowed': accepted,
                              'max_seconds': round(max(result[1] for result in results), 4),
                              'prior_preserved': preserved,
                              'new_charges': charged - 2 * args.entries, 'pass': ok}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
