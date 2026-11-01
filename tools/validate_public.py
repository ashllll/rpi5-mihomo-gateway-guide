#!/usr/bin/env python3
"""Validate the published repository structure and privacy.

The repository mirrors one running gateway: the automation layer, the pinned
core patch, the documents and the diagrams the machine actually uses.  This
checker fails when a required piece is missing, when the publication contains a
symlink or an unapproved binary, when a tracked text file matches a publication
pattern, or when a commit after the remediation baseline carries one.

Privacy is checked in four layers:

* structure -- required files, parseable SVGs, the one approved binary;
* content   -- every tracked text file (and the PNG payload) against
               ``PUBLIC_PATTERNS``, plus base64 payloads;
* docs      -- the public documentation surface against ``DOC_PATTERNS`` and a
               documented-network check for private IPv4 addresses;
* history   -- commit trees and messages for every commit reachable from
               HEAD.  The published history was rewritten on 2026-11-02, so
               every commit is clean; there is deliberately no baseline that
               could keep older commits out of the gate.

Run from the repository root:  python3 tools/validate_public.py
"""

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from publication_privacy import (
    ARCHIVE_PATHS,
    ARCHIVE_PATTERNS,
    DOC_PATTERNS,
    PUBLIC_PATTERNS,
    base64_labels,
    documented_networks,
    matching_labels,
    undocumented_private_addresses,
)

ROOT = Path(__file__).resolve().parents[1]
DOC_ROOT_FILES = ("README.md", "SECURITY.md", "GLOSSARY.md")
DOC_PREFIX = "docs/"

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
    "automation/etc/dnsmasq/resolver.conf",
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


def is_public_doc(name):
    return name in DOC_ROOT_FILES or name.startswith(DOC_PREFIX)


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def git_text(*args):
    completed = git(*args)
    return completed.returncode, completed.stdout.decode("utf-8", "ignore")


def batch_objects(items):
    """Yield (sha, path, bytes-or-None) for blob objects, one git process."""
    if not items:
        return
    request = "".join(sha + "\n" for sha, _ in items).encode()
    completed = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=ROOT, input=request, stdout=subprocess.PIPE
    )
    data = completed.stdout
    position = 0
    for sha, path in items:
        end = data.find(b"\n", position)
        if end < 0:
            return
        header = data[position:end].decode("utf-8", "replace").split()
        position = end + 1
        if len(header) < 3:
            yield sha, path, None
            continue
        size = int(header[2])
        content = data[position:position + size]
        position += size + 1
        yield (sha, path, content if header[1] == "blob" else None)


def history_findings():
    findings = []
    revision_range = "HEAD"
    code, count_text = git_text("rev-list", "--count", revision_range)
    if code != 0:
        return ["history unavailable: git rev-list failed"], 0
    scanned = int(count_text.strip() or 0)
    if scanned == 0:
        return findings, scanned

    code, log = git_text("log", "--format=%H%x1f%B%x1e", revision_range)
    if code == 0:
        for record in log.split("\x1e"):
            record = record.strip("\n")
            if "\x1f" not in record:
                continue
            sha, message = record.split("\x1f", 1)
            for label in matching_labels(message, PUBLIC_PATTERNS):
                findings.append("commit {} message: {}".format(sha[:12], label))

    code, listing = git_text("rev-list", "--objects", revision_range)
    items = []
    seen = set()
    if code == 0:
        for line in listing.splitlines():
            sha, _, path = line.partition(" ")
            if sha in seen:
                continue
            seen.add(sha)
            items.append((sha, path))

    doc_texts = []
    for name in tracked_files():
        if is_public_doc(name):
            path = ROOT / name
            if path.is_file():
                doc_texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    allowed = documented_networks(doc_texts)

    for sha, path, content in batch_objects(items):
        if not content or b"\0" in content:
            continue
        text = content.decode("utf-8", "ignore")
        labels = matching_labels(text, PUBLIC_PATTERNS) + base64_labels(text)
        if is_public_doc(path):
            labels += matching_labels(text, DOC_PATTERNS)
            if undocumented_private_addresses(text, allowed):
                labels.append("undocumented private address")
        for label in labels:
            findings.append("commit tree {}:{}: {}".format(sha[:12], path or "(object)", label))
    return findings, scanned


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
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
    doc_texts = []
    for name in paths:
        if is_public_doc(name):
            path = ROOT / name
            if path.is_file():
                doc_texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    allowed = documented_networks(doc_texts)

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
        labels = matching_labels(text, PUBLIC_PATTERNS) + base64_labels(text)
        if is_public_doc(name):
            labels += matching_labels(text, DOC_PATTERNS)
            if undocumented_private_addresses(text, allowed):
                labels.append("undocumented private address")
        labels += matching_labels(name, PUBLIC_PATTERNS)
        for label in labels:
            failures.append("{}: {} (value redacted)".format(name, label))

    history, scanned = history_findings()
    for finding in history:
        failures.append("history {} (value redacted)".format(finding))

    print(
        "history: {} commit(s) scanned; full reachable history is in scope".format(scanned)
    )

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
