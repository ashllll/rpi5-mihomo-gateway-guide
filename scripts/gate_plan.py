#!/usr/bin/env python3
"""Plan which gate entries must run for a set of changed files.

This is the machine-readable half of ``scripts/preflight.sh``.  The full gate
is always "every ``tools/check_*.py`` plus the two release/publication steps";
the fast path is a change-to-check map so a commit cannot quietly skip the
check that covers the file it edits.  Unknown paths and gate changes fall back
to the full gate: a missing rule must never mean "nothing to run".

The matrix lives in ``tools/verification-matrix.yaml``.  ``tools/check_gate_integrity.py``
keeps it exhaustive and tests this planner with synthetic trees.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MATRIX = Path("tools/verification-matrix.yaml")
CHECK_GLOB = "tools/check_*.py"
CHECK_PREFIX = "check:"
STEP_PREFIX = "step:"

# Steps are not check_*.py files but are part of the same local gate.
DEFAULT_STEPS = ("automation/check-release.py", "tools/validate_public.py")


def load_matrix(root: Path) -> dict:
    path = root / MATRIX
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{MATRIX}: expected a mapping")
    return data


def _glob_regex(pattern: str) -> re.Pattern:
    """Translate a POSIX glob with ** into a regex anchored at both ends."""
    out = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def matches(path: str, pattern: str) -> bool:
    return bool(_glob_regex(pattern).match(path))


def all_checks(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.glob(CHECK_GLOB))


def check_paths(root: Path, profile: str) -> list[str]:
    checks = all_checks(root)
    profiles = load_matrix(root).get("profiles", {}) or {}
    core_only = set(profiles.get("core_only", []) or [])
    core_extra = set(profiles.get("core_extra", []) or [])
    if profile == "default":
        return [name for name in checks if name not in core_only]
    if profile == "core":
        core = list(core_only | core_extra)
        missing = [name for name in core if name not in checks]
        if missing:
            raise ValueError(f"core profile names missing checks: {missing}")
        return core
    raise ValueError(f"unknown profile: {profile}")


def steps(root: Path) -> list[str]:
    configured = load_matrix(root).get("steps", list(DEFAULT_STEPS)) or []
    return list(configured)


def _groups(matrix: dict) -> dict:
    groups = matrix.get("groups") or {}
    if not isinstance(groups, dict):
        raise ValueError("matrix groups must be a mapping")
    return groups


def plan(root: Path, changed: list[str] | None, profile: str) -> list[str]:
    """Return ['check:<path>', 'step:<path>', ...] in stable order.

    ``changed is None`` means the full gate.  An empty list is treated as the
    full gate too: at pre-push time there is no staged diff, and "nothing to
    check" is the wrong answer there.
    """
    matrix = load_matrix(root)
    checks = check_paths(root, profile)
    configured_steps = steps(root)
    if profile == "core":
        return sorted(CHECK_PREFIX + name for name in checks)

    if not changed:
        selected = set(checks)
        chosen_steps = set(configured_steps)
    else:
        groups = _groups(matrix)
        selected = set()
        chosen_steps = set(configured_steps)
        rules = matrix.get("changes") or []
        for path in changed:
            path = path.strip()
            if not path:
                continue
            path = path[2:] if path.startswith("./") else path
            rule = next((item for item in rules
                         if any(matches(path, pattern) for pattern in item.get("paths", []))), None)
            if rule is None:
                # No rule: fail safe, run the whole gate.
                selected = set(checks)
                break
            run = rule.get("run")
            if run == "all":
                selected = set(checks)
                break
            if run == "self":
                if path in checks:
                    selected.add(path)
                selected.add("tools/check_gate_integrity.py")
                continue
            for group in run or []:
                if group not in groups:
                    raise ValueError(f"matrix change rule names unknown group: {group}")
                selected.update(groups[group])
        selected &= set(checks)
        selected.add("tools/check_gate_integrity.py")
    selected &= set(checks)
    return sorted([CHECK_PREFIX + name for name in selected] +
                  [STEP_PREFIX + name for name in chosen_steps])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--profile", choices=("default", "core"), default="default")
    parser.add_argument("--changed", nargs="*", default=None, help="changed paths; omit for full gate")
    parser.add_argument("--list", action="store_true", help="print the plan")
    args = parser.parse_args(argv)
    try:
        result = plan(args.root, args.changed, args.profile)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print("gate_plan: " + str(error), file=sys.stderr)
        return 1
    if args.list:
        for item in result:
            print(item)
    else:
        print("\n".join(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
