#!/usr/bin/env python3
"""Regression fixtures are synthetic and assembled to remain publication safe."""
from publication_privacy import (
    PUBLIC_PATTERNS, AUTOMATION_PATTERNS, NODE_PATTERNS,
    ARCHIVE_PATTERNS, ARCHIVE_PATHS, matching_labels,
)
from check_automation import overlay_manifest_keys


def main():
    common = {
        'private user path': '/' + 'Users' + '/fixture/file',
        'subscription endpoint': 'https://' + 'glados-config' + '.com/' + 'x' * 16,
        'UUID/node identity': '-'.join(['12345678', 'abcd', 'abcd', 'abcd', '123456789abc']),
        'MAC identity': ':'.join(['ab'] * 6),
        'private key': '-----BEGIN ' + 'PRIVATE KEY-----',
        'GitHub token': 'gh' + 'p_' + 'x' * 24,
        'public IPv6 identity': ':'.join(['2001', 'abcd', '1234', '5678', '9abc']),
        'subscription node label': chr(0x4e28),
    }
    assert set(common) == set(PUBLIC_PATTERNS)
    for label, fixture in common.items():
        assert label in matching_labels(fixture, PUBLIC_PATTERNS), label
        assert label in matching_labels(fixture.upper(), AUTOMATION_PATTERNS), label
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
    assert matching_labels(common['private user path'], ARCHIVE_PATTERNS)
    assert set(ARCHIVE_PATHS) == {
        'README.md', 'SECURITY.md', 'docs/deployment-debugging-summary.zh-CN.md',
        'assets/network-topology.svg', 'web/index.html',
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
    print('Privacy coverage and checker formatting regressions passed')


if __name__ == '__main__':
    main()
