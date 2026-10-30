#!/usr/bin/env python3
"""Exercise the candidate command through its production file interface."""
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml
from check_overlay import (
    ALL_NODES, MANIFEST_OVERLAY, ROUTE_DEFAULTS, build_fixture,
    group_proxies, legacy_manifest, migrated_default_manifest,
)

REPO = Path(__file__).resolve().parents[1]
COMMAND = REPO / 'automation/sbin/mihomo-subscription-candidate'


def write_yaml(path, value):
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def run(*args, manifest):
    return subprocess.run(
        [sys.executable, str(COMMAND), *map(str, args)],
        env=dict(os.environ, MIHOMO_CAPABILITIES=str(manifest),
                 MIHOMO_ROUTE_DEFAULTS=str(ROUTE_DEFAULTS), PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=30,
    )


def legacy_provider(profile_id, profile, payload, interval):
    """Reproduce the archived quality generator's exact file-provider contract."""
    canonical = sorted(copy.deepcopy(payload), key=lambda item: str(item.get('name', '')))
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=True,
                         separators=(',', ':')).encode('ascii')
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    health_url = profile.get('health_url', profile['sample_url'])
    expected = profile.get('health_expected', '200-399')
    provider_name = 'quality-' + profile_id
    provider = {
        'type': 'file',
        'path': f'./providers/quality-{profile_id}-{digest}.yaml',
        'payload': copy.deepcopy(payload),
        'health-check': {
            'enable': True, 'interval': interval, 'url': health_url,
            'expected-status': expected, 'lazy': False,
        },
    }
    group = {
        'name': profile['group'], 'type': 'fallback', 'use': [provider_name],
        'url': health_url, 'interval': interval, 'lazy': False,
        'expected-status': expected,
    }
    return provider_name, provider, group


def attach_legacy_quality(config, manifest, profile_ids=None):
    providers = config.setdefault('proxy-providers', {})
    download = manifest['download_quality']
    social = manifest['social_quality']
    specifications = [
        (profile_id, profile, int(download.get('health_interval_seconds', 300)))
        for profile_id, profile in download['profiles'].items()
    ]
    specifications.extend(
        (profile_id, profile, int(social.get('health_interval_seconds', 300)))
        for profile_id, profile in social['profiles'].items()
    )
    for profile_id, profile, interval in specifications:
        if profile_ids is not None and profile_id not in profile_ids:
            continue
        provider_name, provider, group = legacy_provider(profile_id, profile, config['proxies'], interval)
        providers[provider_name] = provider
        for index, previous in enumerate(config['proxy-groups']):
            if previous.get('name') == profile['group']:
                config['proxy-groups'][index] = group
                break
        else:
            config['proxy-groups'].append(group)


def rotated_candidate_inputs(root, config):
    current = root / 'current.yaml'
    write_yaml(current, config)
    subscription = root / 'rotated-subscription.yaml'
    primary = copy.deepcopy(config)
    primary['proxies'] = [
        proxy for proxy in primary['proxies']
        if not proxy['name'].startswith(('ByWave | ', 'BWB | ', 'InfraAPI | '))
    ]
    for proxy in primary['proxies']:
        proxy['port'] = 8443
    write_yaml(subscription, primary)
    extras = root / 'rotated-extras.manifest'
    entries = []
    for source_id, names in (
        ('ByWave', ['Bulk 01']), ('BWB', ['Backup 01', 'Backup 02']),
        ('InfraAPI', ['Backup 01']),
    ):
        source = root / (source_id + '-rotated.yaml')
        nodes = [{'name': name, 'type': 'ss', 'server': '198.51.100.1', 'port': 8443}
                 for name in names]
        write_yaml(source, {'proxies': nodes})
        entries.append(f'{source_id}|{source}|1\n')
    extras.write_text(''.join(entries))
    return current, subscription, extras, root / 'rotated-candidate.yaml', root / 'rotated-status'


