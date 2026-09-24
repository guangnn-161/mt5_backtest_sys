"""No10 — large-range close-location momentum."""
from indicators.price_indicators import add_atr
from strategy.candidate_base import CandidateBase


class No10Strategy(CandidateBase):
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
