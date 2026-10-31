#!/usr/bin/env python3
"""Exercise the real notifier CLI without reaching any notification service."""
import os
import json
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'automation/sbin/mihomo-alert'


class Delivery(unittest.TestCase):
    def render(self, key, message, serverchan=False):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root/'env').write_text('SERVERCHAN_SENDKEY=fixture\n' if serverchan else 'BARK_URL=http://fixture.invalid/key\n')
            source = SOURCE.read_text().replace('/etc/mihomo-alert.env', str(root/'env'))
            source = source.replace('/run/mihomo-alert.state', str(root/'state'))
            (root/'alert').write_text(source)
            for name in ('logger', 'flock'):
                path = root/name; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755)
            curl = root/'curl'
            curl.write_text('#!'+sys.executable+'\n'+'''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if '-d' in args:
    payload=json.loads(args[args.index('-d')+1]); code=200
else:
    payload=dict(args[i+1].split('=',1) for i,a in enumerate(args) if a=='--data-urlencode'); code=0
Path(os.environ['PAYLOAD']).write_text(json.dumps(payload,ensure_ascii=False))
print(json.dumps({'code':code}))
''')
            curl.chmod(0o755)
            result = subprocess.run(['sh',str(root/'alert'),'--health',message,key],
                env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'],PAYLOAD=str(root/'payload')),
                capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads((root/'payload').read_text())

    def test_serverchan_has_readable_unicode_title_and_body(self):
        payload = self.render('mihomo-degraded', 'github=fail status=000 path=proxy;domestic=ok status=200 path=direct;', serverchan=True)
        self.assertIn('GitHub访问检查异常',payload['title'])
        self.assertIn('国内网站：检查通过',payload['desp'])
        self.assertLessEqual(len(payload['title']),32)
        self.assertNotIn('status=',payload['desp'])

    def test_partial_probe_names_affected_sites(self):
        payload = self.render('mihomo-degraded', '出站路径抽样异常（partial）；ai=ok status=401 path=proxy;github=fail status=000 path=proxy;video=fail status=000 path=proxy;domestic=ok status=200 path=direct;')
        self.assertIn('GitHub',payload['title'])
        self.assertIn('视频',payload['title'])
        self.assertIn('国内网站：检查通过',payload['body'])
        self.assertIn('AI 服务：检查通过',payload['body'])
        self.assertIn('原因尚未确定',payload['body'])
        for jargon in ('partial','status=','DIRECT','business='):
            self.assertNotIn(jargon,payload['title']+payload['body'])

    def test_recovery_and_service_failure_are_specific(self):
        self.assertIn('尚未检查',self.render('mihomo-recovery','egress=unknown')['body'])
        self.assertIn('订阅更新失败',self.render('unit:mihomo-update-subscription.service','systemd 单元失败')['title'])
        self.assertIn('订阅更新失败',self.render('unit:mihomo-subscription-update.service','failed')['title'])
        self.assertIn('内存',self.render('mem','可用内存 128MB（下限 512MB）')['title'])

    def test_notification_catalog_covers_dns_hardware_and_media(self):
        cases = {
            'mosdns-degraded': ('cn=1 proxy_fake=0 metrics=1', '域名查询检查有异常'),
            'dnsmasq-restart': ('Requested recovery', '已请求重启'),
            'dns-path-down': ('silent', '整条域名查询链路'),
            'dns-path-degraded': ('degraded', '部分设备域名查询'),
            'dns-path-recovery': ('healthy', '检查恢复'),
            'wan': ('WAN wan0 link missing', '主路由'),
            'disk': ('磁盘使用率 95%', '存储空间'),
            'temp': ('throttled=0x1', '温度或供电'),
            'media-empty:Social': ('empty', '推特图片和视频'),
            'media-empty:Streaming-recovered': ('ready', '候选节点已补回'),
        }
        for key,(message,expected) in cases.items():
            with self.subTest(key=key):
                payload = self.render(key,message)
                self.assertIn(expected,payload['title'])
                self.assertNotIn(message,payload['body'])
        body = self.render('mosdns-degraded','cn=1 proxy_fake=0 metrics=1')['body']
        self.assertIn('代理网站地址分配：检查异常',body)
        self.assertIn('国内网站地址查询：检查通过',body)
        self.assertNotIn('已恢复',self.render('unit:mihomo.service-recovered','')['body'])

    def test_failed_delivery_retries_and_recovery_has_its_own_key(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / 'state'
            config = root / 'env'
            config.write_text('BARK_URL=http://fixture.invalid/fake-key\n')
            source = SOURCE.read_text().replace('/etc/mihomo-alert.env', str(config))
            source = source.replace('/run/mihomo-alert.state', str(state))
            script = root / 'alert'
            script.write_text(source)
            for name, body in {
                'logger': '#!/bin/sh\nexit 0\n',
                'flock': '#!/bin/sh\nexit 0\n',
                'curl': '#!/bin/sh\nprintf "call\\n" >> "$CALLS"\nprintf "%s" "$RESPONSE"\n',
            }.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       CALLS=str(root / 'calls'), RESPONSE='{"code":500}')
            def invoke(key):
                return subprocess.run(['sh', str(script), '--health', 'fixture', key],
                                      env=env, capture_output=True, text=True)
            self.assertNotEqual(invoke('proxy-down').returncode, 0)
            self.assertFalse(state.exists(), 'failed delivery must not start cooldown')
            env['RESPONSE'] = '{"code":200}'
            self.assertEqual(invoke('proxy-down').returncode, 0)
            self.assertTrue(state.exists())
            self.assertEqual(invoke('proxy-down').returncode, 75)
            self.assertEqual(len((root / 'calls').read_text().splitlines()), 2)
            self.assertEqual(invoke('proxy-up').returncode, 0)
            self.assertEqual(len((root / 'calls').read_text().splitlines()), 3)
            self.assertEqual(invoke('proxy-down').returncode, 75)
            state.write_text('proxy-down\t1\n')
            self.assertEqual(invoke('proxy-down').returncode, 0)
            self.assertEqual(len((root / 'calls').read_text().splitlines()), 4)


if __name__ == '__main__':
    unittest.main()
