#!/usr/bin/env python3
"""The 8799 status agent is managed, token-gated and loopback-only.

F-11/F-12: SECURITY.md used to promise a bearer secret that the running agent did
not implement, and the agent's controller default pointed at a stale network.
This check runs the *shipped* agent and asserts:

* the status endpoint rejects a missing and a wrong bearer token with 401, and
  accepts the configured token with 200;
* /health stays public but carries no deployment data;
* / and unknown paths are 401 without a token (no endpoint enumeration);
* the agent refuses to start without a token file, without RP5_MIHOMO_API, or
  with a non-loopback controller URL;
* the token is never printed;
* the shipped unit pins the controller to 127.0.0.1, names the token file and
  keeps the hardening directives.
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-" + "token-" + "value-0123456789abcdef"


def configure(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", type=Path, default=ROOT / "automation/sbin/rp5-agent.py")
    parser.add_argument("--unit", type=Path, default=ROOT / "automation/systemd/rp5-agent.service")
    parser.add_argument("--only", choices=("static", "runtime"), default=None)
    return parser.parse_args(argv)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/metrics"):
            body = (
                'node_cpu_seconds_total{cpu="0",mode="idle"} 100\n'
                'node_cpu_seconds_total{cpu="0",mode="user"} 50\n'
                'node_memory_MemAvailable_bytes 512000\n'
                'node_memory_MemTotal_bytes 1024000\n'
                'node_load1 0.1\nnode_load5 0.1\nnode_load15 0.1\n'
                'node_boot_time_seconds %d\n' % int(time.time())
            ).encode()
        elif self.path.startswith("/proxies"):
            body = json.dumps({"proxies": {"G1": {"type": "URLTest", "now": "N1"}},
                               "G1": {"type": "URLTest", "now": "N1"}}).encode()
        elif self.path.startswith("/configs"):
            body = json.dumps({"mode": "rule"}).encode()
        elif self.path.startswith("/connections"):
            body = json.dumps({"downloadTotal": 10, "uploadTotal": 5,
                               "connections": [{"chains": ["N1", "G1"]}]}).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_stub():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait_ready(port: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", "/health")
            response = connection.getresponse()
            response.read()
            connection.close()
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"agent did not listen on 127.0.0.1:{port}")


def request(port: int, path: str, token: str | None = None) -> tuple[int, dict | bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Authorization": "Bearer " + token} if token else {}
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    try:
        return response.status, json.loads(body)
    except ValueError:
        return response.status, body


def agent_env(tmp: Path, stub_port: int, *, api: str | None = None,
              token_file: Path | None = None, port: int = 0) -> dict:
    env = dict(os.environ)
    env.update({
        "RP5_AGENT_HOST": "127.0.0.1",
        "RP5_AGENT_PORT": str(port),
        "RP5_MIHOMO_API": api if api is not None else f"http://127.0.0.1:{stub_port}",
        "RP5_NODE_EXPORTER": f"http://127.0.0.1:{stub_port}/metrics",
        "RP5_STATUS_TOKEN_FILE": str(token_file) if token_file else str(tmp / "missing.token"),
        "RP5_MIHOMO_CONFIG": str(tmp / "config.yaml"),
        "RP5_LEASES": str(tmp / "leases"),
        "RP5_HEALTH_STATUS": str(tmp / "status"),
        "RP5_BUDGET_LEDGER": str(tmp / "budget.json"),
    })
    return env


def run_agent(agent: Path, env: dict, timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(agent)], env=env, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=timeout)


def check_unit(unit: Path, agent: Path) -> None:
    text = unit.read_text(encoding="utf-8")
    for needle in (
        "ExecStart=/usr/local/sbin/rp5-agent.py",
        "Environment=RP5_MIHOMO_API=http://127.0.0.1:9090",
        "Environment=RP5_STATUS_TOKEN_FILE=/etc/mihomo/secrets/rp5-agent.token",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "PrivateTmp=yes",
        "RestrictAddressFamilies=AF_INET AF_INET6",
    ):
        if needle not in text:
            raise AssertionError(f"rp5-agent.service lost: {needle}")
    source = agent.read_text(encoding="utf-8")
    if "hmac.compare_digest" not in source:
        raise AssertionError("agent must compare the bearer token in constant time")
    if "192.168.1." in source or "192.168.1." in text:
        raise AssertionError("agent/unit still contains the stale controller network")


def check_runtime(agent: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-") as raw:
        tmp = Path(raw)
        token_file = tmp / "status.token"
        token_file.write_text(TOKEN + "\n", encoding="utf-8")
        stub = start_stub()
        port = free_port()
        env = agent_env(tmp, stub.server_address[1], token_file=token_file, port=port)
        process = subprocess.Popen([sys.executable, str(agent)], env=env, cwd=str(ROOT),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            wait_ready(port)
            status, body = request(port, "/api/v1/status")
            if status != 401:
                raise AssertionError(f"status without token returned {status}, expected 401")
            status, _ = request(port, "/api/v1/status", "wrong-token")
            if status != 401:
                raise AssertionError(f"status with a wrong token returned {status}, expected 401")
            status, body = request(port, "/api/v1/status", TOKEN)
            if status != 200 or not isinstance(body, dict):
                raise AssertionError(f"status with the token returned {status}, expected 200")
            for key in ("agent", "pi", "gw", "proxy", "gate"):
                if key not in body:
                    raise AssertionError(f"status JSON lost family {key!r}")
            health_status, health = request(port, "/health")
            if health_status != 200 or set(health) - {"ok", "up_s", "version"}:
                raise AssertionError(f"/health leaked data or failed: {health_status} {health}")
            for path in ("/", "/api/v2/unknown"):
                status, _ = request(port, path)
                if status != 401:
                    raise AssertionError(f"{path} without token returned {status}, expected 401")
        finally:
            process.terminate()
            try:
                out, err = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                out, err = process.communicate()
            stub.shutdown()
        if TOKEN in out or TOKEN in err:
            raise AssertionError("agent printed its bearer token")

    with tempfile.TemporaryDirectory(prefix="agent-") as raw:
        tmp = Path(raw)
        stub = start_stub()
        try:
            missing = run_agent(agent, agent_env(tmp, stub.server_address[1]))
            if missing.returncode == 0 or "status token" not in (missing.stdout + missing.stderr).lower():
                raise AssertionError("agent started without a token file")
            token_file = tmp / "status.token"
            token_file.write_text(TOKEN + "\n", encoding="utf-8")
            stale = run_agent(agent, agent_env(tmp, stub.server_address[1], api="http://192.0.2.1:9090",
                                        token_file=token_file))
            if stale.returncode == 0 or "loopback" not in (stale.stdout + stale.stderr).lower():
                raise AssertionError("agent accepted a non-loopback controller URL")
        finally:
            stub.shutdown()


def main(argv: list[str] | None = None) -> int:
    args = configure(argv)
    if not args.agent.is_file():
        print("status agent: FAIL: missing agent " + str(args.agent))
        return 1
    if not args.unit.is_file():
        print("status agent: FAIL: missing unit " + str(args.unit))
        return 1
    try:
        if args.only != "runtime":
            check_unit(args.unit, args.agent)
        if args.only != "static":
            check_runtime(args.agent)
    except AssertionError as error:
        print("status agent: FAIL: " + str(error))
        return 1
    except (OSError, subprocess.SubprocessError) as error:
        print("status agent: FAIL: " + str(error))
        return 1
    print("status agent: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
