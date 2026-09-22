"""No7 — Bollinger squeeze breakout."""
from indicators.price_indicators import add_atr, add_bollinger
from strategy.candidate_base import CandidateBase


class No7Strategy(CandidateBase):
    key, defaults = 'no7', {'period': 20, 'width_pct': 0.007, 'stop_atr': 1.3, 'target_atr': 2.8}

    def prepare_data(self, df): return add_atr(add_bollinger(df, self.p['period'], 2.0, 'n7'))

    def generate_signal(self, row):
        if not self.valid(row.n7_upper, row.n7_lower, row.n7_mid): return None
        width = (row.n7_upper - row.n7_lower) / row.n7_mid
        if width > self.p['width_pct']: return None
        if row.close > row.n7_upper: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close < row.n7_lower: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
