#!/usr/bin/env python3
"""Check rendering isolation, parameter propagation and unsafe-site rejection."""
import copy
import json
from pathlib import Path
import tempfile
from render import render

root = Path(__file__).resolve().parents[1]
site = json.loads((root / "examples/site.example.json").read_text())
with tempfile.TemporaryDirectory() as temp:
    output = Path(temp) / "candidate"
    render(site, output)
    config = json.loads((output / "mihomo/config.yaml").read_text())
    assert config["bind-address"] == site["lan_address"]
    assert config["tun"]["include-interface"] == [site["lan_interface"]]
    assert len(config["secret"]) == 64
    assert {"general-a", "general-b", "download-only"} == set(config["proxy-groups"][2]["use"])
    assert "ai-only" not in config["proxy-groups"][2]["use"]
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
    changed["lan_interface"] = "downstream"
    changed["wan_interface"] = "uplink"
    changed["mixed_port"] = 17890
    render(changed, Path(temp) / "alternate")
    alternate = json.loads((Path(temp) / "alternate/mihomo/config.yaml").read_text())
    assert alternate["mixed-port"] == 17890
    assert alternate["tun"]["include-interface"] == ["downstream"]
print("Renderer isolation and parameter checks passed")
