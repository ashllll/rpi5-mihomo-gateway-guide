#!/usr/bin/python3

"""Static and end-to-end checks for the published automation layer.

The automation layer is the part of this guide that runs on the gateway, so it
is checked the way it is shipped:

* every shell and Python file parses,
* code and configuration stay ASCII (node labels are not published),
* no deployment identity (private path, LAN address, subscription endpoint,
  connection UUID, MAC address, key material, access token) appears,
* every environment variable a script reads is documented in
  automation/env/automation.env.example, and every documented variable is used,
* every unit that runs an environment-dependent script loads the env file,
* the unit tree is the recorded gateway's tree: exactly the twelve units that
  box enabled, four timers installed but left off, the startup evidence gate and
  the budget wrapper drop-in, and none of the retired adaptive units,
* the scripts and units are the recorded set (no adaptive selector), every
  `/usr/local/sbin/...` a unit runs is shipped, and `mihomo -t` /
  `--startup-check` / `--run-core` stay wired,
* curated-pool keys match capabilities.yaml and route data comes from the
  independently installed route-defaults.yaml,
* `automation/install.sh` installs the documented layout into a sandbox, keeps
  operator-edited deployment data, enables exactly the twelve recorded units and
  disables exactly the four dormant timers, and changes nothing in --dry-run
  mode,
* the gateway address and ports really come from the environment at runtime.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import ast
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# Importing the published scripts must not leave bytecode caches in the tree.
sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
AUTOMATION = REPO / "automation"
ENV_EXAMPLE = AUTOMATION / "env" / "automation.env.example"
# The alert notifier and the watchdog keep their secrets and thresholds in their
# own least-privilege site file, so those variables are documented there.
ALERT_ENV_EXAMPLE = AUTOMATION / "env" / "mihomo-alert.env.example"
ALERT_ENV_FILE = "EnvironmentFile=-/etc/mihomo-alert.env"
ALERT_SCRIPTS = {"mihomo-alert", "mihomo-watch"}

from publication_privacy import AUTOMATION_PATTERNS, NODE_PATTERNS, matching_labels

# These scripts recognise provider announcements or send Chinese operator
# notifications. Node-label patterns are still checked in every file.
CJK_ALLOWED = {
    "sbin/mihomo-subscription-candidate",
    "sbin/mihomo-subscription-normalize",
    "sbin/mihomo-alert",
    "sbin/mihomo-healthcheck",
    "sbin/mosdns-healthcheck",
    "sbin/mihomo-watch",
}

# The recorded gateway's installed set. Twelve units are enabled; four maintenance
# timers ship for completeness but stay off. Keeping both lists literal is the
# point: a unit silently moving between them is exactly the drift this checker
# exists to catch.
ENABLED_UNITS = (
    "mihomo.service",
    "mihomo-measurement-budget.service",
    "mihomo-healthcheck.timer",
    "mihomo-watch.timer",
    "mihomo-dnsmasq-guard.timer",
    "mosdns.service",
    "mosdns-healthcheck.timer",
    "mosdns-rules-update.timer",
    "mosdns-binary-update.timer",
    "dnsmasq-vlan-pilot.service",
    "dnsmasq.service",
    "homeassistant-access-guard.service",
)
DORMANT_TIMERS = (
    "mihomo-rules-update.timer",
    "mihomo-subscription-update.timer",
    "mihomo-download-quality.timer",
    "mihomo-social-quality.timer",
)
SBIN_SCRIPTS = (
    "dnsmasq-safe-start",
    "homeassistant-access-guard",
    "mihomo-alert",
    "mihomo-core-swap",
    "mihomo-dns-mode",
    "mihomo-download-quality",
    "mihomo-dnsmasq-guard",
    "mihomo-healthcheck",
    "mihomo-measurement-budget",
    "mihomo-measurement-policy",
    "mihomo-policy-overlay",
    "mihomo-reconcile-policy",
    "mihomo-social-quality",
    "mihomo-subscription-candidate",
    "mihomo-subscription-normalize",
    "mihomo-update-rules",
    "mihomo-update-subscription",
    "mihomo-watch",
    "mosdns-emergency-direct",
    "mosdns-healthcheck",
    "mosdns-resume-auto",
    "mosdns-rollout-rollback",
    "mosdns-update-binary",
    "mosdns-update-rules",
)
# Retired by the recorded gateway: the adaptive selector, its timers and the
# Home Assistant unit. The guard script itself survives because the operator
# still runs it by hand.
RETIRED_UNIT_MARKERS = ("mihomo-adaptive",)

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)


def check(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def automation_files() -> list[Path]:
    return sorted(path for path in AUTOMATION.rglob("*") if path.is_file())


def read_source(path: Path) -> str:
    """Read a published file, reporting binary content instead of crashing."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail(
            f"{path.relative_to(REPO)} is not UTF-8 text; only text sources and "
            "configuration may live under automation/"
        )
        return ""