def set_native_account(manifest):
    manifest['measurement'] = {
        'enabled': True, 'native_budget': True, 'native_interval_seconds': 600,
        'accounts': {'synthetic-account': {
            'prefixes': ['Example | ', '示例 | ', 'STT | ', 'ByWave | ', 'BWB | ',
                         'InfraAPI | ', 'GLaDOS | '],
            'native_probes_per_hour': 10000, 'quality_attempts_per_hour': 10000,
        }},
    }


def check_fresh_generation(directory, legacy=False):
    directory.mkdir()
    current, capabilities = build_fixture(directory, MANIFEST_OVERLAY, ALL_NODES)
    previous = yaml.safe_load(current.read_text())
    previous['proxy-groups'].append({'name': 'STT', 'type': 'select', 'proxies': ['STT | Bulk 01']})
    write_yaml(current, previous)
    renamed = {name: name + ' generation-2' for name in ALL_NODES}
    policy = yaml.safe_load(capabilities.read_text())
    policy['overlay'] = {key.replace('_nodes', '_patterns'): ['^' + re.escape(renamed[name]) + '$' for name in names]
                         for key, names in MANIFEST_OVERLAY.items()}
    if not legacy:
        policy['selections'] = {'STT': {'type': 'select', 'patterns': [r'^STT \| ']}}
    write_yaml(capabilities, policy)
    subscription = directory / 'bare-fresh.yaml'
    write_yaml(subscription, {'proxies': [dict(node, name=renamed[node['name']]) for node in previous['proxies']]})
    extras = directory / 'extras'
    extras.write_text('')
    output, status = directory / 'candidate.yaml', directory / 'status'
    before = current.read_bytes()
    result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
    assert result.returncode == 0, result.stderr
    candidate = yaml.safe_load(output.read_text())
    node_names = {node['name'] for node in candidate['proxies']}
    group_names = {group['name'] for group in candidate['proxy-groups']}
    assert node_names == set(renamed.values())
    assert group_proxies(candidate, 'STT') == [renamed['STT | Bulk 01']]
    assert all(member in node_names | group_names | {'DIRECT', 'REJECT'}
               for group in candidate['proxy-groups'] for member in group.get('proxies', []))
    assert current.read_bytes() == before


