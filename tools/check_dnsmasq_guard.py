#!/usr/bin/python3

"""Drive the dnsmasq guard's state machine with fake dig/systemctl/alert.

The guard is what notices "dnsmasq is running but nobody gets an answer", so the
decision ladder (restart the right unit, stop restarting when it does not help,
never blame dnsmasq for a mosdns outage) is worth a regression check.  Nothing
here touches a real resolver: dig, systemctl and mihomo-alert are shell stubs on
PATH, and the run directory is rewritten into a temporary directory.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "automation" / "sbin" / "mihomo-dnsmasq-guard"

DIG = """#!/bin/sh
port=53
prev=
for arg in "$@"; do
  [ "$prev" = "-p" ] && port=$arg
  prev=$arg
done
file="$FAKE_STATE/count.$port"
n=$(cat "$file" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\\n' "$n" > "$file"
limit=$(eval echo "\\$FAKE_LAN")
[ "$port" != 53 ] && limit=$(eval echo "\\$FAKE_RES")
if [ "$n" -le "${limit:-0}" ]; then
  printf '198.18.0.1\\n'
fi
exit 0
"""

SYSTEMCTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_STATE/systemctl.log"
exit 0
"""

ALERT = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_STATE/alert.log"
exit 0
"""


def build_run(run: Path) -> Path:
    text = SCRIPT.read_text()
    for old, new in (
        ("exec 8>/run/lock/mihomo-dnsmasq-guard.lock\nflock -n 8 || exit 0", ":"),
        ("state_dir=/run/mihomo-dnsmasq-guard", f"state_dir={run}/state"),
        ("alert=/usr/local/sbin/mihomo-alert", f"alert={run}/bin/alert"),
        ("restart_gap=20", "restart_gap=0"),
    ):
        if old not in text:
            raise SystemExit(f"harness no longer matches the guard: {old!r}")
        text = text.replace(old, new)
    script = run / "guard"
    script.write_text(text)
    script.chmod(0o755)
    return script


def run_once(script: Path, run: Path, lan: int, res: int) -> dict:
    for path in run.glob("state/count.*"):
        path.unlink()
    env = {
        "PATH": f"{run}/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_STATE": str(run / "state"),
        "FAKE_LAN": str(lan),
        "FAKE_RES": str(res),
    }
    subprocess.run(["sh", str(script)], env=env, check=True, capture_output=True, timeout=30)
    text = (run / "state" / "status").read_text().strip()
    fields = dict(item.split("=", 1) for item in text.split(" ")[1:] if "=" in item)
    fields["restarts"] = (run / "state" / "systemctl.log").read_text().count("restart dnsmasq") if (run / "state" / "systemctl.log").exists() else 0
    alerts = (run / "state" / "alert.log").read_text().splitlines() if (run / "state" / "alert.log").exists() else []
    fields["alert_keys"] = [line.rsplit(" ", 1)[-1] if "--health" in line else "" for line in alerts]
    return fields


def main() -> int:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp)
        (run / "bin").mkdir()
        (run / "state").mkdir()
        for name, body in (("dig", DIG), ("systemctl", SYSTEMCTL), ("alert", ALERT)):
            stub = run / "bin" / name
            stub.write_text(body)
            stub.chmod(0o755)
        script = build_run(run)

        # healthy: no restart, no alert, partial reset
        state = run_once(script, run, 2, 2)
        check(state.get("action") == "none" and state.get("reason") == "healthy", f"healthy run: {state}")
        check(state["restarts"] == 0, "healthy run must not restart dnsmasq")
        check(not state["alert_keys"], f"healthy run must stay silent: {state['alert_keys']}")

        # one of two domains missing is degraded, and a single round is not enough
        state = run_once(script, run, 1, 2)
        check(state.get("action") == "degraded-watch", f"single degraded round: {state}")
        check(state["restarts"] == 0, "one degraded round must not restart")
        run_once(script, run, 2, 2)

        # two consecutive degraded rounds restart dnsmasq
        run_once(script, run, 1, 2)
        state = run_once(script, run, 1, 2)
        check(state.get("action") == "restart", f"persistent degraded must restart: {state}")
        check(state["restarts"] == 1, f"expected one restart: {state}")
        check("dnsmasq-restart" in state["alert_keys"], f"restart must alert: {state['alert_keys']}")

        # dnsmasq silent while mosdns answers: restart dnsmasq
        state = run_once(script, run, 0, 2)
        check(state.get("action") == "restart" and state.get("reason") == "dnsmasq-silent", f"dnsmasq-silent: {state}")

        # both silent: never restart dnsmasq
        state = run_once(script, run, 0, 0)
        check(state.get("action") == "chain-down", f"chain down: {state}")
        before = state["restarts"]
        check("dns-path-down" in state["alert_keys"], f"chain down must alert: {state['alert_keys']}")
        state = run_once(script, run, 0, 0)
        check(state["restarts"] == before, "chain down must not restart dnsmasq")

        # dnsmasq answers, mosdns does not: not dnsmasq's fault
        state = run_once(script, run, 2, 0)
        check(state.get("action") == "escalate-resolver", f"resolver silent: {state}")
        check(state["restarts"] == before, "resolver silent must not restart dnsmasq")

        # restart budget: three restarts inside the window, then throttle loudly
        for _ in range(6):
            state = run_once(script, run, 0, 2)
        check(state.get("action") == "throttled", f"budget must throttle: {state}")
        check(state["restarts"] == 3, f"expected exactly three restarts including the earlier two: {state['restarts']}")
        check("dnsmasq-guard-exhausted" in state["alert_keys"], f"throttle must alert: {state['alert_keys']}")

        # recovery clears the degraded counter so a later outage starts fresh
        state = run_once(script, run, 2, 2)
        check(state.get("action") == "none", f"recovery: {state}")
        state = run_once(script, run, 1, 2)
        check(state.get("action") == "degraded-watch", f"counter must reset after recovery: {state}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("dnsmasq guard checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