def shipped_files() -> list[Path]:
    """Everything install.sh copies to the gateway (excludes docs and the optional tree."""
    skipped = {"install.sh", "check-release.py", "release-manifest.json"}
    return [
        path
        for path in automation_files()
        if path.name not in skipped
        and not path.name.endswith(".md")
        and "optional" not in path.relative_to(AUTOMATION).parts
    ]


def check_syntax() -> None:
    for path in automation_files():
        if path.suffix == ".md":
            continue
        text = read_source(path)
        first_line = text.splitlines()[0] if text else ""
        if "python" in first_line:
            try:
                compile(text, str(path), "exec")
            except SyntaxError as error:
                fail(f"{path.relative_to(REPO)} does not parse: {error}")
        elif path.suffix == ".sh" or path.name.endswith("sh") or first_line.startswith("#!"):
            result = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                fail(f"{path.relative_to(REPO)} fails `sh -n`: {result.stderr.strip()}")


def check_node_labels() -> None:
    """No file may carry a real node label.

    Scripts may hold the provider announcement strings they have to match (a
    subscription whose only entries are "remaining traffic" notices is treated
    as expired), but configuration and unit files stay ASCII so a pasted node
    list cannot slip in there.
    """
    for path in automation_files():
        if path.suffix == ".md":
            continue
        text = read_source(path)
        for label in matching_labels(text, NODE_PATTERNS, flags=0):
            fail(f"{path.relative_to(REPO)}: {label} (value redacted)")
        relative = str(path.relative_to(AUTOMATION))
        if path.suffix in {".yaml", ".yml"} or path.name.endswith((".service", ".timer", ".conf")):
            try:
                text.encode("ascii")
            except UnicodeEncodeError as error:
                fail(
                    f"{path.relative_to(REPO)} must stay ASCII "
                    f"(unexpected character {text[error.start]!r})"
                )
        elif relative not in CJK_ALLOWED:
            non_ascii = sorted({character for character in text if ord(character) > 127})
            if non_ascii:
                fail(
                    f"{path.relative_to(REPO)} contains non-ASCII text "
                    f"({''.join(non_ascii)[:20]!r}); node labels belong to the deployment manifest"
                )


def check_forbidden() -> None:
    for path in automation_files():
        text = read_source(path)
        for label in matching_labels(text, AUTOMATION_PATTERNS):
            fail(f"{path.relative_to(REPO)}: {label} (value redacted)")


# Variables that are not deployment parameters of the shared env file:
#   * GATEWAY_ROOT - install.sh's own staging knob, documented in its header,
#   * CONNECTION_UUID / IP4_* - injected by NetworkManager into dispatcher
#     scripts rather than configured by the operator,
#   * MOSDNS_UPDATE_DRY_RUN - a per-run validation switch documented in the
#     usage text of mosdns-update-rules itself.
NOT_DEPLOYMENT_PARAMETERS = {
    "GATEWAY_ROOT",
    "CONNECTION_UUID",
    "IP4_ADDRESS_0",
    "IP4_GATEWAY",
    "MOSDNS_UPDATE_DRY_RUN",
    # Generated and overwritten by the updater for descendant lifetime checks.
    # These identify this invocation; operators must not configure them.
    "MIHOMO_UPDATE_OWNER_PID",
    "MIHOMO_UPDATE_OWNER_START",
}

