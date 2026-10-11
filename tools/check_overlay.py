#!/usr/bin/python3

"""Offline behaviour test for the published policy overlay.

Runs `automation/sbin/mihomo-policy-overlay` against a synthetic Mihomo
configuration and the published capability manifest, so the refactored script is
verified without a gateway, a subscription or node credentials.  It checks that

* curated pools take their membership and order from the deployment manifest,
* provider-prefixed nodes never leak into the media/AI pools,
* bulk pools keep only their own provider's nodes,
* an empty manifest fails closed instead of publishing empty groups,
* repeated runs are byte-identical.

The fixture values are fictional: they exist only to describe the shape of a
real subscription.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
OVERLAY = REPO / "automation" / "sbin" / "mihomo-policy-overlay"
MANIFEST = REPO / "automation" / "policy" / "capabilities.yaml"

GROUPS = [
    "Auto",
    "Developer",
    "Streaming",
    "Social",
    "Telegram",
    "Research + AI",
    "Download",
    "STT Download",
    "ByWave Download",
    "STT GitHub Download",
    "ByWave GitHub Download",
    "GitHub Control",
    "GitHub Download",
    "Model Download",
    "TW Fixed IP",
]

PLAIN_NODES = ["Example | HK 01", "Example | JP 01", "Example | TW 01"]
PROVIDER_NODES = [
    "STT | Bulk 01",
    "ByWave | Bulk 01",
    "BWB | Backup 01",
    "BWB | Backup 02",
    "InfraAPI | Backup 01",
]
ALL_NODES = [*PLAIN_NODES, *PROVIDER_NODES]

MANIFEST_OVERLAY = {
    "media_nodes": ["Example | HK 01", "Example | JP 01"],
    "ai_nodes": ["Example | JP 01"],
    "auto_nodes": ["Example | HK 01"],
    "developer_nodes": ["Example | TW 01"],
    "tw_fixed_nodes": ["Example | TW 01"],
}

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def build_fixture(directory: Path, overlay: dict, nodes: list[str]) -> tuple[Path, Path]:
    config = {
        "mixed-port": 7890,
        "proxies": [
            {"name": name, "type": "ss", "server": "198.51.100.1", "port": 443}
            for name in nodes
        ],
        "proxy-groups": [
            {"name": name, "type": "select", "proxies": ["DIRECT", *nodes]}
            for name in GROUPS
        ],
        "rules": ["MATCH,Auto"],
    }
    source = directory / "source.yaml"
    source.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    manifest["overlay"] = copy.deepcopy(overlay)
    manifest_path = directory / "capabilities.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
    return source, manifest_path


def run_overlay(source: Path, manifest: Path, destination: Path) -> subprocess.CompletedProcess:
    environment = dict(os.environ, MIHOMO_CAPABILITIES=str(manifest))
    return subprocess.run(
        [sys.executable, str(OVERLAY), str(source), str(destination)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def group_proxies(config: dict, name: str) -> list[str]:
    for group in config.get("proxy-groups", []):
        if group.get("name") == name:
            return list(group.get("proxies", []))
    return []


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)

        # ---- curated manifest -------------------------------------------------
        source, manifest = build_fixture(directory, MANIFEST_OVERLAY, ALL_NODES)
        first = directory / "overlay-1.yaml"
        result = run_overlay(source, manifest, first)
        check(result.returncode == 0, f"overlay failed: {result.stderr.strip()[:400]}")
        if result.returncode != 0:
            print("\n".join(FAILURES))
            return 1

        config = yaml.safe_load(first.read_text(encoding="utf-8"))
        check(
            group_proxies(config, "Streaming") == MANIFEST_OVERLAY["media_nodes"],
            f"Streaming pool is not the manifest order: {group_proxies(config, 'Streaming')}",
        )
        for name in ("Social", "Telegram"):
            check(
                group_proxies(config, name) == MANIFEST_OVERLAY["media_nodes"],
                f"{name} pool is not the manifest order: {group_proxies(config, name)}",
            )
        check(
            group_proxies(config, "TW Fixed IP") == MANIFEST_OVERLAY["tw_fixed_nodes"],
            f"TW Fixed IP pool: {group_proxies(config, 'TW Fixed IP')}",
        )
        ai = group_proxies(config, "Research + AI")
        check(ai[0] == "Example | JP 01", f"AI pool preference order lost: {ai}")
        check(
            not [name for name in ai if name.split(" | ")[0] in {"STT", "ByWave", "BWB", "InfraAPI"}],
            f"provider nodes leaked into the AI pool: {ai}",
        )
        auto = group_proxies(config, "Auto")
        check(auto[0] == "Example | HK 01", f"Auto pool preference order lost: {auto}")
        check(
            {"BWB | Backup 01", "BWB | Backup 02", "InfraAPI | Backup 01"}.issubset(set(auto)),
            f"backup providers are missing from Auto: {auto}",
        )
        for pool, prefix in (("STT Download", "STT | "), ("ByWave Download", "ByWave | ")):
            members = group_proxies(config, pool)
            check(bool(members), f"{pool} is empty")
            check(
                all(name.startswith(prefix) for name in members),
                f"{pool} contains foreign nodes: {members}",
            )
        check(
            "cache-file" not in config or config["cache-file"] is not None,
            "cache-file handling changed",
        )

        # ---- deterministic ----------------------------------------------------
        second = directory / "overlay-2.yaml"
        rerun = run_overlay(source, manifest, second)
        check(rerun.returncode == 0, f"second run failed: {rerun.stderr.strip()[:400]}")
        check(
            first.read_bytes() == second.read_bytes(),
            "two runs produced different output",
        )

        # ---- empty manifest fails closed -------------------------------------
        empty_source, empty_manifest = build_fixture(directory, {}, PLAIN_NODES)
        empty_result = run_overlay(empty_source, empty_manifest, directory / "overlay-empty.yaml")
        check(
            empty_result.returncode != 0,
            "empty manifest published a config instead of failing closed",
        )
        check(
            "no usable subscription nodes" in empty_result.stderr,
            f"fail-closed message changed: {empty_result.stderr.strip()[:200]}",
        )
        check(
            not (directory / "overlay-empty.yaml").exists(),
            "failed run still wrote an output file",
        )

    if FAILURES:
        print("policy overlay checks FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("policy overlay checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
