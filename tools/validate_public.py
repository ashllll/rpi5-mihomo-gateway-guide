#!/usr/bin/env python3
"""Validate all publication files without printing matching secrets."""
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    required = ["README.md", "SECURITY.md", "LICENSE", "examples/site.example.json",
                "docs/current-architecture.zh-CN.md", "docs/deployment-current.zh-CN.md",
                "docs/adaptive-selection.zh-CN.md", "docs/operations-current.zh-CN.md",
                "assets/network-topology.svg", "assets/network-topology.png", "web/index.html"]
    for name in required:
        if not (ROOT / name).is_file():
            raise SystemExit(f"Missing publication file: {name}")
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
    patterns = {
        "private user path": r"/(?:Users|home)/[A-Za-z0-9._-]+/",
        "subscription endpoint": r"https?://[^\s\"'<>]*(?:glados-config\.com|bwbwbw\.cc|infraapi\.cloud)[^\s\"'<>]*",
        "UUID/node identity": r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b",
        "MAC identity": r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b",
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b",
        "public IPv6 identity": r"\b(?:2[0-9a-f]{3}:)(?:[0-9a-f]{0,4}:){2,}[0-9a-f]{0,4}\b",
    }
    failures = []
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
        for label, pattern in patterns.items():
            if re.search(pattern, content, re.I):
                failures.append((name, label))
    if failures:
        for name, label in failures:
            print(f"BLOCKED {name}: {label} (value redacted)")
        raise SystemExit(1)
    print(f"Publication structure and privacy checks passed: {len(paths)} files")


if __name__ == "__main__":
    main()
