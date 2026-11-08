import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest

M = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'automation/sbin/mihomo-traffic'))
ACCOUNTS = {'glados': {'prefixes': ['Fast-']}, 'bwb': {'prefixes': ['BWB | ']},
            'stt': {'prefixes': ['STT | ']}}
IDENTITIES = {'192.0.2.10': ('test-device-a', 'test-pc')}


def connection(cid, up, down, chain=None):
    return {'id': cid, 'upload': up, 'download': down,
            'metadata': {'sourceIP': '192.0.2.10'}, 'chains': chain or ['Fast-test', 'AI']}


def snapshot(up, down, *connections):
    return {'uploadTotal': up, 'downloadTotal': down, 'connections': list(connections)}


class TrafficTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.ledger = M['Ledger'](self.path)
        self.now = 1800000000

    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()

    def consume(self, snap, offset=0, core='boot:1:100', identities=IDENTITIES):
        self.ledger.consume(snap, self.now + offset, core, identities, ACCOUNTS)

    def sum(self, table):
        return tuple(self.ledger.db.execute(
            f'SELECT coalesce(sum(upload),0),coalesce(sum(download),0) FROM {table}').fetchone())

    def test_baseline_deltas_short_connection_gap_and_collector_restart(self):
        self.consume(snapshot(1000, 2000, connection('old', 900, 1900)))
        self.ledger.flush()
        self.assertEqual(self.sum('usage'), (0, 0))
        self.consume(snapshot(1150, 2400, connection('old', 950, 2000),
                              connection('new', 20, 200, ['BWB | test', 'Download'])), 1)
        self.ledger.flush()
        self.assertEqual(self.sum('usage'), (70, 300))
        self.assertEqual(self.sum('totals'), (150, 400))
        self.ledger.db.close()
        self.ledger = M['Ledger'](self.path)
        self.consume(snapshot(1150, 2400, connection('old', 950, 2000),
                              connection('new', 20, 200, ['BWB | test', 'Download'])), 2)
        self.ledger.flush()
        self.assertEqual(self.sum('usage'), (70, 300), 'restart double-counted active traffic')
        self.consume(snapshot(1160, 2450, connection('new', 30, 250, ['BWB | test'])), 3)
        self.ledger.flush()
        self.assertEqual(self.sum('usage'), (80, 350))

    def test_core_restart_and_counter_reset_do_not_import_old_lifetime(self):
        self.consume(snapshot(100000, 200000, connection('old', 90000, 190000)))
        self.consume(snapshot(20, 50, connection('new', 10, 40)), 5, core='boot:2:500')
        self.ledger.flush()
        self.assertEqual(self.sum('usage'), (10, 40))
        self.assertEqual(self.sum('totals'), (20, 50))
        self.consume(snapshot(4, 6, connection('new', 14, 46)), 6, core='boot:2:500')
        self.ledger.flush()
        self.assertEqual(self.sum('usage'), (14, 46))
        self.assertEqual(self.sum('totals'), (24, 56))
        events = {x[0] for x in self.ledger.db.execute('SELECT kind FROM events')}
        self.assertTrue({'sampling_gap', 'core_restart', 'counter_reset'} <= events)

    def test_dhcp_identity_stays_with_existing_connection(self):
        self.consume(snapshot(10, 20, connection('old', 10, 20)))
        other = {'192.0.2.10': ('test-device-b', 'new-pc')}
        self.consume(snapshot(30, 60, connection('old', 20, 40), connection('new', 10, 20)),
                     1, identities=other)
        self.ledger.flush()
        rows = self.ledger.db.execute('SELECT device,name,upload,download FROM usage ORDER BY name').fetchall()
        self.assertEqual(rows, [('test-device-b', 'new-pc', 10, 20),
                                ('test-device-a', 'test-pc', 10, 20)])

    def test_invalid_snapshot_is_atomic(self):
        self.consume(snapshot(1, 2, connection('old', 1, 2)))
        before = copy.deepcopy(self.ledger.state)
        bad = snapshot(5, 8, connection('old', 4, 6), connection('bad', -1, 5))
        with self.assertRaises(ValueError):
            self.consume(bad, 1)
        self.assertEqual(self.ledger.state, before)
        self.assertEqual(len(self.ledger.pending), 0)
        bad['connections'] = [connection('bad', 1, 2)]
        bad['connections'][0]['metadata']['sourceIP'] = 123
        with self.assertRaises(ValueError):
            self.consume(bad, 1)
        self.assertEqual(self.ledger.state, before)

    def test_buckets_retention_and_no_destination_or_credential_storage(self):
        self.consume(snapshot(0, 0))
        c = connection('a', 1, 2)
        c['metadata']['host'] = 'private.example'
        self.consume(snapshot(1, 2, c), 1)
        self.ledger.flush()
        self.consume(snapshot(3, 6, connection('a', 3, 6)), 301)
        self.ledger.flush()
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM usage').fetchone()[0], 2)
        state = self.ledger.db.execute("SELECT value FROM meta WHERE key='checkpoint'").fetchone()[0]
        self.assertNotIn('private.example', state)
        self.assertNotIn('Authorization', state)
        self.consume(snapshot(3, 6), M['RETENTION'] + 1000)
        self.ledger.flush()
        self.assertEqual(self.sum('usage'), (0, 0))
        self.assertIsNotNone(json.loads(state)['active'])

    def test_gateway_direct_and_unknown_airport(self):
        c = connection('a', 0, 0, ['DIRECT'])
        self.assertEqual(M['dimensions'](c, IDENTITIES, ACCOUNTS)[3:], ('DIRECT', 'DIRECT'))
        c['metadata']['sourceIP'] = ''
        c['chains'] = ['new-provider']
        dims = M['dimensions'](c, {}, ACCOUNTS)
        self.assertEqual(dims[0], 'gateway')
        self.assertEqual(dims[3], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
