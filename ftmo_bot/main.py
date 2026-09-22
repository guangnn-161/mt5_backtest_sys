"""Run every discoverable strategy against the same backtest dataset.

Add a strategy as a ``*Strategy`` subclass in ``strategy/``. This runner
discovers it automatically; no strategy name needs to be edited in this file.
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path
import pkgutil
import sys

import pandas as pd
import yaml

import strategy
from strategy.base import BaseStrategy
from tools.experiment_logger import log_experiment, get_git_commit
from tools.analyzer import generate_mt5_report, save_report_and_trades
from backtest.monte_carlo import MonteCarloFTMO
from backtest.engine import BacktestEngine
from risk.risk_manager import RiskManager
from risk.compliance_guard import ComplianceGuard
from backtest.walk_forward import run_rolling_window_backtest, summarize
from tools.market_data_store import MarketDataStore


ROOT_DIR = Path(__file__).parent
sys.path.append(str(ROOT_DIR))


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load the new MT5 lake first, with a backwards-compatible CSV fallback."""
    timeframe = timeframe.upper()
    market_root = ROOT_DIR / 'data' / 'market' / 'mt5'
    legacy_path = ROOT_DIR / 'data' / 'raw' / f'{symbol.lower()}_{timeframe.lower()}.csv'
    store_error = None
    if (market_root / 'catalog.sqlite').exists():
        store = MarketDataStore(market_root)
        try:
            return store.read_bars(symbol, timeframe)
        except FileNotFoundError as error:
            store_error = error
        finally:
            store.close()
    if legacy_path.exists():
        df = pd.read_csv(legacy_path)
        df['time'] = pd.to_datetime(df['time'])
        return df
    detail = f' {store_error}' if store_error is not None else ''
    raise FileNotFoundError(
        f'No backtest data found for {symbol} {timeframe}. '
        'Run: python ftmo_bot/tools/download_mt5_data.py.' + detail
    )


def discover_strategies() -> dict[str, type[BaseStrategy]]:
    """Discover concrete ``*Strategy`` classes defined in ``strategy/*.py``."""
    discovered = {}
    for module_info in pkgutil.iter_modules(strategy.__path__):
        if module_info.name.startswith('_') or module_info.name == 'base':
            continue
        module = importlib.import_module(f'{strategy.__name__}.{module_info.name}')
        for class_name, candidate in inspect.getmembers(module, inspect.isclass):
            if (
                candidate.__module__ != module.__name__
                or candidate is BaseStrategy
                or not issubclass(candidate, BaseStrategy)
                or not class_name.endswith('Strategy')
            ):
                continue
            name = class_name.removesuffix('Strategy').lower()
            if name in discovered:
                raise ValueError(
                    f"Duplicate automatic strategy name '{name}' from "
                    f"{candidate.__module__}.{class_name}"
                )
            discovered[name] = candidate
    return dict(sorted(discovered.items()))


def load_strategy_params(strategy_name: str) -> dict:
    """Use an optional per-strategy config, otherwise the shared config."""
    config_path = ROOT_DIR / 'configs' / f'{strategy_name}_params.yaml'
    if not config_path.exists():
        config_path = ROOT_DIR / 'configs' / 'strategy_params.yaml'
    with open(config_path, 'r', encoding='utf-8') as handle:
        params = yaml.safe_load(handle)
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ValueError(f'Strategy config must be a mapping: {config_path}')
    return params


