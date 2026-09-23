from strategy.base import BaseStrategy
from indicators.oscillators import add_rsi
import pandas as pd


class SimpleRSIStrategy(BaseStrategy):
    supported_asset_classes = ('metal',)
    supported_timeframes = ('M5', 'M15')
    def __init__(self, params: dict = None):
        # Định nghĩa sẵn thông số mặc định ngay trong code
        default_params = {
            'rsi_period': 14,
            'rsi_threshold': 30,
            'start_hour': 0,
            'end_hour': 12
        }

        if params:
            default_params.update(params)

        super().__init__(default_params)

        self.rsi_period = self.params['rsi_period']
        self.rsi_threshold = self.params['rsi_threshold']
        self.start_hour = self.params['start_hour']
        self.end_hour = self.params['end_hour']
        self.prev_rsi = None

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = add_rsi(df, period=self.rsi_period, column_name='rsi')
        return df

    def generate_signal(self, row):
        current_rsi = getattr(row, 'rsi', None)

        if current_rsi is None or pd.isna(current_rsi):
            return None

        if self.prev_rsi is None:
            self.prev_rsi = current_rsi
            return None

        rsi_delta = current_rsi - self.prev_rsi
        self.prev_rsi = current_rsi

        # LỌC PHIÊN: Từ start_hour đến end_hour
        if not (self.start_hour <= row.time.hour < self.end_hour):
            return None

        # LOGIC: Quá bán và ngóc đầu lên
        if current_rsi < self.rsi_threshold and rsi_delta > 0:
            return {
                'type': 'BUY',
                'entry': row.close,
                'sl': row.close - 3.0,
                'tp': row.close + 6.0
            }

        return None
