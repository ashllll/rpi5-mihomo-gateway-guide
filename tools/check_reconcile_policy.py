#!/usr/bin/env python3
"""Exercise reconcile CLI against a local Mihomo-compatible HTTP controller."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation/sbin/mihomo-reconcile-policy"
SECRET = "fixture-secret-never-log"


class ControllerState:
    def __init__(self, proxies: dict, fail_delete: bool = False, retain_delete: bool = False,
                 fail_group_get: bool = False, bad_proxy_list: bool = False):
        self.proxies = proxies
        self.fail_delete = fail_delete
        self.retain_delete = retain_delete
        self.fail_group_get = fail_group_get
        self.bad_proxy_list = bad_proxy_list
        self.requests: list[tuple[str, str]] = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    state: ControllerState
    version_delay = 0.0
    proxies_delay = 0.0

    def log_message(self, *_args):
        pass

    def respond(self, code: int, value: dict | None = None):
        body = b"" if value is None else json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        name = unquote(self.path.removeprefix("/proxies/")) if self.path.startswith("/proxies/") else ""
        status = 200
        with self.state.lock:
            self.state.requests.append(("GET", self.path))
            if self.path == "/version":
                delay = self.version_delay
                value = {"version": "fixture"}
            elif self.path == "/proxies":
                delay = self.proxies_delay
                value = ({"unknown": True} if self.state.bad_proxy_list else
                         {"proxies": {key: {"type": proxy.get("type")} for key, proxy in self.state.proxies.items()}})
            elif name in self.state.proxies:
                delay = 0
                value = self.state.proxies[name]
                if self.state.fail_group_get:
                    value = {"error": SECRET, "proxy": name}
                    status = 500
            else:
                delay = 0
                value = None
        if delay:
            time.sleep(delay)
        if value is None:
            self.respond(404, {"error": SECRET})
        else:
            self.respond(status, value)

    def do_DELETE(self):
        name = unquote(self.path.removeprefix("/proxies/"))
        with self.state.lock:
            self.state.requests.append(("DELETE", self.path))
            proxy = self.state.proxies.get(name)
            fail = self.state.fail_delete
            retain = self.state.retain_delete
            if proxy is not None and not fail and not retain:
                proxy.pop("fixed", None)
        if fail:
            self.respond(500, {"error": SECRET, "proxy": name})
        elif proxy is None:
            self.respond(404, {"error": SECRET})
        else:
            self.respond(204)

    def do_PUT(self):
        name = unquote(self.path.removeprefix("/proxies/"))
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        with self.state.lock:
            self.state.requests.append(("PUT", self.path))
            proxy = self.state.proxies.get(name)
            if proxy and payload.get("name") in proxy.get("all", []):
                proxy["now"] = payload["name"]
        self.respond(204 if proxy else 404)


class ReconcilePolicyCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.log = self.directory / "log"
        self.secret_file = self.directory / "dashboard.secret"
        self.secret_file.write_text(SECRET)
        source = SCRIPT.read_text()
        maintenance_path = "/run/lock/mihomo-maintenance.lock"
        self.assertEqual(source.count(maintenance_path), 1, "fixture cannot safely isolate the fixed maintenance lock")
        self.script = self.directory / "mihomo-reconcile-policy"
        self.script.write_text(source.replace(maintenance_path, str(self.directory / "maintenance.lock")))
        self.script.chmod(0o755)
        self.env_file = self.directory / "automation.env"
        self.env_file.write_text("")
        self.targets = self.directory / "targets.conf"
        self.targets.write_text("")
        self.own_lock = self.directory / "reconcile.lock"
        self.maintenance_lock = self.directory / "maintenance.lock"
        self.state = ControllerState({})
        handler = type("FixtureHandler", (Handler,), {"state": self.state})
        self.handler = handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self._write_command(
            "flock",
            f"#!{shutil.which('python3')}\nimport fcntl,sys\n"
            "try: fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except BlockingIOError: sys.exit(1)\n",
        )
        self._write_command("logger", "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$TEST_LOG\"\n")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temp.cleanup()

    def _write_command(self, name: str, body: str):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def cli_environment(self, **settings):
        env = dict(os.environ)
        env.update(
            PATH=f"{self.bin}:{os.environ['PATH']}",
            TEST_LOG=str(self.log),
            MIHOMO_AUTOMATION_ENV_FILE=str(self.env_file),
            MIHOMO_RECONCILE_SECRET_FILE=str(self.secret_file),
            MIHOMO_RECONCILE_LOCK_FILE=str(self.own_lock),
            MIHOMO_RECONCILE_TARGETS=str(self.targets),
            MIHOMO_CONTROLLER_PORT=str(self.server.server_port),
            MIHOMO_RECONCILE_TIMEOUT_SECONDS="5",
        )
        env.update({key: str(value) for key, value in settings.items()})
        return env

    def run_cli(self, *args: str, timeout: float = 8, **settings):
        env = self.cli_environment(**settings)
        return subprocess.run(["sh", str(self.script), *args], env=env, capture_output=True,
                              text=True, timeout=timeout)

    def logs(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def fallback(self, *, fixed="Gone | node", members=None):
        return {"type": "Fallback", "all": members if members is not None else ["Live | node"],
                "now": "Live | node", "fixed": fixed}

    def test_repair_stale_deletes_only_stale_fallback_override_and_is_idempotent(self):
        group = "Fallback/A + 国内"
        self.state.proxies = {
            group: self.fallback(),
            "Valid Fixed": self.fallback(fixed="Live | node"),
            "Selector": {"type": "Selector", "all": ["A", "B"], "now": "A"},
        }
        self.targets.unlink()
        first = self.run_cli("--repair-stale")
        self.assertEqual(first.returncode, 0, (first.stdout, first.stderr, self.logs()))
        self.assertNotIn("fixed", self.state.proxies[group])
        self.assertEqual(self.state.proxies["Valid Fixed"]["fixed"], "Live | node")
        deletes = [path for method, path in self.state.requests if method == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertIn("Fallback%2FA%20%2B%20%E5%9B%BD%E5%86%85", deletes[0])
        self.assertFalse([row for row in self.state.requests if row[0] == "PUT"])
        before = len(deletes)
        second = self.run_cli("--repair-stale")
        self.assertEqual(second.returncode, 0, (second.stdout, second.stderr, self.logs()))
        self.assertEqual(len([row for row in self.state.requests if row[0] == "DELETE"]), before)

    def test_normal_reconcile_cleans_stale_fallback_then_keeps_selector_mapping(self):
        group = "Fallback stale"
        self.state.proxies = {
            group: self.fallback(),
            "Selector / group": {"type": "Selector", "all": ["target", "other"], "now": "other"},
        }
        self.targets.write_text("Selector / group=target\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr, self.logs()))
        self.assertNotIn("fixed", self.state.proxies[group])
        self.assertEqual(self.state.proxies["Selector / group"]["now"], "target")
        methods = [method for method, _ in self.state.requests]
        self.assertLess(methods.index("DELETE"), methods.index("PUT"))

    def test_unknown_fallback_structure_is_never_deleted(self):
        self.state.proxies = {"Unknown": {"type": "Fallback", "fixed": "stale"}}
        result = self.run_cli("--repair-stale")
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr, self.logs()))
        self.assertFalse([row for row in self.state.requests if row[0] == "DELETE"])

    def test_unknown_controller_list_structure_fails_closed_without_delete(self):
        self.state.proxies = {"Fallback": self.fallback()}
        self.state.bad_proxy_list = True
        result = self.run_cli("--repair-stale")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse([row for row in self.state.requests
                          if row[0] == "DELETE" or (row[0] == "GET" and row[1].startswith("/proxies/"))])

    def test_delete_api_error_is_nonzero_and_does_not_log_secret_or_proxy_name(self):
        name = "Private proxy name"
        self.state.proxies = {name: self.fallback()}
        self.state.fail_delete = True
        result = self.run_cli("--repair-stale")
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr + self.logs()
        self.assertNotIn(SECRET, output)
        self.assertNotIn(name, output)
        self.assertEqual(len([row for row in self.state.requests if row[0] == "DELETE"]), 1)

    def test_delete_readback_that_still_has_stale_fixed_fails_without_leaking_names(self):
        name = "Private proxy name"
        self.state.proxies = {name: self.fallback()}
        self.state.retain_delete = True
        result = self.run_cli("--repair-stale")
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr + self.logs()
        self.assertNotIn(SECRET, output)
        self.assertNotIn(name, output)
        self.assertGreaterEqual(len([row for row in self.state.requests if row[0] == "GET" and row[1] == "/proxies/Private%20proxy%20name"]), 2)

    def test_repair_only_skips_when_subscription_maintenance_lock_is_held(self):
        self.state.proxies = {"Fallback stale": self.fallback()}
        with self.maintenance_lock.open("a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_cli("--repair-stale")
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr, self.logs()))
            self.assertFalse(self.state.requests)

    def test_reconcile_lock_skips_concurrent_cli_invocation(self):
        self.state.proxies = {"Fallback stale": self.fallback()}
        self.handler.proxies_delay = 0.8
        env = self.cli_environment()
        first = subprocess.Popen(["sh", str(self.script), "--repair-stale"], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if ("GET", "/proxies") in self.state.requests:
                break
            time.sleep(0.02)
        else:
            first.kill()
            first.communicate()
            self.fail("first reconcile invocation never reached the controller")
        count = len(self.state.requests)
        second = self.run_cli("--repair-stale")
        self.assertEqual(second.returncode, 0, (second.stdout, second.stderr, self.logs()))
        self.assertEqual(len(self.state.requests), count, "concurrent repair entered the controller")
        stdout, stderr = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, (stdout, stderr, self.logs()))

    def test_total_deadline_bounds_controller_wait(self):
        self.handler.version_delay = 4
        started = time.monotonic()
        result = self.run_cli("--repair-stale", MIHOMO_RECONCILE_TIMEOUT_SECONDS="1", timeout=3)
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 2.5, (elapsed, result.stderr))
        self.assertFalse([row for row in self.state.requests if row[1] == "/proxies"])

    def test_timeout_setting_cannot_remove_the_global_cap(self):
        result = self.run_cli("--repair-stale", MIHOMO_RECONCILE_TIMEOUT_SECONDS="31")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.state.requests)

    def test_healthcheck_service_runs_repair_as_best_effort_post_start(self):
        unit = (ROOT / "automation/systemd/mihomo-healthcheck.service").read_text()
        self.assertIn("ExecStartPost=-/usr/local/sbin/mihomo-reconcile-policy --repair-stale", unit)

    def test_normal_selector_reconcile_still_works(self):
        self.state.proxies = {"Selector": {"type": "Selector", "all": ["A", "B"], "now": "A"}}
        self.targets.write_text("Selector=B\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr, self.logs()))
        self.assertEqual(self.state.proxies["Selector"]["now"], "B")

    def test_missing_optional_selector_group_is_reported_and_skipped(self):
        self.state.proxies = {}
        self.targets.write_text("Optional selector=target\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr, self.logs()))
        self.assertIn(("GET", "/proxies/Optional%20selector"), self.state.requests)
        self.assertFalse([row for row in self.state.requests if row[0] == "PUT"])

    def test_normal_mode_still_requires_its_targets_file(self):
        self.state.proxies = {"Fallback stale": self.fallback()}
        self.targets.unlink()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.state.requests)

    def test_custom_env_and_secret_paths_are_used(self):
        self.env_file.write_text(f"MIHOMO_CONTROLLER_PORT={self.server.server_port}\n")
        result = self.run_cli("--repair-stale", MIHOMO_CONTROLLER_PORT="1")
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr, self.logs()))
        self.assertIn(("GET", "/version"), self.state.requests)

    def test_non_404_controller_group_api_error_is_nonzero_and_private(self):
        name = "Private selector name"
        self.state.proxies = {name: {"type": "Selector", "all": ["A", "B"], "now": "A"}}
        self.state.fail_group_get = True
        self.targets.write_text(f"{name}=B\n")
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr + self.logs()
        self.assertNotIn(SECRET, output)
        self.assertNotIn(name, output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
