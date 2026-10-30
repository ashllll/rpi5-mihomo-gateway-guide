#!/usr/bin/env python3
"""Run the entire updater CLI with offline I/O and failed-candidate rollback."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(os.environ.get('UPDATER_UNDER_TEST', str(Path(__file__).resolve().parents[1] / 'automation/sbin/mihomo-update-subscription')))
STUB = r'''
import os,sys,shutil
from pathlib import Path
r=Path(os.environ['FIXTURE']); mode=os.environ['FAILURE']; name=Path(sys.argv[0]).name; a=sys.argv[1:]
if name=='curl':
    if '--output' in a and a[a.index('--output')+1]!='/dev/null':
        Path(a[a.index('--output')+1]).write_text('proxies: []\nmarker: candidate\n')
    else: print('500',end='')
elif name=='mihomo-subscription-normalize': shutil.copyfile(a[0],a[1])
elif name=='mihomo-subscription-candidate':
    if a[0]=='prepare': shutil.copyfile(a[2],a[4]); Path(a[5]).write_text('changed\n')
elif name=='mihomo-policy-overlay':
    shutil.copyfile(a[0],a[1]); (r/'rollback-phase').touch()
elif name=='mihomo-media-guard':
    assert '--check-ready' in a
    (r/'media-ready').touch()
elif name=='systemctl':
    if a[0]=='restart':
        p=r/'restarts'; n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))
        if (r/'rollback-phase').exists() and mode=='restart': sys.exit(1)
    if a[0]=='is-active' and (r/'rollback-phase').exists() and mode=='inactive': sys.exit(1)
elif name=='mihomo-healthcheck':
    (r/'observed').touch()
    if mode=='structure': sys.exit(1)
elif name=='install':
    clean=[]
    while a:
        v=a.pop(0)
        if v in ['-o','-g','-m']: a.pop(0)
        else: clean.append(v)
    if '-d' in clean: Path(clean[-1]).mkdir(parents=True,exist_ok=True)
    else: shutil.copyfile(clean[-2],clean[-1])
elif name=='logger':
    with (r/'messages').open('a') as f: f.write(' '.join(a)+'\n')
elif name=='xargs': sys.stdin.read()
'''


class Rollback(unittest.TestCase):
    def test_candidate_failure_never_hides_failed_recovery(self):
        for failure in ['restart', 'inactive', 'structure', 'proxy-unavailable']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                for name in ['etc/secrets/subscriptions.d', 'bin', 'lock', 'cache']:
                    (root/name).mkdir(parents=True)
                (root/'etc/config.yaml').write_text('proxies: []\nmarker: previous\n')
                (root/'etc/secrets/subscription.url').write_text('http://fixture.invalid/config\n')
                for name in ['curl','mihomo','systemctl','install','logger','sleep','flock','find','xargs',
                             'mihomo-subscription-normalize','mihomo-subscription-candidate','mihomo-policy-overlay',
                             'mihomo-reconcile-policy','mihomo-healthcheck','mihomo-media-guard']:
                    p=root/'bin'/name
                    p.write_text('#!'+sys.executable+'\n'+STUB)
                    p.chmod(0o755)
                source=SOURCE.read_text()
                for old,new in [('/etc/mihomo-automation.env',str(root/'absent.env')),
                                ('/etc/mihomo',str(root/'etc')),('/usr/local/sbin',str(root/'bin')),
                                ('/run/lock',str(root/'lock')),('/var/tmp',str(root)),
                                ('/var/lib/mihomo-extra-subscriptions',str(root/'cache')),
                                ('/var/lib/mihomo-subscription-backups',str(root/'backups'))]:
                    source=source.replace(old,new)
                (root/'updater').write_text(source)
                env=dict(os.environ,PATH=str(root/'bin')+os.pathsep+os.environ['PATH'],
                         FIXTURE=str(root),FAILURE=failure)
                result=subprocess.run(['sh',str(root/'updater')],env=env,capture_output=True,text=True,timeout=15)
                self.assertNotEqual(result.returncode,0)
                self.assertTrue((root/'media-ready').exists(),'candidate must pass media readiness before rollback')
                self.assertIn('marker: previous',(root/'etc/config.yaml').read_text())
                messages=(root/'messages').read_text()
                if failure!='proxy-unavailable':
                    self.assertIn('manual recovery required',messages,(failure,messages,result.stderr))
                    self.assertNotIn('previous nodes restored',messages)
                else:
                    self.assertIn('previous nodes restored',messages)
                    self.assertTrue((root/'observed').exists(),'rollback must verify recovered structure')


if __name__=='__main__': unittest.main()
