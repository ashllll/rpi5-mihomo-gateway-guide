#!/usr/bin/env python3
"""Keep documented counts equal to what install.sh and the trees actually do.

Counts drifted across nine documents because each one was updated by hand.  This
check derives every number from automation/install.sh and the installed trees,
compares the numbers the documents state, and holds the deployment dependency
list to the commands the managed scripts actually use.  Documentation facts must
be derivable, not remembered.

Run:  python3 tools/check_doc_facts.py [--root REPO]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TREE_DOCS = [
    "README.md",
    "SECURITY.md",
    "docs/deployment.zh-CN.md",
    "docs/operations.zh-CN.md",
    "docs/architecture.zh-CN.md",
    "docs/wiki/reference.md",
    "docs/wiki/getting-started.md",
    "docs/wiki/roadmap.md",
    "GLOSSARY.md",
    "automation/README.zh-CN.md",
    "docs/recovery-image.zh-CN.md",
]

CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
NUMBER = r"(\d+|[一二三四五六七八九十])"

COUNT_PATTERNS = [
    (re.compile(r"(\d+)\s*个单元"), "enabled_total"),
    (re.compile(r"(\d+)\s*个 enabled"), "enabled_total"),
    (re.compile(r"正好是\s*(\d+)\s*行"), "enabled_total"),
    (re.compile(r"(\d+)\s*个 disabled timer"), "retired"),
    (re.compile(r"(\d+)\s*个\s*.?mihomo\.service\.d"), "dropins"),
    (re.compile(r"(\d+)\s*个[^\n]{0,10}drop-in"), "dropins"),
    (re.compile(r"(\d+)\s*个脚本"), "sbin"),
    (re.compile(r"(\d+)\s*个 sbin"), "sbin"),
    (re.compile(r"automation/systemd/\*\*.{0,2}（(\d+)\s*个文件）"), "systemd_files"),
    (re.compile(NUMBER + r"\s*个 service \+ " + NUMBER + r"\s*个 timer"), "split"),
    (re.compile(NUMBER + r"\s*个 service、?" + NUMBER + r"\s*个 timer"), "split"),
]

# Commands and modules the managed scripts call; the deployment guide must install them.
REQUIREMENTS = [
    (re.compile(r"(?<![\w-])jq\b"), "jq"),
    (re.compile(r"(?<![\w-])dig\b"), "dnsutils"),
    (re.compile(r"(?<![\w-])flock\b"), "util-linux"),
    (re.compile(r"^\s*import yaml\b", re.M), "python3-yaml"),
]
APT_LINE = re.compile(r"apt install -y ([^\n]+)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_installer(root: Path) -> dict[str, int]:
    text = read(root / "automation" / "install.sh")
    enabled = re.search(r"for unit in \\\n(.*?); do", text, re.S)
    if not enabled:
        raise SystemExit("check_doc_facts: cannot find the enable list in install.sh")
    units = [line.strip().rstrip("\\").strip() for line in enabled.group(1).splitlines()]
    units = [unit for unit in units if unit]
    retired = re.search(r"for retired in ([^;]+); do", text)
    if not retired:
        raise SystemExit("check_doc_facts: cannot find the retire list in install.sh")

    dropins = list((root / "automation" / "systemd" / "mihomo.service.d").glob("*.conf"))
    sbin = [path for path in (root / "automation" / "sbin").iterdir() if path.is_file()]
    systemd = [path for path in (root / "automation" / "systemd").rglob("*") if path.is_file()]

    return {
        "enabled_total": len(units),
        "services": len([unit for unit in units if unit.endswith(".service")]),
        "timers": len([unit for unit in units if unit.endswith(".timer")]),
        "retired": len(retired.group(1).split()),
        "dropins": len(dropins),
        "sbin": len(sbin),
        "systemd_files": len(systemd),
    }


def required_packages(root: Path) -> set[str]:
    sources = [path for path in (root / "automation" / "sbin").iterdir() if path.is_file()]
    sources += [path for path in (root / "automation").glob("*.py") if path.is_file()]
    needed: set[str] = set()
    for path in sources:
        try:
            text = read(path)
        except UnicodeDecodeError:
            continue
        for pattern, package in REQUIREMENTS:
            if pattern.search(text):
                needed.add(package)
    return needed



# Claims that were measured and proven false.  Each entry stays here so the
# correction cannot silently regress; the third field lists the wording that
# makes the literal legitimate (for example "the unit does not declare <X>").
DENIED = [
    ("After=mihomo", "mosdns.service declares only network-online.target; the DNS upstream is converged by mihomo-dns-mode auto", "不声明"),
    ("never edits sshd", "the installer never writes sshd configuration, but it does manage other files", ""),
    ("开机 10 分钟", "the subscription timer starts 1 minute after boot and ticks every 10 minutes", ""),
    ("任务结束后每 48 小时", "the updater schedules 48 hours only after success; failures retry in minutes", ""),
    ("pinned to loopback", "listening is 0.0.0.0 with lan-allowed-ips and rp_filter; only the gateway own calls use 127.0.0.1", ""),
]


def scan_denied(relative, text, failures):
    for literal, reason, negated in DENIED:
        start = 0
        while True:
            index = text.find(literal, start)
            if index < 0:
                break
            start = index + 1
            if negated and negated in text[max(0, index - 30):index]:
                continue
            line = text[:index].count(chr(10)) + 1
            failures.append(relative + ':' + str(line) + ': states ' + repr(literal) + ', which is false: ' + reason)


def number(token: str) -> int:
    return int(token) if token.isdigit() else CN_NUMBERS.get(token, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    root = Path(parser.parse_args().root).resolve()

    facts = parse_installer(root)
    failures: list[str] = []
    claims = 0

    for relative in TREE_DOCS:
        path = root / relative
        if not path.is_file():
            failures.append(f"{relative}: missing")
            continue
        text = read(path)
        scan_denied(relative, text, failures)
        for pattern, key in COUNT_PATTERNS:
            for match in pattern.finditer(text):
                claims += 1
                stated = [number(group) for group in match.groups() if group]
                expected = [facts["services"], facts["timers"]] if key == "split" else [facts[key]] * len(stated)
                if stated != expected:
                    line = text[: match.start()].count("\n") + 1
                    failures.append(f"{relative}:{line}: states {stated}, derived {expected} ({key})")

    deployment = read(root / "docs" / "deployment.zh-CN.md")
    apt = APT_LINE.search(deployment)
    if not apt:
        failures.append("docs/deployment.zh-CN.md: no apt install line")
    else:
        packages = set(apt.group(1).split())
        for package in sorted(required_packages(root) - packages):
            failures.append(f"docs/deployment.zh-CN.md: dependency list is missing {package}")

    if failures:
        print("check_doc_facts: FAIL")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(
        "check_doc_facts: ok "
        f"({facts['enabled_total']} enabled = {facts['services']} service + {facts['timers']} timer, "
        f"{facts['retired']} retired, {facts['dropins']} drop-ins, {facts['sbin']} sbin scripts, "
        f"{facts['systemd_files']} unit files; {claims} stated counts across {len(TREE_DOCS)} documents)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