SHELL_VAR = re.compile(r'\$\{([A-Z_][A-Z0-9_]*):[-=]')
PYTHON_VAR = re.compile(r"os\.environ(?:\.get\(\s*\"|\[\s*\")([A-Z_][A-Z0-9_]*)\"")


def used_variables() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for path in automation_files():
        if path.suffix == ".md":
            continue
        text = read_source(path)
        found = (set(SHELL_VAR.findall(text)) | set(PYTHON_VAR.findall(text))) - NOT_DEPLOYMENT_PARAMETERS
        if found:
            used[str(path.relative_to(REPO))] = found
    return used


def documented_variables(path: Path = ENV_EXAMPLE) -> set[str]:
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", path.read_text(encoding="utf-8"), re.MULTILINE))


def check_environment_documentation() -> None:
    used = used_variables()
    alert_used = {name: variables for name, variables in used.items()
                  if Path(name).name in ALERT_SCRIPTS}
    deploy_used = {name: variables for name, variables in used.items()
                   if name not in alert_used}
    documented = documented_variables()
    alert_documented = documented_variables(ALERT_ENV_EXAMPLE)

    read = set().union(*deploy_used.values()) if deploy_used else set()
    missing = sorted(read - documented)
    if missing:
        fail(f"environment variables are read but not documented in env/automation.env.example: {missing}")
    unused = sorted(documented - read)
    if unused:
        fail(f"env/automation.env.example documents variables no script reads: {unused}")

    alert_read = set().union(*alert_used.values()) if alert_used else set()
    alert_missing = sorted(alert_read - alert_documented)
    if alert_missing:
        fail(f"environment variables are read but not documented in env/mihomo-alert.env.example: {alert_missing}")
    alert_unused = sorted(alert_documented - alert_read)
    if alert_unused:
        fail(f"env/mihomo-alert.env.example documents variables no script reads: {alert_unused}")

    # A unit that runs an environment-dependent script must load the env file the
    # script expects: the deployment file, or the alert site file for the
    # notifier and the watchdog. Drop-ins inherit their parent unit's directives.
    dependent = {
        Path(name).name: variables
        for name, variables in used.items()
        if variables and "sbin/" in name
    }
    exec_directive = re.compile(r"^Exec(?:Start|StartPre|StartPost|Stop|StopPost|Reload)=", re.MULTILINE)
    for unit in sorted((AUTOMATION / "systemd").rglob("*")):
        if not unit.is_file():
            continue
        text = read_source(unit)
        if unit.parent.name.endswith(".d"):
            parent = AUTOMATION / "systemd" / unit.parent.name[:-2]
            if parent.is_file():
                text += "\n" + read_source(parent)
        if not exec_directive.search(text):
            continue
        without_unit_refs = re.sub(r"^OnFailure=.*$", "", text, flags=re.MULTILINE)
        invoked = {name for name in dependent if re.search(rf"\b{re.escape(name)}\b", without_unit_refs)}
        if not invoked:
            continue
        required = set()
        for name in invoked:
            if name in ALERT_SCRIPTS:
                required.add(ALERT_ENV_FILE)
                continue
            # Self-sourcing scripts (the guard and dnsmasq helpers) already load
            # the deployment file, so the unit does not need to duplicate it.
            script = AUTOMATION / "sbin" / name
            if script.is_file() and "/etc/mihomo-automation.env" in read_source(script):
                continue
            required.add("EnvironmentFile=-/etc/mihomo-automation.env")
        for directive in sorted(required):
            if directive not in text:
                fail(
                    f"systemd/{unit.relative_to(AUTOMATION / 'systemd')} runs {sorted(invoked)} "
                    f"without {directive}"
                )


