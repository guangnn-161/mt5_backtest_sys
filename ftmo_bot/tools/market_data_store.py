"""Partitioned, incremental storage for MT5 OHLCV history.

The files are intentionally independent of an individual backtest run:
``data/market/mt5/bars/<symbol>/<timeframe>/<year>/<month>.parquet``.
SQLite is the authoritative catalog of coverage and sync state; JSON manifests
are human-readable snapshots of each batch run.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

import pandas as pd


BAR_COLUMNS = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize('UTC')
    return timestamp.tz_convert('UTC').isoformat()


def storage_key(symbol: str) -> str:
    """Return a portable directory name while retaining the original in SQLite."""
    key = re.sub(r'[^A-Za-z0-9._-]+', '_', symbol).strip('._') or 'symbol'
    if key != symbol:
        key += '_' + hashlib.sha256(symbol.encode('utf-8')).hexdigest()[:10]
    return key


class MarketDataStore:
    """Write monthly Parquet partitions and maintain a transactional catalog."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.bars_root = self.root / 'bars'
        self.manifests_root = self.root / 'manifests'
        self.bars_root.mkdir(parents=True, exist_ok=True)
        self.manifests_root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.root / 'catalog.sqlite')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA foreign_keys=ON')
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS sync_runs (
                run_id TEXT PRIMARY KEY,
                started_at_utc TEXT NOT NULL,
                completed_at_utc TEXT,
                status TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                summary_json TEXT
            );
            CREATE TABLE IF NOT EXISTS sync_state (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                storage_key TEXT NOT NULL,
                first_utc TEXT,
                last_utc TEXT,
                rows INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                last_success_utc TEXT,
                last_error TEXT,
                PRIMARY KEY (symbol, timeframe)
            );
            CREATE TABLE IF NOT EXISTS partitions (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                rows INTEGER NOT NULL,
                first_utc TEXT NOT NULL,
                last_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (symbol, timeframe, year, month)
            );
            CREATE TABLE IF NOT EXISTS sync_errors (
                run_id TEXT NOT NULL,
                symbol TEXT,
                timeframe TEXT,
                occurred_at_utc TEXT NOT NULL,
                error_type TEXT NOT NULL,
                message TEXT NOT NULL
            );
        ''')
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def start_run(self, run_id: str, configuration: dict[str, Any]) -> None:
        self.connection.execute(
            'INSERT INTO sync_runs(run_id, started_at_utc, status, configuration_json) VALUES (?, ?, ?, ?)',
            (run_id, iso_utc(utc_now()), 'running', json.dumps(configuration, ensure_ascii=False, sort_keys=True)),
        )
        self.connection.commit()

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        self.connection.execute(
            'UPDATE sync_runs SET completed_at_utc=?, status=?, summary_json=? WHERE run_id=?',
            (iso_utc(utc_now()), status, json.dumps(summary, ensure_ascii=False, sort_keys=True), run_id),
        )
        self.connection.commit()
        manifest = {
            'run_id': run_id,
            'completed_at_utc': iso_utc(utc_now()),
            'status': status,
            'summary': summary,
        }
        (self.manifests_root / f'{run_id}.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
        )

    def latest_timestamp(self, symbol: str, timeframe: str):
        row = self.connection.execute(
            'SELECT last_utc FROM sync_state WHERE symbol=? AND timeframe=? AND status="ok"',
            (symbol, timeframe),
        ).fetchone()
        return pd.Timestamp(row['last_utc']) if row and row['last_utc'] else None

    def mark_state(self, symbol: str, timeframe: str, status: str, *, error: str | None = None) -> None:
        key = storage_key(symbol)
        self.connection.execute('''
            INSERT INTO sync_state(symbol, timeframe, storage_key, status, last_error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                storage_key=excluded.storage_key, status=excluded.status, last_error=excluded.last_error
        ''', (symbol, timeframe, key, status, error))
        self.connection.commit()

    def record_error(self, run_id: str, symbol: str, timeframe: str, error: Exception) -> None:
        self.connection.execute(
            'INSERT INTO sync_errors VALUES (?, ?, ?, ?, ?, ?)',
            (run_id, symbol, timeframe, iso_utc(utc_now()), type(error).__name__, str(error)),
        )
        self.connection.commit()

    @staticmethod
    def _normalise_bars(frame: pd.DataFrame) -> pd.DataFrame:
        missing = set(BAR_COLUMNS).difference(frame.columns)
        if missing:
            raise ValueError('MT5 response is missing columns: ' + ', '.join(sorted(missing)))
        clean = frame[BAR_COLUMNS].copy()
        clean['time'] = pd.to_datetime(clean['time'], unit='s', utc=True)
        for column in BAR_COLUMNS[1:]:
            clean[column] = pd.to_numeric(clean[column], errors='coerce')
        clean = clean.dropna(subset=['time', 'open', 'high', 'low', 'close'])
        clean = clean.drop_duplicates('time', keep='last').sort_values('time').reset_index(drop=True)
        return clean

    @staticmethod
    def _require_pyarrow() -> None:
        try:
            import pyarrow  # noqa: F401
        except ImportError as error:
            raise RuntimeError(
                'Parquet storage requires pyarrow. Install it with: python -m pip install pyarrow'
            ) from error

    def _partition_path(self, symbol: str, timeframe: str, year: int, month: int) -> Path:
        return self.bars_root / storage_key(symbol) / timeframe / f'{year:04d}' / f'{month:02d}.parquet'

    def read_bars(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Load one complete symbol/timeframe history from its catalogued partitions."""
        rows = self.connection.execute('''
            SELECT relative_path FROM partitions
            WHERE symbol=? AND timeframe=? ORDER BY year, month
        ''', (symbol, timeframe)).fetchall()
        if not rows:
            available = self.connection.execute('''
                SELECT symbol, timeframe FROM sync_state WHERE status='ok'
                ORDER BY symbol, timeframe LIMIT 12
            ''').fetchall()
            examples = ', '.join(f"{row['symbol']} {row['timeframe']}" for row in available)
            hint = f' Available examples: {examples}.' if examples else ''
            raise FileNotFoundError(
                f'No catalogued MT5 data for {symbol} {timeframe}.{hint} '
                'Run download_mt5_data.py first, or use the exact broker symbol in strategy_params.yaml.'
            )
        self._require_pyarrow()
        frames = []
        for row in rows:
            path = self.root / row['relative_path']
            if not path.exists():
                raise FileNotFoundError(f'Catalog points to a missing partition: {path}')
            frame = pd.read_parquet(path, engine='pyarrow')
            frame['time'] = pd.to_datetime(frame['time'], utc=True)
            frames.append(frame)
        bars = pd.concat(frames, ignore_index=True)
        return bars.drop_duplicates('time', keep='last').sort_values('time').reset_index(drop=True)

    def write_bars(self, symbol: str, timeframe: str, raw_frame: pd.DataFrame) -> int:
        """Merge a MT5 response into monthly partitions; return received rows."""
        self._require_pyarrow()
        bars = self._normalise_bars(raw_frame)
        if bars.empty:
            return 0
        bars['_year'] = bars.time.dt.year
        bars['_month'] = bars.time.dt.month
        for (year, month), incoming in bars.groupby(['_year', '_month'], sort=True):
            incoming = incoming.drop(columns=['_year', '_month'])
            path = self._partition_path(symbol, timeframe, int(year), int(month))
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                existing = pd.read_parquet(path, engine='pyarrow')
                existing['time'] = pd.to_datetime(existing['time'], utc=True)
                merged = pd.concat([existing, incoming], ignore_index=True)
                merged = merged.drop_duplicates('time', keep='last').sort_values('time').reset_index(drop=True)
            else:
                merged = incoming.reset_index(drop=True)
            temporary = path.with_suffix('.tmp.parquet')
            merged.to_parquet(temporary, engine='pyarrow', index=False)
            temporary.replace(path)
            relative = path.relative_to(self.root).as_posix()
            self.connection.execute('''
                INSERT INTO partitions(symbol, timeframe, year, month, relative_path, rows, first_utc, last_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, year, month) DO UPDATE SET
                    relative_path=excluded.relative_path, rows=excluded.rows,
                    first_utc=excluded.first_utc, last_utc=excluded.last_utc,
                    updated_at_utc=excluded.updated_at_utc
            ''', (symbol, timeframe, int(year), int(month), relative, len(merged),
                  iso_utc(merged.time.iloc[0]), iso_utc(merged.time.iloc[-1]), iso_utc(utc_now())))

        state = self.connection.execute('''
            SELECT MIN(first_utc) AS first_utc, MAX(last_utc) AS last_utc, SUM(rows) AS rows
            FROM partitions WHERE symbol=? AND timeframe=?
        ''', (symbol, timeframe)).fetchone()
        self.connection.execute('''
            INSERT INTO sync_state(symbol, timeframe, storage_key, first_utc, last_utc, rows, status, last_success_utc, last_error)
            VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, NULL)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                storage_key=excluded.storage_key, first_utc=excluded.first_utc,
                last_utc=excluded.last_utc, rows=excluded.rows, status='ok',
                last_success_utc=excluded.last_success_utc, last_error=NULL
        ''', (symbol, timeframe, storage_key(symbol), state['first_utc'], state['last_utc'],
              state['rows'], iso_utc(utc_now())))
        self.connection.commit()
        return len(bars)
