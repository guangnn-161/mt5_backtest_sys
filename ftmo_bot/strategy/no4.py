"""No4 — MACD momentum with EMA trend filter."""
from indicators.momentum_indicators import add_ema, add_macd
from indicators.price_indicators import add_atr
from strategy.candidate_base import CandidateBase


class No4Strategy(CandidateBase):
    key, defaults = 'no4', {'fast': 12, 'slow': 26, 'signal': 9, 'trend': 100, 'stop_atr': 1.5, 'target_atr': 2.4}

    def prepare_data(self, df): return add_atr(add_ema(add_macd(df, self.p['fast'], self.p['slow'], self.p['signal']), self.p['trend'], 'n4_trend'))

    def generate_signal(self, row):
        if not self.valid(row.macd_hist, row.n4_trend): return None
        if row.close > row.n4_trend and row.macd_hist > 0: return self.order('BUY', row, self.p['stop_atr'], self.p['target_atr'])
        if row.close < row.n4_trend and row.macd_hist < 0: return self.order('SELL', row, self.p['stop_atr'], self.p['target_atr'])
        return None
