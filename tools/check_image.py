#!/usr/bin/env python3
"""Exercise recovery checks and rejected disk writes without accessing real disks."""
import base64
import hashlib
import importlib.util
import json
import lzma
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gateway_image", ROOT / "image/gateway_image.py")
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)


def rejected(call):
    try:
        call()
    except (ValueError, OSError, lzma.LZMAError):
        return
    raise AssertionError("Unsafe input was accepted")


def core_stub(path, version_line, protocol_line):
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  -v) echo '" + version_line + "'; exit 0 ;;\n"
        "  -measurement-budget-protocol) echo '" + protocol_line + "'; exit 0 ;;\n"
        "esac\n"
        "exit 1\n"
    )
    path.chmod(0o755)


def main():
    sources = json.loads((ROOT / "image/sources.json").read_text())
    upstream = json.loads((ROOT / "core/upstream.json").read_text())
    assert sources["mihomo"]["version"] == "v1.19.28-budget-status-contract-v3"
    assert sources["mihomo"]["protocol"] == upstream["protocol"] == "mihomo-budget-v1"
    assert sources["mihomo"]["patch"] == "core/mihomo-v1.19.28-budget.patch"
    with tempfile.TemporaryDirectory() as temporary:
        core = Path(temporary) / "mihomo"
        core_stub(core, "mihomo v1.19.28-budget-status-contract-v3 with_gvisor no_tailscale",
                  "mihomo-budget-v1")
        image.verify_core(core, sources["mihomo"])
        for version_line, protocol_line in (
            ("mihomo v1.19.28-budget-status-contract-v3 with_gvisor", "mihomo-budget-v1"),
            ("mihomo v1.19.28 with_gvisor no_tailscale", "mihomo-budget-v1"),
            ("mihomo v1.19.28-budget-status-contract-v3 with_gvisor no_tailscale", "upstream"),
        ):
            core_stub(core, version_line, protocol_line)
            rejected(lambda: image.verify_core(core, sources["mihomo"]))

    key_parts = [b"ssh-ed25519", bytes(range(32))]
    key = "ssh-ed25519 " + base64.b64encode(b"".join(struct.pack(">I", len(p)) + p for p in key_parts)).decode()
    config = image.seed_config("gateway", key, "gateway-test")
    assert config["users"][0]["ssh_authorized_keys"] == [key]
    assert config["ssh_pwauth"] is False and config["disable_root"] is True
    assert config["users"][0]["lock_passwd"] is True
    for user, public_key, hostname in (("root", key, "gateway"), ("bad/name", key, "gateway"),
                                       ("gateway", "PRIVATE DATA", "gateway"),
                                       ("gateway", "ssh-ed25519 AAAA", "gateway"),
                                       ("gateway", key + "\n" + key, "gateway"),
                                       ("gateway", key, "bad/hostname")):
        rejected(lambda: image.seed_config(user, public_key, hostname))
    size = 2048 * image.MIB
    header = image.mbr(size, 12345)
    image.check_mbr(header, size)
    for offset in (450, 466, 454, 470, 510):
        corrupt = bytearray(header)
        corrupt[offset] ^= 1
        rejected(lambda: image.check_mbr(bytes(corrupt), size))
    rejected(lambda: image.mbr(1024, 12345))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        boot = root / "boot"
        boot.mkdir()
        image.seed_boot(boot, config)
        assert json.loads((boot / "user-data").read_text().split("\n", 1)[1]) == config
        first_id = (boot / "meta-data").read_text()
        image.seed_boot(boot, config)
        assert first_id != (boot / "meta-data").read_text()
        network = json.loads((boot / "network-config").read_text())
        assert network == {"config": "disabled"}
        # Test the real compressed stream and hash path; disk writes use a regular
        # temporary file only. Layout is independently checked above and on Pi.
        content = header + b"synthetic recovery data\n" * 300
        archive = root / "fixture.img.xz"
        archive.write_bytes(lzma.compress(content))
        manifest = {"format": 1, "name": archive.name, "size": len(content),
                    "sha256": image.digest(archive), "raw_sha256": hashlib.sha256(content).hexdigest()}
        with patch.object(image, "check_mbr") as layout_check:
            image.verify_image(archive, manifest)
            layout_check.assert_called_once_with(header, len(content))
            rejected(lambda: image.verify_image(archive, {**manifest, "raw_sha256": "0" * 64}))
            rejected(lambda: image.verify_image(archive, {**manifest, "size": len(content) - 1}))
        rejected(lambda: image.verify_image(archive, {**manifest, "sha256": "0" * 64}))
        rejected(lambda: image.verify_image(archive, {**manifest, "format": 99}))
        rejected(lambda: image.verify_image(archive, {**manifest, "sha256": []}))
        rejected(lambda: image.verify_image(archive, []))
        destination = root / "fake-disk"
        image.copy_image(archive, destination, manifest["raw_sha256"])
        assert destination.read_bytes() == content
        # A dry run must never copy bytes, seed a boot volume or request unmount.
        manifest_file, key_file = root / "manifest.json", root / "key.pub"
        manifest_file.write_text(json.dumps(manifest))
        key_file.write_text(key)
        args = SimpleNamespace(manifest=manifest_file, ssh_key=key_file, user="gateway",
                               hostname="gateway", image=archive, device="/dev/disk99", dry_run=True)
        with patch.object(image, "verify_image"), patch.object(image, "disk_info", return_value={
                "size": size, "label": "test", "raw": str(destination), "identity": "test"}), \
                patch.object(image, "copy_image") as copy, patch.object(image, "seed_boot") as seed, \
                patch.object(image, "run") as command:
            image.restore(args)
            assert not copy.called and not seed.called and not command.called
        args.dry_run = False
        with patch.object(image.platform, "system", return_value="Linux"), \
                patch.object(image, "verify_image"), patch.object(image.os, "geteuid", return_value=0), \
                patch.object(image, "disk_info", return_value={"size": size, "label": "test", "raw": str(destination), "identity": "test"}), \
                patch("builtins.input", return_value="cancel"), patch.object(image, "copy_image") as copy, \
                patch.object(image, "digest", return_value="0" * 64), patch.object(image, "seed_image") as seed:
            rejected(lambda: image.restore(args))
            assert not copy.called and seed.called
        with patch.object(image, "verify_image"), patch.object(image, "disk_info", return_value={"size": 1}), \
                patch.object(image, "copy_image") as copy:
            rejected(lambda: image.restore(args))
            assert not copy.called
        with patch.object(image.platform, "system", return_value="Darwin"):
            info = {"Whole": True, "Internal": False, "VirtualOrPhysical": "Physical", "TotalSize": size}
            def mac_run(*args, **kwargs):
                return plistlib.dumps({"WholeDisks": ["disk99"]} if args[1] == "list" else info)
            with patch.object(image, "run", side_effect=mac_run):
                assert image.disk_info("/dev/disk99")["size"] == size
                rejected(lambda: image.disk_info("/dev/disk99s1"))
            for bad in ({"Internal": True}, {"Whole": False}, {"VirtualOrPhysical": "Virtual"}, {"Writable": False}):
                def bad_mac_run(*args, **kwargs):
                    return plistlib.dumps({"WholeDisks": ["disk99"]} if args[1] == "list" else {**info, **bad})
                with patch.object(image, "run", side_effect=bad_mac_run):
                    rejected(lambda: image.disk_info("/dev/disk99"))
            info = {"WholeDisk": True, "Internal": False, "VirtualOrPhysical": "Unknown", "TotalSize": size}
            with patch.object(image, "run", side_effect=mac_run):
                assert image.disk_info("/dev/disk99")["size"] == size
            with patch.object(image, "run", side_effect=lambda *a,**k: plistlib.dumps({"WholeDisks": []} if a[1] == "list" else info)):
                rejected(lambda: image.disk_info("/dev/disk99"))
        linux = {"path": "/dev/test", "type": "disk", "size": size, "ro": False,
                 "mountpoints": [None], "children": [{"path": "/dev/test1", "mountpoints": [None]}]}
        with patch.object(image.platform, "system", return_value="Linux"), \
                patch.object(Path, "is_block_device", return_value=True):
            with patch.object(image, "run", return_value=json.dumps({"blockdevices": [linux]}).encode()) as listing:
                assert image.disk_info("/dev/test")["size"] == size
                assert "--tree" in listing.call_args.args
            for bad in ({"type": "part"}, {"ro": True}, {"mountpoints": ["/"]},
                        {"children": [{"mountpoints": ["[SWAP]"]}]},
                        {"children": [{"type": "crypt", "mountpoints": [None]}]}):
                with patch.object(image, "run", return_value=json.dumps({"blockdevices": [{**linux, **bad}]}).encode()):
                    rejected(lambda: image.disk_info("/dev/test"))
        # Sanitization is exercised on an artificial root, including backup
        # shadow files, personal users, memberships, keys and connection state.
        clean = root / "clean-root"
        fixtures = {"etc/passwd": "root:x:0:0:root:/root:/bin/bash\nfixture:x:1000:1000::/home/fixture:/bin/bash\n",
                    "etc/shadow": "root:secret:1:0:99999:7:::\nfixture:secret:1:0:99999:7:::\n",
                    "etc/group": "root:x:0:\nsudo:x:27:fixture\nfixture:x:1000:\n",
                    "etc/gshadow": "root:!::\nsudo:!::fixture\nfixture:!::\n",
                    "etc/shadow-": "secret", "root/.history": "secret",
                    "etc/ssh/ssh_host_test_key": "secret", "var/lib/cloud/private": "secret",
                    "etc/NetworkManager/system-connections/private": "secret"}
        for name, data in fixtures.items():
            image.write(clean / name, data)
        image.clean_root(clean)
        assert "fixture" not in (clean / "etc/passwd").read_text()
        assert "fixture" not in (clean / "etc/group").read_text()
        assert "secret" not in (clean / "etc/shadow").read_text()
        assert not (clean / "etc/shadow-").exists()
        assert not list((clean / "etc/ssh").iterdir())
        assert not list((clean / "var/lib/cloud").iterdir())
        assert (clean / "etc/machine-id").read_text() == ""
    print("Recovery stream, first-boot seed, sanitization and disk refusal checks passed")


if __name__ == "__main__":
    main()
