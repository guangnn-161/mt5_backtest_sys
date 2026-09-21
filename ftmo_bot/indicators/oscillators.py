# File: indicators/oscillators.py
import pandas as pd


def add_rsi(df: pd.DataFrame, period: int = 14, column_name: str = 'rsi') -> pd.DataFrame:
    """
    Tính RSI dạng vector hóa siêu tốc và gắn vào DataFrame.
    """
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/period, adjust=False).mean()
    loss = -delta.where(delta < 0, 0).ewm(alpha=1/period, adjust=False).mean()

    rs = gain / loss
    df[column_name] = 100 - (100 / (1 + rs))
    return df
