# File: strategy/momentum.py
from strategy.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    def __init__(self, params: dict):
        super().__init__(params)
        self.symbol = params.get('symbol', 'XAUUSDm')
        momentum = params.get('momentum_params', {})
        self.body_threshold = float(momentum.get('body_threshold', 2.0))
        self.stop_distance = float(momentum.get('stop_distance', 5.0))
        self.target_distance = float(momentum.get('target_distance', 10.0))

    def generate_signal(self, row):
        # This remains an intentionally simple candle-impulse baseline. Parameters
        # are explicit so experiments are reproducible and no threshold is hidden.
        body = row.close - row.open
        if body > self.body_threshold:
            return {
                'type': 'BUY',
                'entry': row.close,
                'sl': row.close - self.stop_distance,
                'tp': row.close + self.target_distance,
            }
        elif body < -self.body_threshold:
            return {
                'type': 'SELL',
                'entry': row.close,
                'sl': row.close + self.stop_distance,
                'tp': row.close - self.target_distance,
            }
        return None
