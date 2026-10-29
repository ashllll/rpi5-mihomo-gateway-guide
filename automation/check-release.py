#!/usr/bin/env python3
"""Verify package identity and managed-file drift before installing anything."""
import argparse
import hashlib
import json
import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
STATE = Path('var/lib/mihomo-automation/installed-manifest.json')


def digest(path):
    if path.is_symlink():
        raise ValueError('unexpected symlink: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def catalog(package):
    entries = []
    for folder, target, mode in [('sbin', '/usr/local/sbin/', 0o755),
                                  ('systemd', '/etc/systemd/system/', 0o644),
                                  ('etc/systemd/journald.conf.d', '/etc/systemd/journald.conf.d/', 0o644)]:
        for path in sorted((package / folder).rglob('*')):
            if path.is_file():
                relative = str(path.relative_to(package))
                entries.append(dict(source=relative, target=target + str(path.relative_to(package / folder)),
                                    sha256=digest(path), mode=mode))
    for name in ['install.sh', 'check-release.py']:
        entries.append(dict(source=name, target=None, sha256=digest(package / name)))
    dispatcher = 'etc/NetworkManager/dispatcher.d/90-wifi-management-routing'
    entries.append(dict(source=dispatcher, target='/' + dispatcher,
                        sha256=digest(package / dispatcher), mode=0o700,
                        when_directory='etc/NetworkManager'))
    # Template bytes identify the release, but existing site data is never checked
    # against a public template or overwritten as a managed executable.
    for folder in ['env', 'policy', 'etc']:
        for path in sorted((package / folder).rglob('*')):
            if path.is_file() and not any(e['source'] == str(path.relative_to(package)) for e in entries):
                entries.append(dict(source=str(path.relative_to(package)), target=None, sha256=digest(path)))
    return sorted(entries, key=lambda entry: entry['source'])


def release(entries):
    identity = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(format=1, release_id=identity, files=entries)


def managed(entries, root):
    return {entry['target']: entry for entry in entries if entry['target'] and
            ('when_directory' not in entry or (root / entry['when_directory']).is_dir())}


def verify_installed(root, current, expected):
    old = managed(expected['files'], root)
    new = managed(current['files'], root)
    if set(old) - set(new):
        raise ValueError('managed file removal requires a reviewed migration')
    for target, entry in new.items():
        path = root / target.lstrip('/')
        previous = old.get(target)
        if previous:
            if not path.is_file() or digest(path) != previous['sha256']:
                raise ValueError('installed drift: ' + target)
        elif path.exists() and digest(path) != entry['sha256']:
            raise ValueError('untracked installed file differs: ' + target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='generate a package manifest before validation')
    parser.add_argument('--root', type=Path, help='check installed managed files under this filesystem root')
    parser.add_argument('--record', action='store_true', help='record only after every installed file matches this release')
    args = parser.parse_args()
    path = PACKAGE / 'release-manifest.json'
    actual = release(catalog(PACKAGE))
    if args.write:
        if args.root or args.record:
            parser.error('--write cannot install or record a release')
        path.write_text(json.dumps(actual, indent=2) + '\n')
        print('manifest generated: ' + actual['release_id'])
        return
    expected = json.loads(path.read_text())
    if actual != expected:
        raise ValueError('package differs from its reviewed release manifest')
    if args.record and args.root is None:
        parser.error('--record requires --root')
    if args.root is not None:
        state = args.root / STATE
        if args.record:
            for target, entry in managed(actual['files'], args.root).items():
                installed = args.root / target.lstrip('/')
                if not installed.is_file() or digest(installed) != entry['sha256']:
                    raise ValueError('installed candidate mismatch: ' + target)
            state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = state.with_suffix('.tmp')
            with temporary.open('w') as stream:
                json.dump(actual, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            temporary.replace(state)
        else:
            previous = json.loads(state.read_text()) if state.exists() else dict(files=[])
            verify_installed(args.root, actual, previous)
    print('release verified: ' + actual['release_id'])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error))
