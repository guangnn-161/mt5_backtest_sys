"""No5 — stochastic extreme reversal."""
from indicators.price_indicators import add_atr, add_stochastic
from strategy.candidate_base import CandidateBase


class No5Strategy(CandidateBase):
    key, defaults = 'no5', {'period': 14, 'oversold': 20, 'overbought': 80, 'stop_atr': 1.1, 'target_atr': 1.7}

    def prepare_data(self, df): return add_atr(add_stochastic(df, self.p['period'], 3, 'n5'))

    def generate_signal(self, row):
        if not self.valid(row.n5_k, row.n5_d): return None
        if row.n5_k < self.p['oversold'] and row.n5_k > row.n5_d: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.n5_k > self.p['overbought'] and row.n5_k < row.n5_d: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
