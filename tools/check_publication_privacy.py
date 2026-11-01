#!/usr/bin/env python3
"""Regression fixtures are synthetic and assembled to remain publication safe.

The fixtures below are built at runtime on purpose: this file is itself scanned
by the publication gate, so a literal MAC, token or credential URL here would
trip the very rule it tests.
"""
import base64

from publication_privacy import (
    ARCHIVE_PATHS, ARCHIVE_PATTERNS, AUTOMATION_PATTERNS, DOC_PATTERNS,
    NODE_PATTERNS, PUBLIC_PATTERNS, base64_labels, documented_networks,
    matching_labels, undocumented_private_addresses,
)
from check_automation import overlay_manifest_keys


def test_public_patterns():
    common = {
        'private user path': '/' + 'Users' + '/fixture/file',
        'subscription endpoint': 'https://' + 'glados-config' + '.com/' + 'x' * 16,
        'generic subscription endpoint': 'https://' + 'sub' + '.invalid/api/v1/client/subscribe?token=' + 'x' * 16,
        'UUID/node identity': '-'.join(['12345678', 'abcd', 'abcd', 'abcd', '123456789abc']),
        'MAC identity': ':'.join(['ab'] * 6),
        'private key': '-----BEGIN ' + 'PRIVATE KEY-----',
        'GitHub token': 'gh' + 'p_' + 'x' * 24,
        'platform token': 'xox' + 'b-' + 'x' * 24,
        'public IPv6 identity': ':'.join(['2001', 'abcd', '1234', '5678', '9abc']),
        'subscription node label': chr(0x4e28),
        'split secret assignment': 'token=' + '\n' + 'x' * 24,
        'secret assignment': 'password' + ': ' + 'fixture' * 4,
        'environment secret': 'API_' + 'TOKEN=' + 'fixture' * 4,
    }
    assert set(common) == set(PUBLIC_PATTERNS)
    for label, fixture in common.items():
        assert label in matching_labels(fixture, PUBLIC_PATTERNS), label
        assert label in matching_labels(fixture.upper(), AUTOMATION_PATTERNS), label


def test_doc_patterns():
    fixtures = {
        'provider endpoint': 'glados-config' + '.com/' + 'x' * 16,
        'internal hostname': 'host ' + 'nas' + '.lan',
        'email address': 'guard-test' + '@' + 'mail.invalid',
        'provider node label': 'node ' + 'STT' + ' | 99',
        'device/SSID name': 'wifi ' + 'RP5' + '-GUEST-99',
        'proxy URI': 'vmess' + '://' + 'x' * 24,
        'URL with credential': 'http://' + 'user' + ':' + 'pass' + '@' + '10.255.255.9:3000/repo',
    }
    assert set(fixtures) == set(DOC_PATTERNS)
    for label, fixture in fixtures.items():
        assert label in matching_labels(fixture, DOC_PATTERNS), label
    # Executable fixtures may legitimately carry sample hostnames and URLs.
    assert not matching_labels('https://example.invalid/template ' + 'x' * 8, DOC_PATTERNS)


def test_private_addresses():
    declared = documented_networks([
        'wan0 192.168.0.117/24 and proxy20 192.168.20.1/24',
        'wg 10.253.254.1/30; local set 172.17.0.0-' + '172.21.255.255',
    ])
    assert not undocumented_private_addresses('gateway 192.168.0.1', declared)
    assert not undocumented_private_addresses('gateway 172.19.0.9', declared)
    assert not undocumented_private_addresses('docs 198.51.100.7', declared)
    assert undocumented_private_addresses('gateway 10.255.255.1', declared) == ['10.255.255.1']
    assert undocumented_private_addresses('gateway 100.64.1.1', declared) == ['100.64.1.1']


def test_base64_payloads():
    endpoint = 'https://' + 'sub' + '.invalid/api/v1/client/subscribe?token=' + 'x' * 16
    payload = base64.b64encode(('proxy list\n' + endpoint).encode()).decode()
    assert 'base64 generic subscription endpoint' in base64_labels(payload)
    clean = base64.b64encode(b'plain documentation text ' * 8).decode()
    assert not base64_labels(clean)


def main():
    test_public_patterns()
    test_doc_patterns()
    test_private_addresses()
    test_base64_payloads()

    lan = '.'.join(['192', '168', '42', '7'])
    assert 'deployment LAN address' in matching_labels(lan, AUTOMATION_PATTERNS)
    assert not matching_labels(lan, PUBLIC_PATTERNS)
    node = '"' + 'STT' + ' | fixture 2"'
    assert 'provider label with index in code' in matching_labels(node, NODE_PATTERNS, flags=0)
    assert matching_labels(chr(0x4e28), NODE_PATTERNS, flags=0)
    archive = {
        'GLaDOS subscription token': 'glados-config' + '.com/' + 'x' * 16,
        'InfraAPI subscription token': 'infraapi' + '.cloud/link/' + 'x' * 16,
        'BWB subscription token': 'bwbwbw' + '.cc/subscribe/' + 'x' * 16,
    }
    for label, fixture in archive.items():
        assert label in matching_labels(fixture, ARCHIVE_PATTERNS), label
        assert not matching_labels(fixture, PUBLIC_PATTERNS), label
    assert matching_labels('bwbwbw' + '.cc/123/' + 'x', ARCHIVE_PATTERNS)
    assert matching_labels('/' + 'Users' + '/fixture/file', ARCHIVE_PATTERNS)
    assert set(ARCHIVE_PATHS) == {
        'README.md', 'SECURITY.md', 'docs/deployment-debugging-summary.zh-CN.md',
        'assets/network-topology.svg',
    }
    for patterns in (PUBLIC_PATTERNS, AUTOMATION_PATTERNS, NODE_PATTERNS, ARCHIVE_PATTERNS):
        assert not matching_labels('https://example.invalid/template\n10.77.0.1', patterns)
    for source in (
        "overlay_labels(capabilities, 'ai_nodes')",
        'overlay_labels(\n caps,\n "ai_nodes"\n)',
        "overlay_labels(capabilities=caps, key='ai_nodes')",
    ):
        assert overlay_manifest_keys(source) == {'ai_nodes'}
    try:
        overlay_manifest_keys('overlay_labels(caps, variable)')
    except ValueError:
        pass
    else:
        raise AssertionError('dynamic manifest key silently skipped')
    assert overlay_manifest_keys('''
def pool_nodes(nodes, capabilities, key):
    return overlay_labels(capabilities, f"{key}_nodes")
pool_nodes(nodes, caps, "ai")
''') == {'ai_nodes'}
    try:
        overlay_manifest_keys('pool_nodes(nodes, caps, variable)')
    except ValueError:
        pass
    else:
        raise AssertionError('dynamic pool key silently skipped')
    print('Privacy coverage and checker formatting regressions passed')


if __name__ == '__main__':
    main()