def main():
    # Escaped subscription names must reach validation without YAML folding them.
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for enabled in (False, True):
            for source in ('primary', 'retained', 'retained-active', 'extra', 'provider'):
                for character in ('\u0085', '\u2028', '\u2029'):
                    case = root / f'{enabled}-{source}-{ord(character)}'
                    case.mkdir()
                    current, capabilities = build_fixture(case, MANIFEST_OVERLAY, ALL_NODES)
                    original = yaml.safe_load(current.read_text())
                    primary = copy.deepcopy(original)
                    manifest = yaml.safe_load(capabilities.read_text())
                    if enabled:
                        set_native_account(manifest)
                    write_yaml(capabilities, manifest)
                    bad = copy.deepcopy(primary['proxies'][0])
                    bad['name'] = 'Example | Bad' + character + 'Tail'
                    extras = case / 'extras.manifest'
                    extras.write_text('')
                    if source == 'primary':
                        primary['proxies'].append(bad)
                    elif source == 'retained':
                        original['proxies'].append(bad)
                    elif source == 'retained-active':
                        original['proxies'].append(bad)
                        primary['proxies'].append(bad)
                    elif source == 'provider':
                        original.setdefault('proxy-providers', {})['user-inline'] = {
                            'type': 'inline', 'payload': [bad]}
                    else:
                        extra = case / 'extra.yaml'
                        extra.write_text(yaml.safe_dump({'proxies': [bad]}, allow_unicode=False))
                        extras.write_text(f'ByWave|{extra}|1\n')
                    current.write_text(yaml.safe_dump(original, allow_unicode=False))
                    subscription = case / 'subscription.yaml'
                    subscription.write_text(yaml.safe_dump(primary, allow_unicode=False))
                    before = current.read_bytes()
                    output, status = case / 'output.yaml', case / 'status'
                    result = run('prepare', current, subscription, extras, output, status,
                                 manifest=capabilities)
                    if source == 'retained':
                        assert result.returncode == 0, result.stderr
                        candidate = yaml.safe_load(output.read_text())
                        assert bad['name'] not in {node['name'] for node in candidate['proxies']}
                        assert current.read_bytes() == before
                        continue
                    assert result.returncode != 0, (enabled, source, repr(character))
                    assert 'control characters' in result.stderr, result.stderr
                    assert current.read_bytes() == before
                    assert not output.exists() and not status.exists()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        current, capabilities = build_fixture(root, MANIFEST_OVERLAY, ALL_NODES)
        original = yaml.safe_load(current.read_text())
        original['dns'] = {'enable': True, 'listen': '127.0.0.1:1053'}
        original['proxy-groups'].append({'name': 'STT', 'type': 'select', 'proxies': ['STT | Bulk 01']})
        write_yaml(current, original)
        subscription = root / 'primary.yaml'
        primary = copy.deepcopy(original)
        primary['proxies'] = [node for node in primary['proxies']
                              if not node['name'].startswith(('ByWave | ', 'BWB | ', 'InfraAPI | '))]
        write_yaml(subscription, {'proxies': primary['proxies']})
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
        stt = group_proxies(candidate, 'STT Download')
        assert stt == ['STT | Bulk 01'], f'foreign retained nodes entered STT: {stt}'
        assert group_proxies(candidate, 'STT') == ['STT | Bulk 01']
        renamed = next(node for node in candidate['proxies'] if node['name'] == 'BWB | Backup 02')
        assert renamed['dialer-proxy'] == renamed['detour'] == 'BWB | Backup 01'
        assert group_proxies(candidate, 'Streaming') == MANIFEST_OVERLAY['media_nodes']
        download = next(group for group in candidate['proxy-groups'] if group['name'] == 'Download')
        assert download['type'] == 'select'
        assert 'url' not in download
        assert output.stat().st_mode & 0o777 == 0o600

        # Provider-branded groups cannot replace the gateway's local topology.
        branded = dict(primary, **{'proxy-groups': [
            {'name': 'Airport Branded Group', 'type': 'select', 'proxies': [primary['proxies'][0]['name']]},
        ]})
        write_yaml(subscription, branded)
        result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
        assert result.returncode == 0, result.stderr
        branded_candidate = yaml.safe_load(output.read_text())
        assert branded_candidate['proxy-groups'] == candidate['proxy-groups']
        assert branded_candidate['dns'] == original['dns']
        write_yaml(subscription, {'proxies': primary['proxies']})

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

        # An upgrade from the old enabled quality schema must retire its exact
        # generated providers before native ownership inspects payloads. The
        # subscription rotates every node's port, including one non-ASCII leaf
        # used by the archived provider path hash contract.
        migration = root / 'legacy-upgrade'
        migration.mkdir()
        legacy_caps = legacy_manifest(MANIFEST_OVERLAY)
        set_native_account(legacy_caps)
        legacy_cap_path = migration / 'legacy-capabilities.yaml'
        write_yaml(legacy_cap_path, legacy_caps)
        legacy_source, _ = build_fixture(migration, MANIFEST_OVERLAY, ALL_NODES)
        old_config = yaml.safe_load(legacy_source.read_text())
        old_config['proxies'].append({
            'name': '示例 | HK 99', 'type': 'ss', 'server': '198.51.100.99', 'port': 443,
        })
        old_config['proxy-groups'].append({
            'name': 'Unrelated User Group', 'type': 'select', 'proxies': ['Example | HK 01'],
        })
        old_config['rules'].insert(0, 'DOMAIN-SUFFIX,unrelated.example.invalid,Default Proxy')
        attach_legacy_quality(old_config, legacy_caps)
        # Older paused overlays leave the generated identity/path/payload in
        # place but disable the provider check; that is still an owned asset.
        old_config['proxy-providers']['quality-git-transport']['health-check'].update(
            {'enable': False, 'interval': 0}
        )
        # Native measurement ownership can change the old generated check's
        # interval and lazy mode; generator identity is carried by type/path/
        # payload, not these mutable health settings.
        old_config['proxy-providers']['quality-github-release']['health-check'].update(
            {'interval': 900, 'lazy': True}
        )
        user_provider = {
            'type': 'http', 'url': 'https://provider.example.invalid/feed',
            'path': './providers/quality-user-owned.yaml',
        }
        old_config['proxy-providers']['quality-user-owned'] = copy.deepcopy(user_provider)
        legacy_current, legacy_subscription, legacy_extras, legacy_output, legacy_status = (
            rotated_candidate_inputs(migration, old_config)
        )
        legacy_before = legacy_cap_path.read_bytes()
        migrated = run('prepare', legacy_current, legacy_subscription, legacy_extras,
                       legacy_output, legacy_status, manifest=legacy_cap_path)
        assert migrated.returncode == 0, migrated.stderr
        assert legacy_cap_path.read_bytes() == legacy_before
        rotated = yaml.safe_load(legacy_output.read_text())
        rotated_nodes = {node['name']: node for node in rotated['proxies']}
        assert all(node.get('port') == 8443 for node in rotated['proxies'])
        assert rotated_nodes['Example | HK 01']['port'] == 8443
        assert rotated_nodes['示例 | HK 99']['port'] == 8443
        for name in ('quality-github-release', 'quality-git-transport',
                     'quality-packages', 'quality-x-video'):
            assert name not in rotated.get('proxy-providers', {}), name
            assert all(name not in group.get('use', []) for group in rotated['proxy-groups']), name
        assert rotated['proxy-providers']['quality-user-owned'] == user_provider
        assert any(group.get('name') == 'Unrelated User Group'
                   and group.get('proxies') == ['Example | HK 01']
                   for group in rotated['proxy-groups'])
        assert 'DOMAIN-SUFFIX,unrelated.example.invalid,Default Proxy' in rotated['rules']

        # The public new schema drops the retired profile sections. Default
        # provider IDs from the route policy still identify old orphan assets;
        # an arbitrary user quality-prefixed provider remains untouched.
        public_upgrade = root / 'public-upgrade'
        public_upgrade.mkdir()
        public_caps = migrated_default_manifest(MANIFEST_OVERLAY)
        set_native_account(public_caps)
        public_cap_path = public_upgrade / 'public-capabilities.yaml'
        write_yaml(public_cap_path, public_caps)
        public_source, _ = build_fixture(public_upgrade, MANIFEST_OVERLAY, ALL_NODES)
        public_old = yaml.safe_load(public_source.read_text())
        public_old['proxies'].append({
            'name': '示例 | HK 99', 'type': 'ss', 'server': '198.51.100.99', 'port': 443,
        })
        attach_legacy_quality(public_old, legacy_caps,
                              profile_ids={'github-release', 'x-video'})
        public_old['proxy-providers']['quality-github-release']['health-check'].update(
            {'enable': False, 'interval': 0}
        )
        public_old['proxy-providers']['quality-x-video']['health-check'].update(
            {'enable': False, 'interval': 0}
        )
        public_old['proxy-providers']['quality-user-owned'] = copy.deepcopy(user_provider)
        public_current, public_subscription, public_extras, public_output, public_status = (
            rotated_candidate_inputs(public_upgrade, public_old)
        )
        public_result = run('prepare', public_current, public_subscription, public_extras,
                            public_output, public_status, manifest=public_cap_path)
        assert public_result.returncode == 0, public_result.stderr
        public_candidate = yaml.safe_load(public_output.read_text())
        assert 'quality-github-release' not in public_candidate.get('proxy-providers', {})
        assert 'quality-x-video' not in public_candidate.get('proxy-providers', {})
        assert public_candidate['proxy-providers']['quality-user-owned'] == user_provider
        check_fresh_generation(root / 'fresh-generation')
        check_fresh_generation(root / 'legacy-fresh-generation', legacy=True)
    print('Subscription candidate composition checks passed')


if __name__ == '__main__':
    main()
