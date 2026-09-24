"""No2 — RSI and Bollinger mean reversion."""
from indicators.oscillators import add_rsi
from indicators.price_indicators import add_atr, add_bollinger
from strategy.candidate_base import CandidateBase


class No2Strategy(CandidateBase):
    key, defaults = 'no2', {'rsi_period': 14, 'oversold': 30, 'overbought': 70, 'stop_atr': 1.2, 'target_atr': 1.6}

    def prepare_data(self, df):
        return add_atr(add_bollinger(add_rsi(df, self.p['rsi_period'], 'n2_rsi'), 20, 2.0, 'n2'))

    def generate_signal(self, row):
        if not self.valid(row.n2_rsi, row.n2_lower, row.n2_upper): return None
        if row.close < row.n2_lower and row.n2_rsi < self.p['oversold']: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close > row.n2_upper and row.n2_rsi > self.p['overbought']: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