def overlay_manifest_keys(source: str) -> set[str]:
    """Read literal manifest keys from Python calls regardless of formatting."""
    keys = set()
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"overlay_labels", "pool_nodes"}:
            continue
        position = 2 if node.func.id == "pool_nodes" else 1
        key = node.args[position] if len(node.args) > position else next(
            (item.value for item in node.keywords if item.arg == "key"), None
        )
        if node.func.id == "overlay_labels" and isinstance(key, ast.JoinedStr):
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, ast.FunctionDef):
                owner = parents.get(owner)
            # Only this known adapter may form a dynamic legacy key. Its actual
            # callers below still have to supply literal pool names.
            template = ast.parse('f"{key}_nodes"', mode='eval').body
            if owner is not None and owner.name == "pool_nodes" and ast.dump(key) == ast.dump(template):
                continue
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ValueError("overlay_labels requires a literal manifest key for coverage checks")
        keys.add(key.value + "_nodes" if node.func.id == "pool_nodes" else key.value)
    return keys


def check_unit_layout() -> None:
    """The shipped units and wiring must be the recorded gateway's."""
    units = AUTOMATION / "systemd"
    # dnsmasq.service comes from the distribution package; the repo ships its
    # drop-in only. Everything else in the enabled set must be a shipped file.
    for unit in ENABLED_UNITS + DORMANT_TIMERS:
        if unit == "dnsmasq.service":
            continue
        check((units / unit).is_file(), f"automation/systemd is missing {unit}")

    scripts = {
        path.name
        for path in (AUTOMATION / "sbin").iterdir()
        if path.is_file() and not path.name.startswith(".")
    }
    check(
        scripts == set(SBIN_SCRIPTS),
        "automation/sbin differs from the recorded script set: "
        f"missing={sorted(set(SBIN_SCRIPTS) - scripts)} extra={sorted(scripts - set(SBIN_SCRIPTS))}",
    )

    for path in sorted(units.rglob("*")):
        if any(marker in path.name for marker in RETIRED_UNIT_MARKERS):
            fail(f"systemd/{path.relative_to(units)} is retired and must not ship")

    # Reverse assertion: no publishable file may reintroduce the retired
    # selector or the Home Assistant unit. Ignored forensics trees stay out of
    # the file list, so only what would be published is considered.
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, stdout=subprocess.PIPE, check=True,
    ).stdout.decode()
    for name in filter(None, listed.split("\0")):
        if not (REPO / name).is_file():
            continue
        if any(marker in name for marker in RETIRED_UNIT_MARKERS):
            fail(f"{name} is retired and must not be published")

    # A unit that calls /usr/local/sbin/<name> must ship <name>: the installer
    # copies sbin/, so a typo would otherwise enable a unit that cannot start.
    for unit in sorted(units.rglob("*")):
        if not unit.is_file():
            continue
        for name in re.findall(r"/usr/local/sbin/([A-Za-z0-9._-]+)", read_source(unit)):
            check(
                (AUTOMATION / "sbin" / name).is_file(),
                f"systemd/{unit.relative_to(units)} runs sbin/{name}, which is not shipped",
            )

    service = read_source(units / "mihomo.service")
    check(
        "ExecStartPre=/usr/local/bin/mihomo -t -d /etc/mihomo" in service,
        "mihomo.service lost the `mihomo -t` config gate",
    )
    probe = read_source(units / "mihomo.service.d" / "zz-startup-probe.conf")
    check(
        "--startup-check" in probe,
        "mihomo.service no longer runs mihomo-healthcheck --startup-check",
    )

    budget = read_source(units / "mihomo.service.d" / "measurement-budget.conf")
    check(
        "ExecStart=/usr/local/sbin/mihomo-measurement-budget --run-core /usr/local/bin/mihomo"
        in budget,
        "measurement-budget.conf does not wrap the core with --run-core",
    )
    check(
        re.search(r"^ExecStart=$", budget, re.MULTILINE) is not None,
        "measurement-budget.conf must clear the parent ExecStart before replacing it",
    )


