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
from tools.data_quality import validate_bars
from tools.research_profiles import resolve_instrument, resolved_risk_params, strategy_supports
from backtest.walk_forward import expand_parameter_grid, run_walk_forward_backtest, summarize_walk_forward


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

    def test_data_quality_rejects_structural_errors_but_reports_gaps(self):
        frame = __import__('pandas').DataFrame([
            ('2025-01-01T00:00:00Z', 1, 2, 0.5, 1.5),
            ('2025-01-01T00:05:00Z', 1.5, 2, 1, 1.7),
            ('2025-01-01T00:30:00Z', 1.7, 2, 1.5, 1.8),
        ], columns=['time', 'open', 'high', 'low', 'close'])
        report = validate_bars(frame, 'M5', {'min_bars': 3})
        self.assertEqual(report['status'], 'passed_with_warnings')
        broken = frame.copy()
        broken['high'] = broken['high'].astype(float)
        broken.loc[1, 'high'] = 0.1
        self.assertEqual(validate_bars(broken, 'M5', {'min_bars': 3})['status'], 'failed')

    def test_instrument_profile_requires_exact_symbol_and_compatibility(self):
        registry = {'symbols': {'XAUUSDm': {'enabled': True, 'asset_class': 'metal',
                    'execution': {'contract_size': 100, 'point_size': .01, 'spread_points': 20,
                                  'slippage_points': 5, 'commission_per_lot_round_turn_usd': 0,
                                  'intrabar_policy': 'stop_first'}}}}
        instrument = resolve_instrument(registry, 'XAUUSDm')
        class MetalM5:
            supported_asset_classes = ('metal',)
            supported_timeframes = ('M5',)
        self.assertTrue(strategy_supports(MetalM5, instrument, 'M5')[0])
        self.assertFalse(strategy_supports(MetalM5, instrument, 'H1')[0])
        with self.assertRaisesRegex(ValueError, 'No instrument profile'):
            resolve_instrument(registry, 'EURUSD')
        merged = resolved_risk_params({'execution': {'spread_points': 99}, 'risk_per_trade_pct': .5}, instrument)
        self.assertEqual(merged['execution']['spread_points'], 20)

    def test_parameter_grid_expands_nested_paths(self):
        items = expand_parameter_grid({'momentum_params': {'body_threshold': 2}},
                                      {'momentum_params.body_threshold': [1, 2], 'x': [3]})
        self.assertEqual(len(items), 2)
        self.assertEqual({item['momentum_params']['body_threshold'] for item in items}, {1, 2})

    def test_walk_forward_separates_train_from_test_and_emits_oos_summary(self):
        import pandas as pd
        from backtest.engine import BacktestEngine
        from risk.compliance_guard import ComplianceGuard
        from risk.risk_manager import RiskManager

        class NoTradeStrategy:
            supported_asset_classes = ('metal',)
            supported_timeframes = ('M5',)
            def __init__(self, params): self.params = params
            def prepare_data(self, frame): return frame
            def generate_signal(self, row): return None

        frame = pd.DataFrame({
            'time': pd.date_range('2025-01-01', periods=90, freq='D', tz='UTC'),
            'open': 100., 'high': 101., 'low': 99., 'close': 100.,
        })
        ftmo = {'account_size': 10000, 'max_daily_loss_pct': 5, 'max_total_loss_pct': 10,
                'profit_target_pct': 10, 'drawdown_type': 'static_from_initial',
                'data_timezone': 'UTC', 'daily_reset_timezone': 'UTC'}
        risk = {'risk_per_trade_pct': .5, 'max_open_risk_pct': 1.5, 'daily_loss_buffer_pct': 1,
                'total_loss_buffer_pct': 1, 'execution': {'contract_size': 100, 'point_size': .01,
                'spread_points': 0, 'slippage_points': 0, 'commission_per_lot_round_turn_usd': 0,
                'intrabar_policy': 'stop_first'}}
        results = run_walk_forward_backtest(frame, NoTradeStrategy, {}, ftmo, risk,
                                            train_days=30, test_days=10, step_days=10, warmup_bars=2)
        self.assertGreater(len(results), 0)
        self.assertTrue((results.train_end < results.test_start).all())
        self.assertIn('oos_net_profit', summarize_walk_forward(results))
