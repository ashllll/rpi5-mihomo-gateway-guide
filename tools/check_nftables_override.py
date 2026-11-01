#!/usr/bin/env python3
"""nftables.service must destroy only this repo's tables, never flush ruleset.

F-20/DR-04: the recorded gateway carries an undeclared drop-in that replaces
Debian's `ExecStop=/usr/sbin/nft flush ruleset`.  Without it, stopping or
upgrading nftables wipes the runtime-owned `inet mihomo` and `inet dns_bypass`
tables.  The drop-in must be in the repository and in the release manifest.

Run with `--live` on the gateway to compare the loaded ExecStop as well.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DROPIN = ROOT / "automation/systemd/nftables.service.d/override.conf"
MANIFEST = ROOT / "automation/release-manifest.json"
SOURCE = "systemd/nftables.service.d/override.conf"
TARGET = "/etc/systemd/system/nftables.service.d/override.conf"
VENDOR = """[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.conf
ExecReload=/usr/sbin/nft -f /etc/nftables.conf
ExecStop=/usr/sbin/nft flush ruleset
"""
EXPECTED = [
    "/usr/sbin/nft destroy table inet rp_filter",
    "/usr/sbin/nft destroy table ip rp_nat",
]


def effective_execstop(vendor: str, dropin: str) -> list[str]:
    """Minimal systemd drop-in merge: an empty ExecStop resets the list."""
    commands: list[str] = []
    for text in (vendor, dropin):
        section = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section != "Service" or not line.startswith("ExecStop="):
                continue
            value = line.split("=", 1)[1].strip()
            if not value:
                commands = []
            else:
                commands.append(value)
    return commands


def check_dropin(path: Path) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"missing drop-in: {path}"]
    text = path.read_text(encoding="utf-8")
    if "[Service]" not in text:
        problems.append("drop-in has no [Service] section")
    commands = effective_execstop(VENDOR, text)
    if commands != EXPECTED:
        problems.append(f"effective ExecStop is {commands!r}, expected {EXPECTED!r}")
    if any("flush ruleset" in command for command in commands):
        problems.append("effective ExecStop still flushes the whole ruleset")
    if "ExecStop=" not in text:
        problems.append("drop-in does not reset the vendor ExecStop list")
    return problems


def check_manifest() -> list[str]:
    problems: list[str] = []
    if not MANIFEST.is_file():
        return ["missing automation/release-manifest.json"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [entry for entry in manifest.get("files", []) if entry.get("source") == SOURCE]
    if len(entries) != 1:
        return [f"manifest has {len(entries)} entries for {SOURCE}, expected 1"]
    entry = entries[0]
    if entry.get("target") != TARGET:
        problems.append(f"manifest target is {entry.get('target')!r}, expected {TARGET!r}")
    digest = hashlib.sha256(DROPIN.read_bytes()).hexdigest()
    if entry.get("sha256") != digest:
        problems.append("manifest sha256 does not match the drop-in bytes")
    if entry.get("mode") != 0o644:
        problems.append(f"manifest mode is {entry.get('mode')!r}, expected 420 (0644)")
    return problems


def check_live() -> list[str]:
    if not shutil.which("systemctl"):
        return ["--live requested but systemctl is not available"]
    result = subprocess.run(["systemctl", "show", "nftables.service", "-p", "ExecStop", "--value"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return [f"systemctl show failed: {result.stderr.strip()}"]
    loaded = result.stdout
    if "flush ruleset" in loaded:
        return ["loaded nftables.service ExecStop still contains flush ruleset"]
    missing = [command for command in EXPECTED if command.split("/usr/sbin/nft ", 1)[-1] not in loaded]
    if missing:
        return [f"loaded ExecStop is missing: {missing}"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dropin", type=Path, default=DROPIN)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    problems = check_dropin(args.dropin)
    if args.dropin == DROPIN:
        problems += check_manifest()
    if args.live:
        problems += check_live()
    if problems:
        print("nftables override: FAIL")
        for problem in problems:
            print("  - " + problem)
        return 1
    print("nftables override: ok (drop-in, manifest, effective ExecStop)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
