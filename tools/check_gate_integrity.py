#!/usr/bin/env python3
"""Prove that the gate itself cannot quietly stop proving things.

This check guards the gate, not the product:

* every ``tools/check_*.py`` is discovered and runs in the default gate unless
  it is explicitly in the real-core profile;
* the change-to-check map is exhaustive for ``automation/**`` and a changed
  check runs itself;
* both CI systems call ``scripts/preflight.sh`` instead of enumerating checks;
* the Gitea workflow no longer puts its token in a clone URL and no longer
  executes pull-request code on the self-hosted runner;
* ``SKIP`` (exit 77), an expired whitelist entry, a malformed whitelist entry
  and a check that only *looks* green are all RED in a synthetic mini-repo;
* ``check_media_core`` without a real core exits non-zero with a dependency
  message instead of returning a silent zero.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gate_plan  # noqa: E402
import preflight  # noqa: E402

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)


def check(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def _relative_files(root: Path, directory: str) -> list[str]:
    base = root / directory
    if not base.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in base.rglob("*") if path.is_file())


def check_matrix() -> None:
    matrix = gate_plan.load_matrix(ROOT)
    checks = gate_plan.all_checks(ROOT)
    profiles = matrix.get("profiles") or {}
    core_only = profiles.get("core_only") or []
    core_extra = profiles.get("core_extra") or []
    check(set(core_only) <= set(checks), f"core_only names missing checks: {sorted(set(core_only) - set(checks))}")
    check(set(core_extra) <= set(checks), f"core_extra names missing checks: {sorted(set(core_extra) - set(checks))}")
    check(not (set(core_only) & set(core_extra)), "core_only and core_extra overlap")

    groups = matrix.get("groups") or {}
    covered: set[str] = set()
    for name, members in groups.items():
        for member in members:
            check(member in checks, f"group {name} names missing check {member}")
            covered.add(member)
    missing_groups = sorted(set(checks) - covered)
    check(not missing_groups, f"checks not assigned to any group (change map cannot trigger them): {missing_groups}")

    rules = matrix.get("changes") or []
    catch_all = [index for index, rule in enumerate(rules) if rule.get("paths") == ["**"]]
    check(bool(catch_all), "matrix needs an explicit catch-all rule")
    catch_index = catch_all[0] if catch_all else len(rules)
    for index, rule in enumerate(rules):
        for pattern in rule.get("paths") or []:
            if pattern == "**":
                continue
            absolute = ROOT / pattern
            if any(char in pattern for char in "*?["):
                matches = [path for path in ROOT.rglob("*") if path.is_file()
                           and gate_plan.matches(path.relative_to(ROOT).as_posix(), pattern)]
                check(bool(matches), f"change rule {index} pattern matches nothing: {pattern}")
            else:
                check(absolute.is_file(), f"change rule {index} names a missing file: {pattern}")
        run = rule.get("run")
        if run == "all":
            continue
        if run == "self":
            continue
        for group in run or []:
            check(group in groups, f"change rule {index} names unknown group: {group}")

    # Every automation file must be claimed by a specific rule, not by the
    # catch-all.  A new automation file therefore forces a conscious mapping.
    for path in _relative_files(ROOT, "automation"):
        rule = next((item for item in rules
                     if any(gate_plan.matches(path, pattern) for pattern in item.get("paths", []))), None)
        if rule is None:
            fail(f"automation file has no change rule: {path}")
            continue
        if rules.index(rule) >= catch_index:
            fail(f"automation file only matches the catch-all rule: {path}")


def check_workflows() -> None:
    github_path = ROOT / ".github/workflows/validate.yml"
    gitea_path = ROOT / ".gitea/workflows/gates.yml"
    check(github_path.is_file(), "missing .github/workflows/validate.yml")
    check(gitea_path.is_file(), "missing .gitea/workflows/gates.yml")

    github_text = github_path.read_text(encoding="utf-8") if github_path.is_file() else ""
    gitea_text = gitea_path.read_text(encoding="utf-8") if gitea_path.is_file() else ""
    for name, text in (("github", github_text), ("gitea", gitea_text)):
        check("./scripts/preflight.sh" in text, f"{name} workflow does not call the single gate")
        check(not re.search(r"python3\s+tools/check_", text),
              f"{name} workflow still enumerates tools/check_*.py")
        check(not re.search(r"python3\s+automation/check-release", text),
              f"{name} workflow still enumerates release checks")
        check("--profile core" not in text or name == "github",
              f"{name} workflow runs the core profile without building a core")
    check("--profile core" in github_text and "MIHOMO_TEST_BINARY" in github_text,
          "github core-build must run the core profile with a built binary")

    gitea = yaml.safe_load(gitea_text) if gitea_text else {}
    triggers = gitea.get("on") or gitea.get(True) or {}
    check("pull_request" not in triggers,
          "gitea gate must not execute pull-request code on the self-hosted runner")
    check(not re.search(r"https?://[^\n]*@", gitea_text),
          "gitea workflow puts credentials in a URL")
    check("http.extraheader" in gitea_text.lower(), "gitea workflow must pass the token in an http header")
    check("192.168." not in gitea_text, "gitea workflow still contains a private Gitea address")
    check("GITHUB_SHA" in gitea_text or "github.sha" in gitea_text, "gitea workflow must fetch the triggering commit")


def check_whitelists() -> None:
    import datetime as dt
    import tempfile as tf
    today = dt.date.today()
    for relative in (preflight.BASELINE, preflight.ADVISORY):
        entries, errors = preflight._read_whitelist(ROOT, relative, today)
        for error in errors:
            fail(error)
        planned = {item.split(":", 1)[1] for item in gate_plan.plan(ROOT, None, "default")}
        for name in entries:
            check(any(preflight._entry_matches(name, entry) for entry in planned),
                  f"{relative}: entry {name} does not name a default-gate check")


def check_media_dependency() -> None:
    media = ROOT / "tools/check_media_core.py"
    env = {key: value for key, value in os.environ.items() if key != "MIHOMO_TEST_BINARY"}
    result = subprocess.run([sys.executable, str(media)], cwd=str(ROOT), env=env,
                            capture_output=True, text=True, timeout=60)
    combined = result.stdout + result.stderr
    check(result.returncode != 0, "check_media_core without a core must not exit 0")
    check("MIHOMO_TEST_BINARY" in combined and "MISSING DEPENDENCY" in combined,
          "check_media_core must explain the missing MIHOMO_TEST_BINARY dependency")
    client = (ROOT / "tools/check_client_dns.py").read_text(encoding="utf-8")
    check("shutil.which('dig')" in client and "MISSING DEPENDENCY: dig" in client,
          "check_client_dns must fail loudly when dig is missing")


def check_tempdirs() -> None:
    pattern = re.compile(r"dir\s*=\s*['\"](/tmp|/var/tmp)['\"]")
    for path in sorted((ROOT / "tools").glob("check_*.py")):
        text = path.read_text(encoding="utf-8")
        match = pattern.search(text)
        if match:
            fail(f"{path.name} still hard-codes {match.group(1)}")
    helper = ROOT / "tools/_tempdirs.py"
    check(helper.is_file(), "tools/_tempdirs.py helper is missing")
    if helper.is_file():
        text = helper.read_text(encoding="utf-8")
        check("AUDIT_TMP_ROOT" in text and "gettempdir" in text,
              "temp helper must honour AUDIT_TMP_ROOT and TMPDIR")


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _mini_repo(base: Path) -> Path:
    root = base / "repo"
    _write(root / "scripts/preflight.py", (SCRIPTS / "preflight.py").read_text(encoding="utf-8"))
    _write(root / "scripts/gate_plan.py", (SCRIPTS / "gate_plan.py").read_text(encoding="utf-8"))
    _write(root / "tools/verification-matrix.yaml", """
