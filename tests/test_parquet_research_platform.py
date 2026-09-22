import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'ftmo_bot'))

from tools.market_data_store import MarketDataStore, iso_utc
from tools.research_catalog import ResearchCatalog
from tools.run_research import load_config, select_pairs


class ParquetResearchPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_data_snapshot_is_content_addressed_and_time_bounded(self):
        store = MarketDataStore(self.root / 'lake')
        try:
            partition = store.bars_root / 'XAUUSDm' / 'M5' / '2025' / '01.parquet'
            partition.parent.mkdir(parents=True)
            partition.write_bytes(b'first synthetic partition')
            first = '2025-01-01T00:00:00+00:00'
            last = '2025-01-31T23:55:00+00:00'
            store.connection.execute('''
                INSERT INTO partitions(symbol, timeframe, year, month, relative_path, rows,
                                       first_utc, last_utc, updated_at_utc, content_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ''', ('XAUUSDm', 'M5', 2025, 1, partition.relative_to(store.root).as_posix(),
                  100, first, last, first))
            store.connection.execute('''
                INSERT INTO sync_state(symbol, timeframe, storage_key, first_utc, last_utc, rows, status)
                VALUES (?, ?, ?, ?, ?, ?, 'ok')
            ''', ('XAUUSDm', 'M5', 'XAUUSDm', first, last, 100))
            store.connection.commit()

            snapshot = store.data_snapshot(
                'XAUUSDm', 'M5', start='2025-01-10T00:00:00Z', end='2025-01-20T00:00:00Z'
            )
            self.assertEqual(snapshot['requested_start_utc'], iso_utc('2025-01-10T00:00:00Z'))
            self.assertEqual(len(snapshot['partitions']), 1)
            self.assertEqual(snapshot['partitions'][0]['sha256'], hashlib.sha256(partition.read_bytes()).hexdigest())
            original = snapshot['fingerprint']

            partition.write_bytes(b'changed synthetic partition')
            self.assertNotEqual(original, store.data_snapshot('XAUUSDm', 'M5')['fingerprint'])
        finally:
            store.close()

    def test_catalog_pair_selection_respects_symbol_and_timeframe_filters(self):
        store = MarketDataStore(self.root / 'lake')
        try:
            for symbol, timeframe in [('XAUUSDm', 'M5'), ('XAUUSDm', 'H1'), ('EURUSD', 'M5')]:
                store.mark_state(symbol, timeframe, 'ok')
            config = {'symbols': ['XAUUSDm'], 'timeframes': ['M5']}
            self.assertEqual(select_pairs(store, config), [('XAUUSDm', 'M5')])
        finally:
            store.close()

    def test_research_config_has_safe_batch_defaults(self):
        config = load_config(PROJECT_ROOT / 'ftmo_bot' / 'configs' / 'research.yaml')
        self.assertEqual(config['symbols'], 'catalog')
        self.assertEqual(config['timeframes'], 'all')
        self.assertTrue(config['rolling']['enabled'])
        self.assertFalse(config['monte_carlo']['enabled'])

    def test_research_catalog_keeps_job_provenance(self):
        catalog = ResearchCatalog(self.root / 'research_catalog')
        try:
            catalog.start_run('r1', '2025-01-01T00:00:00+00:00', {'x': 1}, 'abc123', self.root / 'manifest.json')
            snapshot = {'fingerprint': 'datahash', 'partitions': []}
            catalog.start_job('r1', 'job1', 'momentum', 'XAUUSDm', 'M5', snapshot)
            catalog.complete_job('r1', 'job1', self.root / 'report', {'net_profit': 12.5})
            row = catalog.connection.execute('SELECT status, data_fingerprint, metrics_json FROM research_jobs').fetchone()
            self.assertEqual(row['status'], 'completed')
            self.assertEqual(row['data_fingerprint'], 'datahash')
            self.assertIn('12.5', row['metrics_json'])
        finally:
            catalog.close()