def run_strategy(strategy_name: str, strategy_class: type[BaseStrategy], raw_df: pd.DataFrame,
                 data_symbol: str, data_timeframe: str) -> dict:
    """Run one isolated strategy and write its own report directory."""
    print(f'\n[>] Strategy [{strategy_name.upper()}]')
    strat_params = load_strategy_params(strategy_name)
    guard = ComplianceGuard(ROOT_DIR / 'configs' / 'ftmo_rules.yaml',
                            ROOT_DIR / 'configs' / 'risk_params.yaml')
    risk = RiskManager(ROOT_DIR / 'configs' / 'risk_params.yaml')

    def run_path(*, enforce_limits: bool):
        strategy = strategy_class(dict(strat_params))
        prepared = strategy.prepare_data(raw_df.copy())
        engine = BacktestEngine(
            prepared, strategy,
            RiskManager(ROOT_DIR / 'configs' / 'risk_params.yaml'),
            ComplianceGuard(ROOT_DIR / 'configs' / 'ftmo_rules.yaml', ROOT_DIR / 'configs' / 'risk_params.yaml'),
            continue_after_failure=False,
            enforce_limits=enforce_limits,
            enforce_internal_stop=False,
        )
        return engine.run(), strategy

    print('[*] Running paired FTMO-constrained and no-loss-constraint backtests...')
    df_trades, strategy_instance = run_path(enforce_limits=True)
    unconstrained_trades, _ = run_path(enforce_limits=False)
    constrained_history = df_trades.to_dict('records') if not df_trades.empty else []
    if not constrained_history:
        print('[!] No orders were generated.')

    constrained_metrics = generate_mt5_report(
        constrained_history, guard.initial_balance, df_trades.attrs['equity_curve'],
        guard.ftmo_rules.get('data_timezone', 'UTC'),
        guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
    )
    metrics = generate_mt5_report(
        unconstrained_trades.to_dict('records') if not unconstrained_trades.empty else [],
        guard.initial_balance, unconstrained_trades.attrs['equity_curve'],
        guard.ftmo_rules.get('data_timezone', 'UTC'),
        guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
    )
    print('[*] No-loss-constraint result: P/L {net_profit:.2f}$ | Win rate: {win_rate_pct:.2f}% | Trades: {total_trades}'.format(**metrics))

    print('[*] Running rolling-window robustness test (30 days, step 5 days)...')
    rolling_results = run_rolling_window_backtest(
        df=raw_df,
        strategy_factory=lambda: strategy_class(dict(strat_params)),
        ftmo_rules_path=ROOT_DIR / 'configs' / 'ftmo_rules.yaml',
        risk_params_path=ROOT_DIR / 'configs' / 'risk_params.yaml',
        window_days=30,
        step_days=5,
        enforce_limits=False,
    )
    rolling_metrics = summarize(rolling_results)

    mc_metrics = {'status': 'skipped', 'reason': 'no_trades'}
    if constrained_history:
        print('[*] Running Monte Carlo (10,000 simulations)...')
        mc = MonteCarloFTMO(ROOT_DIR / 'configs' / 'ftmo_rules.yaml')
        mc_metrics = mc.run_simulation(df_trades[['exit_time', 'pnl_pct']], n_sims=10000)
        mc_metrics['source_scope'] = 'ftmo_constrained_path'

    combined_metrics = {
        **metrics,
        'initial_balance': guard.initial_balance,
        'simulation_scope': 'no_loss_constraint_full_history',
        'first_fail_time': (str(df_trades.attrs['first_fail_time'])
                            if df_trades.attrs.get('first_fail_time') is not None else None),
        'first_fail_reason': df_trades.attrs.get('first_fail_reason'),
        'post_failure_trades': 0,
        'ftmo_constrained_path': constrained_metrics,
        'monte_carlo': mc_metrics,
        'rolling_window': rolling_metrics,
    }
    run_id = log_experiment(params=strat_params, metrics=combined_metrics,
                            notes=f'Automatic run for strategy {strategy_name}')
    report_dir = save_report_and_trades(
        strategy_name, run_id, constrained_history, combined_metrics, df_trades,
        unconstrained_df_trades=unconstrained_trades,
        rolling_results=rolling_results,
        reports_root=ROOT_DIR / 'reports',
        metadata={
            'symbol': data_symbol,
            'timeframe': data_timeframe,
            'currency': guard.ftmo_rules.get('currency', 'USD'),
            'bars': len(raw_df),
            'period_start': str(raw_df.time.min()) if len(raw_df) else 'N/A',
            'period_end': str(raw_df.time.max()) if len(raw_df) else 'N/A',
            'source_timezone': guard.ftmo_rules.get('data_timezone', 'UTC'),
            'report_timezone': guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
            'git_commit': get_git_commit(),
        },
        config={'strategy': strategy_instance.params, 'ftmo_rules': guard.ftmo_rules,
                'risk': risk.risk_params,
                'rolling': {'window_days': 30, 'step_days': 5, 'warmup_bars': 300},
                'monte_carlo': {'n_sims': 10000, 'seed': 42, 'block_days': 5}},
    )
    return {'strategy': strategy_name, 'status': 'completed', 'report_dir': report_dir,
            'net_profit': metrics['net_profit'], 'total_trades': metrics['total_trades']}


def main() -> list[dict]:
    strategies = discover_strategies()
    if not strategies:
        raise RuntimeError('No *Strategy subclasses were found in ftmo_bot/strategy')
    data_params = load_strategy_params('__batch__')
    data_symbol = str(data_params.get('symbol', '')).strip()
    data_timeframe = str(data_params.get('timeframe', '')).strip().upper()
    if not data_symbol or not data_timeframe:
        raise ValueError('strategy_params.yaml must define symbol and timeframe for the batch data source')
    raw_df = load_data(data_symbol, data_timeframe)
    print(f'[*] Loaded {len(raw_df):,} {data_symbol} {data_timeframe} candles. Running {len(strategies)} strategies: {", ".join(strategies)}')

    results = []
    for name, strategy_class in strategies.items():
        try:
            results.append(run_strategy(name, strategy_class, raw_df, data_symbol, data_timeframe))
        except Exception as error:
            # A broken strategy must not hide reports from the others.
            print(f'[!] Strategy [{name.upper()}] failed: {type(error).__name__}: {error}')
            results.append({'strategy': name, 'status': 'failed', 'error': str(error)})

    print('\n=== Batch backtest summary ===')
    for result in results:
        if result['status'] == 'completed':
            print('[OK] {strategy}: P/L {net_profit:.2f}$ | {total_trades} trades | {report_dir}'.format(**result))
        else:
            print(f'[FAIL] {result["strategy"]}: {result["error"]}')
    return results


if __name__ == '__main__':
    main()