version: 1
profiles: {core_only: [], core_extra: []}
steps: []
groups:
  all: [tools/check_pass.py, tools/check_fail.py, tools/check_skip.py]
changes:
  - paths: ["src/**"]
    run: [all]
  - paths: ["**"]
    run: all
""")
    _write(root / "tools/check_pass.py", "print('pass')\n")
    _write(root / "tools/check_fail.py", "raise SystemExit(1)\n")
    _write(root / "tools/check_skip.py", "import sys\nprint('SKIP: fixture')\nsys.exit(77)\n")
    _write(root / "tools/check_new.py", "print('new')\n")
    _write(root / "scripts/preflight-baseline.txt", "# baseline\n")
    _write(root / "scripts/preflight-advisory.txt", "# advisory\n")
    return root


def _run_mini(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PREFLIGHT_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(root / "scripts/preflight.py"), "--root", str(root),
                           "--no-cache", *args], cwd=str(root), env=env,
                          capture_output=True, text=True, timeout=120)


def check_preflight_mechanics() -> None:
    with tempfile.TemporaryDirectory(prefix="gate-") as raw:
        root = _mini_repo(Path(raw))
        # SKIP is not green, and a passing suite with a skip still fails.
        (root / "tools/check_fail.py").unlink()
        result = _run_mini(root)
        check(result.returncode == 1, "preflight treated SKIP as green")
        check("SKIP" in (result.stdout + result.stderr), "preflight did not print the SKIP entry")
        # Discovery: a new check with no matrix group still runs in the full gate.
        result = _run_mini(root, "--list")
        check("check_new.py" in result.stdout, "preflight did not auto-discover a new tools/check_*.py")
        # Remove the skip: the same tree is green.
        (root / "tools/check_skip.py").unlink()
        result = _run_mini(root)
        check(result.returncode == 0, "preflight did not pass a clean mini tree: " + result.stderr[-300:])
        # Malformed and expired whitelist entries are red; a dated future entry is
        # recorded but not counted as a clean pass.
        _write(root / "scripts/preflight-baseline.txt", "# baseline\ncheck_pass.py 2020-01-01 old\n")
        result = _run_mini(root)
        check(result.returncode == 1 and "expired" in (result.stdout + result.stderr).lower(),
              "preflight did not reject an expired baseline entry")
        _write(root / "scripts/preflight-baseline.txt", "# baseline\ncheck_pass.py 2099-01-01 review\n")
        result = _run_mini(root)
        check(result.returncode == 0 and "baseline" in (result.stdout + result.stderr),
              "preflight did not record a dated baseline entry")
        _write(root / "scripts/preflight-baseline.txt", "check_pass.py no-date\n")
        result = _run_mini(root)
        check(result.returncode == 1, "preflight did not reject a baseline entry without a date")
        _write(root / "scripts/preflight-baseline.txt", "# baseline\n")
        # Fail-closed mapping: a change in a path with no rule runs the full gate.
        _write(root / "src/a", "x")
        result = _run_mini(root, "--changed", "src/a", "--list")
        check("check_pass.py" in result.stdout, "mapped change did not run the matching group")
        result = _run_mini(root, "--changed", "docs/a", "--list")
        check("check_new.py" in result.stdout, "unknown path did not fall back to the full gate")


def main() -> int:
    check_matrix()
    check_workflows()
    check_whitelists()
    check_media_dependency()
    check_tempdirs()
    check_preflight_mechanics()
    if FAILURES:
        print("gate integrity: FAIL")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("gate integrity: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
