#!/usr/bin/env python3
"""Detect policy-overlay drift between the repository, its release manifest and
a live gateway copy.

The 2026-10-31 incident was a hand-edited overlay on the box: it changed the
`GitHub Control` group from `fallback` to `url-test` and removed the media
configure call.  It matched no published release, so the next install would
silently overwrite it and the next subscription update would generate different
policy from the one that was running.

This check has two layers:

* always: the repository script, the manifest entry and the generated policy
  agree (GitHub Control stays a `fallback` group without a tolerance);
* `--live PATH`: the file on the gateway equals the manifest.  Stage 3 runs this
  before and after the recorded revert; CI runs the repository layer only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import yaml
from check_overlay import ALL_NODES, MANIFEST_OVERLAY, build_fixture, run_overlay

REPO = Path(__file__).resolve().parents[1]
TARGET = "/usr/local/sbin/mihomo-policy-overlay"
SOURCE = REPO / "automation/sbin/mihomo-policy-overlay"
MANIFEST = REPO / "automation/release-manifest.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", type=Path, help="gateway copy to compare with the manifest")
    parser.add_argument("--backup", type=Path, help="kept pre-hotfix copy to compare as evidence")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    entry = next((item for item in manifest["files"] if item["target"] == TARGET), None)
    if entry is None:
        print(f"drift: {TARGET} is not in the release manifest", file=sys.stderr)
        return 1

    expected = entry["sha256"]
    actual = digest(SOURCE)
    if actual != expected:
        print(f"drift: repository overlay {actual[:12]} != manifest {expected[:12]}", file=sys.stderr)
        return 1

    # The hotfix was visible in generated policy, not only in the file hash.
    with tempfile.TemporaryDirectory(prefix="overlay-drift-") as raw:
        directory = Path(raw)
        source, capabilities = build_fixture(directory, MANIFEST_OVERLAY, ALL_NODES)
        # Enable measurement so health-strategy groups keep their type instead of
        # being downgraded to select; the hotfix removed media.configure, which is
        # what turns Streaming and Research + AI into fallback groups.
        profile = yaml.safe_load(capabilities.read_text())
        profile["measurement"] = {
            "enabled": True, "native_budget": True, "native_interval_seconds": 900,
            "accounts": {"fixture": {"names": ALL_NODES, "native_probes_per_hour": 10000,
                                     "quality_attempts_per_hour": 600}},
        }
        capabilities.write_text(yaml.safe_dump(profile))
        destination = directory / "config.yaml"
        result = run_overlay(source, capabilities, destination)
        if result.returncode != 0:
            print(f"drift: overlay failed on the fixture: {result.stderr.strip()}", file=sys.stderr)
            return 1
        config = yaml.safe_load(destination.read_text())
    group = next((item for item in config["proxy-groups"] if item["name"] == "GitHub Control"), None)
    if not group or group.get("type") != "fallback" or "tolerance" in group:
        print(f"drift: GitHub Control generated as {group!r}, expected a fallback without tolerance",
              file=sys.stderr)
        return 1
    media = next((item for item in config["proxy-groups"] if item["name"] == "Streaming"), None)
    if not media or media.get("type") != "fallback":
        print(f"drift: Streaming generated as {media!r}; media.configure is missing or changed",
              file=sys.stderr)
        return 1

    for label, path in (("live", args.live), ("backup", args.backup)):
        if path is None:
            continue
        if not path.is_file():
            print(f"drift: {label} copy {path} does not exist", file=sys.stderr)
            return 1
        found = digest(path)
        if found != expected:
            print(f"drift: {label} overlay {found[:12]} != manifest {expected[:12]}", file=sys.stderr)
            return 1

    suffix = ""
    if args.live:
        suffix = " and the live copy matches"
    print(f"overlay drift check passed: repository and manifest agree ({expected[:12]}){suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
