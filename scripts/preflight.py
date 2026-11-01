#!/usr/bin/env python3
"""The one local gate: run the verification matrix and report every outcome.

``scripts/preflight.sh`` is a thin POSIX shell wrapper around this file, so the
hook, a human, and every CI job all execute the same code and the same matrix.
Design rules:

* a new ``tools/check_*.py`` is discovered automatically and runs in the full
  gate and in CI (CI calls this script, it does not enumerate checks);
* a check may exit 77 to declare SKIP.  SKIP is printed and makes the gate
  RED: an unrun assertion is not a pass;
* ``scripts/preflight-baseline.txt`` and ``scripts/preflight-advisory.txt`` are
  empty by default.  Any entry must carry a real ISO expiry date; expired or
  malformed entries make the gate red.  Advisory failures are printed as
  advisory, never as "all checks passed";
* paths under ``tools/``, ``automation/``, docs and the gate itself map to the
  checks that cover them; an unknown path runs the full gate;
* a clean full pass for an identical tree may be cached, keyed on content and
  environment, never on "it passed once".
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import gate_plan  # noqa: E402

BASELINE = Path("scripts/preflight-baseline.txt")
ADVISORY = Path("scripts/preflight-advisory.txt")
CACHE = Path(".git/preflight-cache.json")
CHECK_TIMEOUT = 900
SUBSCRIPTION_TIMEOUT = 1800
SKIP_EXIT = 77
ENTRY_RE = re.compile(r"^(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(.+)$")


def _read_whitelist(root: Path, relative: Path, today: dt.date) -> tuple[dict[str, str], list[str]]:
    """Parse NAME YYYY-MM-DD reason lines; any other non-comment line is an error."""
    entries: dict[str, str] = {}
    errors: list[str] = []
    path = root / relative
    if not path.is_file():
        errors.append(f"{relative}: missing (create it with only the header comment)")
        return entries, errors
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ENTRY_RE.match(line)
        if not match:
            errors.append(f"{relative}:{number}: entry needs 'name YYYY-MM-DD reason': {raw!r}")
            continue
        name, expiry, reason = match.groups()
        try:
            when = dt.date.fromisoformat(expiry)
        except ValueError:
            errors.append(f"{relative}:{number}: invalid date {expiry!r}")
            continue
        if when < today:
            errors.append(f"{relative}:{number}: entry expired on {expiry}: {name}")
            continue
        if name in entries:
            errors.append(f"{relative}:{number}: duplicate entry {name}")
            continue
        entries[name] = f"expires {expiry}: {reason}"
    return entries, errors


def _entry_matches(entry: str, relative: str) -> bool:
    return entry in (relative, Path(relative).name)


def _python() -> str:
    return os.environ.get("PREFLIGHT_PYTHON") or sys.executable


def _cache_key(root: Path, profile: str, entries: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update(profile.encode())
    for name in ("scripts/preflight.py", "scripts/gate_plan.py",
                 "tools/verification-matrix.yaml", "scripts/preflight-baseline.txt",
                 "scripts/preflight-advisory.txt"):
        path = root / name
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    for entry in entries:
        path = root / entry
        digest.update(entry.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    try:
        digest.update(subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                     capture_output=True, check=False).stdout)
        digest.update(subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                     capture_output=True, check=False).stdout)
    except OSError:
        pass
    for key in ("PATH", "AUDIT_TMP_ROOT", "MIHOMO_TEST_BINARY", "TMPDIR"):
        digest.update(f"{key}={os.environ.get(key, '')}".encode())
    return digest.hexdigest()


def _staged_files(root: Path) -> list[str] | None:
    try:
        result = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only", "-z"],
                                capture_output=True, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    names = [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]
    return names


def _short_temp_root(root: Path) -> str | None:
    """A short writable root for AF_UNIX tests when TMPDIR is long."""
    explicit = os.environ.get("AUDIT_TMP_ROOT")
    if explicit:
        Path(explicit).mkdir(parents=True, exist_ok=True)
        return explicit
    system = tempfile.gettempdir()
    if len(system) <= 72:
        return None
    fallback = root / ".preflight-tmp"
    fallback.mkdir(exist_ok=True)
    return str(fallback)


def _run(root: Path, entry: str, timeout: int, env: dict) -> dict:
    path = root / entry
    started = time.monotonic()
    try:
        result = subprocess.run([_python(), str(path)], cwd=str(root), env=env,
                                capture_output=True, text=True, timeout=timeout)
        code = result.returncode
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired as error:
        code = "timeout"
        output = (error.stdout or "") + (error.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        output += f"\npreflight: timed out after {timeout}s"
    except OSError as error:
        code = 127
        output = f"preflight: cannot run {entry}: {error}"
    return {"entry": entry, "returncode": code,
            "duration_s": round(time.monotonic() - started, 2),
            "output": output}


def _print_result(result: dict, status: str) -> None:
    name = result["entry"]
    print(f"  {name:<46} {status:<8} {result['duration_s']:6.2f}s", flush=True)
    if status in ("FAIL", "SKIP"):
        lines = [line for line in result["output"].splitlines() if line.strip()]
        for line in lines[:8]:
            print(f"      {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--profile", choices=("default", "core"), default="default")
    parser.add_argument("--staged", action="store_true", help="map the staged diff; no staged diff means full gate")
    parser.add_argument("--changed", nargs="*", default=None, help="explicit changed paths")
    parser.add_argument("--list", action="store_true", help="print the plan and exit")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    today = dt.date.today()

    try:
        if args.changed is not None:
            changed = args.changed
        elif args.staged:
            changed = _staged_files(root)
            if changed is None:
                print("preflight: cannot read the staged diff; running the full gate")
            elif changed:
                print(f"preflight: {len(changed)} staged path(s); mapped fast gate")
            else:
                print("preflight: no staged paths; running the full gate")
        else:
            changed = None
        entries = [item.split(":", 1)[1] for item in gate_plan.plan(root, changed, args.profile)]
    except (OSError, ValueError) as error:
        print(f"preflight: cannot build the plan: {error}", file=sys.stderr)
        return 1

    checks = [entry for entry in entries if entry.startswith("tools/check_")]
    steps = [entry for entry in entries if not entry.startswith("tools/check_")]
    if args.list:
        for entry in entries:
            print(entry)
        return 0
    if not checks and not steps:
        print("preflight: empty gate plan; refusing to report green", file=sys.stderr)
        return 1

    baseline, baseline_errors = _read_whitelist(root, BASELINE, today)
    advisory, advisory_errors = _read_whitelist(root, ADVISORY, today)
    whitelist_errors = baseline_errors + advisory_errors
    if args.profile == "core":
        # The core profile is run where both files are already proven; keep the
        # whitelist parser active so a malformed file cannot hide there either.
        pass

    cache_key = _cache_key(root, args.profile, entries)
    if (not args.no_cache and args.profile == "default" and changed is None
            and not whitelist_errors and (root / CACHE).is_file()):
        try:
            cached = json.loads((root / CACHE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        if cached.get("key") == cache_key and cached.get("returncode") == 0:
            print(f"preflight: this exact tree and environment already passed the full gate ({cache_key[:12]}); "
                  "use --no-cache to rerun")
            return 0

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    short_root = _short_temp_root(root)
    if short_root:
        env.setdefault("AUDIT_TMP_ROOT", short_root)

    print(f"preflight: profile={args.profile} entries={len(entries)} "
          f"({'full' if changed is None else 'mapped'})", flush=True)
    results = []
    for entry in entries:
        timeout = SUBSCRIPTION_TIMEOUT if entry.endswith("check_subscription_update.py") else CHECK_TIMEOUT
        result = _run(root, entry, timeout, env)
        if result["returncode"] == 0:
            status = "pass"
        elif result["returncode"] == SKIP_EXIT:
            status = "SKIP"
        else:
            status = "FAIL"
        result["status"] = status
        _print_result(result, status)
        results.append(result)

    if whitelist_errors:
        print("\npreflight: whitelist errors (a whitelist entry needs a real expiry date):", file=sys.stderr)
        for error in whitelist_errors:
            print(f"  {error}", file=sys.stderr)

    failed = [item for item in results if item["status"] == "FAIL"]
    skipped = [item for item in results if item["status"] == "SKIP"]
    baseline_hits, advisory_hits = [], []
    for item in failed:
        if any(_entry_matches(entry, item["entry"]) for entry in baseline):
            baseline_hits.append(item)
        elif any(_entry_matches(entry, item["entry"]) for entry in advisory):
            advisory_hits.append(item)
    blocking = [item for item in failed if item not in baseline_hits and item not in advisory_hits]

    for item in baseline_hits:
        matched = next(entry for entry in baseline if _entry_matches(entry, item["entry"]))
        print(f"  baseline: {item['entry']} ({baseline[matched]})", file=sys.stderr)
    for item in advisory_hits:
        matched = next(entry for entry in advisory if _entry_matches(entry, item["entry"]))
        print(f"  ADVISORY: {item['entry']} ({advisory[matched]}) - recorded, NOT counted as proof",
              file=sys.stderr)
    for entry in baseline:
        if not any(_entry_matches(entry, item["entry"]) for item in results):
            print(f"  baseline entry does not match a planned gate: {entry}", file=sys.stderr)
            blocking.append({"entry": entry, "status": "FAIL", "output": "stale whitelist entry"})
    for entry in advisory:
        if not any(_entry_matches(entry, item["entry"]) for item in results):
            print(f"  advisory entry does not match a planned gate: {entry}", file=sys.stderr)
            blocking.append({"entry": entry, "status": "FAIL", "output": "stale whitelist entry"})

    summary = {
        "profile": args.profile,
        "full": changed is None,
        "entries": len(entries),
        "pass": sum(1 for item in results if item["status"] == "pass"),
        "fail": len(failed),
        "skip": len(skipped),
        "baseline": len(baseline_hits),
        "advisory": len(advisory_hits),
        "blocking": [item["entry"] for item in blocking],
        "returncode": 0 if not blocking and not skipped and not whitelist_errors else 1,
        "results": results,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    if skipped:
        print("preflight: SKIP is not a pass; install the dependency or move the check to a profile: " +
              ", ".join(item["entry"] for item in skipped), file=sys.stderr)
    if whitelist_errors or blocking or skipped:
        print(f"preflight: BLOCKED ({len(blocking)} failure(s), {len(skipped)} skip(s), "
              f"{len(whitelist_errors)} whitelist error(s))", file=sys.stderr)
        return 1
    if advisory_hits or baseline_hits:
        print(f"preflight: completed with {len(baseline_hits)} baseline and {len(advisory_hits)} advisory "
              "failure(s); this is NOT a clean green", file=sys.stderr)
        return 0
    print(f"preflight: PASS - {summary['pass']} entries, 0 skips, 0 baseline, 0 advisory", flush=True)
    if args.profile == "default" and changed is None:
        try:
            (root / CACHE).write_text(json.dumps({"key": cache_key, "returncode": 0}),
                                      encoding="utf-8")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
