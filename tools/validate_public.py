#!/usr/bin/env python3
"""Validate all publication files without printing matching secrets."""
from html.parser import HTMLParser
from pathlib import Path
from publication_privacy import PUBLIC_PATTERNS, ARCHIVE_PATTERNS, ARCHIVE_PATHS, matching_labels
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    required = ["README.md", "SECURITY.md", "LICENSE", "examples/site.example.json",
                "docs/current-architecture.zh-CN.md", "docs/deployment-current.zh-CN.md",
                "docs/deployment-debugging-summary.zh-CN.md",
                "docs/adaptive-selection.zh-CN.md", "docs/operations-current.zh-CN.md",
                "assets/network-topology.svg", "assets/network-topology.png", "web/index.html",
                "automation/install.sh", "automation/README.zh-CN.md",
                "automation/env/automation.env.example", "automation/policy/capabilities.yaml",
                "tools/check_automation.py", "tools/check_overlay.py"]
    for name in required:
        if not (ROOT / name).is_file():
            raise SystemExit(f"Missing publication file: {name}")
    # Every diagram the README embeds must be a parseable SVG next to its source.
    for stem in ("current-topology", "current-dns-flow", "current-routing-policy", "current-automation"):
        if not (ROOT / f"assets/{stem}.mmd").is_file():
            raise SystemExit(f"Missing diagram source: assets/{stem}.mmd")
        ET.parse(ROOT / f"assets/{stem}.svg")
    ET.parse(ROOT / "assets/network-topology.svg")
    html = (ROOT / "web/index.html").read_text()
    HTMLParser().feed(html)
    if "../assets/network-topology.svg" not in html:
        raise SystemExit("Archived HTML must reference its SVG")
    if (ROOT / "assets/network-topology.png").stat().st_size < 100_000:
        raise SystemExit("Archived PNG is unexpectedly small")
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, check=True)
    archive = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ARCHIVE_PATHS)
    failures = [("documentation archive", label) for label in matching_labels(archive, ARCHIVE_PATTERNS)]
    paths = sorted(set(p.decode() for p in result.stdout.split(b"\0") if p))
    for name in paths:
        path = ROOT / name
        if not path.is_file():
            continue
        if path.is_symlink():
            failures.append((name, "symlinks not allowed"))
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            if name != "assets/network-topology.png":
                failures.append((name, "unapproved binary"))
            continue
        content = raw.decode("utf-8")
        failures.extend((name, label) for label in matching_labels(content, PUBLIC_PATTERNS))
    if failures:
        for name, label in failures:
            print(f"BLOCKED {name}: {label} (value redacted)")
        raise SystemExit(1)
    print(f"Publication structure and privacy checks passed: {len(paths)} files")


if __name__ == "__main__":
    main()
