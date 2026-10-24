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
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
OVERLAY = REPO / "automation" / "sbin" / "mihomo-policy-overlay"
MANIFEST = REPO / "automation" / "policy" / "capabilities.yaml"
ROUTE_DEFAULTS = REPO / "automation" / "policy" / "route-defaults.yaml"

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
    "Package Download",
    "TW Fixed IP",
]

PLAIN_NODES = ["Example | HK 01", "Example | JP 01", "Example | TW 01"]
PROVIDER_NODES = [
    "STT | Bulk 01",
    "ByWave | Bulk 01",
    "BWB | Backup 01",
    "BWB | Backup 02",
    "InfraAPI | Backup 01",
    "GLaDOS | D1 Eligible",
    "GLaDOS | General AI",
    "GLaDOS | X1",
    "GLaDOS | NF",
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


def legacy_manifest(overlay: dict) -> dict:
    """Anonymous pre-route-table schema kept by install_default on upgrades."""
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    manifest.pop("download_routes", None)
    manifest.pop("video_routes", None)
    manifest["overlay"] = copy.deepcopy(overlay)
    manifest["measurement"] = {
        "enabled": False,
        "native_budget": True,
        "accounts": {"synthetic-account": {"prefixes": ["Example | "]}},
    }
    manifest["download_quality"] = {
        "enabled": True,
        "health_interval_seconds": 300,
        "profiles": {
            "github-release": {
                "group": "GitHub Download",
                "sample_url": "https://downloads.example.invalid/release",
                "health_url": "https://downloads.example.invalid/health",
                "health_expected": "404",
                "domains": ["legacy-release.example.invalid"],
            },
            "git-transport": {
                "group": "GitHub Control",
                "sample_url": "https://git.example.invalid/info/refs",
                "health_url": "https://git.example.invalid/health",
                "health_expected": "200",
                "domains": ["legacy-git.example.invalid"],
            },
            "packages": {
                "group": "Package Download",
                "sample_url": "https://packages.example.invalid/archive",
                "health_url": "https://packages.example.invalid/health",
                "health_expected": "200",
                "domains": ["legacy-package.example.invalid"],
            },
        },
    }
    manifest["social_quality"] = {
        "enabled": False,
        "candidate_groups": ["Social"],
        "health_interval_seconds": 60,
        "profiles": {
            "x-video": {
                "group": "Social",
                "sample_url": "https://media.example.invalid/clip",
                "health_url": "https://social.example.invalid/health",
                "health_expected": "200-499",
                "domains": [],
            },
        },
    }
    return manifest


def migrated_default_manifest(overlay: dict) -> dict:
    """A current public manifest has no retired scorer/provider settings."""
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    manifest.pop("download_routes", None)
    manifest.pop("video_routes", None)
    manifest.pop("download_quality", None)
    manifest.pop("social_quality", None)
    manifest.pop("scoring", None)
    manifest["overlay"] = copy.deepcopy(overlay)
    return manifest


def run_overlay(source: Path, manifest: Path, destination: Path) -> subprocess.CompletedProcess:
    environment = dict(
        os.environ,
        MIHOMO_CAPABILITIES=str(manifest),
        MIHOMO_ROUTE_DEFAULTS=str(ROUTE_DEFAULTS),
    )
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


def native_group_nodes(config: dict, name: str) -> set[str]:
    group = next(g for g in config["proxy-groups"] if g["name"] == name)
    if "proxies" in group:
        return set(group["proxies"])
    provider = config["proxy-providers"][group["use"][0]]
    patterns = group["filter"].split("`")
    return {node["name"] for node in provider["payload"]
            if any(re.fullmatch(pattern, node["name"]) for pattern in patterns)}


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
        check(all(g['type'] == 'select' for g in config['proxy-groups']),
              'paused measurement generated an automatic probe group')
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

        # Native routing owns delay selection within the already-authorized
        # download candidates; platform suffixes stay in their media pools.
        native_manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
        native_manifest["overlay"] = copy.deepcopy(MANIFEST_OVERLAY)
        native_manifest["measurement"] = {
            "enabled": True,
            "native_budget": True,
            "native_interval_seconds": 600,
            "accounts": {"synthetic": {
                "prefixes": ["Example | ", "STT | ", "ByWave | ", "BWB | ", "InfraAPI | ", "GLaDOS | "],
                "native_probes_per_hour": 10000,
                "quality_attempts_per_hour": 10000,
            }},
        }
        native_path = directory / "native-capabilities.yaml"
        native_path.write_text(yaml.safe_dump(native_manifest, allow_unicode=True), encoding="utf-8")
        native_file = directory / "native.yaml"
        native_run = run_overlay(source, native_path, native_file)
        check(native_run.returncode == 0, f"native overlay failed: {native_run.stderr.strip()[:400]}")
        if native_run.returncode == 0:
            native = yaml.safe_load(native_file.read_text(encoding="utf-8"))
            check(native.get("unified-delay") is False,
                  "generated native budget config must set unified-delay false")
            groups = {g["name"]: g for g in native["proxy-groups"]}
            authorized = {"STT | Bulk 01", "ByWave | Bulk 01", "GLaDOS | D1 Eligible"}
            for name in ("Download", "GitHub Download", "Model Download", "Package Download"):
                group = groups.get(name)
                if group is None:
                    check(False, f"{name} group is missing")
                    continue
                check(group.get("type") == "url-test",
                      f"{name} is not native latency-selected: {group}")
                check(native_group_nodes(native, name) == authorized,
                      f"{name} escaped its authorized download leaves: {native_group_nodes(native, name)}")
            download_groups = ("Download", "GitHub Download", "Model Download", "Package Download")
            if all(name in groups for name in download_groups):
                check(len({tuple(groups[name].get("use", [])) for name in download_groups}) == 1,
                      "download URLTests do not share one native probe owner")
            check(groups["Auto"].get("type") == "url-test" and groups["Auto"].get("tolerance") == 100,
                  "Auto lost native latency selection or tolerance")
            check(groups["Developer"].get("type") == "url-test" and groups["Developer"].get("tolerance") == 80,
                  "Developer lost native latency selection or tolerance")
            check(groups["Research + AI"].get("type") == "fallback",
                  "AI lost ordered native fallback")
            check(groups["TW Fixed IP"].get("type") == "select" and "url" not in groups["TW Fixed IP"],
                  "TW Fixed IP is not a fixed native selection")
            for name in ("Streaming", "Social"):
                check(groups[name].get("type") == "fallback",
                      f"{name} is not stability-first fallback")
            for suffix in ("cdninstagram.com", "instagr.am", "instagram.com"):
                check(f"DOMAIN-SUFFIX,{suffix},Streaming" in native["rules"],
                      f"Instagram domain {suffix} is not routed to the existing media pool")
            check("DOMAIN-SUFFIX,grok.com,Research + AI" in native["rules"]
                  and "DOMAIN-SUFFIX,x.ai,Research + AI" in native["rules"],
                  "Grok/X.ai left the AI pool")

        # ---- old private manifest upgrade ----------------------------------
        # Existing installs keep this file unchanged. Missing route tables must
        # therefore inherit public defaults and the old profile domains.
        legacy = legacy_manifest(MANIFEST_OVERLAY)
        legacy_path = directory / "legacy-capabilities.yaml"
        legacy_path.write_text(yaml.safe_dump(legacy, allow_unicode=True), encoding="utf-8")
        legacy_output = directory / "legacy-overlay.yaml"
        legacy_run = run_overlay(source, legacy_path, legacy_output)
        check(legacy_run.returncode == 0,
              f"legacy manifest overlay failed: {legacy_run.stderr.strip()[:400]}")
        if legacy_run.returncode == 0:
            upgraded = yaml.safe_load(legacy_output.read_text(encoding="utf-8"))
            expected_routes = {
                "DOMAIN,legacy-release.example.invalid,GitHub Download",
                "DOMAIN,legacy-git.example.invalid,GitHub Control",
                "DOMAIN,legacy-package.example.invalid,Package Download",
                "DOMAIN,registry.npmjs.org,Package Download",
                "DOMAIN-SUFFIX,youtube.com,Streaming",
                "DOMAIN-SUFFIX,instagram.com,Streaming",
                "DOMAIN-SUFFIX,x.com,Social",
            }
            check(expected_routes.issubset(set(upgraded["rules"])),
                  f"legacy schema lost migrated/default routes: {expected_routes - set(upgraded['rules'])}")
            check(group_proxies(upgraded, "Streaming") == MANIFEST_OVERLAY["media_nodes"],
                  "legacy route migration changed the private media pool")
            check(group_proxies(upgraded, "Research + AI")[:1] == ["Example | JP 01"],
                  "legacy route migration changed the private AI preference")
            check(yaml.safe_load(legacy_path.read_text(encoding="utf-8")) == legacy,
                  "overlay rewrote legacy roles, overlay, account or profile data")

        # YAML blank values parse as null; treat an intentionally empty whole
        # route table like an omitted table, while entries with empty domains
        # remain invalid below.
        empty_tables = copy.deepcopy(legacy)
        empty_tables["download_routes"] = None
        empty_tables["video_routes"] = None
        empty_tables_path = directory / "empty-route-tables-capabilities.yaml"
        empty_tables_path.write_text(yaml.safe_dump(empty_tables, allow_unicode=True), encoding="utf-8")
        empty_tables_output = directory / "empty-route-tables-overlay.yaml"
        empty_tables_run = run_overlay(source, empty_tables_path, empty_tables_output)
        check(empty_tables_run.returncode == 0,
              f"null route tables failed to inherit defaults: {empty_tables_run.stderr.strip()[:400]}")
        if empty_tables_run.returncode == 0:
            empty_tables_rules = set(yaml.safe_load(empty_tables_output.read_text(encoding="utf-8"))["rules"])
            check("DOMAIN-SUFFIX,instagram.com,Streaming" in empty_tables_rules
                  and "DOMAIN,legacy-package.example.invalid,Package Download" in empty_tables_rules,
                  "null route tables silently removed public or migrated routes")

        # Explicit new entries override a legacy profile with the same ID;
        # unrelated missing entries still inherit defaults and legacy routes.
        explicit = copy.deepcopy(legacy)
        explicit["download_routes"] = {
            "packages": {
                "group": "Package Download",
                "domains": ["explicit-package.example.invalid"],
            },
        }
        explicit_path = directory / "explicit-capabilities.yaml"
        explicit_path.write_text(yaml.safe_dump(explicit, allow_unicode=True), encoding="utf-8")
        explicit_output = directory / "explicit-overlay.yaml"
        explicit_run = run_overlay(source, explicit_path, explicit_output)
        check(explicit_run.returncode == 0,
              f"explicit route table overlay failed: {explicit_run.stderr.strip()[:400]}")
        if explicit_run.returncode == 0:
            explicit_rules = set(yaml.safe_load(explicit_output.read_text(encoding="utf-8"))["rules"])
            check("DOMAIN,explicit-package.example.invalid,Package Download" in explicit_rules,
                  "explicit download route did not take precedence")
            check("DOMAIN,legacy-package.example.invalid,Package Download" not in explicit_rules,
                  "overridden legacy profile domain remained active")
            check("DOMAIN,legacy-git.example.invalid,GitHub Control" in explicit_rules,
                  "partial explicit table discarded a missing legacy route")
            check("DOMAIN-SUFFIX,instagram.com,Streaming" in explicit_rules,
                  "partial explicit table discarded packaged video defaults")

        # Existing private profiles and explicit routes can target either of
        # the already-authorized general/model download pools.
        for destination in ("Download", "Model Download"):
            for schema in ("legacy", "explicit"):
                retained = copy.deepcopy(legacy)
                domain = f"{schema}-{destination.lower().replace(' ', '-')}.example.invalid"
                route = {"group": destination, "domains": [domain]}
                if schema == "legacy":
                    retained["download_quality"]["profiles"]["packages"].update(route)
                else:
                    retained["download_routes"] = {"packages": route}
                retained_path = directory / f"retained-{schema}-{destination}.yaml"
                retained_path.write_text(yaml.safe_dump(retained, allow_unicode=True), encoding="utf-8")
                retained_output = directory / f"retained-{schema}-{destination}-output.yaml"
                retained_run = run_overlay(source, retained_path, retained_output)
                check(retained_run.returncode == 0,
                      f"{schema} route to {destination} rejected: {retained_run.stderr.strip()[:400]}")
                if retained_run.returncode == 0:
                    retained_config = yaml.safe_load(retained_output.read_text(encoding="utf-8"))
                    check(f"DOMAIN,{domain},{destination}" in retained_config["rules"],
                          f"{schema} route destination changed: {destination}")
                    check(native_group_nodes(retained_config, destination)
                          == {"STT | Bulk 01", "ByWave | Bulk 01", "GLaDOS | D1 Eligible"},
                          f"{schema} destination expanded authorized leaves: {destination}")

        # Empty tables inherit defaults; an explicit empty required route is
        # invalid so a typo cannot silently disable required business routing.
        empty_route = copy.deepcopy(explicit)
        empty_route["download_routes"] = {"packages": {"group": "Package Download", "domains": []}}
        empty_route_path = directory / "empty-route-capabilities.yaml"
        empty_route_path.write_text(yaml.safe_dump(empty_route, allow_unicode=True), encoding="utf-8")
        empty_route_output = directory / "empty-route-overlay.yaml"
        empty_route_run = run_overlay(source, empty_route_path, empty_route_output)
        check(empty_route_run.returncode != 0,
              "explicit empty route silently disabled a required business route")
        check("domains" in empty_route_run.stderr.lower(),
              f"empty route did not report an actionable validation error: {empty_route_run.stderr.strip()[:200]}")
        check(not empty_route_output.exists(), "invalid route table still wrote an output file")

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
