#!/usr/bin/env python3
"""Verify package identity and managed-file drift before installing anything."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import shutil

PACKAGE = Path(__file__).resolve().parent
STATE = Path('var/lib/mihomo-automation/installed-manifest.json')
TRANSACTION = Path('var/lib/mihomo-automation/.install-pending')


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
            if (not path.is_file() or digest(path) != previous['sha256'] or
                    stat.S_IMODE(path.stat().st_mode) != previous['mode']):
                raise ValueError('installed drift: ' + target)
        elif path.exists() and (digest(path) != entry['sha256'] or
                                stat.S_IMODE(path.stat().st_mode) != entry['mode']):
            raise ValueError('untracked installed file differs: ' + target)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def mkdir_mode(path, mode):
    previous_umask = os.umask(0)
    try:
        os.mkdir(path, mode)
    finally:
        os.umask(previous_umask)


def add_intermediate(entry, state):
    states = entry.setdefault('intermediate', [])
    if not any(same_state(state, existing) for existing in states):
        states.append(state)


def directory_create_states(parent, mode):
    info = parent.stat()
    inherited = bool(info.st_mode & stat.S_ISGID)
    gid = info.st_gid if inherited else os.getegid()
    final = {'kind': 'dir', 'mode': mode, 'uid': os.geteuid(), 'gid': gid}
    intermediate = []
    created_modes = [mode | stat.S_ISGID, mode] if inherited else [mode]
    for created_mode in created_modes:
        state = {'kind': 'dir', 'mode': created_mode, 'uid': os.geteuid(), 'gid': gid}
        if not same_state(state, final) and not any(same_state(state, prior) for prior in intermediate):
            intermediate.append(state)
    return final, intermediate


def file_state(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'kind': 'absent'}
    if stat.S_ISLNK(info.st_mode):
        return {'kind': 'other'}
    if stat.S_ISDIR(info.st_mode):
        return {'kind': 'dir', 'mode': stat.S_IMODE(info.st_mode),
                'uid': info.st_uid, 'gid': info.st_gid}
    if not stat.S_ISREG(info.st_mode):
        return {'kind': 'other'}
    return {'kind': 'file', 'sha256': digest(path), 'mode': stat.S_IMODE(info.st_mode),
            'uid': info.st_uid, 'gid': info.st_gid}


def relative_target(target):
    path = Path(target)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('invalid install target: ' + target)
    return path.relative_to('/').as_posix()


def rooted_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('invalid transaction path: ' + relative)
    current = root
    for part in path.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError('unexpected symlink path component: ' + str(current))
    return root / path


def atomic_write(path, data, mode, owner=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / ('.' + path.name + '.txn-' + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if owner is not None:
            info = temporary.stat()
            if (info.st_uid, info.st_gid) != (owner['uid'], owner['gid']):
                os.chown(temporary, owner['uid'], owner['gid'])
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def transaction_path(root):
    return root / TRANSACTION


def retire_transaction(root, marker):
    directory = transaction_path(root)
    state_dir = root / STATE.parent
    retired = state_dir / ('.install-' + marker + '-' + secrets.token_hex(8))
    os.replace(directory, retired)
    sync_directory(retired.parent)
    try:
        shutil.rmtree(retired)
        sync_directory(retired.parent)
        return True
    except OSError:
        return False


def cleanup_retired_transactions(root):
    state_dir = root / STATE.parent
    if not state_dir.is_dir():
        return
    for path in state_dir.iterdir():
        if not (path.name.startswith('.install-stage-') or
                path.name.startswith('.install-recovered-') or
                path.name.startswith('.install-committed-')):
            continue
        if path.is_symlink() or not path.is_dir():
            raise ValueError('install transaction cleanup conflict: ' + str(path))
        shutil.rmtree(path)
        sync_directory(state_dir)


def read_transaction(root):
    rooted_path(root, TRANSACTION.as_posix())
    directory = transaction_path(root)
    journal_path = directory / 'journal.json'
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('invalid install recovery directory')
    if journal_path.is_symlink() or not journal_path.is_file():
        raise ValueError('install recovery journal is missing or invalid')
    journal = json.loads(journal_path.read_text())
    if journal.get('format') != 1 or not isinstance(journal.get('entries'), list):
        raise ValueError('invalid install recovery journal')
    if journal.get('root') != str(root.resolve()):
        raise ValueError('install recovery journal belongs to another root')
    paths = set()
    temporaries = set()
    manifest_entries = 0
    for entry in journal['entries']:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('invalid install recovery entry')
        if Path(entry['path']).as_posix() != entry['path']:
            raise ValueError('invalid install recovery target: ' + entry['path'])
        rooted_path(root, entry['path'])
        if entry['path'] in paths or entry['path'] in temporaries:
            raise ValueError('duplicate install recovery target: ' + entry['path'])
        paths.add(entry['path'])
        for state_name in ('old', 'new'):
            state = entry.get(state_name)
            if not isinstance(state, dict) or state.get('kind') not in ('absent', 'dir', 'file'):
                raise ValueError('invalid install recovery state: ' + entry['path'])
            kind = state['kind']
            required = {'kind'}
            if kind == 'file':
                required |= {'sha256', 'mode', 'uid', 'gid'}
            elif kind == 'dir':
                required |= {'mode', 'uid', 'gid'}
            if not required.issubset(state):
                raise ValueError('incomplete install recovery state: ' + entry['path'])
            if kind == 'file' and (not isinstance(state['sha256'], str) or
                                   re.fullmatch(r'[0-9a-f]{64}', state['sha256']) is None):
                raise ValueError('invalid install recovery hash: ' + entry['path'])
            if kind != 'absent':
                if (not isinstance(state['mode'], int) or isinstance(state['mode'], bool) or
                        not 0 <= state['mode'] <= 0o7777 or
                        any(not isinstance(state[key], int) or isinstance(state[key], bool) or
                            state[key] < 0 for key in ('uid', 'gid'))):
                    raise ValueError('invalid install recovery metadata: ' + entry['path'])
        intermediates = entry.get('intermediate', [])
        if not isinstance(intermediates, list):
            raise ValueError('invalid install recovery intermediate states: ' + entry['path'])
        for state in intermediates:
            if not isinstance(state, dict) or state.get('kind') != 'dir':
                raise ValueError('invalid install recovery intermediate state: ' + entry['path'])
            if (not {'mode', 'uid', 'gid'}.issubset(state) or
                    not isinstance(state['mode'], int) or isinstance(state['mode'], bool) or
                    not 0 <= state['mode'] <= 0o7777 or
                    any(not isinstance(state[key], int) or isinstance(state[key], bool) or
                        state[key] < 0 for key in ('uid', 'gid'))):
                raise ValueError('invalid install recovery intermediate metadata: ' + entry['path'])
        temporary = entry.get('temp')
        if temporary is not None:
            rooted_path(root, temporary)
            if re.fullmatch(re.escape(entry['path']) + r'\.txn-[0-9a-f]{16}', temporary) is None:
                raise ValueError('invalid install recovery temporary path: ' + temporary)
            if not temporary.startswith(entry['path'] + '.txn-'):
                raise ValueError('invalid install recovery temporary path: ' + temporary)
            if temporary in temporaries or temporary in paths:
                raise ValueError('duplicate install recovery temporary path')
            temporaries.add(temporary)
        old = entry['old']
        if old['kind'] == 'file':
            snapshot = old.get('snapshot')
            if (not isinstance(snapshot, str) or
                    re.fullmatch(r'snapshots/[0-9a-f]{24}', snapshot) is None):
                raise ValueError('invalid install recovery snapshot path: ' + entry['path'])
        if entry['new']['kind'] == 'file' and temporary is None:
            raise ValueError('install recovery file has no temporary path: ' + entry['path'])
        if entry.get('role') == 'manifest':
            manifest_entries += 1
            if entry['path'] != STATE.as_posix():
                raise ValueError('invalid install recovery manifest target')
        elif 'role' in entry:
            raise ValueError('invalid install recovery entry role')
        old_kind, new_kind = entry['old']['kind'], entry['new']['kind']
        if (old_kind, new_kind) not in {('absent', 'file'), ('absent', 'dir'),
                                       ('file', 'file'), ('dir', 'dir')}:
            raise ValueError('invalid install recovery transition: ' + entry['path'])
        if intermediates and (old_kind, new_kind) not in {('absent', 'dir'), ('dir', 'dir')}:
            raise ValueError('unexpected install recovery intermediate state: ' + entry['path'])
    if manifest_entries != 1 or not isinstance(journal.get('manifest'), str):
        raise ValueError('invalid install recovery manifest entry')
    manifest_entry = next(item for item in journal['entries'] if item.get('role') == 'manifest')
    if hashlib.sha256(journal['manifest'].encode()).hexdigest() != manifest_entry['new']['sha256']:
        raise ValueError('install recovery manifest does not match its recorded hash')
    return directory, journal


def write_journal(directory, journal):
    atomic_write(directory / 'journal.json',
                 (json.dumps(journal, sort_keys=True, indent=2) + '\n').encode(), 0o600)


def snapshot_entry(directory, entry, path, new_state):
    old = file_state(path)
    if old['kind'] == 'other':
        raise ValueError('unsupported existing install target: ' + entry['path'])
    if old['kind'] == 'file':
        snapshot = 'snapshots/' + secrets.token_hex(12)
        snapshot_path = directory / snapshot
        snapshot_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(snapshot_path, path.read_bytes(), 0o600)
        old['snapshot'] = snapshot
    entry.update(old=old, new=new_state)


def begin_transaction(root, current):
    destination = transaction_path(root)
    if os.path.lexists(destination):
        raise ValueError('an install recovery transaction is already pending')
    rooted_path(root, STATE.as_posix())
    state_dir = root / STATE.parent
    if state_dir.is_symlink():
        raise ValueError('unexpected symlink state directory: ' + str(state_dir))
    missing = []
    parent = state_dir
    while parent != root and not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700 if path == state_dir else 0o755)
        sync_directory(path.parent)
    stage = state_dir / ('.install-stage-' + secrets.token_hex(8))
    stage.mkdir(mode=0o700)
    try:
        (stage / 'snapshots').mkdir(mode=0o700)
        journal = {'format': 1, 'root': str(root.resolve()),
                   'release_id': current['release_id'], 'entries': []}
        candidates = managed(current['files'], root)
        for target, item in sorted(candidates.items()):
            relative = relative_target(target)
            path = rooted_path(root, relative)
            new = {'kind': 'file', 'sha256': item['sha256'], 'mode': item['mode'],
                   'uid': os.geteuid(), 'gid': os.getegid()}
            entry = {'path': relative, 'temp': relative + '.txn-' + secrets.token_hex(8)}
            snapshot_entry(stage, entry, path, new)
            journal['entries'].append(entry)
        manifest_bytes = (json.dumps(current, indent=2) + '\n').encode()
        manifest_relative = STATE.as_posix()
        manifest_path = rooted_path(root, manifest_relative)
        manifest_entry = {'path': manifest_relative,
                          'temp': manifest_relative + '.txn-' + secrets.token_hex(8),
                          'role': 'manifest'}
        snapshot_entry(stage, manifest_entry, manifest_path,
                       {'kind': 'file', 'sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                        'mode': 0o600, 'uid': os.geteuid(), 'gid': os.getegid()})
        journal['manifest'] = manifest_bytes.decode()
        journal['entries'].append(manifest_entry)
        write_journal(stage, journal)
        sync_directory(stage / 'snapshots')
        sync_directory(stage)
        os.replace(stage, destination)
        sync_directory(state_dir)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def entry_state(root, entry):
    return file_state(rooted_path(root, entry['path']))


def replace_from_bytes(root, entry, data, mode, owner=None):
    target = rooted_path(root, entry['path'])
    temporary = rooted_path(root, entry['temp'])
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    if owner is not None:
        info = temporary.stat()
        if (info.st_uid, info.st_gid) != (owner['uid'], owner['gid']):
            os.chown(temporary, owner['uid'], owner['gid'])
    descriptor = os.open(temporary, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    require_old_or_new(root, entry)
    os.replace(temporary, target)
    sync_directory(target.parent)


def same_state(actual, expected):
    if actual.get('kind') != expected.get('kind'):
        return False
    if actual['kind'] in ('absent', 'other'):
        return actual['kind'] == 'absent'
    return all(actual.get(key) == expected.get(key)
               for key in ('sha256', 'mode', 'uid', 'gid') if key in expected)


def require_old_or_new(root, entry):
    actual = entry_state(root, entry)
    known = [entry['old'], entry['new'], *entry.get('intermediate', [])]
    if not any(same_state(actual, state) for state in known):
        raise ValueError('install recovery conflict: ' + entry['path'])
    return actual


def validate_snapshots(directory, journal):
    for entry in journal['entries']:
        old = entry['old']
        if old['kind'] == 'file':
            snapshot = directory / old['snapshot']
            if (not snapshot.is_file() or snapshot.is_symlink() or
                    digest(snapshot) != old['sha256']):
                raise ValueError('install recovery snapshot is missing or corrupt: ' + entry['path'])


def restore_transaction(root):
    directory = transaction_path(root)
    if not os.path.lexists(directory):
        return False
    directory, journal = read_transaction(root)
    validate_snapshots(directory, journal)
    # Validate the whole live set before restoring any member.
    states = []
    for entry in journal['entries']:
        actual = entry_state(root, entry)
        known = [entry['old'], entry['new'], *entry.get('intermediate', [])]
        if not any(same_state(actual, state) for state in known):
            raise ValueError('install recovery conflict: ' + entry['path'])
        temporary = entry.get('temp')
        if temporary is not None and file_state(rooted_path(root, temporary))['kind'] not in ('absent', 'file'):
            raise ValueError('install recovery temporary path conflict: ' + temporary)
        states.append(actual)
    for entry in journal['entries']:
        if not entry.get('temp'):
            continue
        temporary = rooted_path(root, entry['temp'])
        if temporary.exists():
            temporary.unlink()
            sync_directory(temporary.parent)
    ordered = [entry for entry in journal['entries']
               if entry['new']['kind'] != 'dir' and entry.get('role') != 'manifest']
    ordered += list(reversed([entry for entry in journal['entries'] if entry['new']['kind'] == 'dir']))
    ordered += [entry for entry in journal['entries'] if entry.get('role') == 'manifest']
    for entry in ordered:
        actual = require_old_or_new(root, entry)
        if same_state(actual, entry['old']):
            continue
        path = rooted_path(root, entry['path'])
        old = entry['old']
        if old['kind'] == 'absent':
            if entry['new']['kind'] == 'dir':
                path.rmdir()
            else:
                path.unlink()
            sync_directory(path.parent)
        elif old['kind'] == 'dir':
            if entry['new']['kind'] == 'dir':
                os.chown(path, old['uid'], old['gid'])
                require_old_or_new(root, entry)
                os.chmod(path, old['mode'])
                sync_directory(path)
            else:
                path.rmdir()
                sync_directory(path.parent)
        else:
            data = (directory / old['snapshot']).read_bytes()
            replace_from_bytes(root, entry, data, old['mode'], old)
    for entry in journal['entries']:
        if not same_state(entry_state(root, entry), entry['old']):
            raise ValueError('install recovery readback mismatch: ' + entry['path'])
    if not retire_transaction(root, 'recovered'):
        print('warning: previous files restored; retired recovery data awaits cleanup', file=sys.stderr)
    return True


def add_transaction_entry(root, target, data, mode):
    directory, journal = read_transaction(root)
    relative = relative_target(target)
    for entry in journal['entries']:
        if entry['path'] == relative:
            expected = {'kind': 'file', 'sha256': hashlib.sha256(data).hexdigest(),
                        'mode': mode, 'uid': os.geteuid(), 'gid': os.getegid()}
            if entry['new'] != expected:
                raise ValueError('transaction target differs from prepared candidate: ' + target)
            return entry
    path = rooted_path(root, relative)
    entry = {'path': relative, 'temp': relative + '.txn-' + secrets.token_hex(8)}
    snapshot_entry(directory, entry, path,
                   {'kind': 'file', 'sha256': hashlib.sha256(data).hexdigest(),
                    'mode': mode, 'uid': os.geteuid(), 'gid': os.getegid()})
    journal['entries'].append(entry)
    write_journal(directory, journal)
    sync_directory(directory / 'snapshots')
    return entry


def ensure_directory(root, target, mode):
    read_transaction(root)
    relative = relative_target(target)
    path = rooted_path(root, relative)
    if path.exists():
        if not path.is_dir() or path.is_symlink():
            raise ValueError('install directory is not a real directory: ' + target)
        if mode != 0o700:
            return
        current = file_state(path)
        desired = {'kind': 'dir', 'mode': mode, 'uid': current['uid'], 'gid': current['gid']}
        if same_state(current, desired):
            return
        directory, journal = read_transaction(root)
        entry = next((item for item in journal['entries'] if item['path'] == relative), None)
        if entry is None:
            entry = {'path': relative}
            snapshot_entry(directory, entry, path, desired)
            journal['entries'].append(entry)
        else:
            if not (same_state(current, entry['old']) or same_state(current, entry['new'])):
                raise ValueError('install recovery conflict: ' + relative)
            entry['new'] = desired
        write_journal(directory, journal)
        require_old_or_new(root, entry)
        os.chmod(path, mode)
        sync_directory(path)
        return
    parent = path.parent
    if parent != root and not parent.exists():
        ensure_directory(root, '/' + parent.relative_to(root).as_posix(), 0o755)
    if path.parent != root and path.parent.is_symlink():
        raise ValueError('unexpected symlink path component: ' + str(path.parent))
    directory, journal = read_transaction(root)
    entry = {'path': relative}
    desired, intermediate = directory_create_states(path.parent, mode)
    snapshot_entry(directory, entry, path, desired)
    for state in intermediate:
        add_intermediate(entry, state)
    journal['entries'].append(entry)
    write_journal(directory, journal)
    if file_state(path)['kind'] != 'absent':
        raise ValueError('install recovery conflict: ' + relative)
    mkdir_mode(path, mode)
    actual = require_old_or_new(root, entry)
    if not same_state(actual, desired):
        os.chmod(path, mode)
        require_old_or_new(root, entry)
    sync_directory(path)
    sync_directory(path.parent)


def install_file(root, source, target, mode, default=False):
    source = Path(source).resolve()
    try:
        source.relative_to(PACKAGE)
    except ValueError:
        raise ValueError('install source is outside the verified package')
    data = source.read_bytes()
    destination = rooted_path(root, relative_target(target))
    if default and os.path.lexists(destination):
        return False
    ensure_directory(root, '/' + destination.parent.relative_to(root).as_posix(), 0o755)
    entry = add_transaction_entry(root, target, data, mode)
    temporary = rooted_path(root, entry['temp'])
    require_old_or_new(root, entry)
    try:
        temporary.unlink(missing_ok=True)
        result = subprocess.run(['install', '-m', format(mode, '04o'), '--', str(source), str(temporary)])
        if result.returncode:
            raise ValueError('install command failed for ' + target)
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        if stat.S_IMODE(temporary.stat().st_mode) != mode:
            raise ValueError('temporary install mode mismatch: ' + target)
        require_old_or_new(root, entry)
        os.replace(temporary, destination)
        sync_directory(destination.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def run_locked(root, command):
    lock_path = root / 'run/lock/mihomo-maintenance.lock'
    rooted_path(root, 'run/lock/mihomo-maintenance.lock')
    lock_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise ValueError('invalid gateway maintenance lock path')
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('gateway maintenance is busy')
        command = [item for item in command if item != '--']
        if not command:
            raise ValueError('--lock-run requires a command')
        environment = dict(os.environ, MIHOMO_INSTALL_LOCK_HELD='true')
        os.set_inheritable(descriptor, True)
        os.execvpe(command[0], command, environment)
    finally:
        # The descriptor remains open across exec and closes with the installer.
        if os.get_inheritable(descriptor):
            try:
                os.close(descriptor)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='generate a package manifest before validation')
    parser.add_argument('--root', type=Path, help='check installed managed files under this filesystem root')
    parser.add_argument('--record', action='store_true', help='record only after every installed file matches this release')
    parser.add_argument('--prepare', action='store_true', help='recover an interrupted install, then verify the baseline')
    parser.add_argument('--begin', action='store_true', help='start a durable install transaction')
    parser.add_argument('--recover', action='store_true', help='restore a pending install transaction')
    parser.add_argument('--install-file', nargs=3, metavar=('SOURCE', 'TARGET', 'MODE'))
    parser.add_argument('--default', action='store_true', help='preserve an existing file for --install-file')
    parser.add_argument('--ensure-dir', nargs=2, metavar=('TARGET', 'MODE'))
    parser.add_argument('--rollback', action='store_true', help='restore a pending transaction')
    parser.add_argument('--dry-run', action='store_true', help='check without recovery or writes')
    parser.add_argument('--lock-run', nargs=argparse.REMAINDER,
                        help='hold the maintenance lock while running the given command')
    args = parser.parse_args()
    path = PACKAGE / 'release-manifest.json'
    actual = release(catalog(PACKAGE))
    if args.write:
        if args.root or args.record or args.prepare or args.begin or args.install_file or args.ensure_dir:
            parser.error('--write cannot install or record a release')
        path.write_text(json.dumps(actual, indent=2) + '\n')
        print('manifest generated: ' + actual['release_id'])
        return
    expected = json.loads(path.read_text())
    if actual != expected:
        raise ValueError('package differs from its reviewed release manifest')
    if args.lock_run is not None:
        if args.root is None or args.dry_run:
            parser.error('--lock-run requires --root and cannot be combined with --dry-run')
        raise SystemExit(run_locked(args.root.resolve(), args.lock_run))
    if args.record and args.root is None:
        parser.error('--record requires --root')
    if args.root is not None:
        root = args.root.resolve()
        state = rooted_path(root, STATE.as_posix())
        if state.is_symlink():
            raise ValueError('unexpected symlink installed manifest: ' + str(state))
        if args.dry_run:
            if os.path.lexists(transaction_path(root)):
                raise ValueError('install recovery is pending; dry-run did not modify it')
            previous = json.loads(state.read_text()) if state.exists() else dict(files=[])
            verify_installed(root, actual, previous)
        elif args.recover or args.rollback:
            if not restore_transaction(root):
                print('no pending file transaction')
            return
        elif args.install_file:
            source, target, mode_text = args.install_file
            changed = install_file(root, source, target, int(mode_text, 8), args.default)
            if changed:
                print('installed: ' + target)
        elif args.ensure_dir:
            target, mode_text = args.ensure_dir
            ensure_directory(root, target, int(mode_text, 8))
        elif args.begin:
            previous = json.loads(state.read_text()) if state.exists() else dict(files=[])
            verify_installed(root, actual, previous)
            begin_transaction(root, actual)
        elif args.record:
            if os.path.lexists(transaction_path(root)):
                directory, journal = read_transaction(root)
                for item in journal['entries']:
                    if item.get('temp') and file_state(rooted_path(root, item['temp']))['kind'] != 'absent':
                        raise ValueError('unexpected install temporary file: ' + item['temp'])
                    if item.get('role') == 'manifest':
                        continue
                    if not same_state(entry_state(root, item), item['new']):
                        raise ValueError('installed candidate mismatch: ' + item['path'])
                for target, entry in managed(actual['files'], root).items():
                    installed = rooted_path(root, relative_target(target))
                    if (not installed.is_file() or digest(installed) != entry['sha256'] or
                            stat.S_IMODE(installed.stat().st_mode) != entry['mode']):
                        raise ValueError('installed candidate mismatch: ' + target)
                manifest_entry = next((item for item in journal['entries']
                                       if item.get('role') == 'manifest'), None)
                if manifest_entry is None:
                    raise ValueError('install transaction has no manifest entry')
                replace_from_bytes(root, manifest_entry, journal['manifest'].encode(), 0o600)
                if not retire_transaction(root, 'committed'):
                    print('warning: file transaction committed; retired recovery data awaits cleanup',
                          file=sys.stderr)
            else:
                for target, entry in managed(actual['files'], root).items():
                    installed = root / target.lstrip('/')
                    if (not installed.is_file() or digest(installed) != entry['sha256'] or
                            stat.S_IMODE(installed.stat().st_mode) != entry['mode']):
                        raise ValueError('installed candidate mismatch: ' + target)
                state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary = state.with_suffix('.tmp')
                with temporary.open('w') as stream:
                    json.dump(actual, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(0o600)
                temporary.replace(state)
                sync_directory(state.parent)
        elif args.prepare:
            if os.path.lexists(transaction_path(root)):
                restore_transaction(root)
            cleanup_retired_transactions(root)
            previous = json.loads(state.read_text()) if state.exists() else dict(files=[])
            verify_installed(root, actual, previous)
        elif args.root is not None:
            previous = json.loads(state.read_text()) if state.exists() else dict(files=[])
            verify_installed(root, actual, previous)
    print('release verified: ' + actual['release_id'])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error))
