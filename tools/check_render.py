#!/usr/bin/env python3
"""Check rendering isolation, parameter propagation and unsafe-site rejection."""
import copy
import json
import os
import subprocess
from pathlib import Path
import tempfile
from render import render


def read_environment(path):
    # The generated file must work with the shell used by manual automation.
    result = subprocess.run(
        ["sh", "-c", 'set -a; . "$1"; env', "sh", str(path)],
        env={"PATH": os.defpath}, capture_output=True, text=True, check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

root = Path(__file__).resolve().parents[1]
defaults = read_environment(root / "automation/env/automation.env.example")
shared_keys = {"GATEWAY_LAN_IP", "MIHOMO_MIXED_PORT", "MIHOMO_CONTROLLER_PORT", "WAN_INTERFACE"}
site = json.loads((root / "examples/site.example.json").read_text())
with tempfile.TemporaryDirectory() as temp:
    output = Path(temp) / "candidate"
    render(site, output)
    config = json.loads((output / "mihomo/config.yaml").read_text())
    environment = read_environment(output / "mihomo-automation.env")
    assert environment["GATEWAY_LAN_IP"] == config["bind-address"]
    assert int(environment["MIHOMO_MIXED_PORT"]) == config["mixed-port"]
    assert config["external-controller"] == f'{environment["GATEWAY_LAN_IP"]}:{environment["MIHOMO_CONTROLLER_PORT"]}'
    assert environment["WAN_INTERFACE"] == site["wan_interface"]
    assert config["bind-address"] == site["lan_address"]
    assert config["tun"]["include-interface"] == [site["lan_interface"]]
    assert len(config["secret"]) == 64
    download = next(group for group in config["proxy-groups"] if group["name"] == "Download")
    assert {"general-a", "general-b", "download-only"} == set(download["use"])
    assert "ai-only" not in download["use"]
    assert 'iifname "lan0" accept' in (output / "nftables.conf").read_text()
    assert "flush ruleset" not in (output / "nftables.conf").read_text()
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.rglob("*") if p.is_file())
    try:
        render(site, output)
    except FileExistsError:
        pass
    else:
        raise AssertionError("Existing configuration was overwritten")
    for key, value in (("lan_interface", 'lan0" accept'),
                       ("management_cidr", site["lan_cidr"]),
                       ("dhcp_start", site["lan_address"]),
                       ("controller_port", site["mixed_port"]),
                       ("management_cidr", "0.0.0.0/0")):
        broken = copy.deepcopy(site)
        broken[key] = value
        try:
            render(broken, Path(temp) / "invalid")
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe setting accepted: {key}")
    changed = copy.deepcopy(site)
    changed["lan_interface"] = next(
        name for name in ("downstream", "fixture-lan", "fixture-lan2")
        if name not in {defaults["WLAN_INTERFACE"], defaults["MANAGEMENT_INTERFACE"]}
    )
    changed["wan_interface"] = "uplink"
    changed["mixed_port"] = 17890
    changed["controller_port"] = 19090
    changed["lan_address"] = "10.77.0.2"
    render(changed, Path(temp) / "alternate")
    alternate = json.loads((Path(temp) / "alternate/mihomo/config.yaml").read_text())
    assert alternate["mixed-port"] == 17890
    assert alternate["tun"]["include-interface"] == [changed["lan_interface"]]
    environment = read_environment(Path(temp) / "alternate/mihomo-automation.env")
    assert environment["GATEWAY_LAN_IP"] == alternate["bind-address"] == changed["lan_address"]
    assert int(environment["MIHOMO_MIXED_PORT"]) == alternate["mixed-port"]
    assert alternate["external-controller"] == f'{environment["GATEWAY_LAN_IP"]}:{environment["MIHOMO_CONTROLLER_PORT"]}'
    assert environment["WAN_INTERFACE"] == changed["wan_interface"]
    # Wireless management is independent of the downstream Ethernet interface.
    for key in defaults.keys() - shared_keys:
        assert environment[key] == defaults[key], f"Template default changed unexpectedly: {key}"
    assert environment["WLAN_INTERFACE"] != changed["lan_interface"]
    assert environment["MANAGEMENT_INTERFACE"] != changed["lan_interface"]
print("Renderer isolation and parameter checks passed")
