"""Run reproducible backtests across a Parquet MT5 research lake.

Example::

    python ftmo_bot/tools/run_research.py

The command never connects to MT5. Sync first with ``download_mt5_data.py``;
then this runner selects catalogued symbol/timeframe pairs, writes one immutable
data fingerprint per job, and stores reports beneath one timestamped research run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import uuid

import pandas as pd
import yaml

BOT_ROOT = Path(__file__).resolve().parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from backtest.engine import BacktestEngine
from backtest.monte_carlo import MonteCarloFTMO
from backtest.walk_forward import run_rolling_window_backtest, summarize
from main import discover_strategies, load_strategy_params
from risk.compliance_guard import ComplianceGuard
from risk.risk_manager import RiskManager
from tools.analyzer import generate_mt5_report, save_report_and_trades
from tools.experiment_logger import get_git_commit
from tools.market_data_store import MarketDataStore, iso_utc
from tools.research_catalog import ResearchCatalog


def load_config(path: Path) -> dict:
    with path.open('r', encoding='utf-8') as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ValueError(f'Research config must be a YAML mapping: {path}')
    defaults = {
        'storage_root': 'data/market/mt5', 'reports_root': 'reports/research',
        'symbols': 'catalog', 'timeframes': 'all', 'strategies': 'all',
        'start_utc': None, 'end_utc': None, 'max_jobs': None,
        'rolling': {'enabled': True, 'window_days': 30, 'step_days': 5, 'warmup_bars': 300},
        'monte_carlo': {'enabled': False, 'n_sims': 10_000},
    }
    for key, value in defaults.items():
        config.setdefault(key, value)
    for nested in ('rolling', 'monte_carlo'):
        merged = dict(defaults[nested])
        merged.update(config.get(nested) or {})
        config[nested] = merged
    if config['symbols'] != 'catalog' and not isinstance(config['symbols'], list):
        raise ValueError("symbols must be 'catalog' or a YAML list")
    if config['timeframes'] != 'all' and not isinstance(config['timeframes'], list):
        raise ValueError("timeframes must be 'all' or a YAML list")
    if config['strategies'] != 'all' and not isinstance(config['strategies'], list):
        raise ValueError("strategies must be 'all' or a YAML list")
    if config['max_jobs'] is not None and (not isinstance(config['max_jobs'], int) or config['max_jobs'] < 1):
        raise ValueError('max_jobs must be null or a positive integer')
    for key in ('start_utc', 'end_utc'):
        if config[key] is not None:
            config[key] = iso_utc(config[key])
    if config['start_utc'] and config['end_utc'] and config['start_utc'] > config['end_utc']:
        raise ValueError('start_utc must not be after end_utc')
    return config


def select_pairs(store: MarketDataStore, config: dict) -> list[tuple[str, str]]:
    pairs = [(row['symbol'], row['timeframe']) for row in store.available_pairs()]
    symbols = config['symbols']
    timeframes = config['timeframes']
    if symbols != 'catalog':
        wanted = set(symbols)
        pairs = [pair for pair in pairs if pair[0] in wanted]
    if timeframes != 'all':
        wanted = {value.upper() for value in timeframes}
        pairs = [pair for pair in pairs if pair[1].upper() in wanted]
    return pairs


def select_strategies(config: dict):
    discovered = discover_strategies()
    names = sorted(discovered) if config['strategies'] == 'all' else config['strategies']
    unknown = sorted(set(names).difference(discovered))
    if unknown:
        raise ValueError('Unknown discovered strategy name(s): ' + ', '.join(unknown))
    return [(name, discovered[name]) for name in names]


def safe_id(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', value).strip('._') or 'item'


def execute_job(strategy_name, strategy_class, raw_df, params, symbol, timeframe, config):
    """Run exactly one isolated strategy/pair without sharing indicator state."""
    guard = ComplianceGuard(BOT_ROOT / 'configs' / 'ftmo_rules.yaml', BOT_ROOT / 'configs' / 'risk_params.yaml')
    risk = RiskManager(BOT_ROOT / 'configs' / 'risk_params.yaml')
    strategy = strategy_class(dict(params))
    prepared = strategy.prepare_data(raw_df.copy())
    trades = BacktestEngine(prepared, strategy, risk, guard).run()
    history = trades.to_dict('records') if not trades.empty else []
    metrics = generate_mt5_report(
        history, guard.initial_balance, trades.attrs['equity_curve'],
        guard.ftmo_rules.get('data_timezone', 'UTC'), guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
    )
    rolling_results = pd.DataFrame()
    rolling_metrics = {'status': 'disabled'}
    rolling = config['rolling']
    if rolling['enabled']:
        rolling_results = run_rolling_window_backtest(
            df=raw_df, strategy_factory=lambda: strategy_class(dict(params)),
            ftmo_rules_path=BOT_ROOT / 'configs' / 'ftmo_rules.yaml',
            risk_params_path=BOT_ROOT / 'configs' / 'risk_params.yaml',
            window_days=rolling['window_days'], step_days=rolling['step_days'], warmup_bars=rolling['warmup_bars'],
        )
        rolling_metrics = summarize(rolling_results)
    mc_metrics = {'status': 'disabled'}
    if config['monte_carlo']['enabled'] and history:
        mc_metrics = MonteCarloFTMO(BOT_ROOT / 'configs' / 'ftmo_rules.yaml').run_simulation(
            trades[['exit_time', 'pnl_pct']], n_sims=config['monte_carlo']['n_sims']
        )
        mc_metrics['source_scope'] = 'full_simulation_including_post_failure'
    return trades, {
        **metrics, 'initial_balance': guard.initial_balance,
        'simulation_scope': 'full_history_including_post_failure',
        'first_fail_time': str(trades.attrs['first_fail_time']) if trades.attrs.get('first_fail_time') is not None else None,
        'first_fail_reason': trades.attrs.get('first_fail_reason'),
        'post_failure_trades': sum(bool(trade.get('post_failure_entry', False)) for trade in history),
        'monte_carlo': mc_metrics, 'rolling_window': rolling_metrics,
    }, rolling_results, strategy, guard, risk


def run_research(config: dict) -> dict:
    storage_root = Path(config['storage_root'])
    reports_root = Path(config['reports_root'])
    if not storage_root.is_absolute():
        storage_root = BOT_ROOT / storage_root
    if not reports_root.is_absolute():
        reports_root = BOT_ROOT / reports_root
    store = MarketDataStore(storage_root)
    created = datetime.now(timezone.utc)
    run_id = 'research_' + created.strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8]
    run_root = reports_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    manifest_path = run_root / 'manifest.json'
    catalog = ResearchCatalog(reports_root / 'catalog')
    commit = get_git_commit()
    catalog.start_run(run_id, iso_utc(created), config, commit, manifest_path)
    try:
        pairs = select_pairs(store, config)
        strategies = select_strategies(config)
        jobs = [(name, klass, symbol, timeframe) for name, klass in strategies for symbol, timeframe in pairs]
        if config['max_jobs'] is not None:
            jobs = jobs[:config['max_jobs']]
        if not jobs:
            raise RuntimeError('No research jobs selected. Sync MT5 data first or relax research.yaml filters.')
        manifest = {
            'schema_version': 1, 'run_id': run_id, 'created_at_utc': iso_utc(created),
            'git_commit': commit, 'configuration': config,
            'selected_jobs': [{'strategy': n, 'symbol': s, 'timeframe': t} for n, _, s, t in jobs],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        summary = []
        print(f'[*] Research {run_id}: {len(strategies)} strategies × {len(pairs)} lake pairs = {len(jobs)} jobs')
        for index, (name, klass, symbol, timeframe) in enumerate(jobs, start=1):
            job_id = f'{safe_id(name)}__{safe_id(symbol)}__{safe_id(timeframe)}'
            print(f'[{index}/{len(jobs)}] {name} · {symbol} {timeframe}', end=' ... ', flush=True)
            snapshot = None
            try:
                snapshot = store.data_snapshot(symbol, timeframe, start=config['start_utc'], end=config['end_utc'])
                catalog.start_job(run_id, job_id, name, symbol, timeframe, snapshot)
                raw_df = store.read_bars(symbol, timeframe, start=config['start_utc'], end=config['end_utc'])
                params = dict(load_strategy_params(name))
                params.update({'symbol': symbol, 'timeframe': timeframe})
                trades, metrics, rolling_results, strategy, guard, risk = execute_job(
                    name, klass, raw_df, params, symbol, timeframe, config
                )
                report_path = save_report_and_trades(
                    name, job_id, trades.to_dict('records'), metrics, trades,
                    rolling_results=rolling_results, reports_root=run_root,
                    metadata={
                        'symbol': symbol, 'timeframe': timeframe,
                        'currency': guard.ftmo_rules.get('currency', 'USD'), 'bars': len(raw_df),
                        'period_start': str(raw_df.time.min()), 'period_end': str(raw_df.time.max()),
                        'source_timezone': guard.ftmo_rules.get('data_timezone', 'UTC'),
                        'report_timezone': guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
                        'git_commit': commit, 'data_fingerprint': snapshot['fingerprint'],
                    },
                    config={'strategy': strategy.params, 'ftmo_rules': guard.ftmo_rules,
                            'risk': risk.risk_params, 'research': config,
                            'data_snapshot': snapshot},
                )
                catalog.complete_job(run_id, job_id, report_path, metrics)
                summary.append({'job_id': job_id, 'strategy': name, 'symbol': symbol, 'timeframe': timeframe,
                                'status': 'completed', 'data_fingerprint': snapshot['fingerprint'],
                                'net_profit': metrics['net_profit'], 'total_trades': metrics['total_trades'],
                                'report_path': str(report_path)})
                print(f"OK · P/L {metrics['net_profit']:.2f} · {metrics['total_trades']} trades")
            except Exception as error:
                # A damaged/missing partition can fail before there is a snapshot.
                # Still preserve the job's identity in the catalog for diagnosis.
                if snapshot is None:
                    catalog.start_job(run_id, job_id, name, symbol, timeframe,
                                      {'fingerprint': 'unavailable', 'partitions': []})
                catalog.fail_job(run_id, job_id, error)
                summary.append({'job_id': job_id, 'strategy': name, 'symbol': symbol, 'timeframe': timeframe,
                                'status': 'failed', 'error': f'{type(error).__name__}: {error}'})
                print(f'FAILED · {type(error).__name__}: {error}')
        summary_frame = pd.DataFrame(summary)
        summary_frame.to_csv(run_root / 'summary.csv', index=False)
        try:
            summary_frame.to_parquet(run_root / 'summary.parquet', index=False)
        except ImportError:
            pass
        completed = int((summary_frame.status == 'completed').sum())
        result = {'run_id': run_id, 'jobs': len(summary), 'completed': completed, 'failed': len(summary) - completed,
                  'run_root': str(run_root)}
        manifest.update({'completed_at_utc': iso_utc(datetime.now(timezone.utc)), 'summary': result, 'results': summary})
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        catalog.finish_run(run_id, manifest['completed_at_utc'], 'completed' if not result['failed'] else 'completed_with_errors', result)
        print(f'[*] Research complete: {result}')
        return result
    except Exception as error:
        catalog.finish_run(run_id, iso_utc(datetime.now(timezone.utc)), 'failed', {'fatal_error': str(error)})
        raise
    finally:
        catalog.close()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Run all selected strategies against catalogued Parquet MT5 data.')
    parser.add_argument('--config', type=Path, default=BOT_ROOT / 'configs' / 'research.yaml')
    args = parser.parse_args()
    run_research(load_config(args.config))


if __name__ == '__main__':
    main()
