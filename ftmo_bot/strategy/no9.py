"""No9 — inside-bar setup filtered by prior candle direction."""
from indicators.price_indicators import add_atr
from strategy.candidate_base import CandidateBase


class No9Strategy(CandidateBase):
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
            return self.order('BUY' if row.n9_prev_close >= row.n9_prev_open else 'SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
