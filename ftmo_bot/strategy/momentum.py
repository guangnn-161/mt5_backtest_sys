# File: strategy/momentum.py
from strategy.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    def __init__(self, params: dict):
        super().__init__(params)
        self.symbol = params.get('symbol', 'XAUUSDm')

    def generate_signal(self, row):
        # TODO: Viết logic Momentum thực sự ở đây
        # Ví dụ nháp: Nếu nến đóng cửa tăng mạnh hơn 20 giá, mua đuổi
        body = row.close - row.open
        if body > 2.0:  # Vàng tăng 2 giá trong 5 phút
            return {
                'type': 'BUY',
                'entry': row.close,
                'sl': row.close - 5.0,  # SL 50 pips
                'tp': row.close + 10.0  # TP 100 pips
            }
        elif body < -2.0:
            return {
                'type': 'SELL',
                'entry': row.close,
                'sl': row.close + 5.0,
                'tp': row.close - 10.0
            }
        return None
