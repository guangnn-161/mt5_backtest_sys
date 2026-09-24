"""Causal, vectorised price indicators shared by research strategies."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_atr(df: pd.DataFrame, period: int = 14, column_name: str = 'atr') -> pd.DataFrame:
    previous_close = df['close'].shift(1)
    true_range = pd.concat([
        df['high'] - df['low'],
        (df['high'] - previous_close).abs(),
        (df['low'] - previous_close).abs(),
    ], axis=1).max(axis=1)
    df[column_name] = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return df


def add_bollinger(df: pd.DataFrame, period: int = 20, stdev: float = 2.0,
                  prefix: str = 'bb') -> pd.DataFrame:
    middle = df['close'].rolling(period, min_periods=period).mean()
    sigma = df['close'].rolling(period, min_periods=period).std(ddof=0)
    df[f'{prefix}_mid'] = middle
    df[f'{prefix}_upper'] = middle + stdev * sigma
    df[f'{prefix}_lower'] = middle - stdev * sigma
    return df


def add_donchian(df: pd.DataFrame, period: int = 20, prefix: str = 'dc') -> pd.DataFrame:
    # Shift one bar: a breakout is measured against information available before this bar.
    df[f'{prefix}_high'] = df['high'].rolling(period, min_periods=period).max().shift(1)
    df[f'{prefix}_low'] = df['low'].rolling(period, min_periods=period).min().shift(1)
    return df


def add_stochastic(df: pd.DataFrame, period: int = 14, smooth: int = 3,
                   prefix: str = 'stoch') -> pd.DataFrame:
    low = df['low'].rolling(period, min_periods=period).min()
    high = df['high'].rolling(period, min_periods=period).max()
    raw_k = 100 * (df['close'] - low) / (high - low).replace(0, np.nan)
    df[f'{prefix}_k'] = raw_k.rolling(smooth, min_periods=smooth).mean()
    df[f'{prefix}_d'] = df[f'{prefix}_k'].rolling(smooth, min_periods=smooth).mean()
    return df

