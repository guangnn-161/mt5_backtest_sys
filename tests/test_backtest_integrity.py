import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ftmo_bot"))

from backtest.engine import BacktestEngine
from backtest.monte_carlo import MonteCarloFTMO
from backtest.walk_forward import _compute_max_dd_pct
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
