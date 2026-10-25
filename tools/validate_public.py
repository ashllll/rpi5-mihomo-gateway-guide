#!/usr/bin/env python3
"""Validate the published repository structure and privacy.

The repository mirrors one running gateway: the automation layer, the pinned
core patch, the documents and the diagrams the machine actually uses.  This
checker fails when a required piece is missing, when the publication contains a
symlink or an unapproved binary, or when a tracked text file matches one of the
privacy patterns in publication_privacy.py.

Run from the repository root:  python3 tools/validate_public.py
"""

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from publication_privacy import (
    ARCHIVE_PATHS,
    ARCHIVE_PATTERNS,
    PUBLIC_PATTERNS,
    matching_labels,
)

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = (
    "README.md",
    "GLOSSARY.md",
    "SECURITY.md",
    "LICENSE",
    "docs/README.md",
    "docs/architecture.zh-CN.md",
    "docs/deployment.zh-CN.md",
    "docs/operations.zh-CN.md",
    "docs/deployment-debugging-summary.zh-CN.md",
    "docs/ucg-wireguard-pilot.zh-CN.md",
    "docs/recovery-image.zh-CN.md",
    "docs/adr/README.md",
    "docs/adr/0001-private-deployment-data.md",
    "docs/adr/0002-mihomo-authority.md",
    "docs/adr/0003-capability-policy.md",
    "docs/adr/0004-conservative-recovery.md",
    "docs/adr/0005-trusted-lan.md",
    "docs/adr/0006-download-provider-ranking.md",
    "docs/adr/0007-native-routing-budget.md",
    "docs/wiki/architecture.md",
    "docs/wiki/download-routing.md",
    "docs/wiki/getting-started.md",
    "docs/wiki/maintenance.md",
    "docs/wiki/operations.md",
    "docs/wiki/reference.md",
    "docs/wiki/roadmap.md",
    "docs/wiki/security.md",
    "docs/wiki/validation.md",
    "docs/wiki/x-video-routing.md",
    "docs/research/landscape-acceptance.zh-CN.md",
    "docs/research/landscape-capabilities.zh-CN.md",
    "docs/research/landscape-feasibility.zh-CN.md",
    "assets/network-topology.svg",
    "assets/network-topology.png",
    "assets/historical-automation-2026-10-11.mmd",
    "assets/historical-automation-2026-10-11.svg",
    "assets/diagram-theme.json",
    "assets/readme/hero.svg",
    "assets/readme/path.svg",
    "assets/readme/gates.svg",
    "automation/install.sh",
    "automation/README.zh-CN.md",
    "automation/env/automation.env.example",
    "automation/env/mihomo-alert.env.example",
    "automation/etc/dnsmasq/lan.conf",
    "automation/etc/dnsmasq/vlan-pilot.conf",
    "automation/etc/dnsmasq/upstreams.conf",
    "automation/etc/mosdns/config.yaml",
    "automation/etc/mihomo/config.local.yaml",
    "automation/etc/mihomo/README.md",
    "automation/policy/capabilities.yaml",
    "automation/policy/route-defaults.yaml",
    "core/README.md",
    "core/upstream.json",
    "core/mihomo-v1.19.28-budget.patch",
    "image/gateway_image.py",
    "image/sources.json",
    "examples/ucg-pilot.example.json",
    "tools/check_automation.py",
    "tools/check_overlay.py",
)

# The diagrams are hand-edited SVG; parsing them proves neither file was
# truncated by an editor or a render script.
PARSEABLE_SVG = (
    "assets/network-topology.svg",
    "assets/historical-automation-2026-10-11.svg",
    "assets/readme/hero.svg",
    "assets/readme/path.svg",
    "assets/readme/gates.svg",
)


def tracked_files():
    """Every tracked or untracked-but-not-ignored file, as git sees it."""
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        check=True,
    )
    return [name for name in completed.stdout.decode().split("\0") if name]


def main() -> int:
    failures = []

    for name in REQUIRED:
        if not (ROOT / name).is_file():
            failures.append("missing required file: {}".format(name))

    for name in PARSEABLE_SVG:
        path = ROOT / name
        if path.is_file():
            try:
                ET.parse(str(path))
            except ET.ParseError as error:
                failures.append("{}: unparsable SVG: {}".format(name, error))

    png = ROOT / "assets/network-topology.png"
    if png.is_file() and png.stat().st_size < 100_000:
        failures.append(
            "assets/network-topology.png: too small ({} bytes)".format(
                png.stat().st_size
            )
        )

    archive = "\n".join(
        (ROOT / name).read_text(errors="replace")
        for name in ARCHIVE_PATHS
        if (ROOT / name).is_file()
    )
    for label in matching_labels(archive, ARCHIVE_PATTERNS):
        failures.append("documentation archive: {} (value redacted)".format(label))

    paths = tracked_files()
    for name in paths:
        path = ROOT / name
        if not path.is_file():
            continue
        if path.is_symlink():
            failures.append("{}: symlinks not allowed".format(name))
            continue
        content = path.read_bytes()
        if b"\0" in content and name != "assets/network-topology.png":
            failures.append("{}: unapproved binary".format(name))
            continue
        text = content.decode("utf-8", errors="ignore")
        for label in matching_labels(text, PUBLIC_PATTERNS):
            failures.append("{}: {} (value redacted)".format(name, label))

    if failures:
        for failure in failures:
            print("BLOCKED {}".format(failure))
        return 1

    print(
        "Publication structure and privacy checks passed: {} files".format(len(paths))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
