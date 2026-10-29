#!/usr/bin/env python3
"""Regression checks for refreshes of pre-selections STT deployments."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile

import yaml
from check_overlay import (
    ALL_NODES, MANIFEST_OVERLAY, build_fixture, native_group_nodes, run_overlay,
)
from check_subscription_candidate import run, set_native_account, write_yaml


def validate_core(candidate, directory):
    binary = os.environ.get('MIHOMO_TEST_BINARY')
    if not binary:
        return
    # Validate the real candidate's full group/provider graph without fetching
    # rule databases or using real subscription credentials.
    config = {key: copy.deepcopy(candidate[key])
              for key in ('proxies', 'proxy-groups', 'proxy-providers') if key in candidate}
    config['rules'] = ['MATCH,Auto']
    nodes = list(config['proxies'])
    for provider in config.get('proxy-providers', {}).values():
        nodes.extend(provider.get('payload', []))
    for node in nodes:
        node.update(cipher='aes-128-gcm', password='synthetic-test-password')
    path = directory / 'core-check.yaml'
    write_yaml(path, config)
    result = subprocess.run([binary, '-t', '-d', str(directory), '-f', str(path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def check_case(directory, native, scenario):
    directory.mkdir()
    current, capabilities = build_fixture(directory, MANIFEST_OVERLAY,
                                          [*ALL_NODES, 'STT | Bulk 02'])
    policy = yaml.safe_load(capabilities.read_text())
    policy.pop('selections', None)
    if native:
        set_native_account(policy)
    write_yaml(capabilities, policy)
    previous = yaml.safe_load(current.read_text())
    legacy = {'name': 'STT', 'type': 'fallback',
              'proxies': ['STT | Bulk 01', 'STT | Bulk 02'],
              'url': 'https://www.gstatic.com/generate_204',
              'interval': 180, 'lazy': True, 'hidden': True,
              'icon': 'https://example.invalid/stt.svg'}
    if scenario != 'absent':
        previous['proxy-groups'].insert(2, legacy)
    previous['rules'].insert(0, 'DOMAIN-SUFFIX,tracker.example.invalid,DIRECT')
    write_yaml(current, previous)
    seeded = directory / 'seeded.yaml'
    result = run_overlay(current, capabilities, seeded)
    assert result.returncode == 0, result.stderr
    previous = yaml.safe_load(seeded.read_text())
    write_yaml(current, previous)
    if native and scenario != 'absent':
        old_group = next(group for group in previous['proxy-groups'] if group['name'] == 'STT')
        assert 'use' in old_group and 'filter' in old_group

    if scenario == 'custom-provider':
        custom_group = next(group for group in previous['proxy-groups'] if group['name'] == 'STT')
        custom_group.update(type='select', proxies=['STT | Bulk 01'],
                            use=['user-stt'], filter='^Example.*')
        previous.setdefault('proxy-providers', {})['user-stt'] = {
            'type': 'inline', 'payload': [copy.deepcopy(previous['proxies'][0])],
            'health-check': {'enable': False, 'interval': 0},
        }
        write_yaml(current, previous)

    if scenario == 'explicit':
        policy['selections'] = {'STT': {'type': 'select', 'patterns': [r'^STT \| Bulk 02$']}}
    elif scenario == 'unrelated-selection':
        policy['selections'] = {'Custom': {'type': 'select', 'members': ['Auto']}}
    write_yaml(capabilities, policy)
    incoming = copy.deepcopy(previous['proxies'])
    if scenario in ('rename', 'unrelated-selection', 'absent', 'custom-provider'):
        for node in incoming:
            if node['name'].startswith('STT | '):
                node['name'] += ' generation-2'
    elif scenario in ('delete', 'empty'):
        incoming = [node for node in incoming if node['name'] != 'STT | Bulk 01'
                    and not (scenario == 'empty' and node['name'].startswith('STT | '))]
    subscription, extras = directory / 'subscription.yaml', directory / 'extras'
    write_yaml(subscription, {'proxies': incoming})
    extras.write_text('')
    output, status = directory / 'candidate.yaml', directory / 'status'
    before, manifest_before = current.read_bytes(), capabilities.read_bytes()
    result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
    assert current.read_bytes() == before and capabilities.read_bytes() == manifest_before
    if scenario == 'empty':
        assert result.returncode != 0 and 'no usable subscription nodes for STT Download' in result.stderr
        assert not output.exists() and not status.exists()
        return
    assert result.returncode == 0, result.stderr
    candidate = yaml.safe_load(output.read_text())
    groups = {group['name']: group for group in candidate['proxy-groups']}
    if scenario == 'absent':
        assert 'STT' not in groups
    else:
        expected = ({'STT | Bulk 02'} if scenario == 'explicit' else
                    {node['name'] for node in incoming if node['name'].startswith('STT | ')})
        assert native_group_nodes(candidate, 'STT') == expected
        assert list(groups).index('STT') == 2
        if scenario != 'explicit':
            assert groups['STT']['hidden'] is True and groups['STT']['icon'] == legacy['icon']
            assert groups['STT']['type'] == ('fallback' if native and scenario != 'custom-provider' else 'select')
            if scenario == 'custom-provider':
                assert groups['STT']['use'] == ['user-stt']
                assert groups['STT']['filter'] == '^Example.*'
                assert candidate['proxy-providers']['user-stt'] == previous['proxy-providers']['user-stt']
        else:
            assert groups['STT']['type'] == 'select'
    assert 'DOMAIN-SUFFIX,tracker.example.invalid,DIRECT' in candidate['rules']
    names = {node['name'] for node in candidate['proxies']} | set(groups) | {'DIRECT', 'REJECT'}
    assert all(member in names for group in groups.values() for member in group.get('proxies', []))
    validate_core(candidate, directory)
    # Refresh the generated provider form again; stale ownership must not return.
    write_yaml(current, candidate)
    first = output.read_bytes()
    result = run('prepare', current, subscription, extras, output, status, manifest=capabilities)
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == first and status.read_text() == 'unchanged\n'


def main():
    with tempfile.TemporaryDirectory() as raw:
        for native in (False, True):
            for scenario in ('rename', 'delete', 'explicit', 'absent', 'empty',
                             'unrelated-selection', 'custom-provider'):
                check_case(Path(raw) / f'{native}-{scenario}', native, scenario)
    print('Legacy STT migration checks passed (14 scenarios, repeated refreshes)')


if __name__ == '__main__':
    main()
