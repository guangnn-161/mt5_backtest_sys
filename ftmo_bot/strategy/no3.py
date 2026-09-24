"""No3 — Donchian-channel breakout."""
from indicators.price_indicators import add_atr, add_donchian
from strategy.candidate_base import CandidateBase


class No3Strategy(CandidateBase):
    key, defaults = 'no3', {'period': 20, 'stop_atr': 1.8, 'target_atr': 3.0}

    def prepare_data(self, df): return add_atr(add_donchian(df, self.p['period'], 'n3'))

    def generate_signal(self, row):
        if not self.valid(row.n3_high, row.n3_low): return None
        if row.close > row.n3_high: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close < row.n3_low: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