def check_manifest() -> None:
    manifest_path = AUTOMATION / "policy" / "capabilities.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    check(isinstance(manifest, dict), "capabilities.yaml must be a mapping")
    overlay = manifest.get("overlay")
    check(isinstance(overlay, dict), "capabilities.yaml is missing the overlay section")
    if isinstance(overlay, dict):
        for key, value in overlay.items():
            check(
                isinstance(value, list) and not value,
                f"capabilities.yaml overlay.{key} must ship empty (node labels are deployment data)",
            )

    source = (AUTOMATION / "sbin" / "mihomo-policy-overlay").read_text(encoding="utf-8")
    read_keys = overlay_manifest_keys(source)
    check(bool(read_keys), "mihomo-policy-overlay no longer reads overlay_labels")
    if isinstance(overlay, dict):
        check(
            read_keys == set(overlay),
            f"overlay keys differ: script reads {sorted(read_keys)}, manifest declares {sorted(overlay)}",
        )
    measurement = manifest.get("measurement", {})
    check(isinstance(measurement, dict), "capabilities.yaml measurement must be a mapping")
    if isinstance(measurement, dict):
        check(measurement.get("enabled") is False, "native URLTests must ship paused")
        check(measurement.get("native_budget") is True, "native URLTests must require budget admission")
        check(measurement.get("accounts") == {}, "capabilities.yaml must not ship account identities")
    for key in ("download_routes", "video_routes"):
        check(key not in manifest,
              f"capabilities.yaml must not duplicate the public {key} table")

    defaults_path = AUTOMATION / "policy" / "route-defaults.yaml"
    defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
    check(isinstance(defaults, dict), "route-defaults.yaml must be a mapping")
    if not isinstance(defaults, dict):
        return
    for key, allowed in (("download_routes", {"GitHub Download", "GitHub Control", "Package Download"}),
                         ("video_routes", {"Streaming", "Social"})):
        routes = defaults.get(key)
        check(isinstance(routes, dict) and bool(routes), f"route-defaults.yaml is missing {key}")
        if isinstance(routes, dict):
            for route, spec in routes.items():
                check(isinstance(spec, dict), f"{key}.{route} must be a mapping")
                if isinstance(spec, dict):
                    check(spec.get("group") in allowed, f"{key}.{route} uses an unauthorized group")
                    check(isinstance(spec.get("domains"), list) and bool(spec["domains"]),
                          f"{key}.{route} must list exact domains")
    check(defaults.get("retired_quality_profile_ids") == [
        "github-release", "git-transport", "packages", "x-video"
    ], "route-defaults.yaml must enumerate only generator-owned legacy profiles")


def expected_install_map(root: Path) -> set[Path]:
    expected = set()
    for path in shipped_files():
        relative = path.relative_to(AUTOMATION)
        parts = relative.parts
        if parts[0] == "sbin":
            expected.add(Path("usr/local/sbin") / relative.name)
        elif parts[0] == "systemd":
            expected.add(Path("etc/systemd/system") / Path(*parts[1:]))
        elif parts[0] == "etc":
            if relative.name.startswith("dnsmasq-upstreams."):
                expected.add(Path("etc/mihomo") / relative.name)
            elif relative.as_posix() == "etc/dnsmasq/resolver.conf":
                expected.add(Path("etc/dnsmasq.d/resolver.conf"))
            elif relative.as_posix() == "etc/dnsmasq/vlan-pilot.conf":
                expected.add(Path("etc/dnsmasq-vlan-pilot.conf"))
            elif relative.as_posix() == "etc/dnsmasq/upstreams.conf":
                expected.add(Path("etc/dnsmasq-upstreams.conf"))
            else:
                expected.add(Path("etc") / Path(*parts[1:]))
        elif parts[0] == "policy":
            expected.add(Path("etc/mihomo/adaptive") / relative.name)
        elif parts[0] == "env":
            if relative.name == "automation.env.example":
                expected.add(Path("etc/mihomo-automation.env.example"))
            elif relative.name == "mihomo-alert.env.example":
                expected.add(Path("etc/mihomo-alert.env.example"))
            else:
                expected.add(Path("etc/mihomo") / relative.name)
        else:
            fail(f"install.sh has no destination rule for {relative}")
    expected.add(Path("etc/dnsmasq-upstreams.conf"))
    expected.add(Path("var/lib/mihomo-automation/installed-manifest.json"))
    return expected


