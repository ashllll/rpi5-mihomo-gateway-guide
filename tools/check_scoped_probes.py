#!/usr/bin/env python3
"""Check scoped path reporting and maintenance observation through the health CLI."""
import json
from pathlib import Path
import sys
import unittest
import check_gateway_resilience as fixture


class ScopedProbes(unittest.TestCase):
    setUp = fixture.HealthcheckCLI.setUp
    run_cli = fixture.HealthcheckCLI.run_cli

    def test_each_path_keeps_its_result_and_partial_failure_is_not_total_failure(self):
        probes = self.directory / 'paths.conf'
        probes.write_text('ai|proxy|401|https://ai.invalid/\nvideo|proxy|204|https://video.invalid/\ndomestic|direct|200|https://domestic.invalid/\n')
        (self.commands / 'curl').write_text('#!' + sys.executable + '''
import json,os,sys
with open(os.environ['TEST_ACTIONS'],'a') as f:f.write(json.dumps(['curl']+sys.argv[1:])+'\\n')
u=sys.argv[-1];print('000' if 'video.invalid' in u else ('401' if 'ai.invalid' in u else '200'),end='')
''')
        result, status, actions = self.run_cli(EGRESS_PROBES_FILE=str(probes))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('egress'), 'partial')
        self.assertEqual(status.get('business'), 'degraded')
        details = (self.directory / 'paths').read_text()
        self.assertIn('ai=ok', details)
        self.assertIn('video=fail', details)
        self.assertIn('domestic=ok', details)
        self.assertFalse([a for a in actions if a[:2] == ['systemctl','restart']])
        self.assertTrue(any(a[0]=='curl' and '--noproxy' in a for a in actions))

    def test_maintenance_still_observes_without_recovery_or_external_probes(self):
        (self.commands / 'flock').write_text('#!/bin/sh\nexit 1\n')
        result, status, actions = self.run_cli(TEST_PROCESS='inactive')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('maintenance'), '1')
        self.assertEqual(status.get('business'), 'failed')
        self.assertFalse([a for a in actions if a[0] in ('curl','alert') or a[:2]==['systemctl','restart']])

    def test_invalid_profile_replaces_stale_path_details(self):
        probes = self.directory / 'paths.conf'
        probes.write_text('bad label|proxy|204|https://probe.invalid/\n')
        (self.directory / 'paths').write_text('old=ok status=204 path=proxy\n')
        result, status, _ = self.run_cli(EGRESS_PROBES_FILE=str(probes))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status.get('egress'), 'fail')
        self.assertEqual((self.directory / 'paths').read_text(), 'configuration=fail reason=invalid-profile\n')

    def test_http_status_cannot_hide_a_failed_transfer(self):
        (self.commands / 'curl').write_text('#!/bin/sh\nprintf 204\nexit 18\n')
        _, status, _ = self.run_cli()
        self.assertEqual(status.get('egress'), 'fail')

    def test_notification_settings_cannot_fail_the_startup_gate(self):
        result, status, actions = self.run_cli('--startup-check', '0', EGRESS_ALERT_FAILURES='0', EGRESS_RECOVERY_SUCCESSES='invalid')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(status.get('business'), 'failed')
        self.assertFalse([a for a in actions if a[0] in ('curl', 'alert')])

    def test_notifications_require_fresh_failure_and_recovery_confirmation(self):
        settings=dict(EGRESS_ALERT_FAILURES='2',EGRESS_RECOVERY_SUCCESSES='2',EGRESS_EVERY='10')
        _,_,actions=self.run_cli(TEST_EGRESS='fail',**settings)
        self.assertFalse([a for a in actions if a[0]=='alert'])
        _,_,actions=self.run_cli(TEST_EGRESS='fail',TEST_NOW='1800000001',**settings)
        self.assertFalse([a for a in actions if a[0]=='alert'])
        _,_,actions=self.run_cli(TEST_EGRESS='fail',TEST_NOW='1800000011',**settings)
        self.assertEqual(len([a for a in actions if a[0]=='alert']),1)
        _,_,actions=self.run_cli(TEST_NOW='1800000022',**settings)
        self.assertFalse([a for a in actions if a[0]=='alert'])
        _,_,actions=self.run_cli(TEST_NOW='1800000033',**settings)
        self.assertEqual(len([a for a in actions if a[0]=='alert']),1)


if __name__=='__main__':unittest.main()
