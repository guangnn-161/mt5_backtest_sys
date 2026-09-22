"""Ten deliberately distinct, causal strategy candidates for research.

Each class emits a signal only from the current/past bars.  The backtest engine
then fills that signal at the next bar open, preventing same-bar look-ahead.
These are hypotheses to test, not claims of profitable alpha.
"""
from __future__ import annotations

import math

import pandas as pd

from indicators.momentum_indicators import add_ema, add_macd
from indicators.oscillators import add_rsi
from indicators.price_indicators import add_atr, add_bollinger, add_donchian, add_stochastic
from strategy.base import BaseStrategy


ALL_TIMEFRAMES = ('M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M10', 'M12', 'M15', 'M20', 'M30',
                  'H1', 'H2', 'H3', 'H4', 'H6', 'H8', 'H12', 'D1', 'W1', 'MN1')


class CandidateBase(BaseStrategy):
    supported_asset_classes = ('metal',)
    supported_timeframes = ALL_TIMEFRAMES
    key = ''

    def __init__(self, params: dict | None = None):
        raw = dict(params or {})
        values = dict(self.defaults)
        values.update(raw.get(self.key, {}))
        raw[self.key] = values
        super().__init__(raw)
        self.p = values

    def valid(self, *values) -> bool:
        return all(value is not None and not pd.isna(value) and math.isfinite(float(value)) for value in values)

    def order(self, side: str, row, stop_atr: float, target_atr: float):
        atr = getattr(row, 'atr', None)
        if not self.valid(atr, row.close) or atr <= 0:
            return None
        entry = float(row.close)
        stop = float(atr) * stop_atr
        target = float(atr) * target_atr
        if side == 'BUY':
            return {'type': side, 'entry': entry, 'sl': entry - stop, 'tp': entry + target}
        return {'type': side, 'entry': entry, 'sl': entry + stop, 'tp': entry - target}


class No1Strategy(CandidateBase):
    """Fast/slow EMA trend continuation."""
    key, defaults = 'no1', {'fast': 20, 'slow': 50, 'stop_atr': 1.5, 'target_atr': 2.5}

    def prepare_data(self, df):
        return add_atr(add_ema(add_ema(df, self.p['fast'], 'n1_fast'), self.p['slow'], 'n1_slow'))

    def generate_signal(self, row):
        if not self.valid(row.n1_fast, row.n1_slow): return None
        return self.order('BUY' if row.n1_fast > row.n1_slow else 'SELL', row, self.p['stop_atr'], self.p['target_atr'])


