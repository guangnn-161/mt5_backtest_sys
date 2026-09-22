import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ftmo_bot'))
from tools.backtest_metrics import calculate_metrics, drawdowns, streaks, daily_equity
from tools.report_builder import save_run_report


def sample():
    pnl = [100., -50., -50., 200., 0.]
    balances = 1000 + np.cumsum(pnl)
    entries = pd.date_range('2026-01-01', periods=5, freq='D', tz='UTC')
    trades = pd.DataFrame({
        'entry_time': entries, 'exit_time': entries + pd.Timedelta(hours=1),
        'type': ['BUY', 'SELL', 'BUY', 'SELL', 'BUY'],
        'entry': 100., 'exit_price': 101., 'sl': 99., 'tp': 102., 'size': .1,
        'pnl_usd': pnl, 'pnl_pct': np.array(pnl) / np.r_[1000, balances[:-1]] * 100,
        'balance': balances, 'commission_usd': 1., 'closed_by': 'test',
        'post_failure_entry': [False, False, False, True, True],
    })
    curve = pd.DataFrame({'time': trades.exit_time, 'equity': balances,
                          'balance': balances, 'is_failed': [False, False, True, True, True]})
    trades.attrs.update(equity_curve=curve, initial_balance=1000,
                        first_fail_time=entries[2], first_fail_reason='Test failure')
    return trades, curve


class MetricTests(unittest.TestCase):
    def test_known_ledger_arithmetic(self):
        trades, curve = sample()
        m = calculate_metrics(trades.to_dict('records'), 1000, curve)
        self.assertEqual(m['net_profit'], 200)
        self.assertEqual(m['gross_profit'], 300)
        self.assertEqual(m['gross_loss'], -100)
        self.assertEqual(m['profit_factor'], 3)
        self.assertEqual(m['expected_payoff'], 40)
        self.assertEqual(m['win_rate_pct'], 40)
        self.assertEqual(m['breakeven_trades'], 1)
        self.assertEqual(m['short_won_pct'], 50)
        self.assertAlmostEqual(m['long_won_pct'], 100 / 3)
        self.assertEqual(m['commission_usd'], 5)
        self.assertEqual(m['holding_seconds_mean'], 3600)
        self.assertEqual(m['consecutive_losses']['longest_count'], 2)
        self.assertEqual(m['consecutive_losses']['longest_amount'], -100)
        self.assertAlmostEqual(m['ghpr'], 1.2**.2)
        self.assertEqual(m['balance_drawdown']['maximal'], 100)
        self.assertAlmostEqual(m['equity_drawdown']['relative_pct'], 100 / 11)
        self.assertEqual(m['recovery_factor'], 2)
        r = np.array([.1, -50/1100, -50/1050, .2, 0])
        self.assertAlmostEqual(m['sharpe_daily_annualized'], np.sqrt(252) * r.mean()/r.std(ddof=1))

    def test_cash_and_relative_drawdowns_can_have_different_troughs(self):
        d = drawdowns([1000, 800, 2000, 1700], 1000)
        self.assertEqual(d['absolute'], 200)
        self.assertEqual(d['maximal'], 300)
        self.assertEqual(d['maximal_pct'], 15)
        self.assertEqual(d['relative_pct'], 20)
        self.assertEqual(d['relative_amount'], 200)

    def test_empty_all_win_all_loss_and_zero_variance(self):
        for pnls in ([], [10], [-10], [0, 0], [10, 10]):
            rows = [{'pnl_usd': p, 'pnl_pct': p/10} for p in pnls]
            m = calculate_metrics(rows, 1000)
            json.dumps(m, allow_nan=False)
            self.assertEqual(m['total_trades'], len(pnls))
            self.assertIsNone(m['sharpe_daily_annualized'])
        self.assertIsNone(calculate_metrics([{'pnl_usd': 10}], 1000)['profit_factor'])
        self.assertEqual(calculate_metrics([{'pnl_usd': 10}], 1000)['profit_factor_status'], 'no_losses')
        self.assertIsNone(calculate_metrics([{'pnl_usd': -1000, 'pnl_pct': -100}], 1000)['ghpr'])

    def test_streak_length_and_cash_magnitude_are_distinct(self):
        s = streaks([1, 1, 1, 0, 100, 100])
        self.assertEqual(s['longest_count'], 3)
        self.assertEqual(s['longest_amount'], 3)
        self.assertEqual(s['largest_count'], 2)
        self.assertEqual(s['largest_amount'], 200)
        self.assertEqual(s['average_count'], 2.5)

    def test_daily_timezone_and_initial_return(self):
        curve = pd.DataFrame({'time': pd.to_datetime(['2026-01-01 22:30', '2026-01-01 23:30']),
                              'equity': [1100, 990]})
        daily = daily_equity(curve, 1000, 'UTC', 'Europe/Prague')
        self.assertEqual(len(daily), 2)
        self.assertEqual(daily.pnl.tolist(), [100, -110])
        self.assertAlmostEqual(daily.return_pct.iloc[1], -10)


class ArtifactTests(unittest.TestCase):
    def test_complete_report_exports_standalone_html_and_does_not_overwrite(self):
        trades, curve = sample()
        with tempfile.TemporaryDirectory() as temp:
            kwargs = dict(reports_root=temp, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          metadata={'symbol':'<script>bad()</script>', 'currency':'USD'})
            folder = save_run_report('momentum', 'run_test', trades.to_dict('records'),
                                     {'initial_balance':1000}, trades, **kwargs)
            payload = json.loads((folder/'report.json').read_text())
            html = (folder/'report.html').read_text()
            self.assertEqual(payload['metrics']['net_profit'], 200)
            self.assertEqual(len(list((folder/'images').glob('*.png'))), 7)
            self.assertEqual(html.count('src="data:image/png;base64,'), 7)
            self.assertNotIn('<script>bad()', html)
            self.assertIn('&lt;script&gt;', html)
            for artifact in payload['artifacts']:
                self.assertTrue((folder/artifact).is_file(), artifact)
            eq = pd.read_csv(folder/'equity_curve.csv')
            self.assertEqual(eq.balance.iloc[-1], 1200)
            self.assertEqual(len(pd.read_csv(folder/'trades.csv')), 5)
            self.assertEqual(len(pd.read_csv(folder/'rolling_windows.csv')), 0)
            original = (folder/'report.html').read_bytes()
            other = save_run_report('momentum', 'run_other', [], {'initial_balance':1000}, **kwargs)
            self.assertNotEqual(folder, other)
            self.assertEqual((folder/'report.html').read_bytes(), original)
            self.assertEqual(len(pd.read_csv(other/'trades.csv')), 0)
            empty = json.loads((other/'report.json').read_text())
            self.assertEqual(empty['metrics']['total_trades'], 0)
            self.assertEqual(empty['metrics']['ending_balance'], 1000)

    def test_engine_balance_series_reconciles(self):
        from tests.test_backtest_integrity import IntegrityTests, AlwaysSignalStrategy
        fixture = IntegrityTests()
        fixture.setUp()
        try:
            frame = fixture.total_loss_frame()
            trades = fixture.engine(frame, AlwaysSignalStrategy()).run()
            curve = trades.attrs['equity_curve']
            self.assertEqual(len(curve), len(frame))
            self.assertAlmostEqual(curve.balance.iloc[-1], 10000 + trades.pnl_usd.sum())
            self.assertAlmostEqual(curve.equity.iloc[-1], curve.balance.iloc[-1])
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
