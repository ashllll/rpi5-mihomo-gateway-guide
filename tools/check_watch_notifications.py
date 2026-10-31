#!/usr/bin/python3

"""Exercise mihomo-watch edge notifications with an isolated state directory."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "automation/sbin/mihomo-watch"


class WatchNotifications(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.state = self.run / "state"
        self.bin = self.run / "bin"
        self.state.mkdir()
        self.bin.mkdir()
        self.config = self.run / "mihomo" / "config.yaml"
        self.config.parent.mkdir()
        (self.bin / "systemctl").write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --failed ]; then\n"
            "  [ \"${FAKE_SYSTEMCTL_FAIL:-0}\" != 1 ] || exit 1\n"
            "  [ \"${FAKE_FAILED:-0}\" = 1 ] && printf 'fixture.service loaded failed failed\\n'\n"
            "fi\n"
            "exit 0\n"
        )
        (self.bin / "logger").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "vcgencmd").write_text(
            "#!/bin/sh\n"
            "[ \"${FAKE_VCG_FAIL:-0}\" != 1 ] || exit 1\n"
            "case \"${1:-}\" in\n"
            "  measure_temp)\n"
            "    if [ -n \"${FAKE_TEMP_RAW:-}\" ]; then printf '%s\\n' \"$FAKE_TEMP_RAW\";\n"
            "    else printf \"temp=%s.0'C\\n\" \"${FAKE_TEMP:-50}\"; fi ;;\n"
            "  get_throttled)\n"
            "    if [ -n \"${FAKE_THROTTLED_RAW:-}\" ]; then printf '%s\\n' \"$FAKE_THROTTLED_RAW\";\n"
            "    else printf 'throttled=0x%s\\n' \"${FAKE_THROTTLED:-0}\"; fi ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        )
        for path in self.bin.iterdir():
            path.chmod(0o755)
        source = SOURCE.read_text()
        for old, new in (
            ("env_file=/etc/mihomo-alert.env", f"env_file={self.run}/absent.env"),
            ("state_dir=/run/mihomo-watch", f"state_dir={self.state}\nmedia_config={self.config}"),
            ("alert=/usr/local/sbin/mihomo-alert", f"alert={self.bin}/alert"),
            ("media_config=/etc/mihomo/config.yaml", f"media_config={self.config}"),
        ):
            if old not in source:
                self.fail(f"watch harness no longer matches: {old!r}")
            source = source.replace(old, new)
        self.script = self.run / "watch"
        self.script.write_text(source)
        self.script.chmod(0o755)
        self.alert_log = self.state / "alert.log"
        alert = self.bin / "alert"
        alert.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_ALERT_LOG\"\n"
            "[ \"${FAKE_FAIL_KEY:-}\" != \"${3:-}\" ]\n"
        )
        alert.chmod(0o755)

    def run_watch(self, failed: bool, fail_key: str = "", *, systemctl_error: bool = False,
                  temp: int = 50, temp_error: bool = False, temp_raw: str = "",
                  throttled_raw: str = "") -> list[str]:
        env = {
            "PATH": f"{self.bin}:{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "FAKE_FAILED": "1" if failed else "0",
            "FAKE_SYSTEMCTL_FAIL": "1" if systemctl_error else "0",
            "FAKE_TEMP": str(temp),
            "FAKE_VCG_FAIL": "1" if temp_error else "0",
            "FAKE_TEMP_RAW": temp_raw,
            "FAKE_THROTTLED_RAW": throttled_raw,
            "FAKE_FAIL_KEY": fail_key,
            "FAKE_ALERT_LOG": str(self.alert_log),
            "WATCH_MEM_MB": "0",
            "WATCH_WAN_IF": "absent-test-interface",
        }
        result = subprocess.run(["sh", str(self.script)], env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return self.alert_log.read_text().splitlines() if self.alert_log.exists() else []

    def media_config(self, proxies: list[dict] | None = None, *, enabled: bool = True) -> Path:
        config = {"proxy-groups": [], "proxy-providers": {}}
        if enabled:
            for group in ("Social", "Streaming"):
                name = "media-admission-" + ("a" if group == "Social" else "b") * 16
                config["proxy-groups"].append({
                    "name": group, "type": "fallback", "use": [name], "empty-fallback": "REJECT"})
                config["proxy-providers"][name] = {
                    "type": "file", "path": f"./providers/{name}.yaml"}
                provider_file = self.config.parent / "providers" / f"{name}.yaml"
                provider_file.parent.mkdir(exist_ok=True)
                provider_file.write_text(yaml.safe_dump({"proxies": proxies or []}))
        self.config.write_text(yaml.safe_dump(config))
        return self.config

    def test_trip_and_relax_retry_until_their_notification_succeeds(self) -> None:
        trip = "unit:fixture.service"
        recovery = "unit:fixture.service-recovered"
        log = self.run_watch(True, trip)
        self.assertEqual(sum(line.split()[-1] == trip for line in log), 1)
        self.assertFalse((self.state / trip).exists())

        log = self.run_watch(True)
        self.assertEqual(sum(line.split()[-1] == trip for line in log), 2)
        self.assertTrue((self.state / trip).exists())
        log = self.run_watch(True)
        self.assertEqual(sum(line.split()[-1] == trip for line in log), 2)

        before = len(log)
        log = self.run_watch(False, systemctl_error=True)
        self.assertEqual(len(log), before)
        self.assertTrue((self.state / trip).exists())

        log = self.run_watch(False, recovery)
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), 1, log)
        self.assertTrue((self.state / trip).exists())
        log = self.run_watch(False)
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), 2)
        self.assertFalse((self.state / trip).exists())
        log = self.run_watch(False)
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), 2)

    def test_media_empty_alert_recovers_only_after_readable_nonreject_provider(self) -> None:
        self.media_config([{"name": "reject-adapter", "type": "reject"}])
        log = self.run_watch(False)
        self.assertEqual(sum(line.split()[-1] == "media-empty:Social" for line in log), 1)
        self.assertEqual(sum(line.split()[-1] == "media-empty:Streaming" for line in log), 1)
        log = self.run_watch(False)
        self.assertEqual(sum(line.split()[-1] == "media-empty:Social" for line in log), 1)

        social = "media-admission-" + "a" * 16
        provider_file = self.config.parent / "providers" / f"{social}.yaml"
        provider_file.unlink()
        log = self.run_watch(False)
        self.assertFalse(any(line.split()[-1] == "media-empty:Social-recovered" for line in log))
        provider_file.write_text("proxies: [not-a-proxy-definition]\n")
        log = self.run_watch(False)
        self.assertFalse(any(line.split()[-1] == "media-empty:Social-recovered" for line in log))
        provider_file.write_text(yaml.safe_dump({"proxies": [{"name": "Node A", "type": "http"}]}))
        log = self.run_watch(False)
        self.assertEqual(sum(line.split()[-1] == "media-empty:Social-recovered" for line in log), 1)
        self.run_watch(False)
        log = self.run_watch(False)
        self.assertEqual(sum(line.split()[-1] == "media-empty:Social-recovered" for line in log), 1)

    def test_missing_config_and_disabled_media_groups_never_create_recovery_alerts(self) -> None:
        self.media_config([])
        before = self.run_watch(False)
        self.config.unlink()
        log = self.run_watch(False)
        self.assertEqual(len(log), len(before))
        self.media_config(enabled=False)
        log = self.run_watch(False)
        self.assertEqual(len(log), len(before))

    def test_temperature_read_failure_does_not_clear_an_alert(self) -> None:
        trip = "temp"
        recovery = "temp-recovered"
        log = self.run_watch(False, temp=85)
        self.assertEqual(sum(line.split()[-1] == trip for line in log), 1)
        self.assertTrue((self.state / trip).exists())

        before = len(log)
        log = self.run_watch(False, temp_error=True)
        self.assertEqual(len(log), before)
        self.assertTrue((self.state / trip).exists())

        log = self.run_watch(False, temp=50)
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), 1)
        self.assertFalse((self.state / trip).exists())

        log = self.run_watch(False, temp=85)
        self.assertTrue((self.state / trip).exists())
        before = sum(line.split()[-1] == recovery for line in log)
        log = self.run_watch(False, temp_raw="temp=50oops")
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), before)
        self.assertTrue((self.state / trip).exists())
        log = self.run_watch(False, temp=50)
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), before + 1)

        log = self.run_watch(False, temp=85)
        before = sum(line.split()[-1] == recovery for line in log)
        log = self.run_watch(False, temp=50, throttled_raw="throttled=0x0z")
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), before)
        self.assertTrue((self.state / trip).exists())
        log = self.run_watch(False, temp=50)
        self.assertEqual(sum(line.split()[-1] == recovery for line in log), before + 1)


if __name__ == "__main__":
    unittest.main()
