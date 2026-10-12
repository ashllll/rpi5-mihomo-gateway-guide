#!/usr/bin/env python3
"""Build a node-free Raspberry Pi recovery image, or restore it to a spare disk."""
import argparse
import base64
import binascii
from contextlib import contextmanager, ExitStack
import hashlib
import json
import lzma
import os
from pathlib import Path
import platform
import plistlib
import re
import secrets
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import tarfile

MIB = 1024 * 1024
CHUNK = 4 * MIB
REPO = Path(__file__).resolve().parents[1]
DISABLED = ["mihomo.service", "mosdns.service", "dnsmasq.service", "nftables.service"]


def run(*args, capture=False, **kwargs):
    result = subprocess.run([str(a) for a in args], check=True,
                            stdout=subprocess.PIPE if capture else None, **kwargs)
    return result.stdout if capture else None


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(CHUNK):
            checksum.update(block)
    return checksum.hexdigest()


def write(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def mbr(size, disk_id):
    """Two aligned partitions: 512 MiB FAT32 and the remaining ext4 root."""
    if size < 2048 * MIB or size % MIB or size // 512 > 0xFFFFFFFF:
        raise ValueError("Image size must be a whole MiB and at least 2048 MiB")
    raw = bytearray(512)
    struct.pack_into("<I", raw, 440, disk_id)
    for offset, kind, start, count in ((446, 0x0C, 2048, 512 * 2048),
                                     (462, 0x83, 513 * 2048, size // 512 - 513 * 2048)):
        struct.pack_into("<B3sB3sII", raw, offset, 0, b"\xfe\xff\xff", kind,
                         b"\xfe\xff\xff", start, count)
    raw[510:] = b"\x55\xaa"
    return bytes(raw)


def check_mbr(raw, size):
    if len(raw) != 512 or raw[510:] != b"\x55\xaa":
        raise ValueError("Not an MBR disk image")
    entries = [struct.unpack_from("<B3sB3sII", raw, n) for n in (446, 462)]
    boot, root = entries
    if (boot[2] != 0x0C or root[2] != 0x83 or boot[4] != 2048
            or boot[5] != 512 * 2048 or root[4] != boot[4] + boot[5]
            or (root[4] + root[5]) * 512 != size or size < 2048 * MIB
            or any(raw[478:510])):
        raise ValueError("Unexpected recovery image partition layout")


@contextmanager
def loop_image(path, readonly=False):
    args = ["losetup", "--find", "--show", "--partscan"]
    if readonly:
        args.append("--read-only")
    device = run(*args, path, capture=True).decode().strip()
    try:
        yield device
    finally:
        run("losetup", "--detach", device)


@contextmanager
def mounted(source, target, *options):
    target.mkdir(parents=True, exist_ok=True)
    run("mount", *options, source, target)
    try:
        yield target
    finally:
        run("umount", target)


def chroot(root, *args, capture=False):
    return run("chroot", root, "/usr/bin/env", "-i", "HOME=/root", "LC_ALL=C",
               "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "DEBIAN_FRONTEND=noninteractive", *args, capture=capture)


def install_packages(root):
    # Only synthetic devices; host disks and the host systemd bus stay outside.
    resolver = root / "etc/resolv.conf"
    old_link = os.readlink(resolver) if resolver.is_symlink() else None
    old_content = resolver.read_bytes() if resolver.is_file() and not old_link else None
    policy = root / "usr/sbin/policy-rc.d"
    old_policy = policy.read_bytes() if policy.exists() else None
    write(resolver, "nameserver 223.5.5.5\nnameserver 1.12.12.12\n")
    write(policy, "#!/bin/sh\nexit 101\n", 0o755)
    try:
        with ExitStack() as stack:
            stack.enter_context(mounted("tmpfs", root / "dev", "-t", "tmpfs", "-o", "mode=755,nosuid"))
            for name, minor in (("null", 3), ("zero", 5), ("random", 8), ("urandom", 9)):
                os.mknod(root / "dev" / name, stat.S_IFCHR | 0o666, os.makedev(1, minor))
                (root / "dev" / name).chmod(0o666)
            stack.enter_context(mounted("proc", root / "proc", "-t", "proc", "-o", "ro,nosuid,nodev,noexec"))
            chroot(root, "apt-get", "-o", "Acquire::Retries=3", "-o", "Acquire::http::Timeout=30", "update")
            chroot(root, "apt-get", "-y", "--no-install-recommends", "install",
                   "python3-yaml", "nftables", "dnsmasq", "curl", "rsync", "dnsutils")
            chroot(root, "apt-get", "clean")
    finally:
        resolver.unlink(missing_ok=True)
        if old_link:
            resolver.symlink_to(old_link)
        elif old_content is not None:
            resolver.write_bytes(old_content)
        policy.unlink(missing_ok=True)
        if old_policy is not None:
            policy.write_bytes(old_policy)
            policy.chmod(0o755)


def seed_config(user, key, hostname):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", user) or user in {"root", "nobody", "daemon"}:
        raise ValueError("Choose a new non-root Linux username")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
        raise ValueError("Invalid hostname")
    fields = key.strip().split()
    if (len(key.strip().splitlines()) != 1 or len(fields) < 2 or fields[0] not in {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256",
                                           "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521"}
            or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", fields[1])):
        raise ValueError("Provide an OpenSSH public key, never a private key")
    try:
        wire = base64.b64decode(fields[1], validate=True)
        parts = []
        while wire:
            length = struct.unpack_from(">I", wire)[0]
            if not length or length > len(wire) - 4:
                raise ValueError("Invalid SSH key wire format")
            parts.append(wire[4:4 + length])
            wire = wire[4 + length:]
        if not parts or parts[0].decode("ascii") != fields[0]:
            raise ValueError("SSH key algorithm does not match its data")
        if fields[0] == "ssh-ed25519" and (len(parts) != 2 or len(parts[1]) != 32):
            raise ValueError("Invalid Ed25519 public key")
        if fields[0] == "ssh-rsa" and (len(parts) != 3 or len(parts[2]) < 256):
            raise ValueError("Use an RSA key of at least 2048 bits")
        if fields[0].startswith("ecdsa-") and (len(parts) != 3 or parts[1].decode("ascii") != fields[0][11:]):
            raise ValueError("Invalid ECDSA public key")
    except (binascii.Error, struct.error, UnicodeError) as error:
        raise ValueError("Invalid OpenSSH public key") from error
    return {"hostname": hostname, "manage_etc_hosts": True, "disable_root": True,
            "ssh_pwauth": False,
            "users": [{"name": user, "groups": ["adm", "sudo", "netdev"], "shell": "/bin/bash",
                       "lock_passwd": True, "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
                       "ssh_authorized_keys": [" ".join(fields[:2])]}],
            "growpart": {"mode": "auto", "devices": ["/"]}, "resize_rootfs": True,
            "runcmd": [["systemctl", "enable", "--now", "ssh.service"]]}


def seed_boot(boot, config):
    write(boot / "user-data", "#cloud-config\n" + json.dumps(config, indent=2) + "\n", 0o600)
    write(boot / "meta-data", json.dumps({"instance-id": "gateway-" + secrets.token_hex(16)}) + "\n", 0o600)
    # NoCloud must not turn a wildcard into a nonexistent interface name.
    # The image enables NetworkManager's native DHCP auto-default instead.
    write(boot / "network-config", json.dumps({"config": "disabled"}) + "\n", 0o600)
    write(boot / "ssh", "")


def clean_root(root):
    for name in ("root", "home", "var/log", "var/cache/apt/archives", "var/lib/cloud",
                 "var/lib/NetworkManager", "etc/NetworkManager/system-connections", "tmp", "var/tmp"):
        directory = root / name
        for child in directory.iterdir() if directory.is_dir() else []:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    for path in (root / "etc/ssh").glob("ssh_host_*"):
        path.unlink()
    for name in ("var/lib/systemd/random-seed", "var/lib/dbus/machine-id"):
        (root / name).unlink(missing_ok=True)
    write(root / "etc/machine-id", "")
    write(root / "etc/subuid", "")
    write(root / "etc/subgid", "")
    write(root / "etc/hostname", "rpi-gateway\n")
    write(root / "etc/hosts", "127.0.0.1 localhost\n127.0.1.1 rpi-gateway\n::1 localhost ip6-localhost\n")
    passwd = (root / "etc/passwd").read_text().splitlines()
    keep = {line.split(":")[0] for line in passwd if int(line.split(":")[2]) < 1000
            or line.split(":")[0] == "nobody"}
    write(root / "etc/passwd", "\n".join(line for line in passwd if line.split(":")[0] in keep) + "\n")
    shadow = []
    for line in (root / "etc/shadow").read_text().splitlines():
        fields = line.split(":")
        if fields[0] in keep:
            fields[1] = "!"
            shadow.append(":".join(fields))
    write(root / "etc/shadow", "\n".join(shadow) + "\n", 0o640)
    for name in ("passwd-", "shadow-", "group-", "gshadow-"):
        (root / "etc" / name).unlink(missing_ok=True)
    for name in ("group", "gshadow"):
        rows = []
        for line in (root / "etc" / name).read_text().splitlines():
            fields = line.split(":")
            if name == "group" and int(fields[2]) >= 1000 and fields[0] != "nogroup":
                continue
            fields[1] = "x" if name == "group" else "!"
            for index in ([3] if name == "group" else [2, 3]):
                fields[index] = ",".join(member for member in fields[index].split(",") if member in keep)
            rows.append(":".join(fields))
        if name == "gshadow":
            groups = {line.split(":")[0] for line in (root / "etc/group").read_text().splitlines()}
            rows = [line for line in rows if line.split(":")[0] in groups]
        write(root / "etc" / name, "\n".join(rows) + "\n", 0o640 if name == "gshadow" else 0o644)


def build(args):
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"} or os.geteuid():
        raise ValueError("Build on an ARM64 Linux host as root")
    if not args.in_namespace:
        run("unshare", "--mount", "--propagation", "private", sys.executable,
            Path(__file__).resolve(), *sys.argv[1:], "--in-namespace")
        return
    sources = json.loads(args.sources.read_text())
    for name, path, expected in (("base", args.base, sources["base"]["sha256"]),
                                 ("mihomo", args.mihomo, sources["mihomo"]["binary_sha256"]),
                                 ("mosdns", args.mosdns, sources["mosdns"]["binary_sha256"])):
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"{name}: input checksum mismatch")
    size = args.size_mib * MIB
    disk_id = secrets.randbits(32)
    layout = mbr(size, disk_id)
    parent = args.output.resolve().parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < size * 3:
        raise ValueError("Allow at least three image sizes of free build space")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=args.output) as temporary:
        work = Path(temporary)
        base = work / "base.img"
        print("Extracting the verified official image", flush=True)
        with lzma.open(args.base, "rb") as source, base.open("xb") as target:
            shutil.copyfileobj(source, target, CHUNK)
        root, firmware = work / "root", work / "firmware"
        with loop_image(base, readonly=True) as device, ExitStack() as stack:
            source_root = stack.enter_context(mounted(device + "p2", work / "original-root", "-o", "ro,noload"))
            source_boot = stack.enter_context(mounted(device + "p1", work / "original-boot", "-o", "ro"))
            root.mkdir()
            firmware.mkdir()
            run("rsync", "-aHAX", "--numeric-ids", str(source_root) + "/", root)
            run("rsync", "-rlt", str(source_boot) + "/", firmware)
        base.unlink()
        print("Installing gateway dependencies inside the isolated image root", flush=True)
        install_packages(root)
        repo = root / "opt/rpi5-mihomo-gateway-guide"
        repo.mkdir(parents=True)
        paths = run("git", "-C", REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard", capture=True)
        for raw in sorted(set(paths.split(b"\0")) - {b""}):
            relative = Path(os.fsdecode(raw))
            source = REPO / relative
            if not source.is_file() or source.is_symlink() or ".." in relative.parts:
                raise ValueError("Repository contains an unsupported publication path")
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        run("sh", REPO / "automation/install.sh", env={**os.environ, "GATEWAY_ROOT": str(root)})
        for name, source in (("mihomo", args.mihomo), ("mosdns", args.mosdns)):
            binary = root / "usr/local/bin" / name
            shutil.copy2(source, binary)
            binary.chmod(0o755)
            if digest(binary) != sources[name]["binary_sha256"]:
                raise ValueError(f"{name}: binary changed while copying")
            doc = root / "usr/share/doc/gateway-image"
            doc.mkdir(parents=True, exist_ok=True)
            for suffix, url in (("LICENSE", sources[name]["license_url"]), ("source.tar.gz", sources[name]["source_url"])):
                run("curl", "-q", "--fail", "--location", "--retry", "3", "--connect-timeout", "15",
                    "--retry-all-errors", "--max-time", "180", "--silent", "--show-error", url, "-o", doc / f"{name}.{suffix}")
                path = doc / f"{name}.{suffix}"
                if suffix == "source.tar.gz" and not tarfile.is_tarfile(path):
                    raise ValueError(f"{name}: invalid upstream source archive")
                if suffix == "LICENSE" and "GNU GENERAL PUBLIC LICENSE" not in path.read_text():
                    raise ValueError(f"{name}: missing upstream GPL license")
                sources[name][suffix + "_sha256"] = digest(path)
        write(root / "etc/mihomo/config.yaml", json.dumps({
            "mixed-port": 7890, "bind-address": "127.0.0.1", "allow-lan": False,
            "mode": "direct", "ipv6": False, "proxies": [], "proxy-groups": [],
            "rules": ["MATCH,DIRECT"]}, indent=2) + "\n", 0o600)
        disabled = [*DISABLED, *(path.name for path in (root / "etc/systemd/system").glob("*.timer"))]
        run("systemctl", "--root", root, "disable", *disabled)
        clean_root(root)
        write(root / "etc/NetworkManager/conf.d/90-gateway-recovery.conf", "[main]\nno-auto-default=\n")
        for name in ("user-data", "meta-data", "network-config", "ssh", "userconf", "userconf.txt", "firstrun.sh"):
            (firmware / name).unlink(missing_ok=True)
        # No account or key is distributed. The restore command or Imager seeds it.
        write(firmware / "user-data", "#cloud-config\nusers: []\ndisable_root: true\nssh_pwauth: false\n")
        write(firmware / "meta-data", "instance-id: gateway-unconfigured\n")
        prefix = f"{disk_id:08x}"
        write(root / "etc/fstab", f"proc /proc proc defaults 0 0\nPARTUUID={prefix}-01 /boot/firmware vfat defaults 0 2\nPARTUUID={prefix}-02 / ext4 defaults,noatime 0 1\n")
        cmdline = (firmware / "cmdline.txt").read_text()
        if len(re.findall(r"\broot=\S+", cmdline)) != 1:
            raise ValueError("Official boot command line has no unique root device")
        write(firmware / "cmdline.txt", re.sub(r"\broot=\S+", f"root=PARTUUID={prefix}-02", cmdline))
        chroot(root, "/usr/local/bin/mihomo", "-t", "-d", "/etc/mihomo")
        packages = chroot(root, "dpkg-query", "-W", "-f=${Package} ${Version}\n", capture=True).decode()
        root_bytes = int(run("du", "--summarize", "--block-size=1", root, capture=True).split()[0])
        if root_bytes > (size - 513 * MIB) * 0.9:
            raise ValueError("Root filesystem is too small; increase --size-mib")
        raw_image = args.output / "rpi5-gateway-node-free.img"
        with raw_image.open("xb") as stream:
            stream.write(layout)
            stream.truncate(size)
        print("Creating fresh filesystems; deleted build data is never copied", flush=True)
        with loop_image(raw_image) as device, ExitStack() as stack:
            run("mkfs.vfat", "-F", "32", "-n", "bootfs", device + "p1")
            run("mkfs.ext4", "-q", "-F", "-L", "rootfs", "-E", "lazy_itable_init=0,lazy_journal_init=0", device + "p2")
            dest_root = stack.enter_context(mounted(device + "p2", work / "final-root"))
            dest_boot = stack.enter_context(mounted(device + "p1", work / "final-boot"))
            run("rsync", "-aHAX", "--numeric-ids", str(root) + "/", dest_root)
            run("rsync", "-rlt", str(firmware) + "/", dest_boot)
        with loop_image(raw_image, readonly=True) as device:
            run("fsck.vfat", "-n", device + "p1")
            run("e2fsck", "-fn", device + "p2")
        manifest = {"format": 1, "name": raw_image.name + ".xz", "size": size,
                    "raw_sha256": digest(raw_image), "sources": sources,
                    "node_count": 0, "personal_configuration": False,
                    "physical_boot_tested": False, "disabled_units": disabled,
                    "reserved_users": [line.split(":")[0] for line in (root / "etc/passwd").read_text().splitlines()],
                    "packages": packages.splitlines()}
        print("Compressing with one low-priority worker", flush=True)
        run("xz", "-T1", "-0", raw_image)
        archive = raw_image.with_suffix(".img.xz")
        if archive.stat().st_size >= 2 * 1024 ** 3:
            raise ValueError("Compressed image exceeds the GitHub Release per-file limit")
        manifest["sha256"] = digest(archive)
        write(args.output / "image-manifest.json", json.dumps(manifest, indent=2) + "\n")
        shutil.copy2(Path(__file__), args.output / "gateway_image.py")
        write(args.output / "SHA256SUMS", "".join(f"{digest(path)}  {path.name}\n" for path in
              (archive, args.output / "image-manifest.json", args.output / "gateway_image.py")))
        print(f"Built {archive.name}: {archive.stat().st_size} bytes", flush=True)


def verify_image(image, manifest, expanded=None):
    if (not isinstance(manifest, dict) or type(manifest.get("format")) is not int
            or manifest.get("format") != 1 or image.name != manifest.get("name")
            or type(manifest.get("size")) is not int
            or any(not isinstance(manifest.get(key), str)
                   or not re.fullmatch(r"[a-f0-9]{64}", manifest[key])
                   for key in ("sha256", "raw_sha256"))):
        raise ValueError("Invalid image manifest")
    if digest(image) != manifest["sha256"]:
        raise ValueError("Compressed image checksum mismatch")
    total, checksum = 0, hashlib.sha256()
    with ExitStack() as stack:
        stream = stack.enter_context(lzma.open(image, "rb"))
        target = stack.enter_context(expanded.open("xb")) if expanded else None
        header = stream.read(512)
        check_mbr(header, manifest["size"])
        checksum.update(header)
        if target:
            target.write(header)
        total = len(header)
        while block := stream.read(CHUNK):
            total += len(block)
            if total > manifest["size"]:
                raise ValueError("Image exceeds its declared size")
            checksum.update(block)
            if target:
                target.write(block)
    if total != manifest["size"] or checksum.hexdigest() != manifest["raw_sha256"]:
        raise ValueError("Uncompressed image checksum or size mismatch")


def disk_info(device):
    system = platform.system()
    if system == "Darwin":
        if not re.fullmatch(r"/dev/disk[0-9]+", device):
            raise ValueError("Select a whole external disk, for example /dev/diskN")
        info = plistlib.loads(run("diskutil", "info", "-plist", device, capture=True))
        physical = plistlib.loads(run("diskutil", "list", "-plist", "external", "physical", capture=True))
        if (not info.get("WholeDisk", info.get("Whole", False)) or info.get("Internal", True)
                or device.removeprefix("/dev/") not in physical.get("WholeDisks", [])
                or info.get("VirtualOrPhysical") == "Virtual"
                or info.get("Writable") is False or info.get("WritableMedia") is False):
            raise ValueError("Refusing an internal, virtual or partition target")
        return {"size": info["TotalSize"], "identity": info.get("DeviceTreePath", device),
                "label": info.get("MediaName", device), "raw": device.replace("/dev/disk", "/dev/rdisk")}
    if system != "Linux":
        raise ValueError("Restore supports Linux and macOS")
    if not Path(device).is_block_device():
        raise ValueError("Target must be a physical whole-disk block device")
    info = json.loads(run("lsblk", "--json", "--bytes", "-o", "PATH,TYPE,SIZE,RO,MOUNTPOINTS,MODEL,SERIAL", device, capture=True))["blockdevices"]
    if len(info) != 1 or info[0]["type"] != "disk" or info[0]["ro"]:
        raise ValueError("Refusing a partition, virtual or read-only disk")
    pending = info.copy()
    while pending:
        item = pending.pop()
        if any(item.get("mountpoints") or []):
            raise ValueError("Target disk has mounted filesystems or active swap; unmount it first")
        if item is not info[0] and item.get("type", "part") != "part":
            raise ValueError("Target has an active mapped device; close it first")
        pending.extend(item.get("children") or [])
    item = info[0]
    return {"size": item["size"], "identity": item.get("serial") or item["path"],
            "label": item.get("model") or device, "raw": device}


def copy_image(image, destination, expected_hash, compressed=True):
    checksum = hashlib.sha256()
    opener = lzma.open if compressed else open
    with opener(image, "rb") as source, open(destination, "wb", buffering=0) as target:
        while block := source.read(CHUNK):
            view = memoryview(block)
            while view:
                written = target.write(view)
                if not written:
                    raise OSError("Target stopped accepting image data")
                view = view[written:]
            checksum.update(block)
        os.fsync(target.fileno())
    if checksum.hexdigest() != expected_hash:
        raise ValueError("Image changed while writing; do not boot this disk")


def seed_image(raw, config):
    """Personalize a private image copy before any physical disk is opened."""
    if platform.system() == "Linux":
        with loop_image(raw) as device, tempfile.TemporaryDirectory(prefix="gateway-seed-", dir=raw.parent) as temporary:
            with mounted(device + "p1", Path(temporary)) as boot:
                seed_boot(boot, config)
        return
    entities = plistlib.loads(run("hdiutil", "attach", raw, "-nomount", "-plist",
                                 "-imagekey", "diskimage-class=CRawDiskImage", capture=True))["system-entities"]
    whole = next(item["dev-entry"] for item in entities if re.fullmatch(r"/dev/disk[0-9]+", item.get("dev-entry", "")))
    try:
        partition = whole + "s1"
        run("diskutil", "mount", partition)
        boot = Path(plistlib.loads(run("diskutil", "info", "-plist", partition, capture=True))["MountPoint"])
        seed_boot(boot, config)
    finally:
        run("hdiutil", "detach", whole)


def restore(args):
    manifest = json.loads(args.manifest.read_text())
    config = seed_config(args.user, args.ssh_key.read_text(), args.hostname)
    print("Verifying compressed and uncompressed image checksums", flush=True)
    verify_image(args.image, manifest)
    if args.user in manifest.get("reserved_users", []):
        raise ValueError("Choose a new username; this name belongs to a system account")
    before = disk_info(args.device)
    if before["size"] < manifest["size"]:
        raise ValueError("Target disk is smaller than the image")
    print(f"Target: {args.device}, {before['label']}, {before['size']} bytes")
    if args.dry_run:
        print("Dry run passed; no disk or configuration was changed")
        return
    if os.geteuid():
        raise ValueError("Actual disk restoration requires sudo/root")
    # Linux rejects all mounted targets. macOS allows a mounted removable disk,
    # so also reject inputs/workspace stored on the disk about to be erased.
    work_parent = args.image.resolve().parent
    if platform.system() == "Darwin":
        for path in (args.image, args.manifest, args.ssh_key, Path(__file__)):
            filesystem = run("df", "-P", path.resolve(), capture=True).decode().splitlines()[-1].split()[0]
            if filesystem == args.device or filesystem.startswith(args.device + "s"):
                raise ValueError("Move the image and all restore inputs off the target disk")
    if shutil.disk_usage(work_parent).free < manifest["size"] + 256 * MIB:
        raise ValueError("Not enough space beside the image for a private first-boot copy")
    with tempfile.TemporaryDirectory(prefix=".gateway-restore-", dir=work_parent) as temporary:
        raw = Path(temporary) / "private.img"
        verify_image(args.image, manifest, expanded=raw)
        seed_image(raw, config)
        personalized_hash = digest(raw)
        if input(f"ALL DATA WILL BE ERASED. Type ERASE {args.device}: ") != "ERASE " + args.device:
            raise ValueError("Restoration cancelled")
        if platform.system() == "Darwin":
            run("diskutil", "unmountDisk", args.device)
        if disk_info(args.device) != before:
            raise ValueError("Target disk identity changed; refusing to write")
        copy_image(raw, before["raw"], personalized_hash, compressed=False)
        print("Verifying disk readback including local first-boot settings", flush=True)
        checksum, remaining = hashlib.sha256(), manifest["size"]
        with open(before["raw"], "rb", buffering=0) as stream:
            while remaining:
                block = stream.read(min(CHUNK, remaining))
                if not block:
                    raise OSError("Short disk readback")
                checksum.update(block)
                remaining -= len(block)
        if checksum.hexdigest() != personalized_hash:
            raise ValueError("Disk readback failed; do not boot this disk")
    if platform.system() == "Darwin":
        run("diskutil", "eject", args.device)
    print(f"Restored. Boot the Pi, find its upstream DHCP address, then SSH as {args.user}.")


def main():
    def interrupted(_signal, _frame):
        raise SystemExit("Interrupted; output is incomplete")

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build", help="Build from an official base and explicit binaries on ARM64 Linux")
    for name in ("base", "mihomo", "mosdns", "output"):
        builder.add_argument("--" + name, type=Path, required=True)
    builder.add_argument("--sources", type=Path, default=Path(__file__).with_name("sources.json"))
    builder.add_argument("--size-mib", type=int, default=4096)
    builder.add_argument("--in-namespace", action="store_true", help=argparse.SUPPRESS)
    restorer = commands.add_parser("restore", help="Verify and flash a spare disk; requires typed confirmation")
    for name in ("image", "manifest", "ssh-key"):
        restorer.add_argument("--" + name, type=Path, required=True)
    restorer.add_argument("--device", required=True)
    restorer.add_argument("--user", required=True)
    restorer.add_argument("--hostname", default="rpi-gateway")
    restorer.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        (build if args.command == "build" else restore)(args)
    except (ValueError, OSError, subprocess.CalledProcessError, lzma.LZMAError) as error:
        raise SystemExit(f"ERROR: {error}") from None


if __name__ == "__main__":
    main()
