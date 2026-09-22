import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ftmo_bot"))

from backtest.engine import BacktestEngine
from backtest.monte_carlo import MonteCarloFTMO
from backtest.walk_forward import _compute_max_dd_pct, _classify_outcome, run_rolling_window_backtest
from risk.compliance_guard import ComplianceGuard
from risk.risk_manager import RiskManager


class OneShotStrategy:
    def __init__(self):
        self.sent = False

    def generate_signal(self, row):
        if self.sent:
            return None
        self.sent = True
        return {
            "type": "BUY",
            "entry": row.close,
            "sl": row.close - 1.0,
            "tp": row.close + 2.0,
        }


class AlwaysSignalStrategy:
    def __init__(self, params=None):
        self.params = params or {}

    def prepare_data(self, frame):
        return frame

    def generate_signal(self, row):
        return {
            "type": "BUY",
            "entry": row.close,
            "sl": row.close - 1.0,
            "tp": row.close + 2.0,
        }


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.ftmo_path = root / "ftmo.yaml"
        self.risk_path = root / "risk.yaml"
        self.ftmo_path.write_text(
            yaml.safe_dump(
                {
                    "account_size": 10000,
                    "max_daily_loss_pct": 5.0,
                    "max_total_loss_pct": 10.0,
                    "profit_target_pct": 10.0,
                    "drawdown_type": "static_from_initial",
                    "data_timezone": "UTC",
                    "daily_reset_timezone": "Europe/Prague",
                }
            ),
            encoding="utf-8",
        )
        self.risk_path.write_text(
            yaml.safe_dump(
                {
                    "risk_per_trade_pct": 0.5,
                    "max_open_risk_pct": 1.5,
                    "daily_loss_buffer_pct": 1.0,
                    "total_loss_buffer_pct": 1.5,
                    "caution_threshold_ratio": 0.6,
                    "critical_threshold_ratio": 0.9,
                    "execution": {
                        "contract_size": 100,
                        "point_size": 0.01,
                        "spread_points": 20,
                        "slippage_points": 5,
                        "commission_per_lot_round_turn_usd": 4.0,
                        "intrabar_policy": "stop_first",
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def engine(self, frame, strategy=None, trading_start_time=None):
        return BacktestEngine(
            frame,
            strategy or OneShotStrategy(),
            RiskManager(self.risk_path),
            ComplianceGuard(self.ftmo_path, self.risk_path),
            trading_start_time=trading_start_time,
        )

    @staticmethod
    def frame(rows):
        return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])

    def test_signal_fills_next_bar_and_ambiguous_bar_uses_stop(self):
        frame = self.frame(
            [
                ("2026-01-02 10:00", 100, 101, 99.8, 100),
                ("2026-01-02 10:05", 100, 103, 99, 101),
            ]
        )
        trades = self.engine(frame).run()

        self.assertEqual(len(trades), 1)
        trade = trades.iloc[0]
        self.assertEqual(trade.closed_by, "stop_loss")
        self.assertEqual(trade.signal_time, pd.Timestamp(frame.iloc[0].time))
        self.assertEqual(trade.entry_time, pd.Timestamp(frame.iloc[1].time))
        self.assertAlmostEqual(trade.entry, 100.25)
        self.assertAlmostEqual(trade.pnl_usd, -49.05)
        self.assertLessEqual(abs(trade.pnl_usd), 50.0)

    def test_open_trade_is_closed_at_end_of_data(self):
        frame = self.frame(
            [
                ("2026-01-02 10:00", 100, 100.5, 99.8, 100),
                ("2026-01-02 10:05", 100, 101.5, 99.5, 101),
            ]
        )
        trades = self.engine(frame).run()
        self.assertEqual(trades.iloc[0].closed_by, "end_of_data")
        self.assertAlmostEqual(
            trades.attrs["ending_balance"],
            self.engine(frame).initial_balance + trades.pnl_usd.sum(),
        )

    def test_daily_reset_uses_configured_timezone(self):
        frame = self.frame([("2026-01-01 23:30", 100, 100, 100, 100)])
        engine = self.engine(frame)
        self.assertEqual(str(engine._trading_day(frame.iloc[0].time)), "2026-01-02")

    def test_invalid_ohlc_data_is_rejected(self):
        frame = self.frame(
            [
                ("2026-01-02 10:00", 100, 99, 98, 100),
                ("2026-01-02 10:00", 100, 101, 99, 100),
            ]
        )
        with self.assertRaises(ValueError):
            self.engine(frame)

    def test_internal_stop_is_not_reported_as_hard_breach(self):
        guard = ComplianceGuard(self.ftmo_path, self.risk_path)
        internal, internal_reason = guard.check_internal_stop(9590, 10000, 10000)
        hard, _ = guard.check_hard_violation(9590, 10000, 10000)
        self.assertTrue(internal)
        self.assertIn("INTERNAL STOP", internal_reason)
        self.assertFalse(hard)

    def test_entry_evaluation_boolean_is_not_inverted(self):
        guard = ComplianceGuard(self.ftmo_path, self.risk_path)
        allowed, reason = guard.evaluate_entry(10000, 10000, 10000)
        self.assertTrue(allowed)
        self.assertEqual(reason, "OK")

    def test_max_open_risk_budget_is_enforced(self):
        risk = RiskManager(self.risk_path)
        size = risk.calculate_position_size(
            10000, 100, 99, contract_size=100, current_open_risk_usd=149.5
        )
        self.assertEqual(size, 0.0)

    def test_warmup_bars_cannot_place_orders(self):
        frame = self.frame(
            [
                ("2026-01-02 09:55", 100, 100.1, 99.9, 100),
                ("2026-01-02 10:00", 100, 100.1, 99.9, 100),
                ("2026-01-02 10:05", 100, 103, 99.5, 101),
            ]
        )
        trades = self.engine(
            frame, AlwaysSignalStrategy(), trading_start_time="2026-01-02 10:00"
        ).run()
        self.assertEqual(trades.iloc[0].signal_time, pd.Timestamp(frame.iloc[1].time))
        self.assertEqual(trades.iloc[0].entry_time, pd.Timestamp(frame.iloc[2].time))

    def test_drawdown_is_positive_peak_to_trough(self):
        curve = pd.DataFrame({"equity": [10000, 11000, 10450]})
        self.assertAlmostEqual(_compute_max_dd_pct(curve, 10000), 5.0)

    def total_loss_frame(self, days=1):
        # A gap crosses the total floor before internal gates can prevent it.
        rules = yaml.safe_load(self.ftmo_path.read_text())
        rules['max_daily_loss_pct'] = 90.0
        self.ftmo_path.write_text(yaml.safe_dump(rules))
        rows = []
        for day in pd.date_range('2026-01-02', periods=days):
            for bar in range(60):
                price = 100 if bar < 2 else 70
                high = price + (0.5 if bar < 3 else 3)
                rows.append((day + pd.Timedelta(hours=10, minutes=5 * bar),
                             price, high, price - 0.5, price))
        return self.frame(rows)

    def test_total_loss_keeps_trading_and_failure_stays_after_recovery(self):
        frame = self.total_loss_frame()
        trades = self.engine(frame, AlwaysSignalStrategy()).run()
        fail_time = pd.Timestamp(frame.iloc[2].time)
        self.assertEqual(trades.attrs['first_fail_time'], fail_time)
        self.assertIn('Max Total', trades.attrs['first_fail_reason'])
        later = trades[trades.entry_time > fail_time]
        self.assertGreater(len(later), 1)
        self.assertTrue(later.post_failure_entry.all())
        self.assertGreater(trades.iloc[-1].balance, 11000)
        curve = trades.attrs['equity_curve']
        self.assertTrue(curve.loc[curve.time >= fail_time, 'is_failed'].all())
        self.assertFalse(curve.loc[curve.time < fail_time, 'is_failed'].any())
        self.assertEqual(curve.iloc[-1].time, frame.iloc[-1].time)
        guard = ComplianceGuard(self.ftmo_path, self.risk_path)
        outcome, _, result_time = _classify_outcome(
            trades, guard, frame.iloc[0].time.date(), frame.iloc[-1].time.date())
        self.assertEqual(outcome, 'fail_total')
        self.assertEqual(result_time, fail_time)

    def test_daily_failure_continues_across_daily_reset(self):
        frame = self.total_loss_frame(days=2)
        rules = yaml.safe_load(self.ftmo_path.read_text())
        rules['max_daily_loss_pct'] = 5.0
        self.ftmo_path.write_text(yaml.safe_dump(rules))
        trades = self.engine(frame, AlwaysSignalStrategy()).run()
        self.assertIn('Max Daily', trades.attrs['first_fail_reason'])
        curve = trades.attrs['equity_curve']
        next_day = curve.time.dt.date == frame.iloc[-1].time.date()
        self.assertTrue(curve.loc[next_day, 'is_failed'].all())
        self.assertTrue(curve.loc[next_day, 'stress_mode'].all())
        self.assertTrue((trades.entry_time.dt.date == frame.iloc[-1].time.date()).any())

    def test_halt_mode_still_available(self):
        frame = self.total_loss_frame()
        engine = self.engine(frame, AlwaysSignalStrategy())
        engine.continue_after_failure = False
        trades = engine.run()
        self.assertEqual(len(trades), 1)
        self.assertFalse(trades.post_failure_entry.any())

    def test_each_rolling_window_runs_after_total_loss(self):
        frame = self.total_loss_frame(days=4)
        results = run_rolling_window_backtest(
            frame, AlwaysSignalStrategy, self.ftmo_path, self.risk_path,
            window_days=2, step_days=1, warmup_bars=10)
        self.assertEqual(len(results), 3)
        self.assertTrue((results.outcome == 'fail_total').all())
        self.assertTrue((results.post_failure_trades > 0).all())
        self.assertTrue((results.num_trades_full_simulation > results.num_trades).all())

    def test_dashboard_uses_orange_for_entire_post_failure_path(self):
        import matplotlib
        matplotlib.use('Agg')
        from tools import analyzer
        frame = self.total_loss_frame()
        trades = self.engine(frame, AlwaysSignalStrategy()).run()
        with patch.object(analyzer.plt, 'savefig'), patch.object(analyzer.plt, 'close'):
            analyzer.plot_equity_curve_split(
                trades, {}, Path(self.temp_dir.name) / 'dashboard.jpg', 'TEST', 'test')
            lines = {line.get_label(): line for line in analyzer.plt.gca().lines}
            line = lines['Post-failure simulation']
            self.assertEqual(line.get_color(), 'tab:orange')
            self.assertEqual(pd.Timestamp(line.get_xdata()[-1]), frame.iloc[-1].time)
            self.assertGreater(len(set(line.get_ydata())), 1)
            self.assertIn('First hard breach', lines)
        analyzer.plt.close('all')

    def test_main_runs_rolling_after_failure_and_when_no_trades(self):
        import main as app
        import shutil
        root = Path(self.temp_dir.name)
        frame = self.total_loss_frame(days=31)
        (root / 'configs').mkdir()
        shutil.copy(self.ftmo_path, root / 'configs/ftmo_rules.yaml')
        shutil.copy(self.risk_path, root / 'configs/risk_params.yaml')
        (root / 'configs/strategy_params.yaml').write_text('symbol: TEST\ntimeframe: M5\n')
        (root / 'reports/test').mkdir(parents=True)
        # Orchestration uses the real engine; rolling itself is tested above.
        for has_trades in (True, False):
            with self.subTest(has_trades=has_trades), redirect_stdout(StringIO()), \
                 patch.object(app, 'ROOT_DIR', root), \
                 patch.object(app, 'discover_strategies', return_value={'test': AlwaysSignalStrategy}), \
                 patch.object(app, 'load_data', return_value=frame), \
                 patch.object(app, 'run_rolling_window_backtest', return_value=pd.DataFrame()) as rolling, \
                 patch.object(app.MonteCarloFTMO, 'run_simulation', return_value={'p_pass': 0}) as mc, \
                 patch.object(app, 'log_experiment', return_value='regression'), \
                 patch.object(app, 'save_report_and_trades') as save, \
                 patch.object(AlwaysSignalStrategy, 'generate_signal',
                              autospec=True, side_effect=AlwaysSignalStrategy.generate_signal if has_trades else lambda *args: None):
                app.main()
                rolling.assert_called_once()
                save.assert_called_once()
                metrics = save.call_args.args[3]
                if has_trades:
                    self.assertIn('Max Total', metrics['first_fail_reason'])
                    mc.assert_called_once()
                else:
                    mc.assert_not_called()
                    self.assertEqual(metrics['monte_carlo']['status'], 'skipped')
                self.assertIn('rolling_results', save.call_args.kwargs)
                self.assertEqual(save.call_args.kwargs['reports_root'], root / 'reports')

    def test_main_discovers_all_strategies_without_name_configuration(self):
        import main as app
        discovered = app.discover_strategies()
        self.assertEqual(set(discovered), {'momentum', 'simplersi', 'triplemomentum'})

    def test_monte_carlo_requires_dates_and_preserves_real_days(self):
        mc = MonteCarloFTMO(self.ftmo_path, block_days=2)
        with self.assertRaises(ValueError):
            mc.run_simulation([0.5, -0.5], n_sims=10)

        trades = pd.DataFrame(
            {
                "exit_time": [
                    "2026-01-01 10:00Z",
                    "2026-01-01 11:00Z",
                    "2026-01-02 10:00Z",
                    "2026-01-05 10:00Z",
                ],
                "pnl_pct": [0.5, -0.5, 0.5, -0.5],
            }
        )
        result = mc.run_simulation(trades, n_sims=20, seed=7)
        self.assertEqual(result["source_trading_days"], 3)
        total_probability = sum(
            result[key]
            for key in ("p_pass", "p_fail_max_dd", "p_fail_daily_loss", "p_timeout")
        )
        self.assertAlmostEqual(total_probability, 100.0)


if __name__ == "__main__":
    unittest.main()
