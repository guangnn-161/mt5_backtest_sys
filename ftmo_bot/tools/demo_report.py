"""Generate a clearly labeled synthetic report for layout review.

Run from the repository root: python ftmo_bot/tools/demo_report.py
No market-data file or historical experiment log is modified.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from backtest.monte_carlo import MonteCarloFTMO
from backtest.walk_forward import run_rolling_window_backtest, summarize
from risk.risk_manager import RiskManager
from risk.compliance_guard import ComplianceGuard
from strategy.momentum import MomentumStrategy
from tools.analyzer import generate_mt5_report, save_report_and_trades
from tools.experiment_logger import get_git_commit


def synthetic_bars():
    rng = np.random.default_rng(2026)
    times = pd.date_range('2026-01-05', periods=90*48, freq='30min', tz='UTC')
    bodies = rng.normal(.02, 2.5, len(times))
    bodies[0] = 3.0
    close = 2100 + np.cumsum(bodies)
    open_ = np.r_[2100, close[:-1]]
    # A deliberate gap exercises the permanent failure marker and orange path.
    close[2:] -= 150
    open_[2:] -= 150
    width = rng.uniform(.1, 2.0, len(times))
    return pd.DataFrame({'time': times, 'open': open_, 'close': close,
                         'high': np.maximum(open_, close)+width,
                         'low': np.minimum(open_, close)-width})


def main():
    root = Path(__file__).resolve().parents[1]
    rules_path = root/'configs/ftmo_rules.yaml'
    risk_path = root/'configs/risk_params.yaml'
    params = {'symbol':'XAUUSD · SYNTHETIC', 'timeframe':'M30',
              'momentum_params': {'body_threshold':2., 'stop_distance':5., 'target_distance':10.}}
    raw = synthetic_bars()
    strategy = MomentumStrategy(params)
    guard = ComplianceGuard(rules_path, risk_path)
    risk = RiskManager(risk_path)
    trades = BacktestEngine(strategy.prepare_data(raw.copy()), strategy, risk, guard).run()
    history = trades.to_dict('records')
    metrics = generate_mt5_report(history, guard.initial_balance, trades.attrs['equity_curve'])
    rolling = run_rolling_window_backtest(raw, lambda: MomentumStrategy(params), rules_path, risk_path)
    metrics['rolling_window'] = summarize(rolling)
    metrics['monte_carlo'] = MonteCarloFTMO(rules_path).run_simulation(trades, n_sims=500)
    folder = save_report_and_trades(
        'demo_momentum', 'SYNTHETIC-DEMO', history, metrics, trades,
        rolling_results=rolling,
        metadata={'symbol':params['symbol'], 'timeframe':'M30', 'is_demo':True,
                  'currency':'USD', 'report_timezone':'Europe/Prague',
                  'source_timezone':'UTC', 'git_commit':get_git_commit()},
        config={'strategy':params, 'ftmo_rules':guard.ftmo_rules, 'risk':risk.risk_params,
                'demo':{'seed':2026, 'synthetic':True, 'monte_carlo_simulations':500}},
    )
    print(folder)
    return folder


if __name__ == '__main__':
    main()
