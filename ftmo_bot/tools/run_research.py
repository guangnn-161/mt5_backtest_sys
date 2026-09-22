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
from backtest.walk_forward import (run_rolling_window_backtest, run_walk_forward_backtest,
                                   summarize, summarize_walk_forward)
from main import discover_strategies, load_strategy_params
from risk.compliance_guard import ComplianceGuard
from risk.risk_manager import RiskManager
from tools.analyzer import generate_mt5_report, save_report_and_trades
from tools.experiment_logger import get_git_commit
from tools.market_data_store import MarketDataStore, iso_utc
from tools.research_catalog import ResearchCatalog
from tools.data_quality import validate_bars
from tools.research_profiles import (load_yaml, resolve_instrument, resolved_risk_params,
                                     strategy_supports)


def load_config(path: Path) -> dict:
    with path.open('r', encoding='utf-8') as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ValueError(f'Research config must be a YAML mapping: {path}')
    defaults = {
        'storage_root': 'data/market/mt5', 'reports_root': 'reports/research',
        'instrument_registry': 'configs/instruments.yaml',
        'symbols': 'catalog', 'timeframes': 'all', 'strategies': 'all',
        'start_utc': None, 'end_utc': None, 'max_jobs': None,
        'rolling': {'enabled': True, 'window_days': 30, 'step_days': 5, 'warmup_bars': 300},
        'monte_carlo': {'enabled': False, 'n_sims': 10_000},
        'data_quality': {'min_bars': 100, 'min_bars_by_timeframe': {}, 'fail_on_duplicates': True, 'fail_on_null_ohlc': True,
                         'fail_on_impossible_ohlc': True, 'gap_warning_multiplier': 3},
        'walk_forward': {'enabled': False, 'train_days': 180, 'test_days': 30,
                         'step_days': 30, 'warmup_bars': 300, 'parameter_grids': {}},
    }
    for key, value in defaults.items():
        config.setdefault(key, value)
    for nested in ('rolling', 'monte_carlo', 'data_quality', 'walk_forward'):
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


