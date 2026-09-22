"""No6 — EMA pullback in an established trend."""
from indicators.momentum_indicators import add_ema
from indicators.price_indicators import add_atr
from strategy.candidate_base import CandidateBase


class No6Strategy(CandidateBase):
    key, defaults = 'no6', {'fast': 20, 'slow': 80, 'stop_atr': 1.4, 'target_atr': 2.2}

    def prepare_data(self, df): return add_atr(add_ema(add_ema(df, self.p['fast'], 'n6_fast'), self.p['slow'], 'n6_slow'))

    def generate_signal(self, row):
        if not self.valid(row.n6_fast, row.n6_slow): return None
        if row.n6_fast > row.n6_slow and row.low <= row.n6_fast <= row.close: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.n6_fast < row.n6_slow and row.high >= row.n6_fast >= row.close: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
