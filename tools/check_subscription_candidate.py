#!/usr/bin/env python3
"""Exercise the candidate command through its production file interface."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml
from check_overlay import ALL_NODES, MANIFEST_OVERLAY, build_fixture, group_proxies

REPO = Path(__file__).resolve().parents[1]
COMMAND = REPO / 'automation/sbin/mihomo-subscription-candidate'


def write_yaml(path, value):
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def run(*args, manifest):
    return subprocess.run(
        [sys.executable, str(COMMAND), *map(str, args)],
        env=dict(os.environ, MIHOMO_CAPABILITIES=str(manifest), PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=30,
    )


def main():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        current, capabilities = build_fixture(root, MANIFEST_OVERLAY, ALL_NODES)
        original = yaml.safe_load(current.read_text())
        original['dns'] = {'enable': True, 'listen': '127.0.0.1:1053'}
        write_yaml(current, original)
        subscription = root / 'primary.yaml'
        primary = copy.deepcopy(original)
        primary['proxies'] = [node for node in primary['proxies']
                              if not node['name'].startswith(('ByWave | ', 'BWB | ', 'InfraAPI | '))]
        write_yaml(subscription, primary)
        extras = root / 'sources.manifest'
        entries = []
        for source_id, names in (
            ('ByWave', ['Bulk 01']), ('BWB', ['Backup 01', 'Backup 02']),
            ('InfraAPI', ['Backup 01']),
        ):
            source = root / (source_id + '.yaml')
            nodes = [{'name': name, 'type': 'ss', 'server': '198.51.100.1', 'port': 443}
                     for name in names]
            if source_id == 'BWB':
                nodes[1]['dialer-proxy'] = names[0]
                nodes[1]['detour'] = names[0]
                nodes.append({'name': '\u5269\u4f59\u6d41\u91cf: fixture', 'type': 'ss'})
            write_yaml(source, {'proxies': nodes})
            assert run('validate-extra', source, manifest=capabilities).returncode == 0
            entries.append(f'{source_id}|{source}|1\n')
        extras.write_text(''.join(entries))
        output, status = root / 'candidate.yaml', root / 'status'
        before = current.read_bytes()
        alias = run('prepare', current, subscription, extras, current, status, manifest=capabilities)
        assert alias.returncode != 0 and current.read_bytes() == before
        result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
        assert result.returncode == 0, result.stderr
        assert current.read_bytes() == before
        assert status.read_text() == 'changed\n'
        candidate = yaml.safe_load(output.read_text())
        assert candidate['dns'] == original['dns']
        names = [node['name'] for node in candidate['proxies']]
        assert len(names) == len(set(names))
        assert set(names) == set(ALL_NODES)
        renamed = next(node for node in candidate['proxies'] if node['name'] == 'BWB | Backup 02')
        assert renamed['dialer-proxy'] == renamed['detour'] == 'BWB | Backup 01'
        assert group_proxies(candidate, 'Streaming') == MANIFEST_OVERLAY['media_nodes']
        download = next(group for group in candidate['proxy-groups'] if group['name'] == 'Download')
        assert download['type'] == 'load-balance'
        assert download['expected-status'] == 401
        assert output.stat().st_mode & 0o777 == 0o600

        # Repeated fresh builds and cached extras use the same candidate seam.
        first = output.read_bytes()
        extras.write_text(''.join(entry.rsplit('|', 1)[0] + '|0\n' for entry in entries))
        result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
        assert result.returncode == 0, result.stderr
        assert output.read_bytes() == first
        write_yaml(current, candidate)
        result = run('prepare', current, current, extras, output, status, manifest=capabilities)
        assert result.returncode == 0, result.stderr
        assert yaml.safe_load(output.read_text()) == candidate
        assert status.read_text() == 'unchanged\n'

        # Failures cannot leave a candidate/status that appears successful.
        bad_extra = root / 'invalid.yaml'
        for proxies in ([], [{'name': '\u5269\u4f59\u6d41\u91cf: fixture', 'type': 'ss'}],
                        [{'name': 'node without type'}]):
            write_yaml(bad_extra, {'proxies': proxies})
            assert run('validate-extra', bad_extra, manifest=capabilities).returncode != 0
        broken = copy.deepcopy(primary)
        broken['proxies'].append(copy.deepcopy(broken['proxies'][0]))
        write_yaml(subscription, broken)
        output.unlink()
        status.unlink()
        result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
        assert result.returncode != 0
        assert not output.exists() and not status.exists()
        write_yaml(subscription, primary)
        manifest = yaml.safe_load(capabilities.read_text())
        manifest['overlay'] = {}
        write_yaml(capabilities, manifest)
        empty_primary = copy.deepcopy(primary)
        empty_primary['proxies'] = [node for node in primary['proxies'] if ' | Bulk' not in node['name']]
        write_yaml(subscription, empty_primary)
        write_yaml(current, empty_primary)
        extras.write_text('')
        result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
        assert result.returncode != 0
        assert not output.exists() and not status.exists()
    print('Subscription candidate composition checks passed')


if __name__ == '__main__':
    main()
