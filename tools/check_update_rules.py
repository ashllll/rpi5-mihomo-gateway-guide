#!/usr/bin/env python3
"""Exercise the real rule updater with temporary paths and offline commands."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / "automation/sbin/mihomo-update-rules").read_text()
NAMES = sorted(re.findall(r"^download_rule (\S+)", SOURCE, re.MULTILINE))

# Executed under several command names. No gateway command or network is used.
STUB = r'''
import os
from pathlib import Path
import signal
import subprocess
import sys
root = Path(os.environ["RULE_TEST_ROOT"])
mode = os.environ["RULE_TEST_MODE"]
name = Path(sys.argv[0]).name
args = sys.argv[1:]
if name == "curl":
    Path(args[args.index("--output") + 1]).write_text("new\n")
elif name == "mihomo":
    if args[0] == "-t" and mode in {"validation", "rollback"}:
        sys.exit(1)
elif name == "systemctl":
    with (root / "calls").open("a") as stream:
        stream.write(" ".join(args) + "\n")
    if args[0] == "reload" and mode == "reload" and not (root / "reload-failed").exists():
        (root / "reload-failed").touch()
        sys.exit(1)
    if args[0] == "is-active" and mode == "inactive":
        count = (root / "active-count")
        if count.exists():
            sys.exit(1)
        count.touch()
elif name == "install":
    cleaned = []
    while args:
        item = args.pop(0)
        if item in {"-o", "-g"}:
            args.pop(0)
        else:
            cleaned.append(item)
    if "-d" not in cleaned:
        source = Path(cleaned[-2])
        candidate = source.parent.name.startswith("mihomo-rules.")
        if candidate and source.name == os.environ["RULE_TEST_SECOND"]:
            if mode == "write":
                sys.exit(1)
            if mode == "signal":
                os.kill(os.getppid(), signal.SIGTERM)
                sys.exit(1)
        if mode == "rollback" and "backups" in source.parts:
            sys.exit(1)
    sys.exit(subprocess.call([os.environ["RULE_TEST_INSTALL"], *cleaned]))
elif name == "logger":
    with (root / "messages").open("a") as stream:
        stream.write(" ".join(args) + "\n")
elif name == "xargs":
    sys.stdin.read()
'''


def check_case(mode: str) -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        rules = root / "rules"
        rules.mkdir()
        (root / "lock").mkdir()
        commands = root / "bin"
        commands.mkdir()
        for name in NAMES:
            if mode != "missing" or name != NAMES[1]:
                (rules / name).write_text("old\n")
        before = {p.name: p.read_bytes() for p in rules.iterdir()}
        for name in ("flock", "curl", "mihomo", "systemctl", "install", "logger", "sleep", "find", "xargs"):
            path = commands / name
            path.write_text(f"#!{sys.executable}\n" + STUB)
            path.chmod(0o755)
        source = SOURCE
        for old, new in (
            ("/etc/mihomo/rules", str(rules)),
            ("/var/lib/mihomo-rule-backups", str(root / "backups")),
            ("/run/lock", str(root / "lock")),
            ("/etc/mihomo-automation.env", str(root / "absent.env")),
            ("/var/tmp", str(root)),
        ):
            source = source.replace(old, new)
        script = root / "update"
        script.write_text(source)
        environment = dict(os.environ, PATH=f"{commands}{os.pathsep}{os.environ['PATH']}",
                           RULE_TEST_ROOT=str(root), RULE_TEST_MODE=mode,
                           RULE_TEST_SECOND=NAMES[1], RULE_TEST_INSTALL=shutil.which("install"))
        result = subprocess.run(["sh", str(script)], env=environment, capture_output=True, text=True, timeout=30)
        after = {p.name: p.read_bytes() for p in rules.iterdir()}
        assert (result.returncode == 0) == (mode == "success"), (mode, result.stderr)
        if mode == "success":
            assert after == {name: b"new\n" for name in NAMES}
        elif mode != "rollback":
            assert after == before, (mode, "partial update survived")
        if mode not in {"success", "missing"}:
            messages = (root / "messages").read_text()
            expected = "manual recovery required" if mode == "rollback" else "previous rules restored"
            assert expected in messages, (mode, messages)
        if mode == "missing":
            assert "reload" not in (root / "calls").read_text()


if __name__ == "__main__":
    for case in ("missing", "write", "signal", "validation", "reload", "inactive", "rollback", "success"):
        check_case(case)
    print("Rule update transaction checks passed (8 scenarios)")
