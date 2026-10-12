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
* the curated-pool keys read by the overlay match automation/policy/capabilities.yaml,
* `automation/install.sh` installs the documented layout into a sandbox, keeps
  operator-edited deployment data, and changes nothing in --dry-run mode,
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

from publication_privacy import AUTOMATION_PATTERNS, NODE_PATTERNS, matching_labels

# The subscription validator has to recognise the provider's announcement
# entries; those strings are the only non-ASCII content allowed in the layer.
CJK_ALLOWED = {"sbin/mihomo-subscription-candidate"}

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
    skipped = {"install.sh"}
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


def documented_variables() -> set[str]:
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE))


def check_environment_documentation() -> None:
    used = used_variables()
    documented = documented_variables()
    read = set().union(*used.values()) if used else set()
    missing = sorted(read - documented)
    if missing:
        fail(f"environment variables are read but not documented in env/automation.env.example: {missing}")
    unused = sorted(documented - read)
    if unused:
        fail(f"env/automation.env.example documents variables no script reads: {unused}")

    # A unit that runs an environment-dependent script must load the env file.
    dependent = {
        Path(name).name
        for name, variables in used.items()
        if variables and "sbin/" in name
    }
    exec_directive = re.compile(r"^Exec(?:Start|StartPre|StartPost|Stop|StopPost|Reload)=", re.MULTILINE)
    for unit in sorted((AUTOMATION / "systemd").rglob("*")):
        if not unit.is_file():
            continue
        text = read_source(unit)
        if not exec_directive.search(text):
            continue
        invoked = {name for name in dependent if re.search(rf"\b{re.escape(name)}\b", text)}
        if invoked and "EnvironmentFile=-/etc/mihomo-automation.env" not in text:
            fail(
                f"systemd/{unit.relative_to(AUTOMATION / 'systemd')} runs {sorted(invoked)} "
                "without EnvironmentFile=-/etc/mihomo-automation.env"
            )


def overlay_manifest_keys(source: str) -> set[str]:
    """Read literal manifest keys from Python calls regardless of formatting."""
    keys = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "overlay_labels":
            continue
        key = node.args[1] if len(node.args) > 1 else next(
            (item.value for item in node.keywords if item.arg == "key"), None
        )
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ValueError("overlay_labels requires a literal manifest key for coverage checks")
        keys.add(key.value)
    return keys


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
            else:
                expected.add(Path("etc") / Path(*parts[1:]))
        elif parts[0] == "policy":
            expected.add(Path("etc/mihomo/adaptive") / relative.name)
        elif parts[0] == "env":
            if relative.name == "automation.env.example":
                expected.add(Path("etc/mihomo-automation.env.example"))
            else:
                expected.add(Path("etc/mihomo") / relative.name)
        else:
            fail(f"install.sh has no destination rule for {relative}")
    expected.add(Path("etc/dnsmasq-upstreams.conf"))
    return expected


def check_installer() -> None:
    installer = AUTOMATION / "install.sh"
    if not installer.stat().st_mode & stat.S_IXUSR:
        fail("automation/install.sh is not executable")

    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        root = base / "root"
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
        conf = root / "etc/dnsmasq-upstreams.conf"
        check(conf.is_file(), "install.sh did not seed /etc/dnsmasq-upstreams.conf")
        check(
            conf.read_bytes() == (AUTOMATION / "etc" / "dnsmasq-upstreams.direct").read_bytes(),
            "the seeded dnsmasq upstream file is not the direct baseline",
        )

        # Deployment data the operator already edited must survive a re-install.
        manifest = root / "etc/mihomo/adaptive/capabilities.yaml"
        manifest.write_text("# edited locally\n", encoding="utf-8")
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
        GATEWAY_LAN_IP="10.66.0.9",
        MIHOMO_MIXED_PORT="17890",
        MIHOMO_CONTROLLER_PORT="19090",
        MIHOMO_CAPABILITIES="/tmp/example-capabilities.yaml",
    )
    saved = dict(os.environ)
    os.environ.update(environment)
    try:
        score = load_module(AUTOMATION / "sbin" / "mihomo-adaptive-score")
        apply_module = load_module(AUTOMATION / "sbin" / "mihomo-adaptive-apply")
        overlay = load_module(AUTOMATION / "sbin" / "mihomo-policy-overlay")
        check(
            score.CONTROLLER == "http://10.66.0.9:19090",
            f"mihomo-adaptive-score controller is not environment driven: {score.CONTROLLER}",
        )
        check(
            apply_module.PROXY == "http://10.66.0.9:17890",
            f"mihomo-adaptive-apply proxy is not environment driven: {apply_module.PROXY}",
        )
        check(
            str(overlay.CAPABILITIES_PATH) == "/tmp/example-capabilities.yaml",
            f"mihomo-policy-overlay manifest path is not environment driven: {overlay.CAPABILITIES_PATH}",
        )
        check(
            str(score.CAPABILITIES_PATH) == "/tmp/example-capabilities.yaml",
            f"mihomo-adaptive-score manifest path is not environment driven: {score.CAPABILITIES_PATH}",
        )
    finally:
        os.environ.clear()
        os.environ.update(saved)


def check_subscription_environment() -> None:
    """Run the updater's parameter block and import its real Python child."""
    source = (AUTOMATION / "sbin/mihomo-update-subscription").read_text()
    parameters = source[source.index("# Deployment parameters."):source.index("proxy=http")]
    child = (
        "import runpy, sys; "
        "print(runpy.run_path(sys.argv[1])['CAPABILITIES_PATH'])"
    )
    with tempfile.TemporaryDirectory() as raw:
        env_file = Path(raw) / "automation.env"
        script = Path(raw) / "parameters.sh"
        script.write_text(
            "set -eu\n" + parameters.replace("/etc/mihomo-automation.env", str(env_file))
            + '\nexec "$@"\n'
        )
        environment = dict(os.environ)
        environment.pop("MIHOMO_CAPABILITIES", None)
        for label, configured, inherited, expected in (
            ("default", None, None, "/etc/mihomo/adaptive/capabilities.yaml"),
            ("inherited", None, "/tmp/inherited.yaml", "/tmp/inherited.yaml"),
            ("sourced", "/tmp/custom manifest.yaml", None, "/tmp/custom manifest.yaml"),
            ("override", "/tmp/custom.yaml", "/tmp/inherited.yaml", "/tmp/custom.yaml"),
            ("empty sourced", "", None, "/etc/mihomo/adaptive/capabilities.yaml"),
            ("empty inherited", None, "", "/etc/mihomo/adaptive/capabilities.yaml"),
            ("empty override", "", "/tmp/inherited.yaml", "/etc/mihomo/adaptive/capabilities.yaml"),
        ):
            if configured is not None:
                env_file.write_text(f"MIHOMO_CAPABILITIES='{configured}'\n")
            elif env_file.exists():
                env_file.unlink()
            case_env = dict(environment)
            if inherited is not None:
                case_env["MIHOMO_CAPABILITIES"] = inherited
            result = subprocess.run(
                ["sh", str(script), sys.executable, "-c", child,
                 str(AUTOMATION / "sbin/mihomo-policy-overlay")],
                env=case_env, capture_output=True, text=True, check=True,
            )
            check(result.stdout.strip() == expected, f"subscription environment failed: {label}")


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