def _loop_body(text: str, marker: str) -> str:
    """Return the body of a `for ... in` unit list, one line or continued."""
    match = re.search(re.escape(marker) + r"(.*?);\s*do", text, re.DOTALL)
    return match.group(1) if match else ""


def check_installer() -> None:
    installer = AUTOMATION / "install.sh"
    if not installer.stat().st_mode & stat.S_IXUSR:
        fail("automation/install.sh is not executable")

    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        root = base / "root"
        # Model an existing gateway whose private manifest predates the new
        # route tables. The installer must preserve its bytes while seeding the
        # public defaults as an independent 0644 file.
        old_manifest = (
            b"version: 1\n"
            b"measurement:\n  enabled: false\n  accounts:\n"
            b"    synthetic-account:\n      prefixes: ['Private AI | ']\n"
            b"      native_probes_per_hour: 12\n      quality_attempts_per_hour: 24\n"
            b"roles:\n  bulk_download:\n    stt_prefix: 'Private STT | '\n"
            b"overlay:\n  ai_nodes: ['Private AI | 1']\n"
            b"download_quality:\n  profiles:\n    packages:\n"
            b"      group: Package Download\n      domains: [private.example]\n"
        )
        old_manifest_path = root / "etc/mihomo/adaptive/capabilities.yaml"
        old_manifest_path.parent.mkdir(parents=True)
        old_manifest_path.write_bytes(old_manifest)
        # The NetworkManager dispatcher is installed only where NetworkManager
        # exists; pre-build that directory so the sandbox exercises the install.
        (root / "etc/NetworkManager").mkdir(parents=True, exist_ok=True)
        # A failing systemctl stub proves the sandbox run never touches systemd.
        stub_dir = base / "bin"
        stub_dir.mkdir()
        marker = base / "systemctl-called"
        stub = stub_dir / "systemctl"
        stub.write_text(
            f'#!/bin/sh\necho "systemctl must not run during a sandbox install" >&2\n'
            f'echo called >> "{marker}"\nexit 1\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        sandbox_env = dict(
            os.environ,
            GATEWAY_ROOT=str(root),
            PATH=f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        )
        result = subprocess.run(
            ["sh", str(installer)],
            capture_output=True,
            text=True,
            env=sandbox_env,
            check=False,
        )
        check(result.returncode == 0, f"install.sh failed: {result.stderr.strip()[:400]}")
        if result.returncode != 0:
            return
        check(not marker.exists(), "a sandbox install ran systemctl")

        expected = expected_install_map(root)
        installed = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
        check(installed == expected, f"installed layout differs: missing={sorted(expected - installed)} "
                                    f"extra={sorted(installed - expected)}")
        for path in sorted(root.glob("usr/local/sbin/*")):
            check(
                bool(path.stat().st_mode & stat.S_IXUSR),
                f"{path.relative_to(root)} is not executable",
            )
        for path in sorted((root / "etc/systemd/system").rglob("*")):
            if path.is_file():
                check(
                    not path.stat().st_mode & stat.S_IXUSR,
                    f"{path.relative_to(root)} must not be executable",
                )
        dispatcher = root / "etc/NetworkManager/dispatcher.d/90-wifi-management-routing"
        check(dispatcher.is_file(), "install.sh did not install the NetworkManager dispatcher")
        if dispatcher.is_file():
            check(
                dispatcher.stat().st_mode & 0o777 == 0o700,
                "the NetworkManager dispatcher must be 0700",
            )
        conf = root / "etc/dnsmasq-upstreams.conf"
        check(conf.is_file(), "install.sh did not seed /etc/dnsmasq-upstreams.conf")
        check(
            conf.read_bytes() == (AUTOMATION / "etc" / "dnsmasq" / "upstreams.conf").read_bytes(),
            "the seeded dnsmasq upstream file is not the direct baseline",
        )
        for retired in (root / "var/lib/mihomo-download-quality",
                        root / "var/lib/mihomo-social-quality"):
            check(not retired.exists(), f"installer created retired quality state: {retired.relative_to(root)}")

        manifest = root / "etc/mihomo/adaptive/capabilities.yaml"
        route_defaults = root / "etc/mihomo/adaptive/route-defaults.yaml"
        check(manifest.read_bytes() == old_manifest,
              "install.sh changed a pre-existing private capabilities.yaml")
        check(route_defaults.is_file(), "install.sh did not seed independent route-defaults.yaml")
        check(route_defaults.read_bytes() == (AUTOMATION / "policy" / "route-defaults.yaml").read_bytes(),
              "installed route-defaults.yaml differs from the public source")
        check(stat.S_IMODE(route_defaults.stat().st_mode) == 0o644,
              "route-defaults.yaml must be installed with mode 0644")

        installer_text = installer.read_text(encoding="utf-8")
        enabled = set(
            re.findall(
                r"([a-z0-9@._-]+\.(?:service|timer))",
                _loop_body(installer_text, "for unit in"),
            )
        )
        check(
            enabled == set(ENABLED_UNITS),
            f"installer enables {sorted(enabled)}, expected the recorded twelve {sorted(ENABLED_UNITS)}",
        )
        dormant = set(
            re.findall(
                r"([a-z0-9@._-]+\.timer)",
                _loop_body(installer_text, "for retired in"),
            )
        )
        check(
            dormant == set(DORMANT_TIMERS),
            f"installer disables {sorted(dormant)}, expected the dormant four {sorted(DORMANT_TIMERS)}",
        )
        for unit in ENABLED_UNITS + DORMANT_TIMERS:
            if unit == "dnsmasq.service":
                continue
            check(
                (root / "etc/systemd/system" / unit).is_file(),
                f"installer did not install {unit}",
            )
        check(
            not any("adaptive" in unit for unit in enabled | dormant),
            "installer still manages a retired adaptive unit",
        )

        # Deployment data the operator already edited must survive a re-install.
        manifest.write_text("# edited locally\n", encoding="utf-8")
        route_defaults.write_text("# edited defaults locally\n", encoding="utf-8")
        rerun = subprocess.run(
            ["sh", str(installer)],
            capture_output=True,
            text=True,
            env=sandbox_env,
            check=False,
        )
        check(rerun.returncode == 0, f"second install run failed: {rerun.stderr.strip()[:400]}")
        check(not marker.exists(), "the second sandbox install ran systemctl")
        check(
            manifest.read_text(encoding="utf-8") == "# edited locally\n",
            "install.sh overwrote the operator's capabilities.yaml",
        )
        check(
            route_defaults.read_text(encoding="utf-8") == "# edited defaults locally\n",
            "install.sh overwrote the operator's route-defaults.yaml",
        )

        # --dry-run must not create anything.
        dry_root = base / "dry"
        dry = subprocess.run(
            ["sh", str(installer), "--dry-run"],
            capture_output=True,
            text=True,
            env=dict(sandbox_env, GATEWAY_ROOT=str(dry_root)),
            check=False,
        )
        check(dry.returncode == 0, f"install.sh --dry-run failed: {dry.stderr.strip()[:400]}")
        check(not dry_root.exists(), "install.sh --dry-run created files")
        check(not marker.exists(), "install.sh --dry-run ran systemctl")
        check("install -m 0755" in dry.stdout, "install.sh --dry-run did not report planned installs")


