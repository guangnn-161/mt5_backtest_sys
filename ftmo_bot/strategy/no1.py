"""No1 — fast/slow EMA trend continuation."""
from indicators.momentum_indicators import add_ema
from indicators.price_indicators import add_atr
from strategy.candidate_base import CandidateBase


class No1Strategy(CandidateBase):
    key, defaults = 'no1', {'fast': 20, 'slow': 50, 'stop_atr': 1.5, 'target_atr': 2.5}

    def prepare_data(self, df):
        return add_atr(add_ema(add_ema(df, self.p['fast'], 'n1_fast'), self.p['slow'], 'n1_slow'))

    def generate_signal(self, row):
        if not self.valid(row.n1_fast, row.n1_slow): return None
        return self.order('BUY' if row.n1_fast > row.n1_slow else 'SELL', row, self.p['stop_atr'], self.p['target_atr'])
