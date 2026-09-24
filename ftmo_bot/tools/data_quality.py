"""Deterministic quality gates for OHLC research inputs."""
from __future__ import annotations

import pandas as pd


TIMEFRAME_SECONDS = {
    'M1': 60, 'M2': 120, 'M3': 180, 'M4': 240, 'M5': 300, 'M6': 360,
    'M10': 600, 'M12': 720, 'M15': 900, 'M20': 1200, 'M30': 1800,
    'H1': 3600, 'H2': 7200, 'H3': 10800, 'H4': 14400, 'H6': 21600,
    'H8': 28800, 'H12': 43200, 'D1': 86400, 'W1': 604800, 'MN1': None,
}


def validate_bars(df: pd.DataFrame, timeframe: str, config: dict | None = None) -> dict:
    """Return a machine-readable quality report without inventing market sessions.

    Weekend/holiday gaps are *reported*, not rejected: only structural OHLC
    errors, duplicates, nulls and insufficient coverage are hard failures.
    """
    rules = {
        'min_bars': 100, 'min_bars_by_timeframe': {}, 'fail_on_duplicates': True, 'fail_on_null_ohlc': True,
        'fail_on_impossible_ohlc': True, 'gap_warning_multiplier': 3,
    }
    rules.update(config or {})
    required = ['time', 'open', 'high', 'low', 'close']
    missing_columns = [column for column in required if column not in df.columns]
    report = {'status': 'passed', 'timeframe': timeframe.upper(), 'rows': int(len(df)),
              'failures': [], 'warnings': [], 'metrics': {}}
    if missing_columns:
        report['failures'].append('missing columns: ' + ', '.join(missing_columns))
        report['status'] = 'failed'
        return report
    frame = df[required].copy()
    frame['time'] = pd.to_datetime(frame['time'], errors='coerce', utc=True)
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    null_rows = int(frame.isna().any(axis=1).sum())
    duplicates = int(frame['time'].duplicated().sum())
    invalid = (
        (frame['high'] < frame[['open', 'close', 'low']].max(axis=1)) |
        (frame['low'] > frame[['open', 'close', 'high']].min(axis=1)) |
        (frame[['open', 'high', 'low', 'close']] <= 0).any(axis=1)
    )
    invalid_ohlc = int(invalid.fillna(False).sum())
    report['metrics'].update(null_rows=null_rows, duplicate_timestamps=duplicates,
                             impossible_ohlc_rows=invalid_ohlc)
    minimum = (rules.get('min_bars_by_timeframe') or {}).get(timeframe.upper(), rules['min_bars'])
    if len(frame) < minimum:
        report['failures'].append(f"only {len(frame)} bars; minimum is {minimum}")
    if null_rows and rules['fail_on_null_ohlc']:
        report['failures'].append(f'{null_rows} rows contain null/non-numeric OHLC values')
    if duplicates and rules['fail_on_duplicates']:
        report['failures'].append(f'{duplicates} duplicate timestamps')
    if invalid_ohlc and rules['fail_on_impossible_ohlc']:
        report['failures'].append(f'{invalid_ohlc} impossible/non-positive OHLC rows')
    clean_times = frame['time'].dropna().sort_values()
    seconds = TIMEFRAME_SECONDS.get(timeframe.upper())
    if seconds and len(clean_times) > 1:
        gaps = clean_times.diff().dt.total_seconds().dropna()
        large_gaps = gaps[gaps > seconds * rules['gap_warning_multiplier']]
        report['metrics'].update(
            expected_bar_seconds=seconds, largest_gap_seconds=float(gaps.max()),
            large_gap_count=int(len(large_gaps)),
        )
        if len(large_gaps):
            report['warnings'].append(
                f'{len(large_gaps)} gaps exceed {rules["gap_warning_multiplier"]} expected bars; '
                'review sessions/weekends before interpreting the result.'
            )
    if report['failures']:
        report['status'] = 'failed'
    elif report['warnings']:
        report['status'] = 'passed_with_warnings'
    return report