def load_module(path: Path):
    # The published scripts carry no .py suffix, so the loader is named
    # explicitly instead of relying on suffix-based detection.
    loader = importlib.machinery.SourceFileLoader(f"published_{path.stem}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def check_runtime_parameters() -> None:
    environment = dict(
        os.environ,
        MIHOMO_MIXED_PORT="17890",
        MIHOMO_CONTROLLER_PORT="19090",
        MIHOMO_CAPABILITIES="/tmp/example-capabilities.yaml",
        MIHOMO_ROUTE_DEFAULTS="/tmp/example-route-defaults.yaml",
    )
    saved = dict(os.environ)
    os.environ.update(environment)
    try:
        overlay = load_module(AUTOMATION / "sbin" / "mihomo-policy-overlay")
        check(
            str(overlay.CAPABILITIES_PATH) == "/tmp/example-capabilities.yaml",
            f"mihomo-policy-overlay manifest path is not environment driven: {overlay.CAPABILITIES_PATH}",
        )
        check(
            str(overlay.ROUTE_DEFAULTS_PATH) == "/tmp/example-route-defaults.yaml",
            f"mihomo-policy-overlay route defaults path is not environment driven: {overlay.ROUTE_DEFAULTS_PATH}",
        )
    finally:
        os.environ.clear()
        os.environ.update(saved)


def check_subscription_environment() -> None:
    """Run the updater's parameter block and import its real Python child."""
    source = (AUTOMATION / "sbin/mihomo-update-subscription").read_text()
    parameters = source[source.index("# Deployment parameters."):source.index("proxy=http")]
    child = (
        "import runpy, sys; values = runpy.run_path(sys.argv[1]); "
        "print(values['CAPABILITIES_PATH']); print(values['ROUTE_DEFAULTS_PATH'])"
    )
    with tempfile.TemporaryDirectory() as raw:
        env_file = Path(raw) / "automation.env"
        script = Path(raw) / "parameters.sh"
        script.write_text(
            "set -eu\n" + parameters.replace("/etc/mihomo-automation.env", str(env_file))
            + '\nexec "$@"\n'
        )
        environment = dict(os.environ)
        for key, default in (
            ("MIHOMO_CAPABILITIES", "/etc/mihomo/adaptive/capabilities.yaml"),
            ("MIHOMO_ROUTE_DEFAULTS", "/etc/mihomo/adaptive/route-defaults.yaml"),
        ):
            environment.pop(key, None)
        cases = (
            ("default", None, None, None),
            ("inherited", None, "/tmp/inherited.yaml", "/tmp/inherited.yaml"),
            ("sourced", "/tmp/custom manifest.yaml", None, "/tmp/custom manifest.yaml"),
            ("override", "/tmp/custom.yaml", "/tmp/inherited.yaml", "/tmp/custom.yaml"),
            ("empty sourced", "", None, None),
            ("empty inherited", None, "", None),
            ("empty override", "", "/tmp/inherited.yaml", None),
        )
        for key, default in (
            ("MIHOMO_CAPABILITIES", "/etc/mihomo/adaptive/capabilities.yaml"),
            ("MIHOMO_ROUTE_DEFAULTS", "/etc/mihomo/adaptive/route-defaults.yaml"),
        ):
            other_key, other_default = next(
                item for item in (("MIHOMO_CAPABILITIES", "/etc/mihomo/adaptive/capabilities.yaml"),
                                  ("MIHOMO_ROUTE_DEFAULTS", "/etc/mihomo/adaptive/route-defaults.yaml"))
                if item[0] != key
            )
            for label, configured, inherited, expected in cases:
                configured_values = []
                if configured is not None:
                    configured_values.append(f"{key}='{configured}'")
                if env_file.exists():
                    env_file.unlink()
                env_file.write_text("\n".join(configured_values) + "\n", encoding="utf-8")
                case_env = dict(environment)
                if inherited is not None:
                    case_env[key] = inherited
                result = subprocess.run(
                    ["sh", str(script), sys.executable, "-c", child,
                     str(AUTOMATION / "sbin/mihomo-policy-overlay")],
                    env=case_env, capture_output=True, text=True, check=True,
                )
                expected_value = expected if expected is not None else default
                expected_paths = {key: expected_value, other_key: other_default}
                check(result.stdout.splitlines() == [
                    expected_paths["MIHOMO_CAPABILITIES"],
                    expected_paths["MIHOMO_ROUTE_DEFAULTS"],
                ],
                      f"subscription environment failed: {key} {label}")


def check_no_bytecode() -> None:
    for path in automation_files():
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            fail(f"{path.relative_to(REPO)} must not be committed")


def main() -> int:
    check_no_bytecode()
    check_syntax()
    check_node_labels()
    check_forbidden()
    check_environment_documentation()
    check_unit_layout()
    check_manifest()
    check_installer()
    check_runtime_parameters()
    check_subscription_environment()

    if FAILURES:
        print("automation checks FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"automation checks passed ({len(automation_files())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