class No2Strategy(CandidateBase):
    """RSI + Bollinger mean reversion."""
    key, defaults = 'no2', {'rsi_period': 14, 'oversold': 30, 'overbought': 70, 'stop_atr': 1.2, 'target_atr': 1.6}

    def prepare_data(self, df):
        return add_atr(add_bollinger(add_rsi(df, self.p['rsi_period'], 'n2_rsi'), 20, 2.0, 'n2'))

    def generate_signal(self, row):
        if not self.valid(row.n2_rsi, row.n2_lower, row.n2_upper): return None
        if row.close < row.n2_lower and row.n2_rsi < self.p['oversold']:
            return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close > row.n2_upper and row.n2_rsi > self.p['overbought']:
            return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No3Strategy(CandidateBase):
    """Donchian channel breakout."""
    key, defaults = 'no3', {'period': 20, 'stop_atr': 1.8, 'target_atr': 3.0}

    def prepare_data(self, df): return add_atr(add_donchian(df, self.p['period'], 'n3'))

    def generate_signal(self, row):
        if not self.valid(row.n3_high, row.n3_low): return None
        if row.close > row.n3_high: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close < row.n3_low: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No4Strategy(CandidateBase):
    """MACD histogram momentum with EMA trend filter."""
    key, defaults = 'no4', {'fast': 12, 'slow': 26, 'signal': 9, 'trend': 100, 'stop_atr': 1.5, 'target_atr': 2.4}

    def prepare_data(self, df): return add_atr(add_ema(add_macd(df, self.p['fast'], self.p['slow'], self.p['signal']), self.p['trend'], 'n4_trend'))

    def generate_signal(self, row):
        if not self.valid(row.macd_hist, row.n4_trend): return None
        if row.close > row.n4_trend and row.macd_hist > 0: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close < row.n4_trend and row.macd_hist < 0: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No5Strategy(CandidateBase):
    """Stochastic extreme reversal."""
    key, defaults = 'no5', {'period': 14, 'oversold': 20, 'overbought': 80, 'stop_atr': 1.1, 'target_atr': 1.7}

    def prepare_data(self, df): return add_atr(add_stochastic(df, self.p['period'], 3, 'n5'))

    def generate_signal(self, row):
        if not self.valid(row.n5_k, row.n5_d): return None
        if row.n5_k < self.p['oversold'] and row.n5_k > row.n5_d: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.n5_k > self.p['overbought'] and row.n5_k < row.n5_d: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No6Strategy(CandidateBase):
    """EMA pullback in an established trend."""
    key, defaults = 'no6', {'fast': 20, 'slow': 80, 'stop_atr': 1.4, 'target_atr': 2.2}

    def prepare_data(self, df): return add_atr(add_ema(add_ema(df, self.p['fast'], 'n6_fast'), self.p['slow'], 'n6_slow'))

    def generate_signal(self, row):
        if not self.valid(row.n6_fast, row.n6_slow): return None
        if row.n6_fast > row.n6_slow and row.low <= row.n6_fast <= row.close: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.n6_fast < row.n6_slow and row.high >= row.n6_fast >= row.close: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No7Strategy(CandidateBase):
    """Bollinger squeeze breakout after low realised volatility."""
    key, defaults = 'no7', {'period': 20, 'width_pct': 0.007, 'stop_atr': 1.3, 'target_atr': 2.8}

    def prepare_data(self, df): return add_atr(add_bollinger(df, self.p['period'], 2.0, 'n7'))

    def generate_signal(self, row):
        if not self.valid(row.n7_upper, row.n7_lower, row.n7_mid): return None
        width = (row.n7_upper - row.n7_lower) / row.n7_mid
        if width > self.p['width_pct']: return None
        if row.close > row.n7_upper: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close < row.n7_lower: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No8Strategy(CandidateBase):
    """Price z-score reversion to a moving average."""
    key, defaults = 'no8', {'period': 30, 'entry_z': 2.0, 'stop_atr': 1.4, 'target_atr': 1.8}

    def prepare_data(self, df):
        mean = df.close.rolling(self.p['period'], min_periods=self.p['period']).mean()
        std = df.close.rolling(self.p['period'], min_periods=self.p['period']).std(ddof=0)
        df['n8_z'] = (df.close - mean) / std.replace(0, float('nan'))
        return add_atr(df)

    def generate_signal(self, row):
        if not self.valid(row.n8_z): return None
        if row.n8_z <= -self.p['entry_z']: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.n8_z >= self.p['entry_z']: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No9Strategy(CandidateBase):
    """Inside-bar breakout filtered by the prior candle direction."""
    key, defaults = 'no9', {'stop_atr': 1.2, 'target_atr': 2.0}

    def prepare_data(self, df):
        df['n9_prev_high'] = df.high.shift(1)
        df['n9_prev_low'] = df.low.shift(1)
        df['n9_prev_close'] = df.close.shift(1)
        df['n9_prev_open'] = df.open.shift(1)
        return add_atr(df)

    def generate_signal(self, row):
        if not self.valid(row.n9_prev_high, row.n9_prev_low, row.n9_prev_close, row.n9_prev_open): return None
        if row.high <= row.n9_prev_high and row.low >= row.n9_prev_low:
            if row.n9_prev_close >= row.n9_prev_open: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
            return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None


class No10Strategy(CandidateBase):
    """Large-range close-location momentum."""
    key, defaults = 'no10', {'range_atr': 1.5, 'close_location': 0.75, 'stop_atr': 1.5, 'target_atr': 2.6}

    def prepare_data(self, df): return add_atr(df)

    def generate_signal(self, row):
        if not self.valid(row.atr) or row.atr <= 0: return None
        candle_range = row.high - row.low
        if candle_range < self.p['range_atr'] * row.atr: return None
        close_location = (row.close - row.low) / candle_range if candle_range else .5
        if close_location >= self.p['close_location']: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if close_location <= 1 - self.p['close_location']: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
