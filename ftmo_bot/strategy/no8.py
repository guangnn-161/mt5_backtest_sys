"""No8 — price z-score mean reversion."""
from indicators.price_indicators import add_atr
from strategy.candidate_base import CandidateBase


class No8Strategy(CandidateBase):
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
