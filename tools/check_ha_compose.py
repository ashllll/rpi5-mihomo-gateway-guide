#!/usr/bin/env python3
"""The Home Assistant container template is digest-pinned and skips host D-Bus.

F-13/SC-06: the recorded container ran `:stable`, mounted /run/dbus and stayed
outside the repository.  This check keeps the zero-risk fixes in place:

* the image is an immutable digest, never `:stable`/`:latest`; the compose file
  fails closed when HA_IMAGE is unset;
* /run/dbus is not mounted;
* privileged / host networking are either removed, or explicitly marked as the
  recorded state for the stage-3 integration regression (STAGE3(SC-06));
* the config mount, container name and restart policy match the recorded box.

`docker compose config` is the CI/real-host command; this check parses the same
YAML without Docker so the local gate can prove the invariants.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "ops/homeassistant/docker-compose.yml"
MARKER = "STAGE3(SC-06)"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")


def problems_for(path: Path, text: str, service: dict) -> list[str]:
    problems: list[str] = []
    image = str(service.get("image") or "")
    if not image:
        problems.append("homeassistant.image is missing")
    if re.search(r":(stable|latest)\b", image):
        problems.append("image still uses a floating tag")
    if "@sha256:" not in image and "sha256:<digest>" not in image:
        problems.append("image must be pinned to a digest (or require HA_IMAGE with a digest)")
    if "privileged" not in service:
        problems.append("privileged must be explicit (true with STAGE3 marker, or false)")
    if service.get("privileged") is True and MARKER not in text:
        problems.append("privileged: true must carry the STAGE3(SC-06) regression marker")
    if service.get("network_mode") == "host" and MARKER not in text:
        problems.append("network_mode: host must carry the STAGE3(SC-06) regression marker")
    volumes = [str(item) for item in (service.get("volumes") or [])]
    if any(volume.split(":", 1)[0].rstrip("/") == "/run/dbus" for volume in volumes):
        problems.append("container still mounts host /run/dbus")
    if not any(volume.split(":", 1)[0].rstrip("/") == "/srv/homeassistant" for volume in volumes):
        problems.append("config mount /srv/homeassistant is missing")
    if service.get("container_name") != "homeassistant":
        problems.append("container_name must stay homeassistant")
    if service.get("restart") != "unless-stopped":
        problems.append("restart policy must stay unless-stopped")
    return problems


def main() -> int:
    if not COMPOSE.is_file():
        print("homeassistant: FAIL: missing " + str(COMPOSE.relative_to(ROOT)))
        return 1
    text = COMPOSE.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    service = ((document or {}).get("services") or {}).get("homeassistant")
    if not isinstance(service, dict):
        print("homeassistant: FAIL: services.homeassistant is missing")
        return 1
    problems = problems_for(COMPOSE, text, service)
    if shutil.which("docker"):
        env = dict(os.environ)
        env.setdefault("HA_IMAGE", "ghcr.io/home-assistant/home-assistant@sha256:" + "0" * 64)
        result = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "config"],
                                capture_output=True, text=True, env=env, check=False)
        if result.returncode != 0:
            problems.append("docker compose config failed: " + result.stderr.strip()[:300])
    if problems:
        print("homeassistant: FAIL")
        for problem in problems:
            print("  - " + problem)
        return 1
    print("homeassistant: ok (digest-pinned template, no host D-Bus, residual marker)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
