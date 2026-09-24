"""Synchronise MT5 Market Watch history into a resumable local research lake.

Run: ``python ftmo_bot/tools/download_mt5_data.py``

The default configuration downloads every standard MT5 timeframe for every
symbol currently visible in Market Watch, starting from 2010.  It is designed
to be stopped and run again: existing monthly partitions are merged and only a
small overlap before the last stored candle is requested.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import uuid

import pandas as pd
import yaml

# Direct execution (`python ftmo_bot/tools/download_mt5_data.py`) puts tools/
# on sys.path, while package imports need the ftmo_bot/ directory.
BOT_ROOT = Path(__file__).resolve().parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from tools.market_data_store import MarketDataStore, iso_utc

try:  # Lets users inspect --help/config without the Windows-only package installed.
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - exercised only on machines without MT5
    mt5 = None


TIMEFRAMES = {
    'M1': 31, 'M2': 62, 'M3': 93, 'M4': 124, 'M5': 155, 'M6': 186,
    'M10': 310, 'M12': 365, 'M15': 465, 'M20': 620, 'M30': 930,
    'H1': 1860, 'H2': 3650, 'H3': 3650, 'H4': 3650, 'H6': 3650,
    'H8': 3650, 'H12': 3650, 'D1': 3650, 'W1': 3650, 'MN1': 3650,
}


def project_root() -> Path:
    return BOT_ROOT


def parse_utc(value: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize('UTC')
    return timestamp.tz_convert('UTC').to_pydatetime()


def load_config(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ValueError(f'MT5 sync config must be a YAML mapping: {path}')
    config.setdefault('symbol_universe', 'market_watch')
    config.setdefault('timeframes', 'all')
    config.setdefault('history_start_utc', '2010-01-01T00:00:00Z')
    config.setdefault('overlap_days', 3)
    config.setdefault('storage_root', 'data/market/mt5')
    if config['symbol_universe'] != 'market_watch':
        raise ValueError("This batch runner currently supports symbol_universe: 'market_watch' only")
    if not isinstance(config['overlap_days'], int) or config['overlap_days'] < 0:
        raise ValueError('overlap_days must be a non-negative integer')
    parse_utc(config['history_start_utc'])
    return config


def requested_timeframes(config: dict) -> list[str]:
    value = config['timeframes']
    if value == 'all':
        names = list(TIMEFRAMES)
    elif isinstance(value, list) and all(isinstance(name, str) for name in value):
        names = value
    else:
        raise ValueError("timeframes must be 'all' or a YAML list such as [M5, H1, D1]")
    unknown = sorted(set(names).difference(TIMEFRAMES))
    if unknown:
        raise ValueError('Unsupported MT5 timeframe(s): ' + ', '.join(unknown))
    return names


def market_watch_symbols() -> list[str]:
    infos = mt5.symbols_get()
    if infos is None:
        raise RuntimeError(f'MT5 symbols_get failed: {mt5.last_error()}')
    return sorted(info.name for info in infos if getattr(info, 'visible', False))


def timeframe_constant(name: str):
    value = getattr(mt5, 'TIMEFRAME_' + name, None)
    if value is None:
        raise RuntimeError(f'Installed MetaTrader5 package does not expose TIMEFRAME_{name}')
    return value


def sync_pair(store: MarketDataStore, symbol: str, timeframe: str, *, start: datetime,
              end: datetime, overlap_days: int) -> dict:
    """Sync one symbol/timeframe using bounded MT5 requests and monthly merging."""
    latest = store.latest_timestamp(symbol, timeframe)
    if latest is not None:
        latest_dt = latest.to_pydatetime()
        if latest_dt.tzinfo is None:
            latest_dt = latest_dt.replace(tzinfo=timezone.utc)
        start = max(start, latest_dt.astimezone(timezone.utc) - timedelta(days=overlap_days))
    if start >= end:
        return {'status': 'up_to_date', 'bars_received': 0, 'requests': 0}
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f'MT5 cannot select {symbol}: {mt5.last_error()}')

    cursor = start
    chunk_days = TIMEFRAMES[timeframe]
    received = requests = 0
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        rates = mt5.copy_rates_range(symbol, timeframe_constant(timeframe), cursor, chunk_end)
        requests += 1
        if rates is not None and len(rates):
            received += store.write_bars(symbol, timeframe, pd.DataFrame(rates))
        cursor = chunk_end + timedelta(seconds=1)
    if received:
        return {'status': 'ok', 'bars_received': received, 'requests': requests}
    # An already catalogued pair can legitimately have no new bars in the
    # overlap. Keep its successful coverage so the next run stays incremental.
    if latest is not None:
        return {'status': 'up_to_date', 'bars_received': 0, 'requests': requests}
    store.mark_state(symbol, timeframe, 'no_data')
    return {'status': 'no_data', 'bars_received': 0, 'requests': requests}


def run_sync(config: dict) -> dict:
    if mt5 is None:
        raise RuntimeError(
            'MetaTrader5 is not installed in this Python interpreter. '
            'Run: python -m pip install MetaTrader5 pyarrow'
        )
    # Fail before contacting MT5 or creating hundreds of failed pair jobs.
    MarketDataStore._require_pyarrow()
    root = project_root()
    storage_root = Path(config['storage_root'])
    if not storage_root.is_absolute():
        storage_root = root / storage_root
    store = MarketDataStore(storage_root)
    run_id = 'mt5_sync_' + uuid.uuid4().hex[:12]
    store.start_run(run_id, config)
    summary = {'run_id': run_id, 'symbols': 0, 'pairs': 0, 'ok': 0,
               'up_to_date': 0, 'no_data': 0, 'failed': 0, 'bars_received': 0}
    try:
        if not mt5.initialize():
            raise RuntimeError(f'MT5 initialize failed: {mt5.last_error()}')
        symbols = market_watch_symbols()
        timeframes = requested_timeframes(config)
        if not symbols:
            raise RuntimeError('Market Watch has no visible symbols. Add symbols in MT5 and run again.')
        summary['symbols'] = len(symbols)
        summary['pairs'] = len(symbols) * len(timeframes)
        start, end = parse_utc(config['history_start_utc']), datetime.now(timezone.utc)
        print(f'[*] MT5 batch sync {run_id}: {len(symbols)} Market Watch symbols × {len(timeframes)} timeframes')
        print(f'[*] Requested history: {iso_utc(start)} → {iso_utc(end)}')
        for symbol_index, symbol in enumerate(symbols, start=1):
            for timeframe_index, timeframe in enumerate(timeframes, start=1):
                print(f'[{symbol_index}/{len(symbols)} · {timeframe_index}/{len(timeframes)}] {symbol} {timeframe}', end=' ... ', flush=True)
                try:
                    result = sync_pair(store, symbol, timeframe, start=start, end=end,
                                       overlap_days=config['overlap_days'])
                    summary[result['status']] += 1
                    summary['bars_received'] += result['bars_received']
                    print(f"{result['status']} ({result['bars_received']:,} bars, {result['requests']} requests)")
                except Exception as error:
                    summary['failed'] += 1
                    store.mark_state(symbol, timeframe, 'failed', error=str(error))
                    store.record_error(run_id, symbol, timeframe, error)
                    print(f'FAILED: {type(error).__name__}: {error}')
        status = 'completed' if summary['failed'] == 0 else 'completed_with_errors'
        store.finish_run(run_id, status, summary)
        print(f'\n[*] Sync {status}: {summary}')
        return summary
    except Exception as error:
        store.finish_run(run_id, 'failed', {**summary, 'fatal_error': str(error)})
        raise
    finally:
        if mt5 is not None:
            mt5.shutdown()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Sync MT5 Market Watch data into partitioned Parquet storage.')
    parser.add_argument('--config', type=Path, default=project_root() / 'configs' / 'mt5_sync.yaml',
                        help='Path to YAML sync configuration')
    args = parser.parse_args()
    run_sync(load_config(args.config))


if __name__ == '__main__':
    main()
