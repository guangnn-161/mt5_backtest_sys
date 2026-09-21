# File: strategy/base.py
import pandas as pd


class BaseStrategy:
    def __init__(self, params: dict):
        self.params = params

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Được gọi 1 lần TRƯỚC khi backtest bắt đầu.
        Nơi chiến lược khai báo và tính toán sẵn các cột Indicator nó cần.
        Mặc định trả về df nguyên bản.
        """
        return df

    def generate_signal(self, row) -> dict:
        raise NotImplementedError("Phải ghi đè hàm này ở class con")
