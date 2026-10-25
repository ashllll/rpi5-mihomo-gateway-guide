#!/usr/bin/python3

"""Assert the published stability policy actually re-evaluates nodes.

A generated group only leaves a dead node when *something* probes it.  In this
repository the probes have two possible owners:

* native measurement mode (the production setting): the policy overlay folds
  every automatic group into a shared ``measurement-*`` inline provider whose
  ``health-check`` carries url/expected-status/interval; groups only reference
  that owner.  Effective detection latency is
  ``measurement.native_interval_seconds``.
* without native mode: the measurement policy demotes automatic groups to
  ``select`` (they never switch on their own), which this check reports
  explicitly instead of pretending probing exists.

Either way the published manifest must not be able to produce a group that is
silently pinned to a dead node.  The check reads the manifest, runs the overlay
in both modes against a synthetic subscription and asserts

* ``stability.max_interval`` is a sane ceiling and native probing respects it,
* the AI group keeps its own OpenAI probe (401) instead of a generic 204,
* in native mode every automatic group references one measurement owner whose
  health-check has url, expected-status and an interval within the ceiling,
* without native mode no automatic group survives (documented behaviour),
* every tuned group name exists in the output,
* repeated overlay runs are byte-identical.

The fixture values are fictional: they only describe the shape of a real
subscription.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "automation" / "policy" / "capabilities.yaml"
OVERLAY_CHECK = REPO / "tools" / "check_overlay.py"

# Reuse the overlay behaviour fixture instead of duplicating it.
spec = importlib.util.spec_from_file_location("check_overlay", OVERLAY_CHECK)
overlay_checks = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(overlay_checks)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def check_manifest(manifest: dict) -> int:
    stability = manifest.get("stability")
    check(isinstance(stability, dict), "stability section is missing")
    if not isinstance(stability, dict):
        return 0

    ceiling = stability.get("max_interval")
    check(
        isinstance(ceiling, int) and not isinstance(ceiling, bool) and 1 <= ceiling <= 600,
        f"stability.max_interval must be an integer in 1..600, got {ceiling!r}",
    )
    if not isinstance(ceiling, int):
        return 0

    groups = stability.get("groups") or {}
    check(isinstance(groups, dict) and groups, "stability.groups is empty")
    for name, options in groups.items():
        check(isinstance(options, dict), f"{name}: options must be a mapping")
        if not isinstance(options, dict):
            continue
        interval = options.get("interval")
        check(
            interval is None
            or (isinstance(interval, int) and not isinstance(interval, bool) and interval <= ceiling),
            f"{name}: interval {interval!r} exceeds ceiling {ceiling}",
        )
        failed = options.get("max-failed-times", 1)
        check(
            isinstance(failed, int) and 1 <= failed <= 5,
            f"{name}: max-failed-times {failed!r} outside 1..5",
        )
        check(bool(options.get("timeout")), f"{name}: timeout is required")

    ai = groups.get("Research + AI") or {}
    check(
        str(ai.get("url", "")).startswith("https://api.openai.com/"),
        "Research + AI must probe its own OpenAI endpoint",
    )
    check(
        str(ai.get("expected-status", "")) == "401",
        "Research + AI must expect 401 from the OpenAI endpoint",
    )

    measurement = manifest.get("measurement") or {}
    native = measurement.get("native_interval_seconds")
    if isinstance(native, int) and not isinstance(native, bool):
        check(
            1 <= native <= ceiling,
            f"measurement.native_interval_seconds {native} exceeds stability ceiling {ceiling}",
        )
    return ceiling


def run_mode(manifest: dict, directory: Path, native: bool, ceiling: int) -> None:
    source, manifest_path = overlay_checks.build_fixture(
        directory, copy.deepcopy(overlay_checks.MANIFEST_OVERLAY), overlay_checks.ALL_NODES
    )
    prepared = copy.deepcopy(manifest)
    prepared["overlay"] = copy.deepcopy(overlay_checks.MANIFEST_OVERLAY)
    if native:
        prepared["measurement"] = {
            "enabled": True,
            "native_budget": True,
            "native_interval_seconds": prepared.get("measurement", {}).get(
                "native_interval_seconds", 180
            ),
            # One account per provider family, so every fixture node resolves
            # to exactly one account (nodes without one fail closed).
            "accounts": {
                f"synthetic-{index}": {
                    "prefixes": [prefix],
                    "native_probes_per_hour": 100000,
                    "quality_attempts_per_hour": 100000,
                }
                for index, prefix in enumerate(
                    ["Example | ", "STT | ", "ByWave | ", "BWB | ", "InfraAPI | ", "GLaDOS | "]
                )
            },
        }
    manifest_path.write_text(yaml.safe_dump(prepared, allow_unicode=True), encoding="utf-8")

    first = directory / f"generated-{'native' if native else 'plain'}-1.yaml"
    second = directory / f"generated-{'native' if native else 'plain'}-2.yaml"
    for destination in (first, second):
        result = overlay_checks.run_overlay(source, manifest_path, destination)
        check(
            result.returncode == 0,
            f"overlay failed ({'native' if native else 'plain'}): "
            f"{(result.stderr or result.stdout).strip()[:300]}",
        )
        check(destination.exists(), "overlay published no configuration")
    if not first.exists():
        return
    check(
        first.read_bytes() == second.read_bytes(),
        f"overlay output is not byte-identical across runs ({'native' if native else 'plain'})",
    )

    config = yaml.safe_load(first.read_text(encoding="utf-8"))
    groups = [g for g in config.get("proxy-groups") or [] if isinstance(g, dict)]
    providers = config.get("proxy-providers") or {}
    tuned = set((manifest["stability"].get("groups") or {}).keys())
    generated = {g.get("name") for g in groups}
    check(tuned <= generated, f"tuned groups missing from output: {sorted(tuned - generated)}")

    automatic = [g for g in groups if g.get("type") != "select"]
    if not native:
        check(
            not automatic,
            "without native measurement the policy must not leave auto-switching groups "
            f"(found {[g['name'] for g in automatic]})",
        )
        return

    check(bool(automatic), "native mode produced no automatic groups")
    for group in automatic:
        name = group.get("name")
        owners = [use for use in group.get("use", []) if use in providers]
        check(
            len(owners) == 1,
            f"{name}: automatic group must reference exactly one measurement owner, "
            f"got {group.get('use')!r}",
        )
        if len(owners) != 1:
            continue
        check(
            str(owners[0]).startswith("measurement-"),
            f"{name}: owner {owners[0]!r} is not a generated measurement provider",
        )
        health = providers[owners[0]].get("health-check") or {}
        check(health.get("enable") is True, f"{name}: owner health-check is disabled")
        check(
            group.get("url") == health.get("url"),
            f"{name}: group must keep the check URL; the group scheduler is the probe source",
        )
        check(
            isinstance(group.get("interval"), int) and group["interval"] <= ceiling,
            f"{name}: group probe interval must stay within the ceiling",
        )
        cadence = prepared["measurement"].get("native_interval_seconds", ceiling)
        check(
            group["interval"] == min(cadence, ceiling),
            f"{name}: group probe interval {group['interval']} must equal the "
            f"{min(cadence, ceiling)}s native cadence",
        )
        if providers[owners[0]].get("type") == "inline":
            check(
                health.get("enable") is True and health.get("lazy") is False,
                f"{name}: inline owner must keep its scheduled health-check contract",
            )
        check(bool(str(health.get("url", "")).startswith("http")), f"{name}: owner url missing")
        check(health.get("expected-status") is not None, f"{name}: owner expected-status missing")
        interval = health.get("interval")
        check(
            isinstance(interval, int) and not isinstance(interval, bool) and interval <= ceiling,
            f"{name}: owner interval {interval!r} exceeds ceiling {ceiling}",
        )
        check(
            str(group.get("expected-status", "")) == str(health.get("expected-status", "")),
            f"{name}: group/owner expected-status contracts differ",
        )


def main() -> int:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    ceiling = check_manifest(manifest)
    if ceiling:
        with tempfile.TemporaryDirectory(prefix="mihomo-policy-") as raw:
            directory = Path(raw)
            run_mode(manifest, directory, native=True, ceiling=ceiling)
            run_mode(manifest, directory, native=False, ceiling=ceiling)

    if FAILURES:
        print("mihomo policy checks FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("mihomo policy checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