def execute_job(strategy_name, strategy_class, raw_df, params, symbol, timeframe, config,
                ftmo_rules, risk_params):
    """Run paired FTMO-constrained and unconstrained paths from fresh state."""
    guard = ComplianceGuard(ftmo_rules, risk_params)
    def run_path(*, enforce_limits: bool):
        strategy = strategy_class(dict(params))
        prepared = strategy.prepare_data(raw_df.copy())
        engine = BacktestEngine(
            prepared, strategy, RiskManager(risk_params), ComplianceGuard(ftmo_rules, risk_params),
            continue_after_failure=False, enforce_limits=enforce_limits,
            # The constrained path represents the published FTMO hard limits;
            # internal buffers are research safeguards, not an FTMO breach.
            enforce_internal_stop=False,
        )
        return engine.run(), strategy

    trades, strategy = run_path(enforce_limits=True)
    unconstrained_trades, _ = run_path(enforce_limits=False)
    risk = RiskManager(risk_params)
    history = trades.to_dict('records') if not trades.empty else []
    metrics = generate_mt5_report(
        history, guard.initial_balance, trades.attrs['equity_curve'],
        guard.ftmo_rules.get('data_timezone', 'UTC'), guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
    )
    unconstrained_metrics = generate_mt5_report(
        unconstrained_trades.to_dict('records') if not unconstrained_trades.empty else [],
        guard.initial_balance, unconstrained_trades.attrs['equity_curve'],
        guard.ftmo_rules.get('data_timezone', 'UTC'), guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
    )
    rolling_results = pd.DataFrame()
    rolling_metrics = {'status': 'disabled'}
    rolling = config['rolling']
    if rolling['enabled']:
        rolling_results = run_rolling_window_backtest(
            df=raw_df, strategy_factory=lambda: strategy_class(dict(params)),
            ftmo_rules_path=ftmo_rules, risk_params_path=risk_params,
            window_days=rolling['window_days'], step_days=rolling['step_days'], warmup_bars=rolling['warmup_bars'],
        )
        rolling_metrics = summarize(rolling_results)
    mc_metrics = {'status': 'disabled'}
    if config['monte_carlo']['enabled'] and history:
        mc_metrics = MonteCarloFTMO(BOT_ROOT / 'configs' / 'ftmo_rules.yaml').run_simulation(
            trades[['exit_time', 'pnl_pct']], n_sims=config['monte_carlo']['n_sims']
        )
        mc_metrics['source_scope'] = 'ftmo_constrained_path'
    walk_forward_results = pd.DataFrame()
    walk_forward_metrics = {'status': 'disabled'}
    walk_forward = config['walk_forward']
    if walk_forward['enabled']:
        walk_forward_results = run_walk_forward_backtest(
            raw_df, strategy_class, params, ftmo_rules, risk_params,
            train_days=walk_forward['train_days'], test_days=walk_forward['test_days'],
            step_days=walk_forward['step_days'], warmup_bars=walk_forward['warmup_bars'],
            parameter_grid=(walk_forward.get('parameter_grids') or {}).get(strategy_name, {}),
        )
        walk_forward_metrics = summarize_walk_forward(walk_forward_results)
    return trades, unconstrained_trades, {
        **metrics, 'initial_balance': guard.initial_balance,
        'simulation_scope': 'ftmo_constrained_until_hard_breach',
        'first_fail_time': str(trades.attrs['first_fail_time']) if trades.attrs.get('first_fail_time') is not None else None,
        'first_fail_reason': trades.attrs.get('first_fail_reason'),
        'post_failure_trades': 0,
        'monte_carlo': mc_metrics, 'rolling_window': rolling_metrics,
        'walk_forward': walk_forward_metrics,
        'unconstrained_path': unconstrained_metrics,
    }, rolling_results, walk_forward_results, strategy, guard, risk


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
        registry_path = Path(config['instrument_registry'])
        if not registry_path.is_absolute():
            registry_path = BOT_ROOT / registry_path
        instrument_registry = load_yaml(registry_path)
        base_risk = load_yaml(BOT_ROOT / 'configs' / 'risk_params.yaml')
        ftmo_rules = load_yaml(BOT_ROOT / 'configs' / 'ftmo_rules.yaml')
        pairs = select_pairs(store, config)
        strategies = select_strategies(config)
        admission_results = []
        jobs = []
        for name, klass in strategies:
            for symbol, timeframe in pairs:
                try:
                    instrument = resolve_instrument(instrument_registry, symbol)
                    allowed, reason = strategy_supports(klass, instrument, timeframe)
                    if not allowed:
                        raise ValueError(reason)
                    jobs.append((name, klass, symbol, timeframe, instrument))
                except ValueError as error:
                    admission_results.append({
                        'job_id': f'{safe_id(name)}__{safe_id(symbol)}__{safe_id(timeframe)}',
                        'strategy': name, 'symbol': symbol, 'timeframe': timeframe,
                        'status': 'skipped', 'reason': str(error),
                    })
        if config['max_jobs'] is not None:
            jobs = jobs[:config['max_jobs']]
        if not jobs and not admission_results:
            raise RuntimeError('No research jobs selected. Sync MT5 data first or relax research.yaml filters.')
        manifest = {
            'schema_version': 1, 'run_id': run_id, 'created_at_utc': iso_utc(created),
            'git_commit': commit, 'configuration': config,
            'selected_jobs': [{'strategy': n, 'symbol': s, 'timeframe': t} for n, _, s, t, _ in jobs],
            'admission_skips': admission_results,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        summary = list(admission_results)
        print(f'[*] Research {run_id}: {len(strategies)} strategies × {len(pairs)} lake pairs = {len(jobs)} jobs')
        if admission_results:
            print(f'[*] Skipped {len(admission_results)} incompatible/unprofiled jobs before execution.')
        for index, (name, klass, symbol, timeframe, instrument) in enumerate(jobs, start=1):
            job_id = f'{safe_id(name)}__{safe_id(symbol)}__{safe_id(timeframe)}'
            print(f'[{index}/{len(jobs)}] {name} · {symbol} {timeframe}', end=' ... ', flush=True)
            snapshot = None
            try:
                snapshot = store.data_snapshot(symbol, timeframe, start=config['start_utc'], end=config['end_utc'])
                catalog.start_job(run_id, job_id, name, symbol, timeframe, snapshot)
                raw_df = store.read_bars(symbol, timeframe, start=config['start_utc'], end=config['end_utc'])
                quality = validate_bars(raw_df, timeframe, config['data_quality'])
                if quality['status'] == 'failed':
                    raise ValueError('data-quality gate failed: ' + '; '.join(quality['failures']))
                params = dict(load_strategy_params(name))
                params.update({'symbol': symbol, 'timeframe': timeframe})
                risk_params = resolved_risk_params(base_risk, instrument)
                trades, unconstrained_trades, metrics, rolling_results, walk_forward_results, strategy, guard, risk = execute_job(
                    name, klass, raw_df, params, symbol, timeframe, config, ftmo_rules, risk_params
                )
                report_path = save_report_and_trades(
                    name, job_id, trades.to_dict('records'), metrics, trades,
                    rolling_results=rolling_results, walk_forward_results=walk_forward_results, reports_root=run_root,
                    unconstrained_df_trades=unconstrained_trades,
                    metadata={
                        'symbol': symbol, 'timeframe': timeframe,
                        'currency': guard.ftmo_rules.get('currency', 'USD'), 'bars': len(raw_df),
                        'period_start': str(raw_df.time.min()), 'period_end': str(raw_df.time.max()),
                        'source_timezone': guard.ftmo_rules.get('data_timezone', 'UTC'),
                        'report_timezone': guard.ftmo_rules.get('daily_reset_timezone', 'Europe/Prague'),
                        'git_commit': commit, 'data_fingerprint': snapshot['fingerprint'],
                        'asset_class': instrument['asset_class'], 'data_quality_status': quality['status'],
                    },
                    config={'strategy': strategy.params, 'ftmo_rules': guard.ftmo_rules,
                            'risk': risk.risk_params, 'research': config,
                            'instrument': instrument, 'data_snapshot': snapshot,
                            'data_quality': quality},
                )
                catalog.complete_job(run_id, job_id, report_path, metrics)
                summary.append({'job_id': job_id, 'strategy': name, 'symbol': symbol, 'timeframe': timeframe,
                                'status': 'completed', 'data_fingerprint': snapshot['fingerprint'],
                                'net_profit': metrics['net_profit'], 'total_trades': metrics['total_trades'],
                                'data_quality_status': quality['status'],
                                'walk_forward_oos_net_profit': metrics['walk_forward'].get('oos_net_profit'),
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
        failed = int((summary_frame.status == 'failed').sum())
        skipped = int((summary_frame.status == 'skipped').sum())
        result = {'run_id': run_id, 'jobs': len(summary), 'completed': completed, 'failed': failed, 'skipped': skipped,
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
